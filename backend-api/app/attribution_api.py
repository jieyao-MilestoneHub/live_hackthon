"""Speaker-Attribution 控制面 API（獨立 APIRouter，不編輯 main.py）。

掛載方式（交給維護者在 main.py 加一行，避免與其他 session 的 main.py 編輯衝突）：

    from app.attribution_api import router as attribution_router
    app.include_router(attribution_router)

端點皆為新路由（不碰既有 /projects 三端點與 501 stub）。Pydantic 模型內嵌於此檔，
不編輯共用的 app/schemas.py。持久化用 app/attribution_repository.py，AWS 用 app/aws/factory。
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.attribution_repository import AttributionRepository, get_attribution_repository
from app.auth import Principal, current_principal
from app.aws import factory
from app.aws.config import get_attribution_config
from app.settings import get_settings

router = APIRouter(tags=["speaker-attribution"])

Role = Literal["protagonist", "host", "guest", "unknown"]


# ---- Pydantic 模型（內嵌，不動 app/schemas.py）----
class PersonCreate(BaseModel):
    display_name: str = Field(..., examples=["主播 A"])
    role: Role = "guest"
    reference_image_keys: list[str] = Field(
        default_factory=list,
        description="raw bucket 內的參考照片 key（3–10 張）；提供則走 Rekognition 登錄（方案A）",
    )


class Person(BaseModel):
    person_id: str
    display_name: str
    role: Role
    identity_source: str
    rekognition_collection_id: str | None = None
    rekognition_external_id: str | None = None
    reference_image_keys: list[str] = Field(default_factory=list)
    enrolled_at: str | None = None
    indexed_faces: int | None = None


class SpeakerLabelUpdate(BaseModel):
    person_id: str


class UtteranceCorrection(BaseModel):
    person_id: str | None = Field(default=None, description="null → 標回未知")


class RunAttributionRequest(BaseModel):
    media_uri: str | None = Field(default=None, description="預設由 raw bucket + source key 推得")
    use_asd: bool = Field(default=True, description="是否啟用 Active Speaker Detection 證據")


def _collection_id(project_id: str) -> str:
    return f"{get_attribution_config().collection_prefix}{project_id}"


# ---- 人物名冊（方案A 登錄 / 列出）----
@router.post("/projects/{project_id}/people", response_model=Person, status_code=201)
def enroll_person(
    project_id: str,
    body: PersonCreate,
    principal: Principal = Depends(current_principal),
    repo: AttributionRepository = Depends(get_attribution_repository),
) -> Person:
    settings = get_settings()
    person_id = f"person_{uuid.uuid4().hex[:8]}"
    indexed = 0
    collection_id: str | None = None
    external_id: str | None = None
    identity_source = "user_label"

    if body.reference_image_keys:
        enroll = factory.get_face_enrollment()
        collection_id = enroll.create_collection(project_id)
        result = enroll.index_faces(
            collection_id,
            person_id,
            [{"bucket": settings.raw_bucket, "key": k} for k in body.reference_image_keys],
        )
        indexed = len(result.get("indexed", []))
        external_id = person_id
        identity_source = "rekognition_collection"

    participant = {
        "person_id": person_id,
        "display_name": body.display_name,
        "role": body.role,
        "identity_source": identity_source,
        "rekognition_collection_id": collection_id,
        "rekognition_external_id": external_id,
        "reference_image_keys": body.reference_image_keys,
        "enrolled_at": None,
    }
    repo.put_people(project_id, [participant])
    return Person(**participant, indexed_faces=indexed)


@router.get("/projects/{project_id}/people", response_model=list[Person])
def list_people(
    project_id: str,
    repo: AttributionRepository = Depends(get_attribution_repository),
) -> list[Person]:
    return [Person(**p) for p in repo.list_people(project_id)]


# ---- 具名逐字稿：產生 / 讀取 ----
@router.post("/projects/{project_id}/attribution", status_code=201)
def run_attribution_endpoint(
    project_id: str,
    body: RunAttributionRequest,
    principal: Principal = Depends(current_principal),
    repo: AttributionRepository = Depends(get_attribution_repository),
) -> dict[str, Any]:
    from analysis.attribution.pipeline import run_attribution
    from workers.asd.worker import HeuristicASD

    settings = get_settings()
    media_uri = body.media_uri or (
        f"s3://{settings.raw_bucket}/{settings.source_key(principal.tenant_id, project_id)}"
    )
    people = repo.list_people(project_id)
    doc = run_attribution(
        project_id,
        media_uri,
        people=people,
        collection_id=_collection_id(project_id),
        cluster_labels=repo.get_cluster_labels(project_id),
        asd_provider=HeuristicASD() if body.use_asd else None,
    )
    repo.put_attributed_transcript(project_id, doc)
    return doc


@router.get("/projects/{project_id}/transcript")
def get_attributed_transcript(
    project_id: str,
    repo: AttributionRepository = Depends(get_attribution_repository),
) -> dict[str, Any]:
    doc = repo.get_attributed_transcript(project_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="attributed transcript not found; run attribution first")
    return doc


# ---- 更正：整個群組 / 單句 ----
@router.patch("/projects/{project_id}/speakers/{cluster_id}")
def label_speaker_cluster(
    project_id: str,
    cluster_id: str,
    body: SpeakerLabelUpdate,
    principal: Principal = Depends(current_principal),
    repo: AttributionRepository = Depends(get_attribution_repository),
) -> dict[str, Any]:
    updated = repo.label_cluster(project_id, cluster_id, body.person_id, corrected_by=principal.user_id)
    return {"cluster_id": cluster_id, "person_id": body.person_id, "updated_utterances": updated}


@router.patch("/projects/{project_id}/utterances/{utterance_id}")
def correct_utterance(
    project_id: str,
    utterance_id: str,
    body: UtteranceCorrection,
    principal: Principal = Depends(current_principal),
    repo: AttributionRepository = Depends(get_attribution_repository),
) -> dict[str, Any]:
    updated = repo.correct_utterance(project_id, utterance_id, body.person_id, corrected_by=principal.user_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="utterance or attributed transcript not found")
    return updated
