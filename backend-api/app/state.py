"""Project / Render state machines (demand.md §十八).

demand.md lists the state *enums* only; the allowed transitions below are the
reconstructed graph from the two pipelines (§五/§六/§七 analysis, §十一 render).
Keep the enums in lockstep with ``ProjectState`` / ``RenderState`` in
``contracts/openapi.yaml``.
"""
from __future__ import annotations

from enum import Enum


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


def can_transition_render(current: RenderState, target: RenderState) -> bool:
    if target is RenderState.FAILED and current not in _RENDER_TERMINAL:
        return True
    return target in _RENDER_TRANSITIONS.get(current, set())


def assert_render_transition(current: RenderState, target: RenderState) -> None:
    if not can_transition_render(current, target):
        raise InvalidTransition(f"illegal Render transition {current.value} -> {target.value}")
