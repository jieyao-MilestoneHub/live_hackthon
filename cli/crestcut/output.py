"""Output plumbing following clig.dev + "a CLI for humans *and* agents":

  * **stdout = data, stderr = messages.** Machine-readable results (JSON/plain)
    and human result tables go to stdout; progress, steps, warnings and errors go
    to stderr. So ``crestcut … --json | jq`` never sees a spinner or a log line.
  * **Three surfaces.** ``--json`` (structured, stable keys), ``--plain`` (one
    record per line, tab-separated for grep/awk), or human (brief, coloured).
  * **TTY / NO_COLOR aware.** Colour and step markers only when stderr is a TTY
    and ``NO_COLOR`` is unset; JSON/plain never carry escape codes.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable

JSON = "json"
PLAIN = "plain"
HUMAN = "human"

_CODES = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}


def _default_plain(payload: Any) -> str:
    """Fallback --plain rendering: compact one-line JSON (grep-able)."""
    return json.dumps(payload, ensure_ascii=False)


class Printer:
    """Routes data to stdout and messages to stderr; honours mode + colour."""

    def __init__(self, mode: str = HUMAN, *, verbose: bool = False, color: bool | None = None):
        self.mode = mode
        self.verbose = verbose
        if color is None:
            color = os.environ.get("NO_COLOR") is None and sys.stderr.isatty()
        self.color = bool(color)

    # -- colour helper ------------------------------------------------------
    def paint(self, text: str, *names: str) -> str:
        if not self.color or not names:
            return text
        prefix = "".join(_CODES.get(n, "") for n in names)
        return f"{prefix}{text}{_CODES['reset']}"

    # -- messages → stderr --------------------------------------------------
    def note(self, msg: str) -> None:
        print(self.paint(msg, "dim"), file=sys.stderr)

    def step(self, msg: str) -> None:
        print(self.paint("→", "cyan") + " " + msg, file=sys.stderr)

    def success(self, msg: str) -> None:
        print(self.paint("✓", "green") + " " + msg, file=sys.stderr)

    def warn(self, msg: str) -> None:
        print(self.paint("! " + msg, "yellow"), file=sys.stderr)

    def error(self, msg: str) -> None:
        print(self.paint("✗ " + msg, "red"), file=sys.stderr)

    def debug(self, msg: str) -> None:
        if self.verbose:
            print(self.paint("· " + msg, "dim"), file=sys.stderr)

    # -- data → stdout ------------------------------------------------------
    def data(
        self,
        payload: Any,
        *,
        human: Callable[["Printer", Any], None] | None = None,
        plain: Callable[[Any], str] | None = None,
    ) -> None:
        if self.mode == JSON:
            json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, default=str)
            sys.stdout.write("\n")
        elif self.mode == PLAIN:
            text = (plain or _default_plain)(payload)
            sys.stdout.write(text if text.endswith("\n") else text + "\n")
        else:  # HUMAN
            if human is not None:
                human(self, payload)
            else:
                json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, default=str)
                sys.stdout.write("\n")

    def emit_error(self, err: dict[str, Any]) -> None:
        """Render a failure. Human message always to stderr; in JSON mode also emit
        a structured ``{"error": …}`` to stdout so an agent can parse it."""
        hint = err.get("hint")
        self.error(err.get("message", "error"))
        if hint:
            self.note("  hint: " + hint)
        if self.mode == JSON:
            json.dump({"error": err}, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
