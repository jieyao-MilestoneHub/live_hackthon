"""Auth precedence + production hardening (WS5).

Verifies that gateway-verified JWT claims are the only trusted identity source in
production, that the dev X-* headers are ignored when a gateway context is present
or when AUTH_DEV_HEADERS is off, and that the moderator role cannot be self-granted
via a request header once hardened.
"""
from __future__ import annotations

import pytest

from app import auth
from app.auth import Principal, current_principal
from app.settings import get_settings


class _FakeReq:
    """Minimal stand-in for starlette.Request — current_principal only reads
    ``request.scope`` (Mangum stores the raw AWS event under ``aws.event``)."""

    def __init__(self, event: dict | None = None) -> None:
        self.scope: dict = {} if event is None else {"aws.event": event}


def _claims_event(claims: dict) -> dict:
    return {"requestContext": {"authorizer": {"jwt": {"claims": claims}}}}


@pytest.fixture(autouse=True)
def _reset_settings():
    """Isolate AUTH_DEV_HEADERS env mutations from other tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- Gateway claims are authoritative; headers are ignored -----------------

def test_gateway_claims_take_precedence_over_headers() -> None:
    req = _FakeReq(_claims_event({
        "sub": "real-user",
        "custom:tenant_id": "real-tenant",
        "cognito:groups": ["moderator"],
    }))
    # Attacker sends spoofed identity/role headers alongside a valid token.
    p = current_principal(req, x_tenant_id="victim", x_user_id="hacker", x_roles="admin")
    assert p.user_id == "real-user"       # header ignored
    assert p.tenant_id == "real-tenant"   # header ignored
    assert p.is_moderator is True         # from the verified cognito:groups claim


def test_gateway_groups_as_stringified_list() -> None:
    # HTTP API JWT authorizer can deliver cognito:groups as "[moderator admin]".
    req = _FakeReq(_claims_event({"sub": "u", "cognito:groups": "[moderator admin]"}))
    p = current_principal(req)
    assert p.is_moderator is True


def test_gateway_non_moderator_cannot_be_elevated_by_header() -> None:
    req = _FakeReq(_claims_event({"sub": "plain-user", "cognito:groups": []}))
    p = current_principal(req, x_roles="moderator")
    assert p.is_moderator is False        # X-Roles must not elevate a real user


# --- Dev fallback (no gateway context) -------------------------------------

def test_dev_headers_honored_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_DEV_HEADERS", "1")
    get_settings.cache_clear()
    # authorization=None: called directly (not via FastAPI DI), so we pass the
    # header args explicitly instead of relying on Header(default=None) resolution.
    p = current_principal(_FakeReq(), authorization=None, x_user_id="mod-1", x_roles="moderator")
    assert p.user_id == "mod-1"
    assert p.is_moderator is True


def test_dev_headers_ignored_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_DEV_HEADERS", "0")
    get_settings.cache_clear()
    p = current_principal(_FakeReq(), authorization=None, x_user_id="mod-1", x_roles="moderator")
    assert p == Principal(tenant_id="demo", user_id="anonymous", roles=())
    assert p.is_moderator is False        # hardened + no gateway => anonymous


def test_override_forbidden_when_dev_headers_off(client, ready_project, monkeypatch) -> None:
    """With production hardening (AUTH_DEV_HEADERS=0) and no gateway claims, the
    moderator override endpoint must reject a header-forged moderator role."""
    monkeypatch.setenv("AUTH_DEV_HEADERS", "0")
    get_settings.cache_clear()
    client.post(f"/projects/{ready_project}/moderation/override", json={"decision": "ALLOW"})
    resp = client.post(
        f"/projects/{ready_project}/moderation/override",
        json={"decision": "ALLOW"},
        headers={"X-Roles": "moderator", "X-User-Id": "attacker"},
    )
    assert resp.status_code == 403
