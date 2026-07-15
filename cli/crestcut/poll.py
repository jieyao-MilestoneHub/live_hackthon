"""Wait-until-terminal helpers for the two async resources (project, render).

Polls the API with a fixed interval up to a deadline. Progress is written to
stderr (a spinner only when it's a TTY) so ``--json`` stdout stays clean.
"""
from __future__ import annotations

import sys
import time
from typing import Any, Iterable

from .api import EditorApi
from .errors import StateError, WaitTimeout

PROJECT_TERMINAL = {"READY_TO_EDIT", "ARTIFACT_READY", "FAILED"}
PROJECT_POLLABLE = {"UPLOADING", "ANALYZING", "COMPOSING", "RENDER_REQUESTED", "RENDERING"}
RENDER_TERMINAL = {"SUCCEEDED", "FAILED"}

_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class _Spinner:
    def __init__(self, printer: Any):
        self.on = bool(printer and printer.color and sys.stderr.isatty())
        self.i = 0

    def tick(self, label: str) -> None:
        if not self.on:
            return
        frame = _FRAMES[self.i % len(_FRAMES)]
        self.i += 1
        sys.stderr.write(f"\r{frame} {label}   ")
        sys.stderr.flush()

    def clear(self) -> None:
        if self.on:
            sys.stderr.write("\r" + " " * 60 + "\r")
            sys.stderr.flush()


def wait_project(
    api: EditorApi,
    project_id: str,
    *,
    until: Iterable[str] = PROJECT_TERMINAL,
    timeout: float = 180.0,
    interval: float = 2.0,
    printer: Any = None,
) -> dict[str, Any]:
    """Poll a project until its status is in ``until`` (or a terminal state)."""
    until = set(until)
    spinner = _Spinner(printer)
    deadline = time.monotonic() + timeout
    while True:
        project = api.get_project(project_id)
        status = project.get("status")
        if status == "FAILED":
            spinner.clear()
            raise StateError(
                f"project {project_id} failed: {project.get('error_message') or project.get('error_code') or 'unknown'}"
            )
        if status in until or status not in PROJECT_POLLABLE:
            spinner.clear()
            return project
        if time.monotonic() >= deadline:
            spinner.clear()
            raise WaitTimeout(
                f"timed out after {timeout:.0f}s waiting for project {project_id} (still {status})",
                hint="raise --timeout, or check `crestcut project get` / server logs",
            )
        spinner.tick(f"project {status} …")
        time.sleep(interval)


def wait_render(
    api: EditorApi,
    render_id: str,
    *,
    timeout: float = 300.0,
    interval: float = 2.0,
    printer: Any = None,
) -> dict[str, Any]:
    """Poll a render until SUCCEEDED / FAILED."""
    spinner = _Spinner(printer)
    deadline = time.monotonic() + timeout
    while True:
        render = api.get_render(render_id)
        status = render.get("status")
        if status == "FAILED":
            spinner.clear()
            raise StateError(
                f"render {render_id} failed: {render.get('error_message') or render.get('error_code') or 'unknown'}"
            )
        if status in RENDER_TERMINAL:
            spinner.clear()
            return render
        if time.monotonic() >= deadline:
            spinner.clear()
            raise WaitTimeout(
                f"timed out after {timeout:.0f}s waiting for render {render_id} (stage {render.get('current_stage')})",
                hint="raise --timeout, or check `crestcut render status`",
            )
        spinner.tick(f"render {render.get('current_stage') or status} …")
        time.sleep(interval)
