"""Tests for the analysis + composer workers (against moto-backed DynamoDB)."""
from __future__ import annotations

import pytest

from analysis.validate import load_sample
from app.repository import get_repository
from app.state import ProjectState, assert_project_transition
from workers import analysis_worker, composer_worker


def _seed_project(repo, target_ms: int = 60000) -> str:
    project_id = f"project-test-{target_ms}"
    repo.create_project({
        "project_id": project_id,
        "tenant_id": "demo",
        "user_id": "tester",
        "status": ProjectState.CREATED.value,
        "target_duration_ms": target_ms,
        "source_bucket": "video-editor-raw-test",
        "source_key": f"tenant=demo/project={project_id}/source/source.mp4",
        "latest_timeline_version": 0,
    })
    for state in (ProjectState.UPLOAD_PENDING, ProjectState.UPLOADING, ProjectState.ANALYZING):
        current = ProjectState(repo.get_project(project_id)["status"])
        assert_project_transition(current, state)
        repo.update_project(project_id, {"status": state.value})
    return project_id


def test_analysis_worker_persists_and_advances(aws) -> None:
    repo = get_repository()
    pid = _seed_project(repo)

    result = analysis_worker.run(repo, pid, load_sample("transcript.sample.json"))

    assert result["project_id"] == pid
    assert result["source_duration_ms"] == 240000
    stored = repo.list_highlights(pid)
    assert len(stored) == len(result["highlights"]) >= 1
    project = repo.get_project(pid)
    assert project["status"] == ProjectState.COMPOSING.value
    assert project["source_duration_ms"] == 240000


def test_composer_worker_creates_v1_and_ready(aws) -> None:
    repo = get_repository()
    pid = _seed_project(repo)
    analysis_worker.run(repo, pid, load_sample("transcript.sample.json"))

    timeline = composer_worker.run(repo, pid)

    assert timeline["version"] == 1
    project = repo.get_project(pid)
    assert project["status"] == ProjectState.READY_TO_EDIT.value
    assert project["latest_timeline_version"] == 1
    assert repo.get_timeline(pid)["version"] == 1


def test_recompose_appends_version_never_overwrites(aws) -> None:
    repo = get_repository()
    pid = _seed_project(repo)
    analysis_worker.run(repo, pid, load_sample("transcript.sample.json"))

    v1 = composer_worker.run(repo, pid)
    v2 = composer_worker.run(repo, pid, target=20000)

    assert v1["version"] == 1
    assert v2["version"] == 2
    assert repo.get_project(pid)["latest_timeline_version"] == 2
    # Both versions survive (append-only).
    assert repo.get_timeline(pid, 1)["version"] == 1
    assert repo.get_timeline(pid, 2)["version"] == 2
    assert repo.get_timeline(pid)["version"] == 2  # latest


def test_compose_without_highlights_raises(aws) -> None:
    repo = get_repository()
    pid = _seed_project(repo)
    # Move to COMPOSING without producing highlights.
    repo.update_project(pid, {"status": ProjectState.COMPOSING.value})
    with pytest.raises(ValueError):
        composer_worker.run(repo, pid)


def test_scores_survive_dynamo_roundtrip(aws) -> None:
    repo = get_repository()
    pid = _seed_project(repo)
    analysis_worker.run(repo, pid, load_sample("transcript.sample.json"))
    for h in repo.list_highlights(pid):
        # Numbers survive the float->Decimal->number round-trip (an integral
        # score like 1.0 legitimately comes back as int 1 — both valid JSON).
        assert isinstance(h["score"], (int, float))
        assert 0.0 <= h["score"] <= 1.0
