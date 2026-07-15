"""Async transcription split (start → poll) + starter partial-batch response.

Runs in-memory (USE_INMEMORY=1): the StubTranscriber reports the canned job as
immediately COMPLETED, so we exercise the handler contract the Step Functions
Wait/Poll/Choice loop depends on — without AWS."""
from __future__ import annotations

import json

import pytest

RAW = "video-editor-raw-test"
WORK = "video-editor-work-test"
OUT = "video-editor-output-test"
ANALYSIS_SM = "arn:aws:states:us-east-1:000000000000:stateMachine:analysis-test"


def _clear() -> None:
    from app.aws import factory, orchestration
    from app.repository import get_repository
    from app.settings import get_settings
    from app.storage import get_storage

    for fn in (get_settings, get_repository, get_storage):
        fn.cache_clear()
    factory.cache_clear()
    orchestration.cache_clear()


@pytest.fixture()
def inmem(monkeypatch):
    monkeypatch.setenv("USE_INMEMORY", "1")
    monkeypatch.setenv("RAW_BUCKET", RAW)
    monkeypatch.setenv("WORK_BUCKET", WORK)
    monkeypatch.setenv("OUTPUT_BUCKET", OUT)
    monkeypatch.setenv("ANALYSIS_STATE_MACHINE_ARN", ANALYSIS_SM)
    _clear()
    yield
    _clear()


def _seed_project(pid: str, tenant: str = "demo", analysis_source: str = "transcribe") -> None:
    from app.repository import get_repository
    from app.settings import get_settings
    from app.state import ProjectState

    settings = get_settings()
    get_repository().create_project({
        "project_id": pid,
        "tenant_id": tenant,
        "user_id": "tester",
        "title": None,
        "status": ProjectState.ANALYZING.value,
        "target_duration_ms": 30000,
        "analysis_source": analysis_source,
        "source_bucket": RAW,
        "source_key": settings.source_key(tenant, pid),
        "latest_timeline_version": 0,
    })


def test_transcribe_handler_starts_without_blocking(inmem) -> None:
    from workers import lambda_handlers

    _seed_project("project-start-1")
    out = lambda_handlers.transcribe({"project_id": "project-start-1"})
    # Start returns immediately with a marker — no transcript, no polling here.
    assert out["project_id"] == "project-start-1"
    assert out["status"] == "STARTED"
    assert "transcript_key" not in out


def test_poll_transcription_completes_and_writes_transcript(inmem) -> None:
    from app.settings import get_settings
    from app.storage import get_storage
    from workers import lambda_handlers

    _seed_project("project-poll-1")
    out = lambda_handlers.poll_transcription({"project_id": "project-poll-1"})
    assert out["status"] == "COMPLETED"  # the Choice advances to DetectHighlights
    # transcript.v1 persisted to the deterministic work-bucket key.
    key = get_settings().transcript_key("demo", "project-poll-1")
    assert out["transcript_key"] == key
    doc = get_storage().get_json(WORK, key)
    assert doc["schema_version"] == "transcript.v1"
    assert doc["segments"]


def test_starter_returns_partial_batch_shape(inmem) -> None:
    from app.settings import get_settings
    from workers import lambda_handlers

    _seed_project("project-starter-1")
    key = get_settings().source_key("demo", "project-starter-1")
    detail = {"detail": {"bucket": {"name": RAW}, "object": {"key": key, "version-id": "v1"}}}
    event = {"Records": [{"messageId": "m1", "body": json.dumps(detail)}]}

    out = lambda_handlers.starter(event)
    # ReportBatchItemFailures contract: both keys present; a clean run has no failures.
    assert "batchItemFailures" in out
    assert out["batchItemFailures"] == []
    assert out["started"] and out["started"][0]["project_id"] == "project-starter-1"
