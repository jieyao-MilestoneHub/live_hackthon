"""Lambda entrypoints packaging the pure-function workers for the async data
plane (demand.md §七/§八: Analysis & Composition Workflow).

Each handler builds the REAL repo / storage / AWS adapters (USE_INMEMORY=0) and
calls the SAME ``*_worker.run`` — zero algorithm change. The seam is: the handler
does S3 / DynamoDB IO and adapter construction; the pure worker stays pure.

Deliberately imports only ``workers.*`` / ``analysis.*`` / ``app.repository`` /
``app.storage`` / ``app.aws`` — NOT ``app.main`` / Mangum — so a worker cold
start does not load FastAPI.

Packaging: ONE container image (the backend image), N Lambdas. Terraform sets
each function's ``image_config.command`` to ``workers.lambda_handlers.<name>``.

State machine (only pointers cross states; big docs live in S3 / DynamoDB):
    validate_source → probe_metadata → transcribe → detect_highlights
      → compose_timeline → mark_ready   (Catch → mark_failed)
The SQS-triggered ``starter`` sits in front (S3 event → StartExecution).
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from analysis import highlights_llm, moderation_policy
from analysis.chatlog import clean_chatlog
from app.aws import factory, orchestration
from app.aws.config import get_attribution_config
from app.progress import StepKey, get_progress_reporter
from app.repository import get_repository
from app.settings import get_settings
from app.state import (
    ModerationStatus,
    ProjectState,
    RenderState,
    advance_to_analyzing,
    moderation_allows_publish,
)
from app.storage import get_storage
from creative import DUAL_TRACK_ROUTES
from workers import (
    analysis_worker,
    chat_analysis_worker,
    composer_worker,
    creative_worker,
    render_worker,
)


def _dual_track_routes() -> tuple[str, ...]:
    """雙軌分流 routes：DUAL_TRACK **預設 off**（只跑 pipeline）；顯式設 on 才加 agent 路線。

    預設 off 的理由：``AgentPlanner`` 目前是 fail-open 佔位（委派 pipeline），預設開會讓部署後
    自動吐出一份「看似 agent、實為 pipeline 換種子」的誤導性成品。待 agent worktree 以
    ``register_planner("agent", RealAgentPlanner())`` 注入真正的 agent 後，於其 infra 設
    ``DUAL_TRACK=on`` 即啟用；planner-registry seam 本檔不需再改。
    """
    if os.environ.get("DUAL_TRACK", "off").strip().lower() in {"0", "false", "off", "no"}:
        return ("pipeline",)
    return DUAL_TRACK_ROUTES

log = logging.getLogger(__name__)

# Cap transcript segments fed to the text moderator to bound Bedrock cost/latency.
_MODERATION_TEXT_SEGMENT_CAP = 120

# Raw key layout (demand.md §五/§十六): tenant={t}/project={p}/source/source.mp4
_SOURCE_KEY_RE = re.compile(r"^tenant=(?P<tenant>[^/]+)/project=(?P<project>[^/]+)/source/")


def _project_id(event: dict[str, Any]) -> str:
    pid = event.get("project_id")
    if not pid:
        raise ValueError("event missing project_id")
    return pid


def _require_project(project_id: str) -> dict[str, Any]:
    project = get_repository().get_project(project_id)
    if project is None:
        raise KeyError(f"project {project_id} not found")
    return project


# --- Step Functions task handlers ------------------------------------------

def validate_source(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Confirm the project exists and drive it into ANALYZING.

    Uses ``update_project`` (no transition guard) so it can jump straight from
    UPLOAD_PENDING / UPLOADING → ANALYZING, matching run_pipeline.py's shortcut.
    """
    settings = get_settings()
    project_id = _project_id(event)
    project = _require_project(project_id)
    get_repository().update_project(project_id, {"status": ProjectState.ANALYZING.value})
    get_progress_reporter().step(
        project_id, StepKey.VALIDATING, phase=ProjectState.ANALYZING.value,
        facts={"inputs": ["來源影片"], "analysis": "驗證編碼與時間基準"},
    )
    bucket = project.get("source_bucket") or settings.raw_bucket
    key = project.get("source_key")
    return {
        "project_id": project_id,
        "tenant_id": project.get("tenant_id"),
        "media_uri": f"s3://{bucket}/{key}",
    }


