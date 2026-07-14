"""ASD worker 進入點 + provider（實作 ``ActiveSpeakerProvider``）。

``HeuristicASD``：MVP 可跑版本（代理訊號）。``StubASD``：測試替身（canned）。
``run_asd`` / ``build_asd_result``：組出並驗證 asd_result.v1。

真模型部署（Batch/SageMaker）以 ``Dockerfile.asd`` 為接縫，換掉 ``HeuristicASD`` 即可，
pipeline 不需改（DIP：fusion 只認 asd_result.v1）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from analysis.attribution.contracts import validate_asd_result
from workers.asd.heuristic import estimate_asd_segments


def build_asd_result(
    project_id: str,
    segments: list[dict[str, Any]],
    model_version: str,
) -> dict[str, Any]:
    """組出並驗證 asd_result.v1 文件。"""
    doc = {
        "schema_version": "asd_result.v1",
        "project_id": project_id,
        "model_version": model_version,
        "segments": segments,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    validate_asd_result(doc)
    return doc


def run_asd(
    project_id: str,
    media_uri: str,
    *,
    transcript: dict[str, Any],
    face_appearances: list[dict[str, Any]],
) -> dict[str, Any]:
    """MVP：以啟發式代理產出 asd_result.v1 文件。"""
    segments = estimate_asd_segments(transcript, face_appearances)
    return build_asd_result(project_id, segments, model_version="asd-heuristic-1.0.0")


class HeuristicASD:
    """ActiveSpeakerProvider：MVP 啟發式，回傳 asd_result.v1 的 segments。"""

    def detect(
        self,
        project_id: str,
        media_uri: str,
        *,
        transcript: dict[str, Any],
        face_appearances: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return estimate_asd_segments(transcript, face_appearances)


class StubASD:
    """ActiveSpeakerProvider 測試替身：回傳 canned 單段。"""

    def detect(
        self,
        project_id: str,
        media_uri: str,
        *,
        transcript: dict[str, Any],
        face_appearances: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [{
            "start_ms": 0, "end_ms": 12500, "speaker_cluster_id": "spk_0",
            "active_face_track_id": "track_1", "person_id": "person_001",
            "lip_sync_confidence": 0.91, "visible_ratio": 0.96,
            "evidence": {"stub": True},
        }]
