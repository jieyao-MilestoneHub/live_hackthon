"""Error taxonomy → process exit codes.

Per clig.dev / the 12-Factor CLI: return 0 on success and map the important
failure modes to distinct non-zero codes so scripts and AI agents can branch on
them. Every error carries an optional ``hint`` — an empathetic next step — which
is surfaced to humans (stderr) and to agents (the ``{"error": …}`` JSON object).
"""
from __future__ import annotations

from typing import Any

EXIT_OK = 0
EXIT_ERROR = 1        # generic / unexpected
EXIT_USAGE = 2        # bad invocation (argparse also exits 2)
EXIT_NOT_FOUND = 3    # resource missing or invalid state for the operation
EXIT_AUTH = 4         # authentication / authorization
EXIT_BACKEND = 5      # backend unreachable or 5xx
EXIT_TIMEOUT = 6      # a --wait / poll deadline elapsed


class CrestcutError(Exception):
    """Base class for expected, user-facing failures (clean exit, no traceback)."""

    exit_code = EXIT_ERROR

    def __init__(self, message: str, *, hint: str | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code or type(self).__name__

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            payload["hint"] = self.hint
        return payload


class UsageError(CrestcutError):
    exit_code = EXIT_USAGE


class NotFoundError(CrestcutError):
    exit_code = EXIT_NOT_FOUND


class StateError(CrestcutError):
    """The backend rejected the operation for the resource's current state (409/422)."""

    exit_code = EXIT_NOT_FOUND


class AuthError(CrestcutError):
    exit_code = EXIT_AUTH


class BackendError(CrestcutError):
    exit_code = EXIT_BACKEND


class WaitTimeout(CrestcutError):
    exit_code = EXIT_TIMEOUT
