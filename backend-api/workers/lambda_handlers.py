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
from typing import Any

from analysis import highlights_llm
from analysis.chatlog import clean_chatlog
from app.aws import factory, orchestration
from app.aws.config import get_attribution_config
from app.repository import get_repository
from app.settings import get_settings
from app.state import ProjectState, RenderState, advance_to_analyzing
from app.storage import get_storage
from workers import analysis_worker, chat_analysis_worker, composer_worker, creative_worker

log = logging.getLogger(__name__)

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
    return {
        "project_id": project_id,
        "timeline_version": timeline["version"],
        "status": ProjectState.READY_TO_EDIT.value,
    }


def mark_ready(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Explicit terminal (composer already set READY_TO_EDIT) for observability."""
    return {"project_id": _project_id(event), "status": ProjectState.READY_TO_EDIT.value}


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
        try:
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
            # analysis_source gate: chat-LOG projects produce highlights via the
            # synchronous POST /analyze (chat volume), NOT this auto video→Transcribe
            # path. Skipping StartExecution prevents the Transcribe run from clobbering
            # chat highlights or flipping the project to FAILED on the
            # ANALYZING→COMPOSING transition assert. Video-only projects
            # (analysis_source="transcribe", the default) proceed as before.
            project = get_repository().get_project(project_id)
            if project and project.get("analysis_source") == "chat":
                log.info(
                    "starter: project %s analysis_source=chat; skipping auto Transcribe StartExecution",
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

        # 2) analyze → COMPOSING (chat-relative timebase; no video probe in auto mode)
        advance_to_analyzing(repo, project_id, ProjectState(repo.get_project(project_id)["status"]))
        result = chat_analysis_worker.run(repo, project_id, chatlog)

        # 3) compose → READY_TO_EDIT (+ persist timeline for the render plane)
        timeline = composer_worker.run(repo, project_id)
        storage.put_json(
            settings.work_bucket,
            settings.timeline_key(tenant_id, project_id, timeline["version"]),
            timeline,
        )

        # 4) render: create record + StartExecution on the render workflow
        render = creative_worker.create_render_record(repo, project_id, timeline["version"])
        exec_arn = orchestration.start_render(render["render_id"], project_id, timeline["version"])
        started.append({
            "project_id": project_id,
            "render_id": render["render_id"],
            "execution_arn": exec_arn,
            "highlight_count": len(result["highlights"]),
        })
    return {"started": started}
