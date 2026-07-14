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

from analysis.validate import validate_timeline
from app.auth import Principal, current_principal
from app.aws import orchestration
from app.repository import ProjectRepository, get_repository
from app.schemas import (
    ComposeRequest,
    DownloadUrl,
    Highlight,
    HighlightList,
    Project,
    ProjectCreate,
    ProjectCreated,
    Render,
    RenderCreate,
    RenderCreated,
    Timeline,
    UploadCompleted,
    UploadCompleteRequest,
    UploadSession,
    UploadSessionCreate,
)
from app.settings import get_settings
from app.state import InvalidTransition, ProjectState, assert_project_transition
from app.storage import Storage, get_storage, resolve_part_count
from workers import composer_worker, creative_worker


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
