"""Pydantic v2 models mirroring contracts/openapi.yaml (浪 LIVE Editor API).

M1 Project/millisecond API surface. The ``ProjectState`` / ``RenderState`` enums
live in ``app.state`` (single source) and are re-exported here for convenience.
Only the endpoints implemented in this milestone (create project / upload-session
/ get project) have models below; Highlight/Timeline/Render/Artifact models land
with their endpoints in M2+. Keep this file in sync with ``contracts/openapi.yaml``.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.state import ModerationStatus, ProjectState, RenderState

AnalysisSource = Literal["transcribe", "chat"]

__all__ = [
    "ProjectState",
    "RenderState",
    "ProjectCreate",
    "ProjectCreated",
    "Project",
    "UploadSessionCreate",
    "UploadPart",
    "UploadSession",
    "UploadPartETag",
    "UploadCompleteRequest",
    "UploadCompleted",
    "ChatUploadUrl",
    "AnalyzeRequest",
    "AnalyzeResult",
    "VideoTimebaseRequest",
    "HighlightPatch",
    "Highlight",
    "HighlightList",
    "Beat",
    "DimensionSpan",
    "Annotation",
    "Annotations",
    "RefineRequest",
    "ProposedOffset",
    "RefineResult",
    "TimelineClip",
    "Timeline",
    "ComposeRequest",
    "RenderCreate",
    "RenderCreated",
    "Render",
    "Artifact",
    "DownloadUrl",
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
    analysis_source: AnalysisSource = Field(
        default="transcribe",
        description=(
            "高光分析來源。'transcribe'（預設）：影片上傳後 S3 事件自動走 Transcribe→highlights；"
            "'chat'：改由聊天 LOG（POST /analyze）產生高光，Starter 會略過自動 Transcribe。"
        ),
    )
    edit_instruction: str | None = Field(
        default=None,
        examples=["剪出幾個最精彩的爆梗片段，串成一支高光短片"],
        description=(
            "AI 剪接路線的預設自然語言指令。分析完成後自動雙軌並行（pipeline + edit）各出一支"
            "artifact，edit 路線用此指令規劃；省略則用系統預設模板。使用者可事後 POST"
            " /edit-by-language 用新指令重剪。"
        ),
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
    analysis_source: AnalysisSource = "transcribe"
    source_duration_ms: int | None = None
    source_key: str | None = None
    video_start_epoch_ms: int | None = Field(
        default=None,
        description="影片 0:00 對應的 epoch 毫秒（來自 MP4 OBS creation_time）；chat epoch ↔ 影片相對毫秒 換算基準",
    )
    latest_timeline_version: int | None = None
    latest_render_id: str | None = None
    latest_artifact_id: str | None = None
    moderation_status: ModerationStatus | None = None
    created_at: str | None = None
    updated_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class ModerationEvent(BaseModel):
    """One moderation.v1 audit record (SCAN / REVIEW / OVERRIDE)."""

    schema_version: Literal["moderation.v1"] = "moderation.v1"
    moderation_id: str
    project_id: str
    status: ModerationStatus
    action: Literal["SCAN", "REVIEW", "OVERRIDE"]
    decided_by: str
    decided_at: str
    note: str | None = None
    policy_version: str | None = None
    visual: dict | None = None
    text: dict | None = None
    created_at: str | None = None


class ModerationView(BaseModel):
    """GET /projects/{id}/moderation response: current verdict + latest + audit trail."""

    project_id: str
    status: ModerationStatus
    latest: ModerationEvent | None = None
    events: list[ModerationEvent] = Field(default_factory=list)


class ProgressEvent(BaseModel):
    """One progress.v1 narration record — an AI-synthesized, human-readable line
    describing what the pipeline is doing at a given step (append-only, immutable)."""

    schema_version: Literal["progress.v1"] = "progress.v1"
    progress_id: str
    project_id: str
    step: str = Field(..., description="機器穩定的步驟代碼（StepKey），如 DETECTING_HIGHLIGHTS")
    phase: ProjectState | None = Field(default=None, description="事件當下的粗粒度生命週期狀態")
    status: Literal["RUNNING", "DONE", "FAILED"] = "RUNNING"
    message: str = Field(..., description="AI 統整的一句進度旁白（資訊量多但精簡，不含技術細節）")
    created_at: str | None = None


class ProgressView(BaseModel):
    """GET /projects/{id}/progress response: latest narration + chronological feed."""

    project_id: str
    latest: ProgressEvent | None = None
    events: list[ProgressEvent] = Field(default_factory=list)


class ModerationOverrideRequest(BaseModel):
    """POST /projects/{id}/moderation/override body (moderator-only)."""

    decision: Literal["ALLOW", "BLOCK"]
    note: str | None = None


class UploadSessionCreate(BaseModel):
    """POST /projects/{id}/upload-session request body.

    0.5.0 (batch upload): ``size_bytes`` is the primary input — the server derives
    the multipart part count from it and enforces the per-file size cap. ``part_count``
    is deprecated (kept for backward compatibility); if provided it overrides the
    size-derived count. The upload path is unified: a single file is a batch of 1.
    """

    filename: str = Field(..., examples=["source.mp4"])
    content_type: str | None = Field(default=None, examples=["video/mp4"])
    size_bytes: int | None = Field(
        default=None, ge=0, description="檔案大小（bytes）；主要輸入，供伺服器推算分段數並強制單檔上限"
    )
    part_count: int | None = Field(
        default=None, ge=1, description="（已棄用）multipart 分段數；提供時覆蓋 size_bytes 推算"
    )


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


class UploadPartETag(BaseModel):
    part_number: int = Field(..., ge=1)
    etag: str = Field(..., description="ETag returned by the S3 part PUT")


class UploadCompleteRequest(BaseModel):
    """POST /projects/{id}/upload-session/complete request body.

    Finalizes the S3 multipart upload with the ETags the browser collected. This
    is what actually materializes source.mp4 and triggers the analysis pipeline.
    """

    upload_id: str
    parts: list[UploadPartETag] = Field(default_factory=list)


class UploadCompleted(BaseModel):
    """POST /projects/{id}/upload-session/complete 200 response."""

    project_id: str
    status: ProjectState
    key: str
class ChatUploadUrl(BaseModel):
    """POST /projects/{id}/chat-upload 201 response — single-part presigned PUT for chat.csv."""

    bucket: str
    key: str
    url: str
    expires_in_sec: int


class AnalyzeRequest(BaseModel):
    """POST /projects/{id}/analyze request body (all optional)."""

    chat_key: str | None = Field(
        default=None, description="覆寫 chat.csv 的 raw-bucket key（預設用 project 慣例路徑）"
    )
    video_start_epoch_ms: int | None = Field(
        default=None,
        description="影片 0:00 的 epoch 毫秒；未連結影片時可省略，退回聊天相對時間模式",
    )
    source_duration_ms: int | None = Field(default=None, ge=0)
    params: dict | None = Field(default=None, description="偵測參數覆寫（sigma / max_clips / 洗版規則版本等）")


class AnalyzeResult(BaseModel):
    """POST /projects/{id}/analyze 202 response."""

    project_id: str
    status: ProjectState
    highlight_count: int
    analysis_version: str
    source_duration_ms: int | None = None


class VideoTimebaseRequest(BaseModel):
    """PUT /projects/{id}/video-timebase — 連結影片時基（擇一提供 epoch 或 creation_time）。"""

    video_start_epoch_ms: int | None = Field(
        default=None, ge=0, description="影片 0:00 的 epoch 毫秒（直接提供）"
    )
    creation_time: str | None = Field(
        default=None,
        description="MP4 OBS creation_time（ISO-8601，可含奈秒/時區）；伺服器換算成 epoch 毫秒",
    )
    source_duration_ms: int | None = Field(default=None, ge=0, description="影片長度毫秒（可選）")


class HighlightPatch(BaseModel):
    """PATCH /projects/{id}/highlights/{hid} — 編輯器逐段校正（欄位皆 optional）。"""

    correction_offset_ms: int | None = Field(
        default=None,
        description="事件窗相對目前窗的位移；往前抓為負（如 -20000）、延後為正。累加進 correction.offset_ms",
    )
    exclude: bool | None = Field(default=None, description="true=排除此段（如開場白）、false=取消排除")
    selected: bool | None = None
    locked: bool | None = None
    note: str | None = Field(default=None, description="校正備註 / 排除原因")


class Highlight(BaseModel):
    """A highlights.v1 highlight item (editor candidate).

    Carries the chat-first analysis fields (optional, additive) alongside the
    original speech-path fields. Nested objects are passed through as dicts —
    the authoritative shape is ``contracts/highlights.v1.schema.json``.
    """

    highlight_id: str
    start_ms: int
    end_ms: int
    score: float
    reason: str | None = None
    transcript: str | None = None
    suggested_title: str | None = None
    source_segment_ids: list[str] | None = None
    selected: bool | None = None
    locked: bool | None = None
    # chat-first additive fields
    signal: str | None = None
    status: str | None = None
    excluded_reason: str | None = None
    description: str | None = None
    chat_window: dict | None = None
    correction: dict | None = None
    emotion: dict | None = None
    detection: dict | None = None
    provenance: dict | None = None


class HighlightList(BaseModel):
    """GET /projects/{id}/highlights response."""

    project_id: str
    source_duration_ms: int | None = None
    highlights: list[Highlight] = Field(default_factory=list)


class Beat(BaseModel):
    """A narrative beat in a highlight's cut-list (annotations.v1 beat)."""

    order: int
    beat: str | None = None
    line: str | None = None
    start_ms: int
    end_ms: int
    duration_ms: int | None = None


