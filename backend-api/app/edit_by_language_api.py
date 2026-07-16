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

from app.auth import Principal, current_principal
from app.aws import orchestration
from app.edit_planning import plan_edit_render
from app.repository import ProjectRepository, get_repository
from app.settings import get_settings
from app.state import InvalidTransition, moderation_allows_publish
from app.storage import Storage, get_storage

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

    # Plan the edit render: NL → effects.v1 + subtitle.v1 → render_spec → QUEUED
    # (route="edit"). Same work-bucket keys render_worker.run reads.
    try:
        render = plan_edit_render(
            repo, storage, project_id,
            instruction=body.instruction,
            timeline_version=body.timeline_version,
            model_tier=body.model_tier,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="project not found")
    except (ValueError, InvalidTransition) as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    render_id = render["render_id"]
    tv = int(render["timeline_version"])
    tenant = project.get("tenant_id", "demo")
    effects = storage.get_json(
        settings.work_bucket, settings.render_key(tenant, project_id, render_id, "effect-plan.json")
    )

    # Trigger the encode by starting the render workflow (async, same data flow as
    # the pipeline route; PlanCreative is skipped because the plan already exists).
    # Offline (no render SM configured) we return the QUEUED plan without encoding.
    started = False
    if body.encode and os.environ.get("RENDER_STATE_MACHINE_ARN"):
        orchestration.start_render(render_id, project_id, tv)
        started = True

    return EditByLanguageResponse(
        render_id=render_id,
        status=render["status"],
        timeline_version=tv,
        effects=effects,
        enqueued=started,
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
