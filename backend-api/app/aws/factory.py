"""AWS adapter 工廠（DIP 綁定）。

依 ``settings.use_inmemory`` 選 Real* / Stub*，回傳型別皆為 ``ports`` 的窄介面。
``lru_cache`` 單例；測試以 ``*.cache_clear()`` 重綁（見 tests 的 fixture）。

boto3 client 僅在 Real* 內建立（lazy）；離線/測試走 Stub*，無需 AWS 憑證。
"""
from __future__ import annotations

import os
from functools import lru_cache

from app.aws import bedrock_nova, moderation, rekognition, transcribe
from app.aws.config import AttributionConfig, get_attribution_config
from app.aws.ports import (
    FaceEnrollmentPort,
    FaceSearchPort,
    NarrativeReviewerPort,
    SemanticReviewerPort,
    TextModerationPort,
    TranscriberPort,
    VisualModerationPort,
)
from app.settings import Settings, get_settings


def _deps() -> tuple[Settings, AttributionConfig, bool]:
    settings = get_settings()
    return settings, get_attribution_config(), settings.use_inmemory


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def get_transcriber() -> TranscriberPort:
    settings, config, inmem = _deps()
    if inmem:
        return transcribe.StubTranscriber(settings, config)

    inner = transcribe.RealTranscriber(settings, config)
    # Wrap the real transcriber so sources over Transcribe's 2GB limit are split
    # into per-segment jobs and merged back (TRANSCRIBE_SPLIT=0 to opt out).
    if not _env_bool("TRANSCRIBE_SPLIT", default=True):
        return inner
    from app.aws.media_segmenter import DEFAULT_MAX_BYTES, DEFAULT_SEGMENT_CAP_SEC, MediaSegmenter
    from app.aws.splitting_transcriber import SplittingTranscriber
    from app.storage import get_storage

    storage = get_storage()
    return SplittingTranscriber(
        inner,
        MediaSegmenter(storage, settings),
        storage,
        settings,
        config,
        max_bytes=int(os.environ.get("TRANSCRIBE_MAX_BYTES", str(DEFAULT_MAX_BYTES))),
        segment_cap_sec=int(os.environ.get("TRANSCRIBE_SEGMENT_SEC", str(DEFAULT_SEGMENT_CAP_SEC))),
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
def _get_content_moderation():
    """One concrete object implements both moderation ports (visual + text),
    mirroring _get_rekognition; exposed via the two typed getters below (ISP)."""
    settings, config, inmem = _deps()
    return (
        moderation.StubContentModeration(settings, config) if inmem
        else moderation.RealContentModeration(settings, config)
    )


def get_visual_moderation() -> VisualModerationPort:
    """影片視覺內容審核（Rekognition StartContentModeration）。"""
    return _get_content_moderation()


def get_text_moderation() -> TextModerationPort:
    """文字內容審核（Bedrock zh-TW 受約束分類）。"""
    return _get_content_moderation()


@lru_cache(maxsize=1)
def get_narrative_reviewer() -> NarrativeReviewerPort:
    settings, config, inmem = _deps()
    return (
        bedrock_nova.StubNarrativeReviewer(settings, config) if inmem
        else bedrock_nova.RealNarrativeReviewer(settings, config)
    )


def cache_clear() -> None:
    """清掉所有 adapter 單例（測試切換 USE_INMEMORY 時呼叫）。"""
    for fn in (
        get_transcriber,
        _get_rekognition,
        get_nova_reviewer,
        get_narrative_reviewer,
        _get_content_moderation,
    ):
        fn.cache_clear()
