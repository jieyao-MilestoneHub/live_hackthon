"""Edit-by-language 控制面 API（獨立 APIRouter，不編輯 main.py 主體）。

輸入「自然語言剪接需求（+ 既有 highlights/timeline）」→ 由 Claude 剪接師 planner 決定
特效落點與爆點關鍵字（effects.v1 + subtitle.v1）→ 寫進 render 的 work-bucket 計畫 keys
→ 觸發 ffmpeg-in-Lambda encode（走既有 ai-task 佇列）。不碰既有 /projects 與 analysis
pipeline；掛載方式（維護者在 main.py 加一行）：

    from app.edit_by_language_api import router as edit_by_language_router
    app.include_router(edit_by_language_router)

狀態/下載復用既有 GET /renders/{render_id} 與 GET /artifacts/{artifact_id}/download。
"""
from __future__ import annotations

import os
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from analysis.validate import validate_effects, validate_subtitle
from app.auth import Principal, current_principal
from app.aws import bedrock_edit_planner, orchestration
from app.repository import ProjectRepository, get_repository
from app.settings import get_settings
from app.state import (
    InvalidTransition,
    RenderState,
    assert_render_transition,
    moderation_allows_publish,
)
from app.storage import Storage, get_storage
from creative import build_render_spec
from workers.creative_worker import create_render_record

router = APIRouter(tags=["edit-by-language"])


class EditByLanguageRequest(BaseModel):
    instruction: str = Field(..., examples=["把最爆笑的那段放大、關鍵字加動畫，節奏快一點"])
    timeline_version: int | None = Field(default=None, description="省略 → 用 project 最新版")
    model_tier: Literal["fast", "quality"] = "fast"
    encode: bool = Field(default=True, description="false → 只規劃、不觸發 encode（乾跑）")


class EditByLanguageResponse(BaseModel):
    render_id: str
    status: str
    timeline_version: int
    effects: dict[str, Any]
    enqueued: bool


class EditPlan(BaseModel):
    render_id: str
    effects: dict[str, Any]
    subtitle: dict[str, Any]


