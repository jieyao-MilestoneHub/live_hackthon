"""浪 LIVE Editor API — FastAPI walking skeleton (M1 Project/millisecond).

Control-plane HTTP API per demand.md §四. This milestone implements the first
three endpoints end-to-end (create project, upload-session, get project) backed
by DynamoDB ``VideoEditor`` (or an in-memory store offline). The remaining
contract endpoints are declared as 501 stubs so the surface matches
``contracts/openapi.yaml``; they are filled in by M2/M3/M4.

Deploy target: container image (ECR) -> AWS App Runner (or Lambda Function URL).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from jsonschema import ValidationError

from analysis.chatlog import clean_chatlog
from analysis.chatlog.correction import apply_correction, creation_time_to_epoch_ms
from analysis.validate import validate_annotations, validate_highlights, validate_timeline
from app.auth import Principal, current_principal
from app.aws import orchestration
from app.repository import ProjectRepository, get_repository
from app.schemas import (
    AnalyzeRequest,
    AnalyzeResult,
    Annotations,
    ChatUploadUrl,
    ComposeRequest,
    DownloadUrl,
    Highlight,
    HighlightList,
    HighlightPatch,
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
    ProjectState,
    advance_to_analyzing,
    assert_project_transition,
)
from app.storage import Storage, get_storage, resolve_part_count
from workers import (
    annotation_worker,
    chat_analysis_worker,
    composer_worker,
    creative_worker,
    refine_worker,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

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
    if repo.get_project(id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    req = body or RenderCreate()
    try:
        if os.environ.get("RENDER_STATE_MACHINE_ARN"):
            render = creative_worker.create_render_record(repo, id, req.timeline_version)
            orchestration.start_render(render["render_id"], id, render["timeline_version"])
        else:
            render = creative_worker.submit_render(repo, storage, id, req.timeline_version)
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


@app.get("/artifacts/{artifact_id}/download", response_model=DownloadUrl)
def get_artifact_download_url(
    artifact_id: str,
    repo: ProjectRepository = Depends(get_repository),
    storage: Storage = Depends(get_storage),
) -> DownloadUrl:
    artifact = repo.get_artifact_by_id(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    settings = get_settings()
    url = storage.presigned_get(settings.output_bucket, artifact["video_key"])
    return DownloadUrl(url=url, expires_in_sec=settings.presign_expiry_sec)


# --- Speaker Attribution feature (mounted) ---------------------------------
# 具名說話者逐字稿端點：POST/GET /projects/{id}/people、POST /projects/{id}/attribution、
# GET /projects/{id}/transcript、PATCH /projects/{id}/speakers|utterances/...
# router 與模型自成一檔（app/attribution_api.py），此處僅一行掛載。
from app.attribution_api import router as attribution_router  # noqa: E402

app.include_router(attribution_router)
