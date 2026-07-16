"""雙軌分流（分流）：route 標記、fork、guarded FSM、list_artifacts、雙下載。"""
from __future__ import annotations

import pytest

from analysis.validate import validate_effects, validate_subtitle
from app.repository import get_repository
from app.state import (
    InvalidTransition,
    ProjectState,
    advance_project_if_allowed,
)
from app.storage import get_storage
from creative import get_creative_planner
from workers import creative_worker, render_worker


# --- guarded advance (unit) ---------------------------------------------------

class _StubRepo:
    def __init__(self, status: str) -> None:
        self._s = status

    def get_project(self, _pid: str) -> dict:
        return {"status": self._s}

    def update_project(self, _pid: str, patch: dict) -> None:
        self._s = patch["status"]


def test_advance_project_if_allowed_advances_and_noops() -> None:
    r = _StubRepo(ProjectState.READY_TO_EDIT.value)
    assert advance_project_if_allowed(r, "p", ProjectState.RENDER_REQUESTED) is True
    assert r._s == ProjectState.RENDER_REQUESTED.value
    # illegal / same-state → no-op, never raises
    assert advance_project_if_allowed(r, "p", ProjectState.RENDER_REQUESTED) is False
    assert advance_project_if_allowed(r, "p", ProjectState.ANALYZING) is False
    assert r._s == ProjectState.RENDER_REQUESTED.value


# --- dual-track routing flag (boundary) ---------------------------------------

def test_dual_track_routes_defaults_to_both(monkeypatch) -> None:
    """預設雙軌：分析後自動並行 pipeline + edit（edit 是真的 AI 剪接路線）。"""
    from workers.lambda_handlers import _dual_track_routes

    monkeypatch.delenv("DUAL_TRACK", raising=False)
    assert _dual_track_routes() == ("pipeline", "edit")


def test_dual_track_routes_escape_hatch_pipeline_only(monkeypatch) -> None:
    """逃生：DUAL_TRACK=off（或 pipeline）只跑 pipeline（demo 省算力時）。"""
    from workers.lambda_handlers import _dual_track_routes

    monkeypatch.setenv("DUAL_TRACK", "off")
    assert _dual_track_routes() == ("pipeline",)


# --- planner registry ---------------------------------------------------------

def test_pipeline_planner_produces_valid_plans() -> None:
    from analysis.annotations import build_annotations
    from analysis.validate import load_sample
    from composer import compose_timeline

    hls = load_sample("highlights.sample.json")["highlights"]
    ann = build_annotations(hls, project_id="p")
    tl = compose_timeline("p", hls, 60000, annotations=ann)

    planner = get_creative_planner("pipeline")
    assert planner.route == "pipeline"
    validate_subtitle(planner.plan_subtitle(tl, hls, "p", "render-pipeline", annotations=ann))
    validate_effects(planner.plan_effects(tl, 4242, "p", "render-pipeline"))


def test_unknown_or_edit_route_falls_back_to_pipeline() -> None:
    # The edit route pre-plans (app.edit_planning) so it never uses this registry;
    # get_creative_planner falls back to pipeline for it and any unknown route.
    assert get_creative_planner("edit").route == "pipeline"
    assert get_creative_planner("nope").route == "pipeline"
    assert get_creative_planner(None).route == "pipeline"


# --- fork + FSM (moto) --------------------------------------------------------

def test_dual_routes_no_invalid_transition_and_tagged(aws, ready_project) -> None:
    repo = get_repository()
    a = creative_worker.create_render_record(repo, ready_project, route="pipeline")
    # second route while project already RENDER_REQUESTED must NOT raise
    b = creative_worker.create_render_record(repo, ready_project, route="edit")
    assert a["route"] == "pipeline" and b["route"] == "edit"
    assert a["render_id"] != b["render_id"] and a["artifact_id"] != b["artifact_id"]


