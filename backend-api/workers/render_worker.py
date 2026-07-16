"""FFmpeg Render Worker（stub）：render_spec.v1 → Artifact（demand.md §十一 後四步）。

demand.md 的 FFmpeg 重型編碼跑在 AWS Batch 容器(非控制面),且本機沒有真實上傳的
source.mp4,因此這裡是 **stub 版**:依 render_spec 產出 artifact 產物包(佔位媒體 +
由 subtitle.v1 真實轉出的 subtitle.vtt + timeline/render-spec 副本 + artifact.v1
manifest)寫入 Output bucket,建立 Artifact item,並走完 Render 狀態機至 SUCCEEDED、
Project → ARTIFACT_READY。真 FFmpeg 編碼由 Batch 容器替換本模組的 encode 段。

狀態:QUEUED → RENDERING → VALIDATING → PUBLISHING → SUCCEEDED。
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from analysis.validate import validate_artifact
from app.progress import StepKey, get_progress_reporter, report_render_stage
from app.repository import ProjectRepository
from app.settings import get_settings
from app.state import (
    ProjectState,
    RenderState,
    advance_project_if_allowed,
    assert_render_transition,
)
from app.storage import Storage

log = logging.getLogger(__name__)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _moderation_summary(
    repo: ProjectRepository, project: dict[str, Any], project_id: str
) -> dict[str, Any]:
    """artifact.v1 moderation summary: the project's current moderation_status
    (reflects any moderator OVERRIDE) plus counts + top categories from the latest
    SCAN audit event. Best-effort — on any read issue we still surface the status so
    a downloader always sees the moderation verdict on the manifest (WS1)."""
    summary: dict[str, Any] = {"status": project.get("moderation_status") or "PENDING"}
    try:
        scans = [e for e in repo.list_moderation_events(project_id) if e.get("action") == "SCAN"]
    except Exception:  # noqa: BLE001 — never fail a publish over an audit-read hiccup
        return summary
    if not scans:
        return summary
    last = scans[-1]
    labels = (last.get("visual") or {}).get("labels") or []
    findings = (last.get("text") or {}).get("findings") or []
    cats: list[str] = []
    for name in [lb.get("name") for lb in labels] + [f.get("category") for f in findings]:
        if name and name not in cats:
            cats.append(name)
    summary["visual_label_count"] = len(labels)
    summary["text_finding_count"] = len(findings)
    summary["top_categories"] = cats[:5]
    if last.get("policy_version"):
        summary["policy_version"] = last["policy_version"]
    if last.get("decided_at"):
        summary["decided_at"] = last["decided_at"]
    return summary


def _fmt_ts(ms: int) -> str:
    h, rem = divmod(int(ms), 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, msec = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{msec:03d}"


def _to_vtt(subtitle: dict[str, Any]) -> str:
    """Render subtitle.v1 cues as WebVTT (the plain-caption sidecar; also the
    burn fallback when no ASS is authored)."""
    lines = ["WEBVTT", ""]
    for cue in subtitle.get("cues", []):
        lines.append(f"{_fmt_ts(cue['start_ms'])} --> {_fmt_ts(cue['end_ms'])}")
        lines.append(cue["text"])
        lines.append("")
    return "\n".join(lines)


def _fmt_ts_ass(ms: int) -> str:
    """ASS timestamp H:MM:SS.cc (centiseconds)."""
    h, rem = divmod(int(ms), 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, msec = divmod(rem, 1000)
    return f"{h:d}:{m:02d}:{s:02d}.{msec // 10:02d}"


def _ass_escape(text: str) -> str:
    """Escape ASS special chars; newlines → hard break ``\\N``."""
    return (
        text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")
    )


# Per-word emphasis (爆點字) animation: overshoot to 145% then settle to 115%,
# golden accent + bold. libass override tags; {\r} resets to the base style.
_EMPH_OPEN = "{\\1c&H00E1FF&\\b1\\t(0,150,\\fscx145\\fscy145)\\t(150,320,\\fscx115\\fscy115)}"
_EMPH_CLOSE = "{\\r}"


def _ass_dialogue_text(cue: dict[str, Any]) -> str:
    """Escape cue text, then wrap each emphasis word with the pop-animation tag."""
    text = _ass_escape(cue.get("text", ""))
    for word in cue.get("emphasis_words", []) or []:
        w = _ass_escape(str(word))
        if not w or "{" in w or "}" in w or w not in text:
            continue
        text = text.replace(w, f"{_EMPH_OPEN}{w}{_EMPH_CLOSE}", 1)
    return text


def _to_ass(subtitle: dict[str, Any]) -> str:
    """Render subtitle.v1 as ASS/SSA so libass can animate emphasis words.

    PlayRes is authored at 1080x1920 (9:16); libass scales it to the actual burn
    resolution, so positions/sizes stay proportional for other aspects. A CJK
    font (``SUBTITLE_FONT``, default Noto Sans CJK TC) must exist in the burn
    container / ``SUBTITLE_FONTS_DIR`` or zh-TW renders as tofu.
    """
    font = os.environ.get("SUBTITLE_FONT", "Noto Sans CJK TC")
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
        "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font},96,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,"
        "4,2,2,60,60,140,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events = [
        "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}".format(
            start=_fmt_ts_ass(cue["start_ms"]),
            end=_fmt_ts_ass(cue["end_ms"]),
            text=_ass_dialogue_text(cue),
        )
        for cue in subtitle.get("cues", [])
    ]
    return "\n".join(header + events) + "\n"


def _placeholder(kind: str, render_id: str) -> bytes:
    # Stub media bytes — the real Batch FFmpeg container writes the encoded file here.
    return f"STUB {kind} for {render_id}\n".encode("utf-8")


# --- Encoder seam ----------------------------------------------------------
# The orchestration below (state machine, manifest, uploads) is identical for
# stub and real; ONLY the encode step differs. Offline / pytest use StubEncoder
# (placeholder bytes, no source, no ffmpeg); the AWS Batch container sets
# RENDER_ENCODER=ffmpeg to swap in the real one-pass FFmpeg encoder.

@dataclass
class EncodeInputs:
    render_id: str
    source: bytes | None          # raw source.mp4 bytes (legacy/stub; None when streamed)
    timeline: dict[str, Any]
    subtitle_vtt: str
    effects: dict[str, Any]
    render_spec: dict[str, Any]
    source_path: str | None = None   # local path to a streamed source.mp4 (real encoder)
    subtitle_ass: str | None = None  # pre-rendered ASS/SSA (edit lane; animated emphasis burn)
    subtitle: dict[str, Any] | None = None  # subtitle.v1 (+style) for styled ASS burn (main pipeline)


class Encoder(Protocol):
    needs_source: bool

    def encode(self, inputs: EncodeInputs) -> dict[str, bytes]:
        """Return {'final': bytes, 'preview': bytes, 'thumbnail': bytes}."""


class StubEncoder:
    """No-ffmpeg placeholder media (keeps offline/tests deterministic)."""

    needs_source = False

    def encode(self, inputs: EncodeInputs) -> dict[str, bytes]:
        return {
            "final": _placeholder("final.mp4", inputs.render_id),
            "preview": _placeholder("preview.mp4", inputs.render_id),
            "thumbnail": _placeholder("thumbnail.jpg", inputs.render_id),
        }


def get_encoder() -> Encoder:
    """Pick the encoder. Defaults to stub; the Batch container opts into ffmpeg
    via RENDER_ENCODER=ffmpeg so existing offline tests are unaffected.

    Fail-closed: when ``RENDER_REQUIRE_FFMPEG`` is set (the real Batch render path
    sets it), a missing / typo'd ``RENDER_ENCODER`` raises instead of silently
    falling back to the stub — so a config drift can never publish a placeholder
    as a real artifact."""
    kind = os.environ.get("RENDER_ENCODER", "stub").strip().lower()
    if kind == "ffmpeg":
        from workers.render.ffmpeg_encoder import FFmpegEncoder  # lazy: heavy deps

        return FFmpegEncoder()
    if _env_truthy("RENDER_REQUIRE_FFMPEG"):
        raise RuntimeError(
            f"RENDER_REQUIRE_FFMPEG is set but RENDER_ENCODER={kind!r} (not 'ffmpeg') — "
            "refusing to stub-render on the real path"
        )
    return StubEncoder()


def _advance(
    repo: ProjectRepository,
    project_id: str,
    render_id: str,
    target: RenderState,
    stage: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = repo.get_render(project_id, render_id)
    if current is None:
        raise KeyError(f"render {render_id} not found")
    assert_render_transition(RenderState(current["status"]), target)
    patch = {"status": target.value, "current_stage": stage}
    if extra:
        patch.update(extra)
    updated = repo.update_render(project_id, render_id, patch)
    report_render_stage(project_id, target.value)  # narrate this render sub-step (best-effort)
    return updated


def run(
    repo: ProjectRepository,
    storage: Storage,
    project_id: str,
    render_id: str,
    encoder: Encoder | None = None,
) -> dict[str, Any]:
    """Render a QUEUED render to a published Artifact. Returns the artifact.v1 manifest."""
    settings = get_settings()
    encoder = encoder or get_encoder()
    log.info("render %s: encoder=%s needs_source=%s", render_id,
             type(encoder).__name__, getattr(encoder, "needs_source", False))
    project = repo.get_project(project_id)
    render = repo.get_render(project_id, render_id)
    if project is None or render is None:
        raise KeyError("project or render not found")

    tv = int(render["timeline_version"])
    timeline = repo.get_timeline(project_id, tv)
    if timeline is None:
        raise ValueError(f"timeline v{tv} missing for {project_id}")
    render_spec_key = render.get("render_spec_key")
    if not render_spec_key:
        raise ValueError(f"render {render_id} has no render_spec (not planned)")

    tenant = project.get("tenant_id", "demo")
    artifact_id = render["artifact_id"]
    route = render.get("route", "pipeline")  # 雙軌分流：pipeline | agent（蓋到 artifact 上）
    render_spec = storage.get_json(settings.work_bucket, render_spec_key)
    subtitle = storage.get_json(
        settings.work_bucket, settings.render_key(tenant, project_id, render_id, "subtitle.json")
    )
    out = render_spec["outputs"]
    ob = settings.output_bucket

    # --- RENDERING: one-pass encode (stub or real FFmpeg per get_encoder) ---
    # 雙軌分流：兩路共用單一 Project.status；用 guarded advance 讓第二路的重複轉移
    # 被略過而非丟例外（Render item 自身狀態機仍嚴格）。
    advance_project_if_allowed(repo, project_id, ProjectState.RENDERING)
    _advance(repo, project_id, render_id, RenderState.RENDERING, "RenderClip", {"started_at": _now_iso()})

    subtitle_vtt = _to_vtt(subtitle)
    subtitle_ass = _to_ass(subtitle)
    effects: dict[str, Any] = {}
    if getattr(encoder, "needs_source", False):
        src = render_spec["source"]
        try:
            effects = storage.get_json(settings.work_bucket, render_spec["inputs"]["effect_plan_key"])
        except KeyError:
            effects = {}
        # Stream source.mp4 to a temp file (flat memory — the source can be many GB).
        with tempfile.TemporaryDirectory() as srcdir:
            source_path = os.path.join(srcdir, "source.mp4")
            storage.download_to_file(src["bucket"], src["key"], source_path)
            media = encoder.encode(EncodeInputs(
                render_id=render_id,
                source=None,
                source_path=source_path,
                timeline=timeline,
                subtitle_vtt=subtitle_vtt,
                subtitle_ass=subtitle_ass,
                effects=effects,
                render_spec=render_spec,
                subtitle=subtitle,
            ))
    else:
        media = encoder.encode(EncodeInputs(
            render_id=render_id,
            source=None,
            timeline=timeline,
            subtitle_vtt=subtitle_vtt,
            subtitle_ass=subtitle_ass,
            effects=effects,
            render_spec=render_spec,
            subtitle=subtitle,
        ))
    video_bytes = media["final"]
    # Fail-closed sanity check: a source-consuming (real) encoder must produce a
    # real MP4 — never a stub/text placeholder. Rejects a silent stub or a corrupt
    # encode BEFORE it is published as an artifact.
    if getattr(encoder, "needs_source", False) and b"ftyp" not in video_bytes[:64]:
        raise RuntimeError(
            f"render {render_id}: {type(encoder).__name__} produced non-MP4 output "
            f"({len(video_bytes)} bytes, no ftyp box) — refusing to publish a stub/corrupt artifact"
        )
    storage.put_bytes(ob, out["video_key"], video_bytes, "video/mp4")
    storage.put_bytes(ob, out["preview_key"], media["preview"], "video/mp4")
    storage.put_bytes(ob, out["thumbnail_key"], media["thumbnail"], "image/jpeg")

    subtitle_key = settings.artifact_output_key(tenant, project_id, artifact_id, "subtitle.vtt")
    storage.put_bytes(ob, subtitle_key, subtitle_vtt.encode("utf-8"), "text/vtt")
    timeline_key = settings.artifact_output_key(tenant, project_id, artifact_id, "timeline.json")
    storage.put_json(ob, timeline_key, timeline)
    spec_key = settings.artifact_output_key(tenant, project_id, artifact_id, "render-spec.json")
    storage.put_json(ob, spec_key, render_spec)

    # --- VALIDATING ---
    _advance(repo, project_id, render_id, RenderState.VALIDATING, "ValidateArtifact")

    # --- PUBLISHING: manifest (artifact.v1) ---
    manifest_key = settings.artifact_output_key(tenant, project_id, artifact_id, "manifest.json")
    artifact = {
        "schema_version": "artifact.v1",
        "artifact_id": artifact_id,
        "project_id": project_id,
        "render_id": render_id,
        "route": route,
        "timeline_version": tv,
        "status": "READY",
        "duration_ms": int(timeline["actual_duration_ms"]),
        "aspect_ratio": render_spec["aspect_ratio"],
        "resolution": render_spec["resolution"],
        "size_bytes": len(video_bytes),
        "checksum": "sha256:" + hashlib.sha256(video_bytes).hexdigest(),
        "moderation": _moderation_summary(repo, project, project_id),
        "files": {
            "video_key": out["video_key"],
            "preview_key": out["preview_key"],
            "thumbnail_key": out["thumbnail_key"],
            "subtitle_key": subtitle_key,
            "timeline_key": timeline_key,
            "render_spec_key": spec_key,
            "manifest_key": manifest_key,
        },
        "created_at": _now_iso(),
    }
    validate_artifact(artifact)
    storage.put_json(ob, manifest_key, artifact)
    _advance(repo, project_id, render_id, RenderState.PUBLISHING, "PublishArtifact")

    # Artifact item (§十七, flat keys) so GET /artifacts/{id}/download can presign.
    repo.put_artifact(project_id, {
        "artifact_id": artifact_id,
        "project_id": project_id,
        "render_id": render_id,
        "route": route,
        "timeline_version": tv,
        "status": "READY",
        "video_key": out["video_key"],
        "preview_key": out["preview_key"],
        "thumbnail_key": out["thumbnail_key"],
        "subtitle_key": subtitle_key,
        "manifest_key": manifest_key,
        "duration_ms": artifact["duration_ms"],
        "aspect_ratio": artifact["aspect_ratio"],
        "resolution": artifact["resolution"],
        "size_bytes": artifact["size_bytes"],
        "checksum": artifact["checksum"],
        "created_at": artifact["created_at"],
    })

    # --- SUCCEEDED + link on Project ---
    _advance(
        repo, project_id, render_id, RenderState.SUCCEEDED, "Done",
        {"artifact_id": artifact_id, "completed_at": _now_iso()},
    )
    # guarded：一路已把 project 推到 ARTIFACT_READY，第二路 no-op（不丟）。latest_artifact_id
    # 為 last-wins；GET /projects/{id}/artifacts（list_artifacts）才是雙軌下載的真相。
    advance_project_if_allowed(repo, project_id, ProjectState.ARTIFACT_READY)
    repo.update_project(project_id, {"latest_artifact_id": artifact_id})

    # 收尾：AI 全流程統整（一句話交代來源、依據、選段、成品）。best-effort facts。
    source_duration_ms = None
    clips = None
    try:
        proj = repo.get_project(project_id)
        source_duration_ms = (proj or {}).get("source_duration_ms")
        timeline = repo.get_timeline(project_id, tv)
        clips = len(timeline.get("clips", []) or []) if timeline else None
    except Exception:  # noqa: BLE001 — facts 皆 best-effort
        pass
    get_progress_reporter().step(
        project_id, StepKey.SUMMARY, phase=ProjectState.ARTIFACT_READY.value, status="DONE",
        facts={
            "source_duration_ms": source_duration_ms,
            "clips": clips,
            "output_duration_ms": artifact.get("duration_ms"),
            "signals": ["情緒高峰", "聊天室熱度"],
        },
    )
    return artifact
