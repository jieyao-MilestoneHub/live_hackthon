"""Authentication — an IdP-agnostic seam.

The rest of the CLI only ever handles a **bearer token** (``--token`` /
``CRESTCUT_TOKEN`` / a cached login). That is what keeps the tool portable: swap
Cognito for Auth0/Keycloak/Entra and *only this file* changes. Today's provider
is Cognito ``USER_PASSWORD_AUTH`` (a direct port of the frontend ``lib/auth.ts``,
stdlib-only, no AWS SDK). The migration-safe upgrade is a browser OIDC
Authorization-Code + PKCE (or device-code) flow — it would slot in here behind the
same ``AuthProvider`` interface without touching any command.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Protocol

from .errors import AuthError


class AuthProvider(Protocol):
    def login(self, **kwargs: Any) -> str:
        """Return a bearer token (an OIDC/JWT IdToken)."""


class CognitoAuth:
    """Amazon Cognito ``InitiateAuth`` (AuthFlow USER_PASSWORD_AUTH) over plain HTTPS."""

    def __init__(self, region: str, client_id: str):
        self.region = region
        self.client_id = client_id

    def login(self, *, email: str, password: str, **_: Any) -> str:  # type: ignore[override]
        if not self.client_id:
            raise AuthError(
                "Cognito client id not configured",
                hint="set CRESTCUT_COGNITO_CLIENT_ID (or profiles.<name>.cognito_client_id in config)",
            )
        endpoint = f"https://cognito-idp.{self.region}.amazonaws.com/"
        body = json.dumps(
            {
                "AuthFlow": "USER_PASSWORD_AUTH",
                "ClientId": self.client_id,
                "AuthParameters": {"USERNAME": email, "PASSWORD": password},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/x-amz-json-1.1",
                "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = _cognito_detail(exc.read())
            raise AuthError(f"login failed: {detail}") from None
        except urllib.error.URLError as exc:
            raise AuthError(f"cannot reach Cognito ({getattr(exc, 'reason', exc)})") from None

        if data.get("ChallengeName"):
            raise AuthError(
                f"account requires an extra step ({data['ChallengeName']}); use a password-set account"
            )
        token = (data.get("AuthenticationResult") or {}).get("IdToken")
        if not token:
            raise AuthError("login response missing IdToken")
        return token


def _cognito_detail(body: bytes) -> str:
    try:
        doc = json.loads(body.decode("utf-8"))
        return doc.get("message") or doc.get("__type") or "authentication error"
    except (ValueError, UnicodeDecodeError):
        return "authentication error"
