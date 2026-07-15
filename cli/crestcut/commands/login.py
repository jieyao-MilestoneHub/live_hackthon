"""`crestcut login|logout` — obtain and cache a bearer token (IdP-agnostic seam).

The CLI only ever handles the resulting token; today's provider is Cognito, but
that is confined to ``auth.py`` so the IdP can be swapped without touching commands.
"""
from __future__ import annotations

import getpass

from ..auth import CognitoAuth
from ..config import clear_cached_token, write_cached_token
from ..errors import AuthError, UsageError


def register(subparsers, parent):
    lp = subparsers.add_parser("login", parents=[parent], help="log in and cache a bearer token")
    lp.add_argument("--email", help="account email / username")
    lp.add_argument("--password", help="password (omit to be prompted; avoid shell history)")
    lp.set_defaults(_handler=_login)

    op = subparsers.add_parser("logout", parents=[parent],
                               help="clear the cached token for the profile")
    op.set_defaults(_handler=_logout)


def _login(ctx, args):
    cfg = ctx.config
    if not cfg.cognito_client_id:
        raise AuthError("no identity provider configured for this profile",
                        hint="use --profile dev, or set CRESTCUT_COGNITO_CLIENT_ID")
    email = args.email
    password = args.password
    if not email:
        if cfg.no_input:
            raise UsageError("--email is required in non-interactive mode")
        email = input("email: ").strip()
    if not password:
        if cfg.no_input:
            raise UsageError("--password is required in non-interactive mode")
        password = getpass.getpass("password: ")

    provider = CognitoAuth(cfg.cognito_region or "us-east-1", cfg.cognito_client_id)
    ctx.printer.step("authenticating …")
    token = provider.login(email=email, password=password)
    path = write_cached_token(cfg.profile, token)
    ctx.printer.success(f"logged in as {email} (profile {cfg.profile})")
    ctx.printer.data({"profile": cfg.profile, "email": email, "token_cached": str(path)})


def _logout(ctx, args):
    cleared = clear_cached_token(ctx.config.profile)
    ctx.printer.success("logged out" if cleared else "no cached token")
    ctx.printer.data({"profile": ctx.config.profile, "cleared": cleared})
