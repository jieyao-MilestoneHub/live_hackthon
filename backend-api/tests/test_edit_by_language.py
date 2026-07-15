"""Edit-by-language 旁路 API（Step 1：contract-through-stub，離線可測）。

EDIT_PLANNER_LLM 未設 → StubEditPlanner（= plan_effects + plan_subtitles baseline），
所以不打 Bedrock。跑在既有 moto + USE_INMEMORY=0 的 conftest fixture 上。
"""
from __future__ import annotations

import json

from analysis.validate import validate_effects, validate_subtitle

REGION = "us-east-1"


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


def test_edit_by_language_enqueues_render(client, ready_project, monkeypatch):
    import boto3

    from app.aws import orchestration

    sqs = boto3.client("sqs", region_name=REGION)
    qurl = sqs.create_queue(QueueName="ai-task-test")["QueueUrl"]
    monkeypatch.setenv("AI_TASK_QUEUE_URL", qurl)
    orchestration.get_orchestrator.cache_clear()  # 重綁為 AwsOrchestrator（USE_INMEMORY=0）
    try:
        resp = client.post(
            f"/projects/{ready_project}/edit-by-language",
            json={"instruction": "加特效", "encode": True},
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["enqueued"] is True
        msgs = sqs.receive_message(QueueUrl=qurl).get("Messages", [])
        assert msgs, "應把 render 任務送進 ai-task 佇列"
    finally:
        orchestration.get_orchestrator.cache_clear()


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


def test_ai_task_render_consumer_dispatches_and_is_idempotent(client, ready_render):
    """ai-task SQS consumer 跑 render_worker.run（stub encoder），重投冪等跳過。"""
    from workers import lambda_handlers

    project_id, render_id = ready_render
    event = {"Records": [{"body": json.dumps(
        {"task": "render", "render_id": render_id, "project_id": project_id}
    )}]}

    out = lambda_handlers.ai_task_render(event)
    assert out["rendered"][0]["status"] == "SUCCEEDED"
    assert not out["rendered"][0].get("skipped")

    # 重投同一則 → 冪等 short-circuit（不重跑、不觸發狀態機 assert）
    out2 = lambda_handlers.ai_task_render(event)
    assert out2["rendered"][0]["skipped"] is True
