"""浪 LIVE Editor API — FastAPI walking skeleton (M1 Project/millisecond).

Control-plane HTTP API per demand.md §四. This milestone implements the first
three endpoints end-to-end (create project, upload-session, get project) backed
by DynamoDB ``VideoEditor`` (or an in-memory store offline). The remaining
contract endpoints are declared as 501 stubs so the surface matches
``contracts/openapi.yaml``; they are filled in by M2/M3/M4.

Deploy target: container image (ECR) -> AWS App Runner (or Lambda Function URL).
"""
from __future__ import annotations

import uuid

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.auth import Principal, current_principal
from app.repository import ProjectRepository, get_repository
from app.schemas import (
    Project,
    ProjectCreate,
    ProjectCreated,
    UploadSession,
    UploadSessionCreate,
)
from app.settings import get_settings
from app.state import InvalidTransition, ProjectState, assert_project_transition
from app.storage import Storage, get_storage, resolve_part_count

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


@app.get("/projects/{id}", response_model=Project)
def get_project(
    id: str,
    repo: ProjectRepository = Depends(get_repository),
) -> Project:
    project = repo.get_project(id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return Project(**project)


# --- Contract-declared endpoints not yet implemented (see contracts/openapi.yaml) ---
# They exist so the API surface matches the contract; filled in by M2/M3/M4.

def _not_implemented(what: str) -> HTTPException:
    return HTTPException(status_code=501, detail=f"{what} not implemented yet")


@app.get("/projects/{id}/highlights")
def get_highlights(id: str) -> dict:  # noqa: ARG001
    raise _not_implemented("highlights (M2 Analysis Worker)")


@app.get("/projects/{id}/timeline")
def get_timeline(id: str) -> dict:  # noqa: ARG001
    raise _not_implemented("timeline read (M2 Composer)")


@app.put("/projects/{id}/timeline")
def update_timeline(id: str) -> dict:  # noqa: ARG001
    raise _not_implemented("timeline update (M2)")


@app.post("/projects/{id}/compose")
def compose_timeline(id: str) -> dict:  # noqa: ARG001
    raise _not_implemented("compose (M2 Composer Worker)")


@app.post("/projects/{id}/renders")
def create_render(id: str) -> dict:  # noqa: ARG001
    raise _not_implemented("render submission (M3/M4)")


@app.get("/renders/{render_id}")
def get_render(render_id: str) -> dict:  # noqa: ARG001
    raise _not_implemented("render status (M3/M4)")


@app.get("/artifacts/{artifact_id}/download")
def get_artifact_download_url(artifact_id: str) -> dict:  # noqa: ARG001
    raise _not_implemented("artifact download (M4)")
