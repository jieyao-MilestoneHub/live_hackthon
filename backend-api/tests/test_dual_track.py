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

def test_dual_track_routes_defaults_to_pipeline_only(monkeypatch) -> None:
    """預設（DUAL_TRACK 未設）只跑 pipeline —— 不自動吐佔位 agent 成品。"""
    from workers.lambda_handlers import _dual_track_routes

    monkeypatch.delenv("DUAL_TRACK", raising=False)
    assert _dual_track_routes() == ("pipeline",)


def test_dual_track_routes_opt_in_yields_both(monkeypatch) -> None:
    """顯式 DUAL_TRACK=on 才啟用雙軌（agent worktree 部署時的開關）。"""
    from workers.lambda_handlers import _dual_track_routes

    monkeypatch.setenv("DUAL_TRACK", "on")
    assert _dual_track_routes() == ("pipeline", "agent")


# --- planner registry ---------------------------------------------------------

def test_agent_planner_produces_valid_plans() -> None:
    from analysis.annotations import build_annotations
    from analysis.validate import load_sample
    from composer import compose_timeline

    hls = load_sample("highlights.sample.json")["highlights"]
    ann = build_annotations(hls, project_id="p")
    tl = compose_timeline("p", hls, 60000, annotations=ann)

    for route in ("pipeline", "agent"):
        planner = get_creative_planner(route)
        assert planner.route == route
        sub = planner.plan_subtitle(tl, hls, "p", f"render-{route}", annotations=ann)
        eff = planner.plan_effects(tl, 4242, "p", f"render-{route}")
        validate_subtitle(sub)
        validate_effects(eff)


def test_unknown_route_falls_back_to_pipeline() -> None:
    assert get_creative_planner("nope").route == "pipeline"
    assert get_creative_planner(None).route == "pipeline"


# --- fork + FSM (moto) --------------------------------------------------------

def test_dual_routes_no_invalid_transition_and_tagged(aws, ready_project) -> None:
    repo = get_repository()
    a = creative_worker.create_render_record(repo, ready_project, route="pipeline")
    # second route while project already RENDER_REQUESTED must NOT raise
    b = creative_worker.create_render_record(repo, ready_project, route="agent")
    assert a["route"] == "pipeline" and b["route"] == "agent"
    assert a["render_id"] != b["render_id"] and a["artifact_id"] != b["artifact_id"]


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
    assert [r["route"] for r in renders] == ["pipeline", "agent"]
    assert all(r["status"] == "QUEUED" for r in renders)
    for r in renders:
        render_worker.run(repo, storage, ready_project, r["render_id"])

    arts = repo.list_artifacts(ready_project)
    assert {a["route"] for a in arts} == {"pipeline", "agent"}
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
    assert {a["route"] for a in arts} == {"pipeline", "agent"}
    # each artifact presigns a download URL (route-agnostic endpoint)
    for a in arts:
        d = client.get(f"/artifacts/{a['artifact_id']}/download")
        assert d.status_code == 200 and d.json()["url"]


def test_list_artifacts_404_for_missing_project(client) -> None:
    assert client.get("/projects/nope/artifacts").status_code == 404
