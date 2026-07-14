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
from dataclasses import dataclass

from fastapi import Header


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    user_id: str


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


def current_principal(
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
) -> Principal:
    tenant_id = x_tenant_id
    user_id = x_user_id

    if authorization and authorization.lower().startswith("bearer "):
        claims = _decode_jwt_unverified(authorization.split(" ", 1)[1].strip())
        user_id = user_id or claims.get("sub")
        tenant_id = tenant_id or claims.get("custom:tenant_id") or claims.get("tenant_id")

    return Principal(
        tenant_id=tenant_id or "demo",
        user_id=user_id or "anonymous",
    )
