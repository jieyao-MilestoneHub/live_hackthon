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
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from analysis.validate import validate_artifact
from app.repository import ProjectRepository
from app.settings import get_settings
from app.state import (
    ProjectState,
    RenderState,
    assert_project_transition,
    assert_render_transition,
)
from app.storage import Storage


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _fmt_ts(ms: int) -> str:
    h, rem = divmod(int(ms), 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, msec = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{msec:03d}"


def _to_vtt(subtitle: dict[str, Any]) -> str:
    """Render subtitle.v1 cues as WebVTT (the format FFmpeg burns in)."""
    lines = ["WEBVTT", ""]
    for cue in subtitle.get("cues", []):
        lines.append(f"{_fmt_ts(cue['start_ms'])} --> {_fmt_ts(cue['end_ms'])}")
        lines.append(cue["text"])
        lines.append("")
    return "\n".join(lines)


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
    source: bytes | None          # raw source.mp4 bytes (None for stub)
    timeline: dict[str, Any]
    subtitle_vtt: str
    effects: dict[str, Any]
    render_spec: dict[str, Any]


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
    via RENDER_ENCODER=ffmpeg so existing offline tests are unaffected."""
    if os.environ.get("RENDER_ENCODER", "stub").strip().lower() == "ffmpeg":
        from workers.render.ffmpeg_encoder import FFmpegEncoder  # lazy: heavy deps

        return FFmpegEncoder()
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
    return repo.update_render(project_id, render_id, patch)


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
    render_spec = storage.get_json(settings.work_bucket, render_spec_key)
    subtitle = storage.get_json(
        settings.work_bucket, settings.render_key(tenant, project_id, render_id, "subtitle.json")
    )
    out = render_spec["outputs"]
    ob = settings.output_bucket

    # --- RENDERING: one-pass encode (stub or real FFmpeg per get_encoder) ---
    assert_project_transition(ProjectState(project["status"]), ProjectState.RENDERING)
    repo.update_project(project_id, {"status": ProjectState.RENDERING.value})
    _advance(repo, project_id, render_id, RenderState.RENDERING, "RenderClip", {"started_at": _now_iso()})

    subtitle_vtt = _to_vtt(subtitle)
    source_bytes: bytes | None = None
    effects: dict[str, Any] = {}
    if getattr(encoder, "needs_source", False):
        src = render_spec["source"]
        source_bytes = storage.get_bytes(src["bucket"], src["key"])
        try:
            effects = storage.get_json(settings.work_bucket, render_spec["inputs"]["effect_plan_key"])
        except KeyError:
            effects = {}

    media = encoder.encode(EncodeInputs(
        render_id=render_id,
        source=source_bytes,
        timeline=timeline,
        subtitle_vtt=subtitle_vtt,
        effects=effects,
        render_spec=render_spec,
    ))
    video_bytes = media["final"]
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
        "timeline_version": tv,
        "status": "READY",
        "duration_ms": int(timeline["actual_duration_ms"]),
        "aspect_ratio": render_spec["aspect_ratio"],
        "resolution": render_spec["resolution"],
        "size_bytes": len(video_bytes),
        "checksum": "sha256:" + hashlib.sha256(video_bytes).hexdigest(),
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
    repo.update_project(
        project_id,
        {"status": ProjectState.ARTIFACT_READY.value, "latest_artifact_id": artifact_id},
    )
    return artifact
