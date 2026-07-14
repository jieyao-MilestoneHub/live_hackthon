"""Amazon Transcribe adapter — 批次轉錄 + 說話者分離（diarization）。

查證結論（見 plan「AWS 服務查證結論 §A」）：
  * 批次 ``StartTranscriptionJob`` 為非同步；mp4 可直接吃、免抽音。
  * diarization：``Settings.ShowSpeakerLabels=true`` + ``MaxSpeakerLabels``（2–30）。
  * 輸出時間為**字串、單位秒** → 此處統一轉成毫秒 int（transcript.v1 用 *_ms）。
  * MVP 以 ``GetTranscriptionJob`` 輪詢（長檔退避）；生產改 EventBridge。

``parse_transcribe_result`` 是純函式（可離線單測，覆蓋秒→毫秒與 words↔speaker 對齊）。
Real 走 boto3；Stub 回傳 canned transcript.v1。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from app.aws.config import AttributionConfig
from app.settings import Settings


def _sec_to_ms(value: Any) -> int:
    return int(round(float(value) * 1000))


def _join_words(words: list[dict[str, Any]], language_code: str) -> str:
    contents = [w["content"] for w in words if w.get("content")]
    if language_code.lower().startswith("zh") or language_code.lower().startswith("ja"):
        return "".join(contents)
    return " ".join(contents)


def parse_transcribe_result(
    raw: dict[str, Any],
    project_id: str,
    language_code: str = "zh-TW",
) -> dict[str, Any]:
    """把 Transcribe 批次結果 JSON 正規化為 transcript.v1（秒→毫秒、對齊說話者）。

    以 ``speaker_labels.segments`` 為單位，收攏落在其時間窗內的 ``results.items``
    （pronunciation）成 utterance 文字；標點無時間，串接於段內。
    """
    results = raw.get("results", {})
    items = results.get("items", [])

    words: list[dict[str, Any]] = []
    for it in items:
        if it.get("type") != "pronunciation":
            continue
        st, en = it.get("start_time"), it.get("end_time")
        if st is None or en is None:
            continue
        alt = (it.get("alternatives") or [{}])[0]
        words.append({
            "start_ms": _sec_to_ms(st),
            "end_ms": _sec_to_ms(en),
            "content": alt.get("content", ""),
            "confidence": float(alt.get("confidence") or 0.0),
        })

    spk = results.get("speaker_labels", {})
    segments: list[dict[str, Any]] = []
    max_end = 0
    for i, seg in enumerate(spk.get("segments", []), start=1):
        s0, s1 = _sec_to_ms(seg["start_time"]), _sec_to_ms(seg["end_time"])
        max_end = max(max_end, s1)
        seg_words = [w for w in words if s0 <= w["start_ms"] < s1]
        text = _join_words(seg_words, language_code)
        conf = (
            round(sum(w["confidence"] for w in seg_words) / len(seg_words), 3)
            if seg_words else None
        )
        segments.append({
            "segment_id": f"seg_{i:04d}",
            "start_ms": s0,
            "end_ms": s1,
            "speaker": seg.get("speaker_label"),
            "text": text,
            "confidence": conf,
        })

    return {
        "schema_version": "transcript.v1",
        "project_id": project_id,
        "language_code": language_code,
        "duration_ms": max_end,
        "segments": segments,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


class RealTranscriber:
    """boto3 Amazon Transcribe（TranscriberPort）。"""

    def __init__(self, settings: Settings, config: AttributionConfig) -> None:
        import boto3  # lazy

        self._settings = settings
        self._config = config
        self._client = boto3.client("transcribe", region_name=settings.aws_region)
        self._s3 = boto3.client("s3", region_name=settings.aws_region)

    def transcribe(
        self,
        project_id: str,
        media_uri: str,
        *,
        language_code: str,
        max_speakers: int,
    ) -> dict[str, Any]:
        import json

        job_name = f"lang-live-{project_id}"
        output_key = f"transcript/{project_id}/transcribe.json"
        self._client.start_transcription_job(
            TranscriptionJobName=job_name,
            Media={"MediaFileUri": media_uri},
            MediaFormat="mp4",
            LanguageCode=language_code,
            OutputBucketName=self._settings.work_bucket,
            OutputKey=output_key,
            Settings={
                "ShowSpeakerLabels": True,
                "MaxSpeakerLabels": max(2, min(30, int(max_speakers))),
            },
        )

        # MVP：輪詢（長檔退避）；生產應改 EventBridge → parser。
        raw: dict[str, Any] | None = None
        for _ in range(self._config.poll_max_attempts):
            resp = self._client.get_transcription_job(TranscriptionJobName=job_name)
            status = resp["TranscriptionJob"]["TranscriptionJobStatus"]
            if status == "COMPLETED":
                obj = self._s3.get_object(Bucket=self._settings.work_bucket, Key=output_key)
                raw = json.loads(obj["Body"].read())
                break
            if status == "FAILED":
                raise RuntimeError(
                    f"Transcribe job failed: {resp['TranscriptionJob'].get('FailureReason')}"
                )
            time.sleep(self._config.poll_interval_sec)

        if raw is None:
            raise TimeoutError(f"Transcribe job {job_name} did not complete in time")
        return parse_transcribe_result(raw, project_id, language_code)


class StubTranscriber:
    """離線替身：回傳 canned 兩說話者 transcript.v1（不需 AWS）。"""

    def __init__(self, settings: Settings, config: AttributionConfig) -> None:
        self._settings = settings
        self._config = config

    def transcribe(
        self,
        project_id: str,
        media_uri: str,
        *,
        language_code: str,
        max_speakers: int,
    ) -> dict[str, Any]:
        return {
            "schema_version": "transcript.v1",
            "project_id": project_id,
            "language_code": language_code,
            "duration_ms": 120000,
            "segments": [
                {"segment_id": "seg_0001", "start_ms": 0, "end_ms": 12500,
                 "speaker": "spk_0", "text": "大家好，歡迎來到今天的直播。", "confidence": 0.96},
                {"segment_id": "seg_0002", "start_ms": 45000, "end_ms": 62000,
                 "speaker": "spk_0", "text": "哇這個真的太扯了，太神了吧！", "confidence": 0.94},
                {"segment_id": "seg_0003", "start_ms": 100000, "end_ms": 108000,
                 "speaker": "spk_1", "text": "那可以請你介紹一下這個功能嗎？", "confidence": 0.9},
            ],
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
