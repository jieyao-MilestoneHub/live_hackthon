"""Render submission + Creative Planning Worker（demand.md §十一/§十二 前四步）。

``submit_render`` 是端點與 CLI 共用的入口:驗 timeline、凍結 timeline_version、
建 render_id（並預配 artifact_id 供 render_spec 輸出 key）、決定固定 effect_seed、
寫 Render item、Project→RENDER_REQUESTED,再跑 ``run``。

``run`` 依序推進 Render 狀態並產出三份計畫寫入 Work bucket:
CREATED → PLANNING_SUBTITLES(subtitle.v1)→ PLANNING_EFFECTS(effects.v1)→
Build Render Spec(render_spec.v1）→ QUEUED（等 FFmpeg Batch,M4）。

MVP:由 API request inline 呼叫（shim）。TODO(async):上雲改為 ai-task 佇列由
Lambda 跑同一 ``run``。
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from app.repository import ProjectRepository
from app.settings import get_settings
from app.state import (
    ProjectState,
    RenderState,
    assert_project_transition,
    assert_render_transition,
)
from app.storage import Storage
from analysis.annotations import build_annotations
from creative import build_render_spec, plan_effects, plan_subtitles


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _effect_seed(render_id: str) -> int:
    """Fixed seed derived from render_id → reproducible across retries."""
    return int.from_bytes(hashlib.sha256(render_id.encode()).digest()[:4], "big")


def create_render_record(
    repo: ProjectRepository,
    project_id: str,
    timeline_version: int | None = None,
) -> dict[str, Any]:
    """Freeze the timeline_version, allocate render_id/artifact_id/effect_seed,
    write the Render item (CREATED) and flip the Project to RENDER_REQUESTED.

    This is the control-plane part (no planning, no FFmpeg). The async render
    workflow then runs creative planning + the Batch encode. Raises ``KeyError``
    if the project is missing, ``ValueError`` if there is no timeline to render.
    """
    project = repo.get_project(project_id)
    if project is None:
        raise KeyError(f"project {project_id} not found")
    assert_project_transition(ProjectState(project["status"]), ProjectState.RENDER_REQUESTED)

    tv = int(timeline_version) if timeline_version is not None else int(
        project.get("latest_timeline_version") or 0
    )
    if repo.get_timeline(project_id, tv) is None:
        raise ValueError(f"project {project_id} has no timeline v{tv} to render")

    render_id = f"render-{uuid.uuid4().hex[:12]}"
    artifact_id = f"artifact-{uuid.uuid4().hex[:12]}"  # pre-allocated for render_spec outputs
    render = {
        "render_id": render_id,
        "project_id": project_id,
        "timeline_version": tv,
        "status": RenderState.CREATED.value,
        "current_stage": "Created",
        "effect_seed": _effect_seed(render_id),
        "artifact_id": artifact_id,
        "created_at": _now_iso(),
    }
    repo.put_render(project_id, render)
    repo.update_project(
        project_id,
        {"status": ProjectState.RENDER_REQUESTED.value, "latest_render_id": render_id},
    )
    return render


def submit_render(
    repo: ProjectRepository,
    storage: Storage,
    project_id: str,
    timeline_version: int | None = None,
) -> dict[str, Any]:
    """Create a render record then run creative planning inline (offline / CLI /
    the control-plane inline shim). Returns the Render item (status QUEUED)."""
    render = create_render_record(repo, project_id, timeline_version)
    return run(repo, storage, project_id, render["render_id"])


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
) -> dict[str, Any]:
    """Creative Planning: produce subtitle/effects/render_spec, advance to QUEUED."""
    settings = get_settings()
    project = repo.get_project(project_id)
    render = repo.get_render(project_id, render_id)
    if project is None or render is None:
        raise KeyError("project or render not found")

    tv = int(render["timeline_version"])
    timeline = repo.get_timeline(project_id, tv)
    if timeline is None:
        raise ValueError(f"timeline v{tv} missing for {project_id}")
    highlights = repo.list_highlights(project_id)
    tenant = project.get("tenant_id", "demo")
    effect_seed = int(render["effect_seed"])
    artifact_id = render["artifact_id"]
    work_bucket = settings.work_bucket

    # 起承轉合標註：優先用已落地/使用者編修過的 annotations.v1，否則就地由 highlights 產生。
    try:
        annotations = storage.get_json(work_bucket, settings.annotations_key(tenant, project_id))
    except KeyError:
        annotations = build_annotations(highlights, project_id=project_id)

    # 使用者在 timeline 上的字幕/特效設定（開放物件；字型/顏色/位置/強度覆寫）。
    subtitle_settings = timeline.get("subtitle_settings")
    effect_settings = timeline.get("effect_settings")

    # 1. Subtitle plan（兩層：逐字稿 caption + 爆點 keyword 動畫，套樣式）。
    _advance(repo, project_id, render_id, RenderState.PLANNING_SUBTITLES, "GenerateSubtitlePlan")
    subtitle = plan_subtitles(
        timeline, highlights, project_id, render_id,
        annotations=annotations, settings=subtitle_settings,
    )
    subtitle_key = settings.render_key(tenant, project_id, render_id, "subtitle.json")
    storage.put_json(work_bucket, subtitle_key, subtitle)

    # 2. Effect plan (deterministic via effect_seed；依 intensity 調強度)。
    _advance(repo, project_id, render_id, RenderState.PLANNING_EFFECTS, "GenerateEffectPlan")
    effects = plan_effects(timeline, effect_seed, project_id, render_id, settings=effect_settings)
    effect_plan_key = settings.render_key(tenant, project_id, render_id, "effect-plan.json")
    storage.put_json(work_bucket, effect_plan_key, effects)

    # 3. Build render spec (aggregates everything for the FFmpeg one-pass).
    inputs = {
        "timeline_key": settings.timeline_key(tenant, project_id, tv),
        "subtitle_key": subtitle_key,
        "effect_plan_key": effect_plan_key,
    }
    outputs = {
        "video_key": settings.artifact_output_key(tenant, project_id, artifact_id, "final.mp4"),
        "preview_key": settings.artifact_output_key(tenant, project_id, artifact_id, "preview.mp4"),
        "thumbnail_key": settings.artifact_output_key(tenant, project_id, artifact_id, "thumbnail.jpg"),
    }
    render_spec = build_render_spec(project, timeline, render_id, effect_seed, inputs, outputs)
    render_spec_key = settings.render_key(tenant, project_id, render_id, "render-spec.json")
    storage.put_json(work_bucket, render_spec_key, render_spec)

    # 4. Queued for the FFmpeg Batch job (M4).
    return _advance(
        repo,
        project_id,
        render_id,
        RenderState.QUEUED,
        "SubmitFFmpegBatchJob",
        {"render_spec_key": render_spec_key},
    )