def probe_metadata(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Near-nop for the happy path: real ``source_duration_ms`` comes from the
    transcript. Kept as an explicit state for observability / future ffprobe."""
    return {"project_id": _project_id(event)}


# --- Content moderation (§合規) --------------------------------------------

def _moderation_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _collect_moderation_text(settings, project: dict[str, Any], project_id: str) -> list[dict[str, Any]]:
    """Gather the user-facing / AI-generated text a moderation pass must scan:
    transcript utterances + generated highlight titles/reasons (which get burned
    into subtitles). Bounded to keep the Bedrock call cheap."""
    tenant_id = project.get("tenant_id") or "unknown"
    items: list[dict[str, Any]] = []
    try:
        transcript = get_storage().get_json(
            settings.work_bucket, settings.transcript_key(tenant_id, project_id)
        )
        for seg in (transcript.get("segments") or [])[:_MODERATION_TEXT_SEGMENT_CAP]:
            if seg.get("text"):
                items.append({"source": "transcript", "text": seg["text"]})
    except Exception:  # noqa: BLE001 — transcript may be absent (e.g. chat project)
        log.info("moderation: no transcript to scan for %s", project_id)
    for h in get_repository().list_highlights(project_id):
        if h.get("suggested_title"):
            items.append({"source": "highlight_title", "text": h["suggested_title"]})
        if h.get("reason"):
            items.append({"source": "highlight_reason", "text": h["reason"]})
    return items


def _persist_moderation(
    project_id: str,
    tenant_id: str,
    status: str,
    *,
    action: str,
    decided_by: str,
    visual: dict[str, Any] | None = None,
    text: dict[str, Any] | None = None,
    policy_version: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Write an immutable moderation.v1 audit event + the latest result doc, and
    set the project's mutable ``moderation_status``. Returns the event."""
    settings = get_settings()
    now = _moderation_now()
    event = {
        "schema_version": "moderation.v1",
        "moderation_id": f"mod-{uuid.uuid4().hex[:12]}",
        "project_id": project_id,
        "status": status,
        "action": action,
        "decided_by": decided_by,
        "decided_at": now,
        "note": note,
        "policy_version": policy_version,
        "visual": visual,
        "text": text,
        "created_at": now,
    }
    repo = get_repository()
    repo.put_moderation_event(project_id, event)
    try:
        get_storage().put_json(
            settings.work_bucket, settings.moderation_key(tenant_id, project_id), event
        )
    except Exception:  # noqa: BLE001 — audit item in Dynamo is the source of truth
        log.warning("moderation: failed to persist result doc for %s", project_id)
    repo.update_project(project_id, {"moderation_status": status})
    return event


def start_moderation(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Step Functions 'StartModeration' task: kick the async Rekognition visual
    scan on the source video and return immediately. Runs right after
    ValidateSource so it overlaps transcription (no added wall-clock). No-op when
    moderation is disabled."""
    settings = get_settings()
    project_id = _project_id(event)
    if not settings.moderation_enabled:
        return {"project_id": project_id, "status": "SKIPPED"}
    config = get_attribution_config()
    project = _require_project(project_id)
    bucket = project.get("source_bucket") or settings.raw_bucket
    key = project.get("source_key")
    media_uri = f"s3://{bucket}/{key}"
    try:
        job_id = factory.get_visual_moderation().start_visual_moderation(
            project_id, media_uri, min_confidence=config.moderation_min_confidence
        )
        get_repository().update_project(project_id, {"moderation_job_id": job_id})
    except Exception:  # noqa: BLE001 — visual scan is best-effort; text scan still runs at the gate
        log.exception("moderation: start_visual_moderation failed for %s", project_id)
        get_repository().update_project(project_id, {"moderation_job_id": None})
    get_progress_reporter().step(
        project_id, StepKey.MODERATION_SCAN, phase=ProjectState.ANALYZING.value,
        facts={"inputs": ["畫面", "字幕"], "analysis": "內容合規掃描"},
    )
    return {"project_id": project_id, "status": "STARTED"}


def moderation_decision(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Step Functions 'ModerationDecision' task (after DetectHighlights, before
    Compose): poll the visual scan, run the zh-TW text scan over transcript +
    AI-generated highlight copy, apply the tiered policy, persist an immutable
    audit record + moderation_status, and return the verdict for the Choice.

    Returns status ∈ PENDING (visual not ready → Wait loop) / ALLOWED / FLAGGED /
    BLOCKED."""
    settings = get_settings()
    project_id = _project_id(event)
    project = _require_project(project_id)
    tenant_id = project.get("tenant_id") or "unknown"

    if not settings.moderation_enabled:
        _persist_moderation(
            project_id, tenant_id, ModerationStatus.ALLOWED.value,
            action="SCAN", decided_by="system", note="moderation disabled",
        )
        return {"project_id": project_id, "status": ModerationStatus.ALLOWED.value}

    config = get_attribution_config()

    # 1) Visual: poll the async Rekognition job started earlier.
    job_id = project.get("moderation_job_id")
    if job_id:
        visual = factory.get_visual_moderation().poll_visual_moderation(job_id)
    else:
        visual = {"status": "SKIPPED", "labels": []}
    if visual["status"] == "IN_PROGRESS":
        return {"project_id": project_id, "status": "PENDING"}  # → Wait → re-poll
    visual_labels = visual.get("labels", [])

    # 2) Text: zh-TW classify transcript + AI-generated highlight copy (Bedrock).
    text_findings: list[dict[str, Any]] = []
    text_error = False
    text_items = _collect_moderation_text(settings, project, project_id)
    if text_items:
        try:
            text_findings = factory.get_text_moderation().moderate_text(text_items)
        except Exception:  # noqa: BLE001 — do not fail the pipeline on a Bedrock error
            log.exception("moderation: text scan failed for %s", project_id)
            text_error = True

    # 3) Tiered decision (pure policy).
    decision = moderation_policy.decide(
        visual_labels, text_findings,
        flag_threshold=config.moderation_flag_threshold,
        block_threshold=config.moderation_block_threshold,
    )
    status = decision["status"]
    note = None
    # Fail-safe: if the text scan errored and nothing else flagged it, escalate to
    # FLAGGED (needs human review) rather than silently ALLOWED.
    if text_error and status == ModerationStatus.ALLOWED.value:
        status = ModerationStatus.FLAGGED.value
        note = "text scan unavailable; flagged for manual review"

    _persist_moderation(
        project_id, tenant_id, status,
        action="SCAN", decided_by="system", policy_version=decision["policy_version"], note=note,
        visual={"provider": "rekognition", "job_status": visual["status"], "labels": visual_labels},
        text={"provider": "bedrock", "model_id": config.moderation_model_id, "findings": text_findings},
    )
    get_progress_reporter().step(
        project_id, StepKey.MODERATION_DECISION, phase=ProjectState.ANALYZING.value,
        facts={"inputs": ["視覺風險", "文字風險"], "analysis": "彙整判定發布分級", "verdict": status},
    )
    return {"project_id": project_id, "status": status}


def _moderate_chat_text(project_id: str, tenant_id: str, messages: list[dict[str, Any]]) -> str:
    """Inline zh-TW text moderation for the chat path (chat_starter bypasses the
    analysis SFN). Persists an audit event + moderation_status; returns the status.
    No-op → ALLOWED when moderation is disabled."""
    settings = get_settings()
    if not settings.moderation_enabled:
        _persist_moderation(
            project_id, tenant_id, ModerationStatus.ALLOWED.value,
            action="SCAN", decided_by="system", note="moderation disabled",
        )
        return ModerationStatus.ALLOWED.value
    config = get_attribution_config()
    items = [
        {"source": "chat", "text": m["text"]}
        for m in messages[:_MODERATION_TEXT_SEGMENT_CAP]
        if m.get("text")
    ]
    findings: list[dict[str, Any]] = []
    text_error = False
    if items:
        try:
            findings = factory.get_text_moderation().moderate_text(items)
        except Exception:  # noqa: BLE001
            log.exception("moderation: chat text scan failed for %s", project_id)
            text_error = True
    decision = moderation_policy.decide(
        [], findings,
        flag_threshold=config.moderation_flag_threshold,
        block_threshold=config.moderation_block_threshold,
    )
    status = decision["status"]
    note = None
    if text_error and status == ModerationStatus.ALLOWED.value:
        status = ModerationStatus.FLAGGED.value
        note = "text scan unavailable; flagged for manual review"
    _persist_moderation(
        project_id, tenant_id, status,
        action="SCAN", decided_by="system", policy_version=decision["policy_version"], note=note,
        text={"provider": "bedrock", "model_id": config.moderation_model_id, "findings": findings},
    )
    return status


def transcribe(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Step Functions 'StartTranscription' task: START the async Amazon Transcribe
    job and return immediately. The workflow then Waits + polls via
    ``poll_transcription`` — the Lambda no longer blocks for the whole job (the old
    ~10-min in-Lambda poll loop capped long videos and held a concurrency slot)."""
    settings = get_settings()
    config = get_attribution_config()
    project_id = _project_id(event)
    project = _require_project(project_id)
    bucket = project.get("source_bucket") or settings.raw_bucket
    key = project.get("source_key")
    media_uri = f"s3://{bucket}/{key}"

    factory.get_transcriber().start_transcription(  # Real when USE_INMEMORY=0
        project_id,
        media_uri,
        language_code=config.language_code,
        max_speakers=config.max_speaker_labels,
    )
    get_progress_reporter().step(
        project_id, StepKey.TRANSCRIBING, phase=ProjectState.ANALYZING.value,
        facts={"inputs": ["直播音訊"], "analysis": "語音轉文字＋說話者分離"},
    )
    return {"project_id": project_id, "status": "STARTED"}


def poll_transcription(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Step Functions 'GetTranscription' task: one non-blocking status check. On
    COMPLETED, write transcript.v1 to the work bucket and report COMPLETED so the
    Choice advances to DetectHighlights; otherwise report IN_PROGRESS (→ Wait loop)
    or FAILED (→ MarkFailed)."""
    settings = get_settings()
    config = get_attribution_config()
    project_id = _project_id(event)
    project = _require_project(project_id)
    tenant_id = project.get("tenant_id") or "unknown"

    result = factory.get_transcriber().poll_transcription(
        project_id, language_code=config.language_code
    )
    status = result["status"]
    if status == "COMPLETED":
        transcript = result["transcript"]
        transcript_key = settings.transcript_key(tenant_id, project_id)
        get_storage().put_json(settings.work_bucket, transcript_key, transcript)
        return {
            "project_id": project_id,
            "status": "COMPLETED",
            "transcript_key": transcript_key,
            "duration_ms": transcript.get("duration_ms"),
        }
    return {"project_id": project_id, "status": status, "reason": result.get("reason")}


def detect_highlights(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """transcript.v1 → highlights.v1 via the rule-based worker, then optional
    (gated) real Bedrock enrichment of the top highlights' title/reason."""
    settings = get_settings()
    repo = get_repository()
    project_id = _project_id(event)
    project = _require_project(project_id)
    tenant_id = project.get("tenant_id") or "unknown"

    transcript_key = event.get("transcript_key") or settings.transcript_key(tenant_id, project_id)
    transcript = get_storage().get_json(settings.work_bucket, transcript_key)

    result = analysis_worker.run(repo, project_id, transcript)  # persists, ANALYZING→COMPOSING

    if highlights_llm.enrich_enabled():
        enriched = highlights_llm.enrich(result["highlights"])
        if enriched is not result["highlights"]:
            repo.put_highlights(project_id, enriched)
            result = {**result, "highlights": enriched}

    get_progress_reporter().step(
        project_id, StepKey.DETECTING_HIGHLIGHTS, phase=ProjectState.COMPOSING.value,
        facts={
            "inputs": ["逐字稿", "聊天室反應"],
            "signals": ["情緒轉折", "關鍵字密度", "聊天室熱度"],
            "found": len(result["highlights"]),
        },
    )
    return {"project_id": project_id, "highlight_count": len(result["highlights"])}


def compose_timeline(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """highlights.v1 → initial timeline.v1 (append-only), COMPOSING→READY_TO_EDIT.
    Also persists the timeline JSON to the work bucket for the render plane (§十)."""
    settings = get_settings()
    repo = get_repository()
    project_id = _project_id(event)
    project = _require_project(project_id)
    tenant_id = project.get("tenant_id") or "unknown"

    timeline = composer_worker.run(repo, project_id)
    get_storage().put_json(
        settings.work_bucket,
        settings.timeline_key(tenant_id, project_id, timeline["version"]),
        timeline,
    )
    clips = len(timeline.get("clips", []) or [])
    get_progress_reporter().step(
        project_id, StepKey.COMPOSING, phase=ProjectState.COMPOSING.value,
        facts={"beats": "起承轉合", "clips": clips, "analysis": "編排初剪時間軸"},
    )
    return {
        "project_id": project_id,
        "timeline_version": timeline["version"],
        "status": ProjectState.READY_TO_EDIT.value,
    }


def mark_ready(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Explicit terminal (composer already set READY_TO_EDIT) for observability."""
    project_id = _project_id(event)
    clips = None
    try:
        timeline = get_repository().get_timeline(project_id)
        clips = len(timeline.get("clips", []) or []) if timeline else None
    except Exception:  # noqa: BLE001 — facts are best-effort; narration is additive
        clips = None
    get_progress_reporter().step(
        project_id, StepKey.READY, phase=ProjectState.READY_TO_EDIT.value,
        facts={"clips": clips}, status="DONE",
    )
    return {"project_id": project_id, "status": ProjectState.READY_TO_EDIT.value}


def mark_blocked(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Step Functions terminal for a moderation BLOCK: stop the pipeline before
    compose/render. moderation_status=BLOCKED was already set by moderation_decision;
    here we move the lifecycle to a terminal state (reusing FAILED with a distinct
    error_code, so no transition-graph change) so the frontend stops polling."""
    project_id = event.get("project_id")
    if project_id:
        try:
            get_repository().update_project(
                project_id,
                {
                    "status": ProjectState.FAILED.value,
                    "error_code": "MODERATION_BLOCKED",
                    "error_message": "內容審核未通過（已封鎖）",
                },
            )
        except KeyError:
            pass
    return {"project_id": project_id, "status": ModerationStatus.BLOCKED.value}


def mark_failed(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Step Functions Catch target: flip the project to FAILED with the error."""
    project_id = event.get("project_id") or (event.get("detail") or {}).get("project_id")
    error = event.get("error") or event.get("Error") or "analysis pipeline error"
    if project_id:
        try:
            get_repository().update_project(
                project_id,
                {"status": ProjectState.FAILED.value, "error_message": str(error)[:500]},
            )
        except KeyError:
            pass
    return {"project_id": project_id, "status": ProjectState.FAILED.value}


# --- Render workflow task handlers -----------------------------------------

def plan_creative(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Render SFN stage: Creative Planning (subtitle.v1 / effects.v1 /
    render_spec.v1 to the work bucket), advancing the render to QUEUED. The
    heavy FFmpeg encode runs next in the Batch container (workers.render)."""
    repo = get_repository()
    storage = get_storage()
    project_id = _project_id(event)
    render_id = event.get("render_id")
    if not render_id:
        raise ValueError("event missing render_id")
    render = creative_worker.run(repo, storage, project_id, render_id)
    return {"project_id": project_id, "render_id": render_id, "status": render["status"]}


def mark_render_failed(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Render SFN Catch target: mark the render FAILED and return the project to
    READY_TO_EDIT so the user can retry."""
    repo = get_repository()
    project_id = event.get("project_id")
    render_id = event.get("render_id")
    error = event.get("error") or event.get("Error") or "render pipeline error"
    if project_id and render_id:
        try:
            repo.update_render(
                project_id, render_id,
                {"status": RenderState.FAILED.value, "error_message": str(error)[:500]},
            )
        except KeyError:
            pass
    if project_id:
        # 雙軌分流：若另一路已產出成品（ARTIFACT_READY），別被這一路的失敗拖回 READY_TO_EDIT。
        try:
            project = repo.get_project(project_id)
            if project and project.get("status") != ProjectState.ARTIFACT_READY.value:
                repo.update_project(project_id, {"status": ProjectState.READY_TO_EDIT.value})
        except KeyError:
            pass
    return {"project_id": project_id, "render_id": render_id, "status": RenderState.FAILED.value}


# --- SQS-triggered starter (idempotent) ------------------------------------

def _parse_source_key(key: str | None) -> tuple[str, str] | None:
    if not key:
        return None
    match = _SOURCE_KEY_RE.match(key)
    if not match:
        return None
    return match.group("tenant"), match.group("project")


def starter(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """SQS handler: each record body is an EventBridge S3 "Object Created" event.
    Derives project_id from the key and StartExecution's the analysis workflow
    with a deterministic name (duplicate events collapse to one run)."""
    started: list[dict[str, Any]] = []
    # ReportBatchItemFailures: only the records that actually threw are re-driven,
    # instead of the whole SQS batch (batch_size=10). start_analysis is idempotent
    # (ExecutionAlreadyExists → no-op), so a redriven record is safe.
    failures: list[dict[str, str]] = []
    for record in event.get("Records", []):
        message_id = record.get("messageId")
        try:
            body = record.get("body")
            detail = json.loads(body) if isinstance(body, str) else (body or {})
            detail = detail.get("detail", detail)  # unwrap EventBridge envelope
            bucket = (detail.get("bucket") or {}).get("name")
            obj = detail.get("object") or {}
            key = obj.get("key")
            version_id = obj.get("version-id") or obj.get("versionId")
            parsed = _parse_source_key(key)
            if not parsed:
                continue
            tenant_id, project_id = parsed
            # Fire the auto video→Transcribe workflow ONLY for a project that exists
            # AND is explicitly analysis_source=transcribe. A chat project (or one not
            # yet created when source.mp4 lands) must never run Transcribe — it would
            # race/clobber the chat pipeline (and fails outright on >2 GB). The
            # video-only flow creates the project (analysis_source="transcribe", the
            # default) via the API before upload completes, so it still proceeds.
            project = get_repository().get_project(project_id)
            if not project or project.get("analysis_source", "transcribe") != "transcribe":
                log.info(
                    "starter: project %s absent or not analysis_source=transcribe; "
                    "skipping auto Transcribe StartExecution",
                    project_id,
                )
                continue
            if not version_id:
                # Raw bucket versioning should always supply version-id; without it
                # the execution name falls back to '{project_id}-v0', so a later
                # re-upload to the same project would be swallowed as a duplicate
                # rather than re-analyzed.
                log.warning(
                    "starter: missing version_id for project %s (key=%s); "
                    "re-upload dedupe may swallow a future run",
                    project_id,
                    key,
                )
            exec_arn = orchestration.start_analysis(
                project_id, tenant_id=tenant_id, bucket=bucket, key=key, version_id=version_id
            )
            started.append({"project_id": project_id, "execution_arn": exec_arn})
        except Exception:  # noqa: BLE001 — isolate one bad record from the batch
            log.exception("starter: record %s failed; will be retried", message_id)
            if message_id:
                failures.append({"itemIdentifier": message_id})
    return {"started": started, "batchItemFailures": failures}


def chat_starter(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """SQS handler for chat.csv uploads (EventBridge S3 'Object Created' on
    ``…/source/chat.csv``). Runs the FULL chat pipeline so a bare S3 drop yields an
    artifact: auto-create the project if missing → clean → analyze (chat volume) →
    compose → StartExecution the render workflow. Idempotent: skips a project that
    is already past the pre-analysis states."""
    settings = get_settings()
    repo = get_repository()
    storage = get_storage()
    started: list[dict[str, Any]] = []
    for record in event.get("Records", []):
        body = record.get("body")
        detail = json.loads(body) if isinstance(body, str) else (body or {})
        detail = detail.get("detail", detail)  # unwrap EventBridge envelope
        bucket = (detail.get("bucket") or {}).get("name") or settings.raw_bucket
        key = (detail.get("object") or {}).get("key")
        parsed = _parse_source_key(key or "")
        if not parsed:
            log.warning("chat_starter: unparseable key %s", key)
            continue
        tenant_id, project_id = parsed

        project = repo.get_project(project_id)
        if project is None:
            # Auto-create so a pure S3 drop (no prior POST /projects) works.
            target = int(os.environ.get("CHAT_TARGET_DURATION_MS", "30000"))
            repo.create_project({
                "project_id": project_id,
                "tenant_id": tenant_id,
                "user_id": "s3-auto",
                "title": None,
                "status": ProjectState.CREATED.value,
                "target_duration_ms": target,
                "analysis_source": "chat",
                "source_bucket": bucket,
                "source_key": settings.source_key(tenant_id, project_id),
                "latest_timeline_version": 0,
            })
            project = repo.get_project(project_id)

        status = ProjectState(project["status"])
        if status not in (
            ProjectState.CREATED, ProjectState.UPLOAD_PENDING,
            ProjectState.UPLOADING, ProjectState.ANALYZING,
        ):
            log.info("chat_starter: project %s already at %s; skip", project_id, status.value)
            continue

        # 1) chat.csv → chatlog.v1 (work bucket)
        csv_bytes = storage.get_bytes(bucket, key)
        chatlog = clean_chatlog(
            csv_bytes.decode("utf-8-sig", errors="replace"),
            project_id,
            source={"bucket": bucket, "key": key},
        )
        if not chatlog["messages"]:
            log.warning("chat_starter: 0 chat messages parsed for %s (key=%s)", project_id, key)
            repo.update_project(project_id, {
                "status": ProjectState.FAILED.value,
                "error_message": "no chat messages parsed from chat.csv",
            })
            continue
        storage.put_json(settings.work_bucket, settings.chatlog_key(tenant_id, project_id), chatlog)
        get_progress_reporter().step(
            project_id, StepKey.ANALYZING_CHATLOG, phase=ProjectState.ANALYZING.value,
            facts={"inputs": ["聊天室 LOG"], "signals": ["情緒起伏", "洗版熱區"],
                   "messages": len(chatlog["messages"])},
        )

        # 1b) content moderation (text) — chat runs inline (no analysis SFN), so
        # scan chat messages here. BLOCKED stops the pipeline before analysis.
        mod_status = _moderate_chat_text(project_id, tenant_id, chatlog["messages"])
        get_progress_reporter().step(
            project_id, StepKey.MODERATION_DECISION, phase=ProjectState.ANALYZING.value,
            facts={"inputs": ["聊天訊息"], "analysis": "內容合規判定", "verdict": mod_status},
        )
        if mod_status == ModerationStatus.BLOCKED.value:
            repo.update_project(project_id, {
                "status": ProjectState.FAILED.value,
                "error_code": "MODERATION_BLOCKED",
                "error_message": "內容審核未通過（已封鎖）",
            })
            log.info("chat_starter: project %s blocked by moderation; skip", project_id)
            started.append({"project_id": project_id, "status": ModerationStatus.BLOCKED.value})
            continue

        # 2) analyze → COMPOSING (chat-relative timebase; no video probe in auto mode)
        advance_to_analyzing(repo, project_id, ProjectState(repo.get_project(project_id)["status"]))
        result = chat_analysis_worker.run(repo, project_id, chatlog)
        get_progress_reporter().step(
            project_id, StepKey.DETECTING_HIGHLIGHTS, phase=ProjectState.COMPOSING.value,
            facts={"inputs": ["聊天室熱度", "情緒反應"], "signals": ["情緒轉折", "聊天室熱度"],
                   "found": len(result["highlights"])},
        )

        # 3) compose → READY_TO_EDIT (+ persist timeline for the render plane)
        timeline = composer_worker.run(repo, project_id)
        storage.put_json(
            settings.work_bucket,
            settings.timeline_key(tenant_id, project_id, timeline["version"]),
            timeline,
        )
        get_progress_reporter().step(
            project_id, StepKey.COMPOSING, phase=ProjectState.COMPOSING.value,
            facts={"beats": "起承轉合", "clips": len(timeline.get("clips", []) or []),
                   "analysis": "編排初剪時間軸"},
        )

        # 4) render: only auto-render when moderation permits publishing. A FLAGGED
        # chat project composes (editable) but waits for a moderator override before
        # it can render/download. When publishing IS allowed, 雙軌分流—each route gets
        # its own render record + StartExecution (route 掛在 render item 上供 plan_creative
        # 選規劃器；DUAL_TRACK 預設 off → 單一 pipeline)。
        if moderation_allows_publish(mod_status):
            for route in _dual_track_routes():
                render = creative_worker.create_render_record(
                    repo, project_id, timeline["version"], route=route
                )
                exec_arn = orchestration.start_render(render["render_id"], project_id, timeline["version"])
                started.append({
                    "project_id": project_id,
                    "render_id": render["render_id"],
                    "route": route,
                    "execution_arn": exec_arn,
                    "highlight_count": len(result["highlights"]),
                })
        else:
            log.info("chat_starter: project %s moderation=%s; composed, awaiting review before render",
                     project_id, mod_status)
            started.append({
                "project_id": project_id,
                "status": mod_status,
                "highlight_count": len(result["highlights"]),
            })
    return {"started": started}


def ai_task_render(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """SQS handler for the ai-task lane (edit-by-language encode).

    Each record body is ``{"task":"render","render_id":...,"project_id":...}``
    put on the queue by ``orchestration.enqueue_ai_task`` from the sidecar. Runs
    the real FFmpeg encode (this Lambda sets ``RENDER_ENCODER=ffmpeg``) via the
    SAME ``render_worker.run`` the Batch path uses — the plan (effects.v1 /
    subtitle.v1) was already written to the work bucket by the sidecar.

    Idempotent (SQS is at-least-once): a redelivered message for an already-
    SUCCEEDED render is a no-op instead of tripping ``render_worker._advance``'s
    state-transition assert."""
    repo = get_repository()
    storage = get_storage()
    done: list[dict[str, Any]] = []
    for record in event.get("Records", []):
        body = record.get("body")
        payload = json.loads(body) if isinstance(body, str) else (body or {})
        if payload.get("task") not in (None, "render"):
            log.info("ai_task_render: skipping task=%s", payload.get("task"))
            continue
        project_id = payload.get("project_id")
        render_id = payload.get("render_id")
        if not project_id or not render_id:
            log.warning("ai_task_render: record missing project_id/render_id: %s", payload)
            continue

        render = repo.get_render(project_id, render_id)
        if render is None:
            log.warning("ai_task_render: render %s not found for %s", render_id, project_id)
            continue
        if render.get("status") == RenderState.SUCCEEDED.value:
            log.info("ai_task_render: render %s already SUCCEEDED; skip (idempotent)", render_id)
            done.append({"render_id": render_id, "status": "SUCCEEDED", "skipped": True})
            continue

        artifact = render_worker.run(repo, storage, project_id, render_id)
        done.append({
            "render_id": render_id,
            "project_id": project_id,
            "artifact_id": artifact["artifact_id"],
            "status": "SUCCEEDED",
        })
    return {"rendered": done}
