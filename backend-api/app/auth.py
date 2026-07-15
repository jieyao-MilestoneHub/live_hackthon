"""Authentication dependency.

M1 skeleton: a pluggable, dependency-light stub. It surfaces a ``Principal``
(tenant_id + user_id) from either a Bearer token (decoded WITHOUT signature
verification — dev only) or explicit ``X-Tenant-Id`` / ``X-User-Id`` headers,
falling back to ``demo`` / ``anonymous``.

TODO(auth): replace ``current_principal`` with real Cognito JWT verification
(fetch JWKS, verify signature/exp/aud, read ``sub`` and ``custom:tenant_id``).
The dependency signature stays the same, so routes need no changes.
"""
from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field

from fastapi import Depends, Header, HTTPException

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


def current_principal(
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_roles: str | None = Header(default=None),
) -> Principal:
    tenant_id = x_tenant_id
    user_id = x_user_id
    roles: list[str] = _parse_roles(x_roles)  # dev/testing fallback

    if authorization and authorization.lower().startswith("bearer "):
        claims = _decode_jwt_unverified(authorization.split(" ", 1)[1].strip())
        user_id = user_id or claims.get("sub")
        tenant_id = tenant_id or claims.get("custom:tenant_id") or claims.get("tenant_id")
        # Cognito puts group membership in the ``cognito:groups`` claim (a list).
        groups = claims.get("cognito:groups") or []
        if isinstance(groups, str):
            groups = _parse_roles(groups)
        roles = roles or [str(g).strip().lower() for g in groups]

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
