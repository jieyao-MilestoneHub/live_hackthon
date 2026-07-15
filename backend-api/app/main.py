"""浪 LIVE Editor API — FastAPI walking skeleton (M1 Project/millisecond).

Control-plane HTTP API per demand.md §四. This milestone implements the first
three endpoints end-to-end (create project, upload-session, get project) backed
by DynamoDB ``VideoEditor`` (or an in-memory store offline). The remaining
contract endpoints are declared as 501 stubs so the surface matches
``contracts/openapi.yaml``; they are filled in by M2/M3/M4.

Deploy target: container image (ECR) -> AWS App Runner (or Lambda Function URL).
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from jsonschema import ValidationError

from analysis.chatlog import clean_chatlog
from analysis.chatlog.correction import apply_correction, creation_time_to_epoch_ms
from analysis.validate import validate_annotations, validate_highlights, validate_timeline
from app.auth import Principal, current_principal, require_moderator
from app.aws import orchestration
from app.repository import ProjectRepository, get_repository
from app.schemas import (
    AnalyzeRequest,
    AnalyzeResult,
    Annotations,
    Artifact,
    ChatUploadUrl,
    ComposeRequest,
    DownloadUrl,
    Highlight,
    HighlightList,
    HighlightPatch,
    ModerationEvent,
    ModerationOverrideRequest,
    ModerationView,
    Project,
    ProjectCreate,
    ProjectCreated,
    RefineRequest,
    RefineResult,
    Render,
    RenderCreate,
    RenderCreated,
    Timeline,
    UploadCompleted,
    UploadCompleteRequest,
    UploadSession,
    UploadSessionCreate,
    VideoTimebaseRequest,
)
from app.settings import get_settings
from app.state import (
    InvalidTransition,
    ModerationStatus,
    ProjectState,
    advance_to_analyzing,
    assert_project_transition,
    moderation_allows_publish,
)
from app.storage import Storage, get_storage, resolve_part_count
from workers import (
    annotation_worker,
    chat_analysis_worker,
    composer_worker,
    creative_worker,
    refine_worker,
    render_worker,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}

VERSION = "0.2.0"

app = FastAPI(title="浪 LIVE Editor API", version=VERSION)

# Skeleton: wide-open CORS so any frontend dev origin can call us.
# TODO(team): tighten allow_origins to the deployed frontend origin(s).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _new_project_id() -> str:
    return f"project-{uuid.uuid4().hex[:12]}"


# Accepted video containers for the batch upload path. Enforced in
# create_upload_session so the presign gate rejects non-video files up front.
_ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def _is_allowed_video(content_type: str | None, filename: str | None) -> bool:
    """A file passes if its content_type is video/* OR its extension is allowed."""
    if content_type and content_type.strip().lower().startswith("video/"):
        return True
    if filename:
        name = filename.strip().lower()
        return any(name.endswith(ext) for ext in _ALLOWED_VIDEO_EXTS)
    return False


# Lifecycle states a project can be rendered from (mirrors _PROJECT_TRANSITIONS).
_RENDERABLE_STATES = {ProjectState.READY_TO_EDIT, ProjectState.ARTIFACT_READY}


def _assert_publishable(project: dict) -> None:
    """403 unless content moderation permits publishing (render/download). No-op
    when moderation is disabled (feature flag off / pre-moderation projects)."""
    if not get_settings().moderation_enabled:
        return
    status = project.get("moderation_status")
    if not moderation_allows_publish(status):
        raise HTTPException(
            status_code=403,
            detail=f"內容審核（{status or 'PENDING'}）尚未通過，不可發布；需管理員複核",
        )


def _moderation_view(project: dict, repo: ProjectRepository) -> ModerationView:
    events = [ModerationEvent(**e) for e in repo.list_moderation_events(project["project_id"])]
    status = project.get("moderation_status") or ModerationStatus.PENDING.value
    return ModerationView(
        project_id=project["project_id"],
        status=status,
        latest=events[-1] if events else None,
        events=events,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": VERSION}


@app.post("/projects", response_model=ProjectCreated, status_code=201)
def create_project(
    body: ProjectCreate,
    principal: Principal = Depends(current_principal),
    repo: ProjectRepository = Depends(get_repository),
) -> ProjectCreated:
    settings = get_settings()
    project_id = _new_project_id()
    source_key = settings.source_key(principal.tenant_id, project_id)

    item = {
        "project_id": project_id,
        "tenant_id": principal.tenant_id,
        "user_id": principal.user_id,
        "title": body.title,
        "status": ProjectState.CREATED.value,
        "target_duration_ms": body.target_duration_ms,
        "analysis_source": body.analysis_source,
        "source_bucket": settings.raw_bucket,
        "source_key": source_key,
        "latest_timeline_version": 0,
    }
    try:
        repo.create_project(item)
    except KeyError:
        raise HTTPException(status_code=409, detail="project already exists")

    return ProjectCreated(
        project_id=project_id,
        status=ProjectState.CREATED,
        target_duration_ms=body.target_duration_ms,
        source_key=source_key,
    )


@app.post(
    "/projects/{id}/upload-session",
    response_model=UploadSession,
    status_code=201,
)
def create_upload_session(
    id: str,
    body: UploadSessionCreate,
    repo: ProjectRepository = Depends(get_repository),
    storage: Storage = Depends(get_storage),
) -> UploadSession:
    project = repo.get_project(id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    try:
        assert_project_transition(ProjectState(project["status"]), ProjectState.UPLOAD_PENDING)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    settings = get_settings()
    # Per-file size cap (default 10GB). Enforced here because the browser uploads
    # bytes straight to S3 via presigned URLs — this is the only server-side gate.
    if body.size_bytes is not None and body.size_bytes > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"file too large: {body.size_bytes} bytes exceeds the "
                f"{settings.max_upload_bytes}-byte per-file limit"
            ),
        )
    if not _is_allowed_video(body.content_type, body.filename):
        raise HTTPException(
            status_code=415,
            detail=(
                "unsupported media type: expected a video (content_type video/* or "
                f"extension in {sorted(_ALLOWED_VIDEO_EXTS)})"
            ),
        )

    part_count = resolve_part_count(body.part_count, body.size_bytes)
    session = storage.create_upload_session(
        key=project["source_key"],
        part_count=part_count,
        content_type=body.content_type,
    )
    repo.update_project(
        id,
        {"status": ProjectState.UPLOAD_PENDING.value, "upload_id": session["upload_id"]},
    )
    return UploadSession(**session)


@app.post(
    "/projects/{id}/upload-session/complete",
    response_model=UploadCompleted,
    status_code=200,
)
def complete_upload_session(
    id: str,
    body: UploadCompleteRequest,
    repo: ProjectRepository = Depends(get_repository),
    storage: Storage = Depends(get_storage),
) -> UploadCompleted:
    """Finalize the S3 multipart upload (submit ETags) and move the project to
    UPLOADING. This materializes source.mp4 → the S3 event triggers analysis."""
    project = repo.get_project(id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    key = project["source_key"]
    parts = [{"part_number": p.part_number, "etag": p.etag} for p in body.parts]
    try:
        storage.complete_multipart_upload(key, body.upload_id, parts)
    except Exception as exc:  # noqa: BLE001 — surface S3 completion errors as 409
        raise HTTPException(status_code=409, detail=f"multipart completion failed: {exc}")
    updated = repo.update_project(id, {"status": ProjectState.UPLOADING.value})
    return UploadCompleted(project_id=id, status=ProjectState(updated["status"]), key=key)


@app.get("/projects/{id}", response_model=Project)
def get_project(
    id: str,
    repo: ProjectRepository = Depends(get_repository),
) -> Project:
    project = repo.get_project(id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return Project(**project)


@app.put("/projects/{id}/video-timebase", response_model=Project)
def set_video_timebase(
    id: str,
    body: VideoTimebaseRequest,
    repo: ProjectRepository = Depends(get_repository),
) -> Project:
    """連結影片時基：設定 video_start_epoch_ms（chat epoch ↔ 影片相對毫秒 換算基準）。

    可直接給 epoch，或給 MP4 OBS creation_time 由伺服器換算。之後 /analyze 產出的
    highlights 才是誠實的影片相對毫秒（否則走 -chattime fallback）。
    """
    project = repo.get_project(id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    updates: dict = {}
    if body.video_start_epoch_ms is not None:
        updates["video_start_epoch_ms"] = body.video_start_epoch_ms
    elif body.creation_time is not None:
        try:
            updates["video_start_epoch_ms"] = creation_time_to_epoch_ms(body.creation_time)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    if body.source_duration_ms is not None:
        updates["source_duration_ms"] = body.source_duration_ms
    if not updates:
        raise HTTPException(
            status_code=422,
            detail="provide video_start_epoch_ms, creation_time, or source_duration_ms",
        )
    return Project(**repo.update_project(id, updates))


@app.get("/projects/{id}/highlights", response_model=HighlightList)
def get_highlights(
    id: str,
    repo: ProjectRepository = Depends(get_repository),
) -> HighlightList:
    project = repo.get_project(id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return HighlightList(
        project_id=id,
        source_duration_ms=project.get("source_duration_ms"),
        highlights=[Highlight(**h) for h in repo.list_highlights(id)],
    )


@app.patch("/projects/{id}/highlights/{highlight_id}", response_model=Highlight)
def patch_highlight(
    id: str,
    highlight_id: str,
    body: HighlightPatch,
    principal: Principal = Depends(current_principal),
    repo: ProjectRepository = Depends(get_repository),
) -> Highlight:
    """編輯器逐段校正：聊天落後位移（往前抓/延後）、排除開場、鎖定、選取。

    位移把事件窗（start_ms/end_ms）相對聊天窗平移，status→shifted；排除 → status=excluded、
    selected=false。校正後仍需符合 highlights.v1。僅允許在編輯迴圈（COMPOSING/READY_TO_EDIT）。
    """
    project = repo.get_project(id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    status = ProjectState(project["status"])
    if status not in (ProjectState.COMPOSING, ProjectState.READY_TO_EDIT):
        raise HTTPException(status_code=409, detail=f"cannot edit highlights in status {status.value}")

    highlight = repo.get_highlight(id, highlight_id)
    if highlight is None:
        raise HTTPException(status_code=404, detail="highlight not found")

    updated = apply_correction(
        highlight,
        offset_ms=body.correction_offset_ms,
        exclude=body.exclude,
        selected=body.selected,
        locked=body.locked,
        corrected_by=principal.user_id,
        note=body.note,
        source_duration_ms=project.get("source_duration_ms"),
    )
    envelope = {
        "schema_version": "highlights.v1",
        "project_id": id,
        "source_duration_ms": project.get("source_duration_ms") or updated["end_ms"],
        "highlights": [updated],
    }
    try:
        validate_highlights(envelope)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"invalid highlight after correction: {exc.message}")

    repo.update_highlight(id, highlight_id, updated)
    return Highlight(**updated)


# ``advance_to_analyzing`` now lives in app.state (shared with the chat Starter).


@app.post("/projects/{id}/chat-upload", response_model=ChatUploadUrl, status_code=201)
def create_chat_upload(
    id: str,
    repo: ProjectRepository = Depends(get_repository),
    storage: Storage = Depends(get_storage),
) -> ChatUploadUrl:
    """Presign a single-part PUT for the chat-room log CSV (data plane bypasses the API)."""
    project = repo.get_project(id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    settings = get_settings()
    key = settings.chat_key(project.get("tenant_id", "demo"), id)
    url = storage.presigned_put(settings.raw_bucket, key, content_type="text/csv")
    return ChatUploadUrl(
        bucket=settings.raw_bucket,
        key=key,
        url=url,
        expires_in_sec=settings.presign_expiry_sec,
    )


@app.post("/projects/{id}/analyze", response_model=AnalyzeResult, status_code=202)
def analyze_project(
    id: str,
    body: AnalyzeRequest | None = None,
    repo: ProjectRepository = Depends(get_repository),
    storage: Storage = Depends(get_storage),
) -> AnalyzeResult:
    """Chat-first analysis (MVP inline shim): chat.csv → chatlog.v1 → highlights.v1.

    Reads the uploaded CSV from the Raw bucket, cleans it (parse/re-sort/spam-tag),
    persists chatlog.v1 to the Work bucket, drives the project into ANALYZING, then
    runs the chat Analysis Worker (→ COMPOSING). TODO(async): move off the request
    path into the ai-task lane (Step Functions) — same pure functions, no rework.
    """
    project = repo.get_project(id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    req = body or AnalyzeRequest()
    settings = get_settings()
    tenant = project.get("tenant_id", "demo")
    chat_key = req.chat_key or settings.chat_key(tenant, id)

    try:
        csv_bytes = storage.get_bytes(settings.raw_bucket, chat_key)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"chat log not found at {chat_key}; upload chat.csv first")

    csv_text = csv_bytes.decode("utf-8-sig", errors="replace")
    chatlog = clean_chatlog(csv_text, id, source={"bucket": settings.raw_bucket, "key": chat_key})
    if not chatlog["messages"]:
        raise HTTPException(
            status_code=422,
            detail="no chat messages parsed from chat.csv (check CSV format / field mapping)",
        )
    storage.put_json(settings.work_bucket, settings.chatlog_key(tenant, id), chatlog)

    try:
        advance_to_analyzing(repo, id, ProjectState(project["status"]))
        result = chat_analysis_worker.run(
            repo,
            id,
            chatlog,
            video_start_epoch_ms=req.video_start_epoch_ms,
            source_duration_ms=req.source_duration_ms,
            params=req.params,
        )
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    proj = repo.get_project(id)
    return AnalyzeResult(
        project_id=id,
        status=ProjectState(proj["status"]),
        highlight_count=len(result["highlights"]),
        analysis_version=result["analysis_version"],
        source_duration_ms=proj.get("source_duration_ms"),
    )


@app.post("/projects/{id}/annotations", response_model=Annotations)
def generate_annotations(
    id: str,
    repo: ProjectRepository = Depends(get_repository),
    storage: Storage = Depends(get_storage),
) -> Annotations:
    """產生結構化標註（階段 7–8）：規則式 5 維度 + 敘事節拍 → annotations.v1（存 work bucket）。

    需已有 highlights（COMPOSING/READY_TO_EDIT）。不改 Project 狀態（編輯迴圈衍生產物）。
    """
    project = repo.get_project(id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    status = ProjectState(project["status"])
    if status not in (ProjectState.COMPOSING, ProjectState.READY_TO_EDIT):
        raise HTTPException(status_code=409, detail=f"cannot annotate in status {status.value}")
    try:
        doc = annotation_worker.run(repo, storage, get_settings(), id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return Annotations(**doc)


@app.get("/projects/{id}/annotations", response_model=Annotations)
def get_annotations(
    id: str,
    repo: ProjectRepository = Depends(get_repository),
    storage: Storage = Depends(get_storage),
) -> Annotations:
    """讀取已產生的 annotations.v1（work bucket）。"""
    project = repo.get_project(id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    settings = get_settings()
    tenant = project.get("tenant_id", "demo")
    try:
        doc = storage.get_json(settings.work_bucket, settings.annotations_key(tenant, id))
    except KeyError:
        raise HTTPException(status_code=404, detail="annotations not generated; POST /annotations first")
    return Annotations(**doc)


@app.put("/projects/{id}/annotations", response_model=Annotations)
def put_annotations(
    id: str,
    body: Annotations,
    repo: ProjectRepository = Depends(get_repository),
    storage: Storage = Depends(get_storage),
) -> Annotations:
    """儲存人工編輯後的 annotations.v1（伺服器蓋 project_id/schema_version，驗證後落地）。"""
    project = repo.get_project(id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    doc = body.model_dump(exclude_none=True)
    doc["schema_version"] = "annotations.v1"
    doc["project_id"] = id
    try:
        validate_annotations(doc)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"invalid annotations: {exc.message}")
    settings = get_settings()
    tenant = project.get("tenant_id", "demo")
    storage.put_json(settings.work_bucket, settings.annotations_key(tenant, id), doc)
    return Annotations(**doc)


@app.post("/projects/{id}/refine", response_model=RefineResult)
def refine_project(
    id: str,
    body: RefineRequest | None = None,
    repo: ProjectRepository = Depends(get_repository),
    storage: Storage = Depends(get_storage),
) -> RefineResult:
    """AI 精修（階段 5–6）：轉錄影片 → 提議笑點校正 offset + 敘事填 annotations 台詞。

    離線走 Stub（Transcribe/Bedrock 罐頭）；真值需 USE_INMEMORY=0 上 AWS。預設只提議 offset
    （交編輯器 PATCH 確認），apply_offsets=true 才自動套用。限 COMPOSING/READY_TO_EDIT。
    """
    project = repo.get_project(id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    status = ProjectState(project["status"])
    if status not in (ProjectState.COMPOSING, ProjectState.READY_TO_EDIT):
        raise HTTPException(status_code=409, detail=f"cannot refine in status {status.value}")
    req = body or RefineRequest()
    try:
        result = refine_worker.run(
            repo, storage, get_settings(), id, apply_offsets=req.apply_offsets, params=req.params
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return RefineResult(
        project_id=id,
        proposed_offsets=result["proposed_offsets"],
        annotations=Annotations(**result["annotations"]),
        transcript_segment_count=result["transcript_segment_count"],
        applied=result["applied"],
    )


@app.get("/projects/{id}/timeline", response_model=Timeline)
def get_timeline(
    id: str,
    version: int | None = None,
    repo: ProjectRepository = Depends(get_repository),
) -> Timeline:
    if repo.get_project(id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    timeline = repo.get_timeline(id, version)
    if timeline is None:
        raise HTTPException(status_code=404, detail="timeline not found")
    return Timeline(**timeline)


@app.put("/projects/{id}/timeline")
def update_timeline(
    id: str,
    body: Timeline,
    principal: Principal = Depends(current_principal),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.get_project(id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    try:
        assert_project_transition(ProjectState(project["status"]), ProjectState.READY_TO_EDIT)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # Server owns version/created_by/created_at; recompute actual from clips.
    version = int(project.get("latest_timeline_version") or 0) + 1
    clips = [c.model_dump() for c in body.clips]
    actual = max((c["timeline_end_ms"] for c in clips), default=0)
    timeline_doc: dict = {
        "schema_version": "timeline.v1",
        "project_id": id,
        "version": version,
        "target_duration_ms": body.target_duration_ms,
        "actual_duration_ms": actual,
        "clips": clips,
        "created_by": principal.user_id,
        "created_at": _now_iso(),
    }
    for opt in ("aspect_ratio", "subtitle_settings", "effect_settings"):
        val = getattr(body, opt)
        if val is not None:
            timeline_doc[opt] = val

    try:
        validate_timeline(timeline_doc)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"invalid timeline: {exc.message}")

    repo.put_timeline(id, timeline_doc)  # append-only new version
    repo.update_project(
        id, {"latest_timeline_version": version, "status": ProjectState.READY_TO_EDIT.value}
    )
    return {"timeline_version": version}


@app.post("/projects/{id}/compose", status_code=202)
def compose_project_timeline(
    id: str,
    body: ComposeRequest | None = None,
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    # MVP shim: run the light (no-FFmpeg) Composer inline. TODO(async): enqueue to
    # the ai-task queue so a Lambda runs composer_worker.run off the request path.
    if repo.get_project(id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    req = body or ComposeRequest()
    try:
        timeline = composer_worker.run(
            repo,
            id,
            target=req.target_duration_ms,
            locked=req.locked_highlight_ids,
            excluded=req.excluded_highlight_ids,
        )
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"timeline_version": timeline["version"]}


@app.post("/projects/{id}/renders", response_model=RenderCreated, status_code=202)
def create_render(
    id: str,
    body: RenderCreate | None = None,
    repo: ProjectRepository = Depends(get_repository),
    storage: Storage = Depends(get_storage),
) -> RenderCreated:
    # Control plane must not run FFmpeg (demand.md §十九). When the render
    # workflow is deployed (RENDER_STATE_MACHINE_ARN set), just create the render
    # record and StartExecution — creative planning + Batch encode run async.
    # Offline / no state machine falls back to the inline shim so tests + CLI work.
    project = repo.get_project(id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    # Moderation gate only for projects that are otherwise renderable — a not-ready
    # project falls through to the worker's 409 readiness error (more precise than
    # a moderation 403 for a project that was never scanned).
    if ProjectState(project["status"]) in _RENDERABLE_STATES:
        _assert_publishable(project)  # no render for BLOCKED / unreviewed-FLAGGED
    req = body or RenderCreate()
    route = req.route or "pipeline"
    try:
        if os.environ.get("RENDER_STATE_MACHINE_ARN"):
            render = creative_worker.create_render_record(repo, id, req.timeline_version, route=route)
            orchestration.start_render(render["render_id"], id, render["timeline_version"])
        else:
            render = creative_worker.submit_render(repo, storage, id, req.timeline_version, route=route)
            # Dev-mode offline shim: with no Batch/state machine, also run the
            # (stub) encode inline so a local CLI/agent gets a finished artifact to
            # download. Opt-in via RENDER_INLINE_ENCODE (default off keeps tests,
            # which assert the QUEUED plan-only result, unchanged).
            if _env_flag("RENDER_INLINE_ENCODE"):
                render_worker.run(repo, storage, id, render["render_id"])
                render = repo.get_render_by_id(render["render_id"]) or render
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return RenderCreated(render_id=render["render_id"], status=render["status"])


@app.get("/renders/{render_id}", response_model=Render)
def get_render(
    render_id: str,
    repo: ProjectRepository = Depends(get_repository),
) -> Render:
    render = repo.get_render_by_id(render_id)
    if render is None:
        raise HTTPException(status_code=404, detail="render not found")
    return Render(**render)


@app.get("/projects/{id}/artifacts", response_model=list[Artifact])
def list_artifacts(
    id: str,
    repo: ProjectRepository = Depends(get_repository),
) -> list[Artifact]:
    """雙軌分流：列出 project 全部成品（每個 route 一份），供前端各給一顆下載鍵。"""
    if repo.get_project(id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    return [Artifact(**a) for a in repo.list_artifacts(id)]


def _resolve_gated_artifact(artifact_id: str, repo: ProjectRepository) -> dict:
    """Fetch an artifact and re-enforce the owning project's moderation gate.

    Shared by the download and preview routes so the 404 (missing) / 403
    (moderation) checks stay in exactly one place. Defense in depth: an artifact
    may have been rendered before a later block / takedown, so we re-check the
    verdict at sign time, not just at render time."""
    artifact = repo.get_artifact_by_id(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    owner = repo.get_project(artifact.get("project_id")) if artifact.get("project_id") else None
    if owner is not None:
        _assert_publishable(owner)
    return artifact


@app.get("/artifacts/{artifact_id}/download", response_model=DownloadUrl)
def get_artifact_download_url(
    artifact_id: str,
    repo: ProjectRepository = Depends(get_repository),
    storage: Storage = Depends(get_storage),
) -> DownloadUrl:
    artifact = _resolve_gated_artifact(artifact_id, repo)
    settings = get_settings()
    # attachment disposition → the browser saves the file to disk (vs. playing it
    # inline). Filename identifies the project + creative route for the user.
    route = artifact.get("route") or "pipeline"
    filename = f"{artifact.get('project_id', 'artifact')}-{route}.mp4"
    url = storage.presigned_get(
        settings.output_bucket,
        artifact["video_key"],
        disposition="attachment",
        filename=filename,
    )
    return DownloadUrl(url=url, expires_in_sec=settings.presign_expiry_sec)


@app.get("/artifacts/{artifact_id}/preview", response_model=DownloadUrl)
def get_artifact_preview_url(
    artifact_id: str,
    repo: ProjectRepository = Depends(get_repository),
    storage: Storage = Depends(get_storage),
) -> DownloadUrl:
    """Signed inline URL for the finished MP4, for in-page ``<video>`` preview
    before download. Same object + moderation gate as the download route, but
    ``inline`` disposition + ``video/mp4`` so the browser streams (Range) it in
    place instead of forcing a save."""
    artifact = _resolve_gated_artifact(artifact_id, repo)
    settings = get_settings()
    url = storage.presigned_get(
        settings.output_bucket,
        artifact["video_key"],
        disposition="inline",
        content_type="video/mp4",
    )
    return DownloadUrl(url=url, expires_in_sec=settings.presign_expiry_sec)


@app.get("/projects/{id}/moderation", response_model=ModerationView)
def get_moderation(
    id: str,
    repo: ProjectRepository = Depends(get_repository),
) -> ModerationView:
    """Current moderation verdict + immutable audit trail (SCAN/REVIEW/OVERRIDE)."""
    project = repo.get_project(id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return _moderation_view(project, repo)


@app.post("/projects/{id}/moderation/override", response_model=ModerationView)
def override_moderation(
    id: str,
    body: ModerationOverrideRequest,
    principal: Principal = Depends(require_moderator),
    repo: ProjectRepository = Depends(get_repository),
) -> ModerationView:
    """Moderator review/override. ALLOW → OVERRIDDEN (publishable); BLOCK → BLOCKED.
    Appends an immutable moderation.v1 audit record stamped with the moderator."""
    project = repo.get_project(id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    new_status = (
        ModerationStatus.OVERRIDDEN.value if body.decision == "ALLOW"
        else ModerationStatus.BLOCKED.value
    )
    now = _now_iso()
    event = {
        "schema_version": "moderation.v1",
        "moderation_id": f"mod-{uuid.uuid4().hex[:12]}",
        "project_id": id,
        "status": new_status,
        "action": "OVERRIDE",
        "decided_by": principal.user_id,
        "decided_at": now,
        "note": body.note,
        "created_at": now,
    }
    repo.put_moderation_event(id, event)
    updated = repo.update_project(id, {"moderation_status": new_status})
    return _moderation_view(updated, repo)


# --- Stub object-store routes (in-memory / offline dev only) ---------------
# StubStorage (USE_INMEMORY=1) hands out presigned URLs of the form
# http://localhost:8080/stub-upload|stub-download/{bucket}/{key}. These two routes
# make those URLs functional so a decoupled HTTP client (the crestcut CLI, or the
# web frontend) can round-trip a real upload/download against the local in-memory
# backend with zero AWS — the same PUT/GET the browser does against real S3. In
# real-AWS mode (USE_INMEMORY=0) presigned URLs point at S3 and these are never hit.


def _require_stub_mode() -> None:
    """Hard security guard: these dev routes exist ONLY for the in-memory backend.
    In real-AWS mode (USE_INMEMORY=0) they MUST be inert — otherwise they'd be an
    unauthenticated arbitrary object read/write against real S3 buckets."""
    if not get_settings().use_inmemory:
        raise HTTPException(status_code=404, detail="Not Found")


@app.put("/stub-upload/{bucket}/{key:path}")
async def stub_upload_put(
    bucket: str,
    key: str,
    request: Request,
    storage: Storage = Depends(get_storage),
) -> Response:
    """Accept a direct PUT to a StubStorage presigned URL; persist the bytes."""
    _require_stub_mode()
    body = await request.body()
    content_type = request.headers.get("content-type", "application/octet-stream")
    storage.put_bytes(bucket, key, body, content_type)
    # Mimic S3: return a (quoted) ETag so multipart clients can complete the upload.
    etag = '"' + hashlib.md5(body).hexdigest() + '"'  # noqa: S324 — non-crypto object id
    return Response(status_code=200, headers={"ETag": etag})


@app.get("/stub-download/{bucket}/{key:path}")
def stub_download_get(
    bucket: str,
    key: str,
    storage: Storage = Depends(get_storage),
) -> Response:
    """Serve bytes previously written to StubStorage (presigned GET target)."""
    _require_stub_mode()
    try:
        data = storage.get_bytes(bucket, key)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no object at {bucket}/{key}")
    return Response(content=data, media_type="application/octet-stream")


# --- Speaker Attribution feature (mounted) ---------------------------------
# 具名說話者逐字稿端點：POST/GET /projects/{id}/people、POST /projects/{id}/attribution、
# GET /projects/{id}/transcript、PATCH /projects/{id}/speakers|utterances/...
# router 與模型自成一檔（app/attribution_api.py），此處僅一行掛載。
from app.attribution_api import router as attribution_router  # noqa: E402

app.include_router(attribution_router)


# --- Edit-by-language feature (mounted) ------------------------------------
# 自然語言剪接：POST /projects/{id}/edit-by-language（NL → effects.v1 + subtitle.v1
# → 觸發 ffmpeg-in-Lambda encode）、GET /projects/{id}/edit-by-language/plan。
# router 與模型自成一檔（app/edit_by_language_api.py），此處僅一行掛載。
from app.edit_by_language_api import router as edit_by_language_router  # noqa: E402

app.include_router(edit_by_language_router)
