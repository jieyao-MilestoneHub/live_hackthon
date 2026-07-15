"""Content moderation + compliance: pure policy, the decision worker, the
render/download gates, and the moderator override/audit-trail endpoints.

The endpoint/gate tests use the shared moto ``client`` fixture; the decision-worker
test runs in-memory (USE_INMEMORY=1) with a fake text moderator injected."""
from __future__ import annotations

import pytest

from analysis import moderation_policy


# --- Pure policy (offline, no AWS) -----------------------------------------

def test_policy_allowed_when_nothing_flagged() -> None:
    d = moderation_policy.decide([], [], flag_threshold=0.5, block_threshold=0.8)
    assert d["status"] == "ALLOWED"
    assert d["policy_version"] == moderation_policy.POLICY_VERSION


def test_policy_flags_mid_severity() -> None:
    d = moderation_policy.decide(
        [{"confidence": 62.0}], [], flag_threshold=0.5, block_threshold=0.8
    )
    assert d["status"] == "FLAGGED"


def test_policy_blocks_high_severity_text() -> None:
    d = moderation_policy.decide(
        [], [{"severity": 0.9}], flag_threshold=0.5, block_threshold=0.8
    )
    assert d["status"] == "BLOCKED"


def test_policy_takes_max_across_signals() -> None:
    d = moderation_policy.decide(
        [{"confidence": 40.0}], [{"severity": 0.85}], flag_threshold=0.5, block_threshold=0.8
    )
    assert d["status"] == "BLOCKED"  # text 0.85 dominates the weak visual 0.40


# --- Decision worker (in-memory + injected fake text moderator) -------------

class _FakeText:
    def __init__(self, findings):
        self._findings = findings

    def moderate_text(self, items):
        return self._findings


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
    monkeypatch.setenv("RAW_BUCKET", "video-editor-raw-test")
    monkeypatch.setenv("WORK_BUCKET", "video-editor-work-test")
    monkeypatch.setenv("OUTPUT_BUCKET", "video-editor-output-test")
    _clear()
    yield
    _clear()


def _seed(pid, analysis_source="transcribe"):
    from app.repository import get_repository
    from app.settings import get_settings
    from app.state import ProjectState

    s = get_settings()
    get_repository().create_project({
        "project_id": pid, "tenant_id": "demo", "user_id": "t", "title": None,
        "status": ProjectState.ANALYZING.value, "target_duration_ms": 30000,
        "analysis_source": analysis_source, "source_bucket": s.raw_bucket,
        "source_key": s.source_key("demo", pid), "latest_timeline_version": 0,
    })


def test_decision_blocks_and_persists_audit(inmem, monkeypatch) -> None:
    from app.aws import factory
    from app.repository import get_repository
    from workers import lambda_handlers as lh

    _seed("pb")
    get_repository().put_highlights("pb", [{"highlight_id": "h1", "suggested_title": "x", "reason": "y"}])
    monkeypatch.setattr(
        factory, "get_text_moderation",
        lambda: _FakeText([{"source": "highlight_title", "category": "hate", "severity": 0.95}]),
    )
    out = lh.moderation_decision({"project_id": "pb"})
    assert out["status"] == "BLOCKED"
    repo = get_repository()
    assert repo.get_project("pb")["moderation_status"] == "BLOCKED"
    events = repo.list_moderation_events("pb")
    assert len(events) == 1 and events[0]["action"] == "SCAN" and events[0]["status"] == "BLOCKED"


def test_decision_allowed_clean_content(inmem) -> None:
    from app.repository import get_repository
    from workers import lambda_handlers as lh

    _seed("pa")
    out = lh.moderation_decision({"project_id": "pa"})  # stub text/visual → clean
    assert out["status"] == "ALLOWED"
    assert get_repository().get_project("pa")["moderation_status"] == "ALLOWED"


def test_decision_disabled_is_allowed(inmem, monkeypatch) -> None:
    monkeypatch.setenv("MODERATION_ENABLED", "0")
    _clear()
    from workers import lambda_handlers as lh

    _seed("pd")
    assert lh.moderation_decision({"project_id": "pd"})["status"] == "ALLOWED"


# --- Render/download gates + override endpoint (moto client) ----------------

def _set_status(project_id, status):
    from app.repository import get_repository

    get_repository().update_project(project_id, {"moderation_status": status})


def test_render_blocked_when_flagged(client, ready_project) -> None:
    _set_status(ready_project, "FLAGGED")
    assert client.post(f"/projects/{ready_project}/renders", json={}).status_code == 403


def test_render_blocked_when_blocked(client, ready_project) -> None:
    _set_status(ready_project, "BLOCKED")
    assert client.post(f"/projects/{ready_project}/renders", json={}).status_code == 403


def test_override_requires_moderator_role(client, ready_project) -> None:
    _set_status(ready_project, "FLAGGED")
    # No role header → 403.
    assert client.post(
        f"/projects/{ready_project}/moderation/override", json={"decision": "ALLOW"}
    ).status_code == 403


def test_override_allows_render_and_logs_audit(client, ready_project) -> None:
    _set_status(ready_project, "FLAGGED")
    resp = client.post(
        f"/projects/{ready_project}/moderation/override",
        json={"decision": "ALLOW", "note": "reviewed, safe"},
        headers={"X-Roles": "moderator", "X-User-Id": "mod-1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "OVERRIDDEN"
    assert body["events"][-1]["action"] == "OVERRIDE"
    assert body["events"][-1]["decided_by"] == "mod-1"
    # Now renderable.
    assert client.post(f"/projects/{ready_project}/renders", json={}).status_code == 202


def test_get_moderation_returns_status_and_trail(client, ready_project) -> None:
    _set_status(ready_project, "FLAGGED")
    client.post(
        f"/projects/{ready_project}/moderation/override",
        json={"decision": "ALLOW"},
        headers={"X-Roles": "admin", "X-User-Id": "admin-1"},
    )
    view = client.get(f"/projects/{ready_project}/moderation").json()
    assert view["status"] == "OVERRIDDEN"
    assert any(e["action"] == "OVERRIDE" for e in view["events"])


def test_download_blocked_when_project_blocked(client, published_artifact) -> None:
    project_id, _render_id, artifact_id = published_artifact
    _set_status(project_id, "BLOCKED")
    assert client.get(f"/artifacts/{artifact_id}/download").status_code == 403
