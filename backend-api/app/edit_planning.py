"""Edit-by-language planning as a reusable, FastAPI-free step (WS3).

The AI "edit" route and the deterministic "pipeline" route are the two creative
routes that produce artifacts. Both now render through the SAME data flow
(render Step Functions → AWS Batch FFmpeg); the only difference is HOW the
effects/subtitle plan is produced:

  * pipeline → ``creative_worker.run`` (rule-based CreativePlanner), planned by the
    render workflow's PlanCreative step.
  * edit     → ``plan_edit_render`` here: the NL edit planner (Claude on Bedrock
    when EDIT_PLANNER_LLM=1, else a deterministic Stub) writes the plan up front,
    so the render workflow SKIPS PlanCreative for it.

``kickoff_dual_track`` fires BOTH routes in parallel (one render_id + one render
SFN execution each). Used by the analysis pipeline's auto dual-track (mark_ready)
and the chat path; the on-demand endpoint reuses ``plan_edit_render``.

This module is import-safe for the worker Lambdas (no FastAPI / Mangum).
"""
from __future__ import annotations

import logging
from typing import Any

from analysis.validate import validate_effects, validate_subtitle
from app.aws import bedrock_edit_planner, orchestration
from app.settings import get_settings
from app.state import RenderState, assert_render_transition
from creative import DUAL_TRACK_ROUTES, build_render_spec

log = logging.getLogger(__name__)

# Default instruction the frontend always sends (users who don't type one still get
# an AI cut). Kept here so the auto dual-track path has the same default.
DEFAULT_EDIT_INSTRUCTION = "剪出幾個最精彩的爆梗片段，串成一支高光短片，節奏明快、爆點加字。"

# The "edit" route is the one that pre-plans (below); other routes plan in the
# render workflow. Kept as a set so callers can branch without hardcoding.
_PREPLANNED_ROUTES = {"edit"}


def _advance(repo, project_id: str, render_id: str, target: RenderState, stage: str,
             extra: dict[str, Any] | None = None) -> dict[str, Any]:
    current = repo.get_render(project_id, render_id)
    if current is None:
        raise KeyError(f"render {render_id} not found")
    assert_render_transition(RenderState(current["status"]), target)
    patch: dict[str, Any] = {"status": target.value, "current_stage": stage}
    if extra:
        patch.update(extra)
    return repo.update_render(project_id, render_id, patch)


def plan_edit_render(
    repo,
    storage,
    project_id: str,
    *,
    instruction: str,
    timeline_version: int | None = None,
    model_tier: str = "fast",
) -> dict[str, Any]:
    """Create an ``edit``-route render, run the NL edit planner, write the same
    work-bucket plan keys ``render_worker.run`` reads, build the render_spec, and
    advance the render to QUEUED. Returns the QUEUED Render item (ready for
    ``orchestration.start_render`` → render SFN, which skips PlanCreative)."""
    from workers.creative_worker import create_render_record  # deferred: avoid import cycle

    settings = get_settings()
    project = repo.get_project(project_id)
    if project is None:
        raise KeyError(f"project {project_id} not found")

    render = create_render_record(repo, project_id, timeline_version, route="edit")
    render_id = render["render_id"]
    tv = int(render["timeline_version"])
    timeline = repo.get_timeline(project_id, tv)
    if timeline is None:
        raise ValueError(f"timeline v{tv} missing for {project_id}")
    highlights = repo.list_highlights(project_id)
    tenant = project.get("tenant_id", "demo")
    effect_seed = int(render["effect_seed"])
    artifact_id = render["artifact_id"]
    work_bucket = settings.work_bucket

    plan = bedrock_edit_planner.get_edit_planner().plan_edit(
        instruction=instruction,
        timeline=timeline,
        highlights=highlights,
        effect_seed=effect_seed,
        project_id=project_id,
        render_id=render_id,
        model_tier=model_tier,
    )
    effects, subtitle = plan["effects"], plan["subtitle"]
    validate_effects(effects)
    validate_subtitle(subtitle)

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
    outputs = {
        "video_key": settings.artifact_output_key(tenant, project_id, artifact_id, "final.mp4"),
        "preview_key": settings.artifact_output_key(tenant, project_id, artifact_id, "preview.mp4"),
        "thumbnail_key": settings.artifact_output_key(tenant, project_id, artifact_id, "thumbnail.jpg"),
    }
    render_spec = build_render_spec(project, timeline, render_id, effect_seed, inputs, outputs)
    render_spec_key = settings.render_key(tenant, project_id, render_id, "render-spec.json")
    storage.put_json(work_bucket, render_spec_key, render_spec)

    return _advance(
        repo, project_id, render_id, RenderState.QUEUED,
        "SubmitEditByLanguageRender", {"render_spec_key": render_spec_key},
    )


def kickoff_dual_track(
    repo,
    storage,
    project_id: str,
    *,
    timeline_version: int,
    instruction: str,
    routes: tuple[str, ...] = DUAL_TRACK_ROUTES,
) -> list[dict[str, Any]]:
    """Fire every creative route in parallel, each as its own render_id + render
    SFN execution → one artifact per route. The ``edit`` route pre-plans via
    ``plan_edit_render`` (render SFN skips PlanCreative); other routes only create
    the render record and let the render SFN's PlanCreative plan them."""
    from workers.creative_worker import create_render_record  # deferred: avoid import cycle

    started: list[dict[str, Any]] = []
    for route in routes:
        if route in _PREPLANNED_ROUTES:
            render = plan_edit_render(
                repo, storage, project_id,
                instruction=instruction, timeline_version=timeline_version,
            )
        else:
            render = create_render_record(repo, project_id, timeline_version, route=route)
        render_id = render["render_id"]
        exec_arn = orchestration.start_render(render_id, project_id, timeline_version)
        started.append({
            "project_id": project_id, "render_id": render_id,
            "route": route, "execution_arn": exec_arn,
        })
    return started