class DimensionSpan(BaseModel):
    """A single 5-dimension annotation span (annotations.v1 dimension_span)."""

    dimension: str
    start_ms: int
    end_ms: int
    text: str | None = None
    messages: list[dict] | None = None


class Annotation(BaseModel):
    """Structured annotation for one highlight (annotations.v1 annotation)."""

    highlight_id: str
    title: str | None = None
    description: str | None = None
    dimensions: list[DimensionSpan] = Field(default_factory=list)
    beats: list[Beat] | None = None
    corrected_by: str | None = None
    corrected_at: str | None = None


class Annotations(BaseModel):
    """Response of GET/POST/PUT /projects/{id}/annotations (annotations.v1 projection)."""

    schema_version: str | None = None
    project_id: str
    annotation_version: str | None = None
    annotations: list[Annotation] = Field(default_factory=list)
    created_at: str | None = None


class RefineRequest(BaseModel):
    """POST /projects/{id}/refine request body (all optional)."""

    apply_offsets: bool = Field(
        default=False,
        description="true=直接把提議的笑點 offset 套用到 highlights；false=只回提議，交編輯器 PATCH 確認",
    )
    params: dict | None = Field(default=None, description="精修參數覆寫（lead_ms 等）")


class ProposedOffset(BaseModel):
    """AI 逐字稿定位笑點的校正提議（annotations/highlights 之外的建議）。"""

    highlight_id: str
    current_start_ms: int
    proposed_start_ms: int
    offset_ms: int
    evidence_text: str | None = None


