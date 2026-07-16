"""Edit-by-language 旁路 API（Step 1：contract-through-stub，離線可測）。

EDIT_PLANNER_LLM 未設 → StubEditPlanner（= plan_effects + plan_subtitles baseline），
所以不打 Bedrock。跑在既有 moto + USE_INMEMORY=0 的 conftest fixture 上。
"""
from __future__ import annotations

from analysis.validate import validate_effects, validate_subtitle


def test_edit_by_language_stub_produces_valid_plan(client, ready_project):
    project_id = ready_project
    resp = client.post(
        f"/projects/{project_id}/edit-by-language",
        json={"instruction": "把最爆點那段放大、關鍵字加動畫，節奏快一點", "encode": False},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "QUEUED"
    assert body["timeline_version"] >= 1
    assert body["enqueued"] is False

    effects = body["effects"]
    assert effects["schema_version"] == "effects.v1"
    validate_effects(effects)
    assert effects["effects"], "baseline 應至少每 clip 產一個特效"

    render_id = body["render_id"]

    # render 進到 QUEUED，且記下 render_spec_key
    from app.repository import get_repository

    render = get_repository().get_render(project_id, render_id)
    assert render["status"] == "QUEUED"
    assert render["render_spec_key"]

    # GET plan：讀回 work-bucket 的 effects.v1 + subtitle.v1
    plan = client.get(
        f"/projects/{project_id}/edit-by-language/plan", params={"render_id": render_id}
    )
    assert plan.status_code == 200, plan.text
    pj = plan.json()
    validate_effects(pj["effects"])
    validate_subtitle(pj["subtitle"])
    assert pj["subtitle"]["schema_version"] == "subtitle.v1"


def test_edit_by_language_starts_render_workflow(client, ready_project, monkeypatch):
    """WS3: the edit route renders through the SAME render SFN → Batch as pipeline
    (start_render), not the removed ai-task lane. encode=True → StartExecution."""
    from app.aws import orchestration

    monkeypatch.setenv(
        "RENDER_STATE_MACHINE_ARN", "arn:aws:states:us-east-1:000000000000:stateMachine:render-test"
    )
    calls: list[tuple] = []
    monkeypatch.setattr(
        orchestration, "start_render",
        lambda render_id, project_id, tv: calls.append((render_id, project_id, tv)) or "exec-arn",
    )
    resp = client.post(
        f"/projects/{ready_project}/edit-by-language",
        json={"instruction": "加特效", "encode": True},
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["enqueued"] is True
    assert calls and calls[0][1] == ready_project  # render SFN started for this project


def test_edit_by_language_missing_project_404(client):
    resp = client.post(
        "/projects/nope/edit-by-language", json={"instruction": "x", "encode": False}
    )
    assert resp.status_code == 404


def test_get_edit_plan_missing_404(client, ready_project):
    resp = client.get(
        f"/projects/{ready_project}/edit-by-language/plan",
        params={"render_id": "render-does-not-exist"},
    )
    assert resp.status_code == 404


def test_edit_render_encodes_via_shared_render_worker(client, ready_project):
    """WS3: an edit-route plan renders through the SAME render_worker.run the Batch
    path uses (the ai-task Lambda lane is removed). Produces a route='edit' artifact."""
    from app.repository import get_repository
    from app.storage import get_storage
    from workers import render_worker

    # Plan an edit render (encode deferred), then run the shared render worker on it.
    resp = client.post(
        f"/projects/{ready_project}/edit-by-language",
        json={"instruction": "加特效", "encode": False},
    )
    render_id = resp.json()["render_id"]
    artifact = render_worker.run(get_repository(), get_storage(), ready_project, render_id)
    assert artifact["status"] == "READY"
    assert artifact["route"] == "edit"
