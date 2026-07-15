"""Speaker-Attribution 專屬設定（獨立於 app/settings.py，避免與其他 session 的
共用檔編輯衝突）。只讀 ``os.environ``；區域/桶名沿用既有 ``app.settings`` 的欄位。

新增環境變數（見 ``.env.attribution.example``）：
  ATTRIBUTION_ENABLED, TRANSCRIBE_LANGUAGE_CODE, MAX_SPEAKER_LABELS,
  TRANSCRIBE_POLL_INTERVAL_SEC, TRANSCRIBE_POLL_MAX_ATTEMPTS,
  FACE_MATCH_THRESHOLD, REKOGNITION_COLLECTION_PREFIX, BEDROCK_REGION,
  NOVA_REVIEW_MODEL_ID, NOVA_REASONING_MODEL_ID, NOVA_PREMIER_MODEL_ID
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AttributionConfig:
    enabled: bool
    language_code: str
    max_speaker_labels: int
    poll_interval_sec: int
    poll_max_attempts: int
    face_match_threshold: float          # 0–1；Rekognition FaceMatchThreshold = x*100
    collection_prefix: str
    bedrock_region: str
    # Nova model ids（查證結論 §C）：
    #  - review  = 便宜文字複核（Micro，us-east-1 in-region）
    #  - reasoning = Nova 2 Lite（us-east-1 須用 us. 跨區 inference profile）
    #  - premier = 複雜複核
    nova_review_model_id: str
    nova_reasoning_model_id: str
    nova_premier_model_id: str
    # Content moderation tuning (see analysis/moderation_policy.py for the pure
    # decision policy that consumes these thresholds).
    moderation_min_confidence: float     # Rekognition MinConfidence (0–100)
    moderation_model_id: str             # Bedrock model for zh-TW text classify
    moderation_flag_threshold: float     # 0–1 severity ⇒ FLAGGED (needs review)
    moderation_block_threshold: float    # 0–1 severity ⇒ BLOCKED (hard gate)


@lru_cache(maxsize=1)
def get_attribution_config() -> AttributionConfig:
    region = os.environ.get("AWS_REGION", "us-east-1")
    return AttributionConfig(
        enabled=_env_bool("ATTRIBUTION_ENABLED", default=True),
        language_code=os.environ.get("TRANSCRIBE_LANGUAGE_CODE", "zh-TW"),
        max_speaker_labels=int(os.environ.get("MAX_SPEAKER_LABELS", "5")),
        poll_interval_sec=int(os.environ.get("TRANSCRIBE_POLL_INTERVAL_SEC", "15")),
        poll_max_attempts=int(os.environ.get("TRANSCRIBE_POLL_MAX_ATTEMPTS", "40")),
        face_match_threshold=float(os.environ.get("FACE_MATCH_THRESHOLD", "0.85")),
        collection_prefix=os.environ.get("REKOGNITION_COLLECTION_PREFIX", "lang-live-"),
        bedrock_region=os.environ.get("BEDROCK_REGION", region),
        nova_review_model_id=os.environ.get("NOVA_REVIEW_MODEL_ID", "amazon.nova-micro-v1:0"),
        nova_reasoning_model_id=os.environ.get("NOVA_REASONING_MODEL_ID", "us.amazon.nova-2-lite-v1:0"),
        nova_premier_model_id=os.environ.get("NOVA_PREMIER_MODEL_ID", "amazon.nova-premier-v1:0"),
        moderation_min_confidence=float(os.environ.get("MODERATION_MIN_CONFIDENCE", "50")),
        moderation_model_id=os.environ.get(
            "MODERATION_MODEL_ID", os.environ.get("NOVA_REVIEW_MODEL_ID", "amazon.nova-micro-v1:0")
        ),
        moderation_flag_threshold=float(os.environ.get("MODERATION_FLAG_THRESHOLD", "0.5")),
        moderation_block_threshold=float(os.environ.get("MODERATION_BLOCK_THRESHOLD", "0.8")),
    )
