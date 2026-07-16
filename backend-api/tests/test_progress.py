"""Tests for the AI progress-narration feature (progress_narrator + progress + API).

Covers: StubNarrator copy quality (info-dense, concise, no tech leakage), the
ProgressReporter append/order contract, RealNarrator fail-open, DynamoDB
immutability, the GET /projects/{id}/progress endpoint, and that the render
pipeline actually emits a chronological feed ending in an AI SUMMARY.
"""
from __future__ import annotations

import pytest

from app.progress import ProgressReporter, StepKey, report_render_stage
from app.progress_narrator import RealNarrator, StubNarrator
from app.repository import InMemoryProjectRepository

# code-leakage markers a human-facing progress line must never contain
_TECH_TOKENS = ("_", "def ", "()", "lambda", "=", "{", "}", "self.")


def test_stub_narrator_every_step_is_concise_and_clean() -> None:
    stub = StubNarrator()
    sample_facts = {
        "ANALYZING_CHATLOG": {"messages": 1280},
        "DETECTING_HIGHLIGHTS": {"found": 7},
        "COMPOSING": {"clips": 5},
        "READY": {"clips": 5},
        "SUMMARY": {"source_duration_ms": 4440000, "clips": 5, "output_duration_ms": 58000},
    }
    for step in StepKey:
        msg = stub.narrate(step=step.value, facts=sample_facts.get(step.value, {}))
        assert msg and msg.strip(), f"{step} produced empty message"
        assert len(msg) <= 55, f"{step} not concise: {len(msg)} chars — {msg}"
        for tok in _TECH_TOKENS:
            assert tok not in msg, f"{step} leaked tech token {tok!r}: {msg}"

    # Info density: the numeric facts must surface in the copy.
    assert "7" in stub.narrate(step="DETECTING_HIGHLIGHTS", facts={"found": 7})
    assert "5" in stub.narrate(step="COMPOSING", facts={"clips": 5})
    summary = stub.narrate(
        step="SUMMARY", facts={"source_duration_ms": 4440000, "clips": 5, "output_duration_ms": 58000}
    )
    assert "74" in summary and "5" in summary and "58" in summary


def test_stub_narrator_unknown_step_falls_back() -> None:
    assert StubNarrator().narrate(step="NOT_A_STEP", facts={}).strip()


def test_reporter_appends_in_chronological_order() -> None:
    repo = InMemoryProjectRepository()
    repo.create_project({"project_id": "project-p", "tenant_id": "t", "user_id": "u",
                         "status": "CREATED", "target_duration_ms": 30000})
    reporter = ProgressReporter(StubNarrator(), repo)

    reporter.step("project-p", StepKey.TRANSCRIBING, facts={}, phase="ANALYZING")
    reporter.step("project-p", StepKey.DETECTING_HIGHLIGHTS, facts={"found": 3}, phase="COMPOSING")
    reporter.step("project-p", StepKey.SUMMARY, facts={"clips": 3}, status="DONE")

    events = repo.list_progress_events("project-p")
    assert [e["step"] for e in events] == ["TRANSCRIBING", "DETECTING_HIGHLIGHTS", "SUMMARY"]
    assert [e["created_at"] for e in events] == sorted(e["created_at"] for e in events)
    assert all(e["schema_version"] == "progress.v1" for e in events)
    assert events[-1]["status"] == "DONE"
    assert all(e["progress_id"].startswith("prog-") for e in events)


def test_reporter_never_raises_when_repo_fails() -> None:
    class _BoomRepo:
        def put_progress_event(self, *a, **k):
            raise RuntimeError("db down")

    # step() must swallow the error — progress is an additive signal.
    ProgressReporter(StubNarrator(), _BoomRepo()).step("project-p", StepKey.READY, facts={})


def test_real_narrator_fails_open_to_stub() -> None:
    class _RaisingClient:
        def converse(self, **kwargs):
            raise RuntimeError("throttled / no access")

    # bypass __init__ (which builds a boto3 client) and inject a raising client
    n = RealNarrator.__new__(RealNarrator)
    n._client = _RaisingClient()
    n._model_id = "test-model"
    n._stub = StubNarrator()

    msg = n.narrate(step="DETECTING_HIGHLIGHTS", facts={"found": 9})
    # exactly the deterministic fallback — pipeline copy never disappears
    assert msg == StubNarrator().narrate(step="DETECTING_HIGHLIGHTS", facts={"found": 9})
    assert "9" in msg


def test_report_render_stage_mapping() -> None:
    import app.progress as progress_mod

    # every render sub-step maps to a StepKey; SUCCEEDED is deliberately unmapped
    # (the closer is the explicit AI SUMMARY, not a duplicate "done" line).
    assert progress_mod._RENDER_STATE_STEP["RENDERING"] is StepKey.RENDERING
    assert progress_mod._RENDER_STATE_STEP["PUBLISHING"] is StepKey.PUBLISHING
    assert progress_mod._RENDER_STATE_STEP.get("SUCCEEDED") is None
    # unmapped state is a safe no-op (must not raise)
    report_render_stage("project-none", "SUCCEEDED")


# --- moto-backed integration (real DynamoDB single-table) ------------------- #

def test_get_progress_endpoint_empty(client) -> None:
    pid = client.post("/projects", json={"target_duration_ms": 30000}).json()["project_id"]
    r = client.get(f"/projects/{pid}/progress")
    assert r.status_code == 200
    body = r.json()
    assert body["project_id"] == pid
    assert body["events"] == [] and body["latest"] is None


def test_get_progress_404_for_missing_project(client) -> None:
    assert client.get("/projects/nope/progress").status_code == 404


def test_progress_event_is_immutable(aws) -> None:
    from app.repository import get_repository

    repo = get_repository()  # DynamoProjectRepository under moto
    event = {
        "schema_version": "progress.v1", "progress_id": "prog-fixed",
        "project_id": "project-x", "step": "READY", "status": "DONE",
        "message": "初剪完成。", "created_at": "2026-07-16T00:00:00Z",
    }
    repo.put_progress_event("project-x", event)
    with pytest.raises(KeyError):
        repo.put_progress_event("project-x", event)  # same SK → conditional-put rejects


def test_render_pipeline_emits_feed_ending_in_summary(published_artifact, client) -> None:
    project_id, _render_id, _artifact_id = published_artifact
    r = client.get(f"/projects/{project_id}/progress")
    assert r.status_code == 200
    view = r.json()
    steps = [e["step"] for e in view["events"]]
    # render sub-steps narrated via the workers' _advance hook + the AI SUMMARY closer
    for expected in ("PLANNING_SUBTITLES", "PLANNING_EFFECTS", "QUEUED",
                     "RENDERING", "VALIDATING_ARTIFACT", "PUBLISHING", "SUMMARY"):
        assert expected in steps, f"missing {expected} in {steps}"
    assert view["latest"]["step"] == "SUMMARY"
    assert view["latest"]["message"].strip()
    # feed is chronological
    assert [e["created_at"] for e in view["events"]] == sorted(e["created_at"] for e in view["events"])


def test_compose_handler_emits_progress(ready_project, client) -> None:
    from workers import lambda_handlers

    lambda_handlers.compose_timeline({"project_id": ready_project})
    steps = [e["step"] for e in client.get(f"/projects/{ready_project}/progress").json()["events"]]
    assert "COMPOSING" in steps