class RefineResult(BaseModel):
    """POST /projects/{id}/refine response."""

    project_id: str
    proposed_offsets: list[ProposedOffset] = Field(default_factory=list)
    annotations: Annotations
    transcript_segment_count: int
    applied: int = 0


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


class RenderCreate(BaseModel):
    """POST /projects/{id}/renders request body (optional)."""

    timeline_version: int | None = Field(
        default=None, description="省略則使用 latest_timeline_version"
    )
    route: Literal["pipeline", "edit"] | None = Field(
        default=None, description="創意路線（省略＝pipeline）"
    )


class RenderCreated(BaseModel):
    """POST /projects/{id}/renders 202 response."""

    render_id: str
    status: RenderState


class Render(BaseModel):
    """GET /renders/{render_id} response (Render Job item projection)."""

    render_id: str
    project_id: str
    status: RenderState
    route: Literal["pipeline", "edit"] | None = None
    current_stage: str | None = None
    timeline_version: int
    effect_seed: int | None = None
    batch_job_id: str | None = None
    artifact_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class Artifact(BaseModel):
    """artifact.v1 projection (final render manifest)."""

    artifact_id: str
    project_id: str
    render_id: str
    route: Literal["pipeline", "edit"] | None = None
    timeline_version: int | None = None
    status: str
    duration_ms: int | None = None
    aspect_ratio: str | None = None
    resolution: dict | None = None
    size_bytes: int | None = None
    files: dict = Field(default_factory=dict)
    created_at: str | None = None


class DownloadUrl(BaseModel):
    """GET /artifacts/{artifact_id}/download response."""

    url: str
    expires_in_sec: int
