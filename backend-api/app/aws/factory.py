"""AWS adapter 工廠（DIP 綁定）。

依 ``settings.use_inmemory`` 選 Real* / Stub*，回傳型別皆為 ``ports`` 的窄介面。
``lru_cache`` 單例；測試以 ``*.cache_clear()`` 重綁（見 tests 的 fixture）。

boto3 client 僅在 Real* 內建立（lazy）；離線/測試走 Stub*，無需 AWS 憑證。
"""
from __future__ import annotations

from functools import lru_cache

from app.aws import bedrock_nova, rekognition, transcribe
from app.aws.config import AttributionConfig, get_attribution_config
from app.aws.ports import (
    FaceEnrollmentPort,
    FaceSearchPort,
    NarrativeReviewerPort,
    SemanticReviewerPort,
    TranscriberPort,
)
from app.settings import Settings, get_settings


def _deps() -> tuple[Settings, AttributionConfig, bool]:
    settings = get_settings()
    return settings, get_attribution_config(), settings.use_inmemory


@lru_cache(maxsize=1)
def get_transcriber() -> TranscriberPort:
    settings, config, inmem = _deps()
    return (
        transcribe.StubTranscriber(settings, config) if inmem
        else transcribe.RealTranscriber(settings, config)
    )


@lru_cache(maxsize=1)
def _get_rekognition():
    settings, config, inmem = _deps()
    return (
        rekognition.StubRekognition(settings, config) if inmem
        else rekognition.RealRekognition(settings, config)
    )


def get_face_enrollment() -> FaceEnrollmentPort:
    """人物登錄（IndexFaces）— 由 API 的 people 端點使用。"""
    return _get_rekognition()


def get_face_search() -> FaceSearchPort:
    """影片人物搜尋（StartFaceSearch）— 由 pipeline 使用。"""
    return _get_rekognition()


@lru_cache(maxsize=1)
def get_nova_reviewer() -> SemanticReviewerPort:
    settings, config, inmem = _deps()
    return (
        bedrock_nova.StubNovaReviewer(settings, config) if inmem
        else bedrock_nova.RealNovaReviewer(settings, config)
    )


@lru_cache(maxsize=1)
def get_narrative_reviewer() -> NarrativeReviewerPort:
    settings, config, inmem = _deps()
    return (
        bedrock_nova.StubNarrativeReviewer(settings, config) if inmem
        else bedrock_nova.RealNarrativeReviewer(settings, config)
    )


def cache_clear() -> None:
    """清掉所有 adapter 單例（測試切換 USE_INMEMORY 時呼叫）。"""
    for fn in (get_transcriber, _get_rekognition, get_nova_reviewer, get_narrative_reviewer):
        fn.cache_clear()
