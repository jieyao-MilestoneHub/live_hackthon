"""chat_starter: a bare chat.csv S3 drop auto-runs the full chat pipeline
(auto-create → analyze → compose → StartExecution render), in-memory."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

RAW = "video-editor-raw-test"
WORK = "video-editor-work-test"
OUT = "video-editor-output-test"
RENDER_SM = "arn:aws:states:us-east-1:000000000000:stateMachine:render-test"
FIXTURE = Path(__file__).parent / "fixtures" / "chatlog_golden.sample.csv"


def _clear() -> None:
    from app.aws import orchestration
    from app.repository import get_repository
    from app.settings import get_settings
    from app.storage import get_storage

    for fn in (get_settings, get_repository, get_storage):
        fn.cache_clear()
    orchestration.cache_clear()


@pytest.fixture()
def inmem(monkeypatch):
    monkeypatch.setenv("USE_INMEMORY", "1")
    monkeypatch.setenv("RAW_BUCKET", RAW)
    monkeypatch.setenv("WORK_BUCKET", WORK)
    monkeypatch.setenv("OUTPUT_BUCKET", OUT)
    monkeypatch.setenv("RENDER_STATE_MACHINE_ARN", RENDER_SM)
    monkeypatch.setenv("CHAT_TARGET_DURATION_MS", "30000")
    _clear()
    yield
    _clear()


def _event(key: str) -> dict:
    detail = {"detail": {"bucket": {"name": RAW}, "object": {"key": key}}}
    return {"Records": [{"body": json.dumps(detail)}]}


def _seed_chat(pid: str, tenant: str = "demo") -> str:
    from app.settings import get_settings
    from app.storage import get_storage

    key = get_settings().chat_key(tenant, pid)
    get_storage().put_bytes(RAW, key, FIXTURE.read_bytes(), "text/csv")
    return key


def test_chat_starter_bare_drop_runs_full_pipeline(inmem) -> None:
    from app.aws import orchestration
    from app.repository import get_repository
    from workers import lambda_handlers

    pid = "project-e2e-test"
    key = _seed_chat(pid)

    res = lambda_handlers.chat_starter(_event(key))

    assert res["started"], "chat_starter started nothing"
    started = res["started"][0]
    assert started["project_id"] == pid
    assert started["highlight_count"] > 0

    repo = get_repository()
    proj = repo.get_project(pid)
    assert proj is not None
    assert proj["analysis_source"] == "chat"          # auto-created as a chat project
    assert proj["status"] == "RENDER_REQUESTED"        # walked to the render hand-off
    assert repo.get_render_by_id(started["render_id"]) is not None
    # StartExecution was issued on the render state machine (stub records it).
    assert any(RENDER_SM in e["arn"] for e in orchestration.get_orchestrator().executions)


def test_chat_starter_idempotent_second_drop(inmem) -> None:
    from workers import lambda_handlers

    pid = "project-idem-test"
    key = _seed_chat(pid)
    first = lambda_handlers.chat_starter(_event(key))
    assert first["started"], "first drop should run"
    # A duplicate event for a project already past pre-analysis is a no-op.
    second = lambda_handlers.chat_starter(_event(key))
    assert second["started"] == []


def test_chat_starter_partial_batch_isolates_failure(inmem) -> None:
    """WS4: a transient failure (chat.csv object missing → get_bytes raises) is
    reported as a batchItemFailure so SQS re-drives ONLY that record."""
    from app.settings import get_settings
    from workers import lambda_handlers

    pid = "project-missing-csv"
    key = get_settings().chat_key("demo", pid)  # deliberately NOT seeded into storage
    detail = {"detail": {"bucket": {"name": RAW}, "object": {"key": key}}}
    event = {"Records": [{"messageId": "bad-1", "body": json.dumps(detail)}]}

    res = lambda_handlers.chat_starter(event)
    assert res["batchItemFailures"] == [{"itemIdentifier": "bad-1"}]
    assert res["started"] == []


def test_chat_starter_empty_chat_marks_failed(inmem) -> None:
    from app.repository import get_repository
    from app.settings import get_settings
    from app.storage import get_storage
    from workers import lambda_handlers

    pid = "project-empty-test"
    key = get_settings().chat_key("demo", pid)
    # a CSV with no parseable chat rows (only a header)
    get_storage().put_bytes(RAW, key, b'"@timestamp",message,msg\n', "text/csv")

    res = lambda_handlers.chat_starter(_event(key))
    assert res["started"] == []
    assert get_repository().get_project(pid)["status"] == "FAILED"
