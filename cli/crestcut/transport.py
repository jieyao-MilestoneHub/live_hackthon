"""The single HTTP seam — a tiny stdlib (``urllib``) client.

All network + auth specifics live here so the rest of the CLI is transport-clean:
this is the file you touch to swap REST for something else, add mTLS, etc. HTTP
status codes are mapped to the error taxonomy (→ exit codes); 5xx and connection
failures are retried with backoff.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import __version__
from .errors import AuthError, BackendError, CrestcutError, NotFoundError, StateError

_USER_AGENT = f"crestcut/{__version__}"


def _detail(body: bytes) -> str | None:
    """Pull FastAPI's ``{"detail": …}`` out of an error body when present."""
    try:
        doc = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    detail = doc.get("detail") if isinstance(doc, dict) else None
    if isinstance(detail, (dict, list)):
        return json.dumps(detail, ensure_ascii=False)
    return detail


class Transport:
    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 30.0,
        retries: int = 2,
        printer: Any = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.retries = max(0, retries)
        self.printer = printer

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        query: dict[str, Any] | None = None,
        expect_json: bool = True,
    ) -> Any:
        url = self.base_url + path
        if query:
            clean = {k: v for k, v in query.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)

        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")

        headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        attempt = 0
        while True:
            attempt += 1
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            if self.printer:
                self.printer.debug(f"{method} {url}")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                if not expect_json or not raw:
                    return raw
                return json.loads(raw.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read() if hasattr(exc, "read") else b""
                if exc.code >= 500 and attempt <= self.retries:
                    self._backoff(attempt)
                    continue
                raise self._map_http_error(method, path, exc.code, body) from None
            except urllib.error.URLError as exc:
                if attempt <= self.retries:
                    self._backoff(attempt)
                    continue
                raise BackendError(
                    f"cannot reach backend at {self.base_url} ({exc.reason})",
                    hint="is the backend running? try `crestcut up` (local) or check --api-base/--profile",
                ) from None

    def _backoff(self, attempt: int) -> None:
        delay = min(2.0, 0.25 * (2 ** (attempt - 1)))
        if self.printer:
            self.printer.debug(f"retrying in {delay:.2f}s (attempt {attempt})")
        time.sleep(delay)

    @staticmethod
    def _map_http_error(method: str, path: str, code: int, body: bytes) -> CrestcutError:
        detail = _detail(body) or f"HTTP {code}"
        where = f"{method} {path}"
        if code in (401, 403):
            return AuthError(
                f"{where}: not authorized ({detail})",
                hint="log in with `crestcut login`, or pass --token / set CRESTCUT_TOKEN",
            )
        if code == 404:
            return NotFoundError(f"{where}: {detail}")
        if code in (400, 409, 422):
            return StateError(f"{where}: {detail}")
        return BackendError(f"{where}: {detail}")
