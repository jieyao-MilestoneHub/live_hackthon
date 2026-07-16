"""Authentication dependency.

Identity comes from ONE of two sources, in strict priority:

1. **Production (behind the API Gateway HTTP API JWT authorizer):** the gateway
   verifies the Cognito token's signature / ``exp`` / ``aud`` / ``iss`` before the
   Lambda runs and delivers the verified claims in the Lambda event at
   ``requestContext.authorizer.jwt.claims`` (Mangum exposes it via
   ``request.scope["aws.event"]``). When those claims are present they are the
   ONLY trusted source — every request header (incl. ``X-Roles``) is
   attacker-controlled and is ignored. No in-app signature re-verification is
   needed (and no JWKS/crypto dependency), because the gateway already did it.

2. **Local / tests / direct invoke (no gateway claims):** fall back to the
   dev-only ``X-Tenant-Id`` / ``X-User-Id`` / ``X-Roles`` headers + an unverified
   Bearer decode. This path is gated by ``AUTH_DEV_HEADERS`` (default on for
   dev/pytest); the deployed Lambda sets ``AUTH_DEV_HEADERS=0`` so that even if the
   gateway authorizer were ever removed, requests could not fall through to
   header-based role self-grant — they'd resolve to ``demo`` / ``anonymous``.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass, field

from fastapi import Depends, Header, HTTPException, Request

from app.settings import get_settings

# Cognito groups that grant content-moderation review/override authority.
_MODERATOR_ROLES = {"moderator", "admin"}


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    user_id: str
    # Roles from the Cognito ``cognito:groups`` claim (or the X-Roles dev header).
    roles: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_moderator(self) -> bool:
        return any(r in _MODERATOR_ROLES for r in self.roles)


def _b64url_json(segment: str) -> dict:
    padded = segment + "=" * (-len(segment) % 4)
    raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    return json.loads(raw)


def _decode_jwt_unverified(token: str) -> dict:
    """Best-effort decode of a JWT payload WITHOUT verifying the signature.

    Dev-only convenience so a real Cognito token still yields sub/tenant.
    Returns {} on any malformed input.
    """
    try:
        _header, payload, _sig = token.split(".")
        return _b64url_json(payload)
    except (ValueError, binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _parse_roles(value: str | None) -> list[str]:
    if not value:
        return []
    return [r.strip().lower() for r in value.split(",") if r.strip()]


def _roles_from_groups(groups: object) -> list[str]:
    """Normalize a ``cognito:groups`` claim. It arrives as a real list, or — over
    the API Gateway JWT authorizer — as a stringified list ("[moderator admin]")
    or a comma/space-separated string, depending on the integration."""
    if not groups:
        return []
    if isinstance(groups, str):
        return [g.strip().lower() for g in re.split(r"[,\s]+", groups.strip("[] ")) if g.strip()]
    return [str(g).strip().lower() for g in groups]


def _gateway_claims(request: Request) -> dict | None:
    """Claims verified by the API Gateway JWT authorizer, delivered in the Lambda
    event at ``requestContext.authorizer.jwt.claims``. ``requestContext`` is set by
    API Gateway itself (a client cannot forge it), so these claims are trusted.
    Returns None when not running behind the gateway (local uvicorn / TestClient /
    direct Lambda invoke)."""
    event = request.scope.get("aws.event")
    if not isinstance(event, dict):
        return None
    try:
        claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
    except (KeyError, TypeError):
        return None
    return claims if isinstance(claims, dict) else None


def _principal_from_claims(claims: dict) -> Principal:
    return Principal(
        tenant_id=claims.get("custom:tenant_id") or claims.get("tenant_id") or "demo",
        user_id=claims.get("sub") or claims.get("username") or "anonymous",
        roles=tuple(_roles_from_groups(claims.get("cognito:groups"))),
    )


def current_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_roles: str | None = Header(default=None),
) -> Principal:
    # 1) Production: trust ONLY the gateway-verified claims; ignore all headers.
    claims = _gateway_claims(request)
    if claims is not None:
        return _principal_from_claims(claims)

    # 2) No gateway context (local / pytest / direct invoke). Dev headers are
    # honored only when AUTH_DEV_HEADERS is on; the deployed Lambda sets it off, so
    # a lost authorizer can never fall through to header-based role self-grant.
    if not get_settings().auth_dev_headers:
        return Principal(tenant_id="demo", user_id="anonymous", roles=())

    tenant_id = x_tenant_id
    user_id = x_user_id
    roles: list[str] = _parse_roles(x_roles)  # dev/testing fallback

    if authorization and authorization.lower().startswith("bearer "):
        token_claims = _decode_jwt_unverified(authorization.split(" ", 1)[1].strip())
        user_id = user_id or token_claims.get("sub")
        tenant_id = tenant_id or token_claims.get("custom:tenant_id") or token_claims.get("tenant_id")
        roles = roles or _roles_from_groups(token_claims.get("cognito:groups"))

    return Principal(
        tenant_id=tenant_id or "demo",
        user_id=user_id or "anonymous",
        roles=tuple(roles),
    )


def require_moderator(principal: Principal = Depends(current_principal)) -> Principal:
    """FastAPI dependency: 403 unless the caller holds a moderator/admin role."""
    if not principal.is_moderator:
        raise HTTPException(status_code=403, detail="moderator role required")
    return principal
