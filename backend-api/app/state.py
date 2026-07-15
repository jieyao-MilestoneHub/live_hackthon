"""Project / Render state machines (demand.md §十八).

demand.md lists the state *enums* only; the allowed transitions below are the
reconstructed graph from the two pipelines (§五/§六/§七 analysis, §十一 render).
Keep the enums in lockstep with ``ProjectState`` / ``RenderState`` in
``contracts/openapi.yaml``.
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class ProjectState(str, Enum):
    CREATED = "CREATED"
    UPLOAD_PENDING = "UPLOAD_PENDING"
    UPLOADING = "UPLOADING"
    ANALYZING = "ANALYZING"
    COMPOSING = "COMPOSING"
    READY_TO_EDIT = "READY_TO_EDIT"
    RENDER_REQUESTED = "RENDER_REQUESTED"
    RENDERING = "RENDERING"
    ARTIFACT_READY = "ARTIFACT_READY"
    FAILED = "FAILED"


class RenderState(str, Enum):
    CREATED = "CREATED"
    PLANNING_SUBTITLES = "PLANNING_SUBTITLES"
    PLANNING_EFFECTS = "PLANNING_EFFECTS"
    QUEUED = "QUEUED"
    RENDERING = "RENDERING"
    VALIDATING = "VALIDATING"
    PUBLISHING = "PUBLISHING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


# Any non-terminal Project state may transition to FAILED (pipeline error).
_PROJECT_TRANSITIONS: dict[ProjectState, set[ProjectState]] = {
    ProjectState.CREATED: {ProjectState.UPLOAD_PENDING},
    ProjectState.UPLOAD_PENDING: {ProjectState.UPLOADING},
    ProjectState.UPLOADING: {ProjectState.ANALYZING},
    ProjectState.ANALYZING: {ProjectState.COMPOSING},
    ProjectState.COMPOSING: {ProjectState.READY_TO_EDIT},
    # Edit loop stays in READY_TO_EDIT (PUT /timeline, POST /compose).
    ProjectState.READY_TO_EDIT: {ProjectState.READY_TO_EDIT, ProjectState.RENDER_REQUESTED},
    ProjectState.RENDER_REQUESTED: {ProjectState.RENDERING},
    ProjectState.RENDERING: {ProjectState.ARTIFACT_READY, ProjectState.READY_TO_EDIT},
    ProjectState.ARTIFACT_READY: {ProjectState.RENDER_REQUESTED, ProjectState.READY_TO_EDIT},
    ProjectState.FAILED: set(),
}

_RENDER_TRANSITIONS: dict[RenderState, set[RenderState]] = {
    RenderState.CREATED: {RenderState.PLANNING_SUBTITLES},
    RenderState.PLANNING_SUBTITLES: {RenderState.PLANNING_EFFECTS},
    RenderState.PLANNING_EFFECTS: {RenderState.QUEUED},
    RenderState.QUEUED: {RenderState.RENDERING},
    RenderState.RENDERING: {RenderState.VALIDATING},
    RenderState.VALIDATING: {RenderState.PUBLISHING},
    RenderState.PUBLISHING: {RenderState.SUCCEEDED},
    RenderState.SUCCEEDED: set(),
    RenderState.FAILED: set(),
}

_PROJECT_TERMINAL = {ProjectState.FAILED, ProjectState.ARTIFACT_READY}
_RENDER_TERMINAL = {RenderState.SUCCEEDED, RenderState.FAILED}


class InvalidTransition(ValueError):
    """Raised when an illegal state transition is attempted."""


def can_transition_project(current: ProjectState, target: ProjectState) -> bool:
    if target is ProjectState.FAILED and current not in _PROJECT_TERMINAL:
        return True
    return target in _PROJECT_TRANSITIONS.get(current, set())


def assert_project_transition(current: ProjectState, target: ProjectState) -> None:
    if not can_transition_project(current, target):
        raise InvalidTransition(f"illegal Project transition {current.value} -> {target.value}")


def advance_project_if_allowed(repo: Any, project_id: str, target: ProjectState) -> bool:
    """Set Project status to ``target`` only if the transition is legal; else no-op.

    For the dual-track (分流) render phase, two routes drive the same Project's single
    ``status`` field. The second route's redundant transitions (e.g. already at
    RENDER_REQUESTED / RENDERING / ARTIFACT_READY) would otherwise raise
    ``InvalidTransition``. This monotonic guard advances the shared status when the
    edge exists and quietly skips when it doesn't — never raising. Single-render
    callers behave identically (their edge is always legal). Returns whether it moved.
    ``repo`` is duck-typed to avoid a state→repository import cycle.
    """
    current = ProjectState(repo.get_project(project_id)["status"])
    if current is target or not can_transition_project(current, target):
        return False
    repo.update_project(project_id, {"status": target.value})
    return True


def can_transition_render(current: RenderState, target: RenderState) -> bool:
    if target is RenderState.FAILED and current not in _RENDER_TERMINAL:
        return True
    return target in _RENDER_TRANSITIONS.get(current, set())


def assert_render_transition(current: RenderState, target: RenderState) -> None:
    if not can_transition_render(current, target):
        raise InvalidTransition(f"illegal Render transition {current.value} -> {target.value}")


# Ordered pre-analysis states the pipeline walks through before ANALYZING.
_TO_ANALYZING_ORDER = [
    ProjectState.CREATED,
    ProjectState.UPLOAD_PENDING,
    ProjectState.UPLOADING,
    ProjectState.ANALYZING,
]


def advance_to_analyzing(repo: Any, project_id: str, current: ProjectState) -> None:
    """Walk a project from a pre-analysis state up to ANALYZING (mirrors the S3-event
    trigger). No-op if already ANALYZING. Raises ``InvalidTransition`` if the project
    is past analysis (COMPOSING/READY_TO_EDIT/…). ``repo`` is duck-typed to avoid a
    state→repository import cycle; shared by the API (/analyze) and the chat Starter.
    """
    if current is ProjectState.ANALYZING:
        return
    if current not in _TO_ANALYZING_ORDER[:-1]:
        raise InvalidTransition(f"cannot analyze from {current.value}")
    idx = _TO_ANALYZING_ORDER.index(current)
    for target in _TO_ANALYZING_ORDER[idx + 1:]:
        now = ProjectState(repo.get_project(project_id)["status"])
        assert_project_transition(now, target)
        repo.update_project(project_id, {"status": target.value})