def _advance(
    repo: ProjectRepository,
    project_id: str,
    render_id: str,
    target: RenderState,
    stage: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mirror creative_worker._advance（保持 render 狀態機不變，不編輯 creative_worker）。"""
    current = repo.get_render(project_id, render_id)
    if current is None:
        raise KeyError(f"render {render_id} not found")
    assert_render_transition(RenderState(current["status"]), target)
    patch: dict[str, Any] = {"status": target.value, "current_stage": stage}
    if extra:
        patch.update(extra)
    return repo.update_render(project_id, render_id, patch)


@router.post(
    "/projects/{project_id}/edit-by-language",
    response_model=EditByLanguageResponse,
    status_code=202,
)
def edit_by_language(
    project_id: str,
    body: EditByLanguageRequest,
    principal: Principal = Depends(current_principal),
    repo: ProjectRepository = Depends(get_repository),
    storage: Storage = Depends(get_storage),
) -> EditByLanguageResponse:
    settings = get_settings()
    project = repo.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    # 內容審核 gate：與主 render/download 路徑一致（app.main._assert_publishable）——BLOCKED /
    # 未複核 FLAGGED 不可經 NL-edit 旁路發布，避免旁路成為未受審核的發布通道。moderation 關閉時 no-op。
    if settings.moderation_enabled and not moderation_allows_publish(project.get("moderation_status")):
        raise HTTPException(
            status_code=403,
            detail=f"內容審核（{project.get('moderation_status') or 'PENDING'}）尚未通過，不可發布；需管理員複核",
        )

    # 1. 配 render_id/effect_seed、凍結 timeline_version、project→RENDER_REQUESTED（復用）。
    try:
        render = create_render_record(repo, project_id, body.timeline_version)
    except KeyError:
        raise HTTPException(status_code=404, detail="project not found")
    except (ValueError, InvalidTransition) as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    render_id = render["render_id"]
    tv = int(render["timeline_version"])
    timeline = repo.get_timeline(project_id, tv)
    if timeline is None:  # create_render_record 已檢查，防禦性再確認
        raise HTTPException(status_code=409, detail=f"timeline v{tv} missing")
    highlights = repo.list_highlights(project_id)
    tenant = project.get("tenant_id", "demo")
    effect_seed = int(render["effect_seed"])
    work_bucket = settings.work_bucket

    # 2. 剪接師 planner：NL → effects.v1 + subtitle.v1（Real fail-open 回 baseline）。
    planner = bedrock_edit_planner.get_edit_planner()
    plan = planner.plan_edit(
        instruction=body.instruction,
        timeline=timeline,
        highlights=highlights,
        effect_seed=effect_seed,
        project_id=project_id,
        render_id=render_id,
        model_tier=body.model_tier,
    )
    effects, subtitle = plan["effects"], plan["subtitle"]
    validate_effects(effects)   # defense-in-depth（planner 內已驗，這裡再擋一次）
    validate_subtitle(subtitle)

    # 3. 寫 render 的 work-bucket 計畫 keys（key 與 render_worker.run 讀取的完全一致）。
    _advance(repo, project_id, render_id, RenderState.PLANNING_SUBTITLES, "GenerateSubtitlePlan")
    subtitle_key = settings.render_key(tenant, project_id, render_id, "subtitle.json")
    storage.put_json(work_bucket, subtitle_key, subtitle)

    _advance(repo, project_id, render_id, RenderState.PLANNING_EFFECTS, "GenerateEffectPlan")
    effect_plan_key = settings.render_key(tenant, project_id, render_id, "effect-plan.json")
    storage.put_json(work_bucket, effect_plan_key, effects)

    inputs = {
        "timeline_key": settings.timeline_key(tenant, project_id, tv),
        "subtitle_key": subtitle_key,
        "effect_plan_key": effect_plan_key,
    }
    artifact_id = render["artifact_id"]
    outputs = {
        "video_key": settings.artifact_output_key(tenant, project_id, artifact_id, "final.mp4"),
        "preview_key": settings.artifact_output_key(tenant, project_id, artifact_id, "preview.mp4"),
        "thumbnail_key": settings.artifact_output_key(tenant, project_id, artifact_id, "thumbnail.jpg"),
    }
    render_spec = build_render_spec(project, timeline, render_id, effect_seed, inputs, outputs)
    render_spec_key = settings.render_key(tenant, project_id, render_id, "render-spec.json")
    storage.put_json(work_bucket, render_spec_key, render_spec)

    queued = _advance(
        repo, project_id, render_id, RenderState.QUEUED,
        "SubmitEditByLanguageRender", {"render_spec_key": render_spec_key},
    )

    # 4. 觸發 async encode（ffmpeg-in-Lambda consumer）。離線/無佇列時跳過。
    enqueued = False
    if body.encode and os.environ.get("AI_TASK_QUEUE_URL"):
        orchestration.enqueue_ai_task(
            {"task": "render", "render_id": render_id, "project_id": project_id, "tenant_id": tenant}
        )
        enqueued = True

    return EditByLanguageResponse(
        render_id=render_id,
        status=queued["status"],
        timeline_version=tv,
        effects=effects,
        enqueued=enqueued,
    )


@router.get("/projects/{project_id}/edit-by-language/plan", response_model=EditPlan)
def get_edit_plan(
    project_id: str,
    render_id: str,
    principal: Principal = Depends(current_principal),
    repo: ProjectRepository = Depends(get_repository),
    storage: Storage = Depends(get_storage),
) -> EditPlan:
    settings = get_settings()
    project = repo.get_project(project_id)
    render = repo.get_render(project_id, render_id)
    if project is None or render is None:
        raise HTTPException(status_code=404, detail="render not found")
    tenant = project.get("tenant_id", "demo")
    try:
        effects = storage.get_json(
            settings.work_bucket, settings.render_key(tenant, project_id, render_id, "effect-plan.json")
        )
        subtitle = storage.get_json(
            settings.work_bucket, settings.render_key(tenant, project_id, render_id, "subtitle.json")
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="edit plan not found; run edit-by-language first")
    return EditPlan(render_id=render_id, effects=effects, subtitle=subtitle)
