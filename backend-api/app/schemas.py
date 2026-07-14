"""Pydantic v2 models mirroring contracts/openapi.yaml (浪 LIVE Job API).

These are the request/response shapes for the Job API. The ``Clip`` model maps
one item of a ``highlights.v1`` document onto the API surface consumed by the
frontend. Keep this file in sync with ``contracts/openapi.yaml`` (source of truth).
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class JobState(str, Enum):
    """Job lifecycle states — mirrors JobState enum in openapi.yaml."""

    CREATED = "CREATED"
    UPLOAD_PENDING = "UPLOAD_PENDING"
    UPLOADED = "UPLOADED"
    QUEUED = "QUEUED"
    VALIDATING = "VALIDATING"
    TRANSCRIBING = "TRANSCRIBING"
    ANALYZING = "ANALYZING"
    RENDERING = "RENDERING"
    FINALIZING = "FINALIZING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobCreate(BaseModel):
    """POST /jobs request body."""

    filename: str = Field(..., examples=["stream.mp4"])
    content_type: str | None = Field(default=None, examples=["video/mp4"])
    tenant_id: str | None = None


class UploadInfo(BaseModel):
    """S3 presigned upload hint. In the skeleton this is a local stub."""

    method: str | None = Field(default=None, examples=["PUT"])
    url: str | None = None
    key: str | None = None


class Clip(BaseModel):
    """A single highlight clip (maps a highlights.v1 highlight item)."""

    clip_id: str
    start_sec: float
    end_sec: float
    score: float | None = None
    reason: str | None = None
    title: str | None = None
    download_ready: bool | None = None


class JobCreated(BaseModel):
    """POST /jobs 201 response."""

    job_id: str
    status: JobState
    upload: UploadInfo | None = None


class JobStatus(BaseModel):
    """GET /jobs/{job_id} response."""

    job_id: str
    status: JobState
    current_stage: str | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    highlights: list[Clip] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
