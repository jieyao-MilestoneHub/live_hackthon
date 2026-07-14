"""Pydantic v2 models mirroring contracts/openapi.yaml (浪 LIVE Editor API).

M1 Project/millisecond API surface. The ``ProjectState`` / ``RenderState`` enums
live in ``app.state`` (single source) and are re-exported here for convenience.
Only the endpoints implemented in this milestone (create project / upload-session
/ get project) have models below; Highlight/Timeline/Render/Artifact models land
with their endpoints in M2+. Keep this file in sync with ``contracts/openapi.yaml``.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.state import ProjectState, RenderState

__all__ = [
    "ProjectState",
    "RenderState",
    "ProjectCreate",
    "ProjectCreated",
    "Project",
    "UploadSessionCreate",
    "UploadPart",
    "UploadSession",
    "Highlight",
    "HighlightList",
    "TimelineClip",
    "Timeline",
    "ComposeRequest",
]


class ProjectCreate(BaseModel):
    """POST /projects request body."""

    title: str | None = Field(default=None, examples=["我的直播精華"])
    target_duration_ms: int = Field(
        ...,
        ge=1000,
        le=60000,
        examples=[30000],
        description="最終短片長度（毫秒），上限 60000（60 秒）",
    )


class ProjectCreated(BaseModel):
    """POST /projects 201 response."""

    project_id: str
    status: ProjectState
    target_duration_ms: int
    source_key: str = Field(..., description="已配置的 Raw bucket object key")


class Project(BaseModel):
    """GET /projects/{id} response (Project META projection)."""

    project_id: str
    status: ProjectState
    title: str | None = None
    target_duration_ms: int
    source_duration_ms: int | None = None
    source_key: str | None = None
    latest_timeline_version: int | None = None
    latest_render_id: str | None = None
    latest_artifact_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class UploadSessionCreate(BaseModel):
    """POST /projects/{id}/upload-session request body."""

    filename: str = Field(..., examples=["source.mp4"])
    content_type: str | None = Field(default=None, examples=["video/mp4"])
    part_count: int | None = Field(default=None, ge=1, description="multipart 分段數；與 size_bytes 擇一")
    size_bytes: int | None = Field(default=None, ge=0, description="檔案大小，供伺服器推算分段數")


class UploadPart(BaseModel):
    part_number: int
    url: str


class UploadSession(BaseModel):
    """POST /projects/{id}/upload-session 201 response."""

    upload_id: str
    bucket: str
    key: str
    parts: list[UploadPart] = Field(default_factory=list)
    expires_in_sec: int


class Highlight(BaseModel):
    """A highlights.v1 highlight item (editor candidate)."""

    highlight_id: str
    start_ms: int
    end_ms: int
    score: float
    reason: str | None = None
    transcript: str | None = None
    suggested_title: str | None = None
    selected: bool | None = None
    locked: bool | None = None


class HighlightList(BaseModel):
    """GET /projects/{id}/highlights response."""

    project_id: str
    source_duration_ms: int | None = None
    highlights: list[Highlight] = Field(default_factory=list)


class TimelineClip(BaseModel):
    timeline_order: int
    highlight_id: str
    source_start_ms: int
    source_end_ms: int
    timeline_start_ms: int
    timeline_end_ms: int


class Timeline(BaseModel):
    """timeline.v1 projection — GET response and PUT request body.

    On PUT the server assigns ``version`` / ``created_by`` / ``created_at`` and
    recomputes ``actual_duration_ms`` from ``clips`` (client values are ignored).
    """

    schema_version: str | None = None
    project_id: str | None = None
    version: int | None = None
    target_duration_ms: int
    actual_duration_ms: int | None = None
    aspect_ratio: str | None = None
    subtitle_settings: dict | None = None
    effect_settings: dict | None = None
    created_by: str | None = None
    created_at: str | None = None
    clips: list[TimelineClip] = Field(default_factory=list)


class ComposeRequest(BaseModel):
    """POST /projects/{id}/compose request body (all optional)."""

    target_duration_ms: int | None = Field(default=None, ge=1000, le=60000)
    locked_highlight_ids: list[str] | None = None
    excluded_highlight_ids: list[str] | None = None
