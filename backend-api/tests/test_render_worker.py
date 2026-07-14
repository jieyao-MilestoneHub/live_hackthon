"""Tests for the (stub) FFmpeg render worker + artifact publishing."""
from __future__ import annotations

from analysis.validate import validate_artifact
from app.repository import get_repository
from app.settings import get_settings
from app.state import ProjectState, RenderState
from app.storage import get_storage
from workers import render_worker
from workers.render_worker import _to_vtt


def test_render_worker_publishes_artifact(ready_render) -> None:
    project_id, render_id = ready_render
    repo, storage, settings = get_repository(), get_storage(), get_settings()

    artifact = render_worker.run(repo, storage, project_id, render_id)
    validate_artifact(artifact)
    aid = artifact["artifact_id"]

    # Render + Project reach terminal success states.
    assert repo.get_render(project_id, render_id)["status"] == RenderState.SUCCEEDED.value
    project = repo.get_project(project_id)
    assert project["status"] == ProjectState.ARTIFACT_READY.value
    assert project["latest_artifact_id"] == aid

    # Artifact item resolvable by bare id (top-level download route).
    item = repo.get_artifact_by_id(aid)
    assert item is not None
    assert item["video_key"] == artifact["files"]["video_key"]

    # Manifest + media written to the Output bucket; manifest re-validates.
    manifest = storage.get_json(settings.output_bucket, artifact["files"]["manifest_key"])
    validate_artifact(manifest)
    assert manifest["status"] == "READY"
    assert manifest["resolution"] == {"width": 1080, "height": 1920}


def test_cannot_rerender_succeeded(ready_render) -> None:
    project_id, render_id = ready_render
    repo, storage = get_repository(), get_storage()
    render_worker.run(repo, storage, project_id, render_id)
    # A second run would try SUCCEEDED -> RENDERING, which is illegal.
    import pytest

    from app.state import InvalidTransition

    with pytest.raises(InvalidTransition):
        render_worker.run(repo, storage, project_id, render_id)


def test_to_vtt_formatting() -> None:
    sub = {
        "cues": [
            {"start_ms": 0, "end_ms": 2400, "text": "第一句"},
            {"start_ms": 2400, "end_ms": 65_000, "text": "第二句"},
        ]
    }
    vtt = _to_vtt(sub)
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:02.400" in vtt
    assert "00:00:02.400 --> 00:01:05.000" in vtt
    assert "第一句" in vtt and "第二句" in vtt