def test_mark_ready_auto_dual_track(aws, ready_project, monkeypatch) -> None:
    """WS3: the analysis terminal auto-fires pipeline + edit renders in parallel when
    moderation permits — each its own render SFN execution (StartExecution)."""
    from app.aws import orchestration
    from workers import lambda_handlers

    monkeypatch.setenv("RENDER_STATE_MACHINE_ARN", "arn:aws:states:us-east-1:0:stateMachine:render-test")
    monkeypatch.setenv("DUAL_TRACK", "on")
    calls: list[tuple] = []
    monkeypatch.setattr(
        orchestration, "start_render", lambda rid, pid, tv: calls.append((rid, pid, tv)) or "exec"
    )
    out = lambda_handlers.mark_ready({"project_id": ready_project})
    assert {r["route"] for r in out["renders"]} == {"pipeline", "edit"}
    assert len(calls) == 2  # both routes started the render SFN


def test_mark_ready_flagged_skips_auto_render(aws, ready_project, monkeypatch) -> None:
    """A FLAGGED project stays an editable draft — no auto-render until override."""
    from app.aws import orchestration
    from app.repository import get_repository
    from workers import lambda_handlers

    monkeypatch.setenv("RENDER_STATE_MACHINE_ARN", "arn:aws:states:us-east-1:0:stateMachine:render-test")
    get_repository().update_project(ready_project, {"moderation_status": "FLAGGED"})
    calls: list[tuple] = []
    monkeypatch.setattr(orchestration, "start_render", lambda *a: calls.append(a) or "exec")
    out = lambda_handlers.mark_ready({"project_id": ready_project})
    assert "renders" not in out and calls == []


def test_plan_creative_skips_preplanned_edit_render(aws, ready_project) -> None:
    """WS3: an edit render is pre-planned (render_spec_key set); plan_creative must
    NOT re-plan it (would overwrite the edit plan / trip the render FSM)."""
    from app.edit_planning import plan_edit_render
    from app.repository import get_repository
    from app.storage import get_storage
    from workers import lambda_handlers

    repo, storage = get_repository(), get_storage()
    render = plan_edit_render(repo, storage, ready_project, instruction="加特效")
    out = lambda_handlers.plan_creative(
        {"project_id": ready_project, "render_id": render["render_id"]}
    )
    assert out["status"] == "QUEUED"  # unchanged; skipped re-planning


def test_render_from_non_capable_state_raises(aws, client) -> None:
    from app.state import assert_project_transition  # noqa: F401 (ensure import path)

    project_id = client.post("/projects", json={"target_duration_ms": 30000}).json()["project_id"]
    repo = get_repository()
    for st in (ProjectState.UPLOAD_PENDING, ProjectState.UPLOADING, ProjectState.ANALYZING):
        repo.update_project(project_id, {"status": st.value})
    with pytest.raises(InvalidTransition):
        creative_worker.create_render_record(repo, project_id, route="pipeline")


def test_submit_render_routes_yields_two_artifacts(aws, ready_project) -> None:
    repo, storage = get_repository(), get_storage()
    renders = creative_worker.submit_render_routes(repo, storage, ready_project)
    assert [r["route"] for r in renders] == ["pipeline", "edit"]
    assert all(r["status"] == "QUEUED" for r in renders)
    for r in renders:
        render_worker.run(repo, storage, ready_project, r["render_id"])

    arts = repo.list_artifacts(ready_project)
    assert {a["route"] for a in arts} == {"pipeline", "edit"}
    assert len({a["artifact_id"] for a in arts}) == 2
    assert all(a["status"] == "READY" for a in arts)
    # a route failing/finishing did not corrupt the shared Project FSM
    assert repo.get_project(ready_project)["status"] == ProjectState.ARTIFACT_READY.value


# --- API: list + download both ------------------------------------------------

def test_list_artifacts_endpoint_and_download_both(client, ready_project) -> None:
    repo, storage = get_repository(), get_storage()
    renders = creative_worker.submit_render_routes(repo, storage, ready_project)
    for r in renders:
        render_worker.run(repo, storage, ready_project, r["render_id"])

    resp = client.get(f"/projects/{ready_project}/artifacts")
    assert resp.status_code == 200
    arts = resp.json()
    assert {a["route"] for a in arts} == {"pipeline", "edit"}
    # each artifact presigns a download URL (route-agnostic endpoint)
    for a in arts:
        d = client.get(f"/artifacts/{a['artifact_id']}/download")
        assert d.status_code == 200 and d.json()["url"]


def test_list_artifacts_404_for_missing_project(client) -> None:
    assert client.get("/projects/nope/artifacts").status_code == 404
