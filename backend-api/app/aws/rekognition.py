"""Amazon Rekognition adapter — 人物臉部登錄 + 影片人物搜尋。

查證結論（見 plan「AWS 服務查證結論 §B」）：
  * 登錄：CreateCollection → IndexFaces（MaxFaces=1、ExternalImageId=person_id、只存臉向量）。
  * 搜尋：StartFaceSearch（Video.S3Object、FaceMatchThreshold）→ 非同步 → GetFaceSearch。
    輸出 Persons[]：Timestamp 為**毫秒**、身分綁 ExternalImageId、Person.Index 僅本 job 內穩定。
  * 稀疏時間點 → 自行合併成區間（``normalize_face_search``，純函式，可離線單測）。

MVP 以 GetFaceSearch 輪詢；生產改 SNS→SQS。Real 走 boto3；Stub 回傳 canned appearances。
"""
from __future__ import annotations

import time
from typing import Any

from app.aws.config import AttributionConfig
from app.settings import Settings

_DEFAULT_GAP_MS = 1500     # 相鄰同人時間點間距 <= 此值則併為同一區間
_DEFAULT_PAD_MS = 500      # 單點區間向後補足，避免零長度


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """``s3://bucket/key`` → ``(bucket, key)``。"""
    if not uri.startswith("s3://"):
        raise ValueError(f"not an s3 uri: {uri}")
    rest = uri[len("s3://"):]
    bucket, _, key = rest.partition("/")
    return bucket, key


def normalize_face_search(
    persons: list[dict[str, Any]],
    *,
    gap_ms: int = _DEFAULT_GAP_MS,
    pad_ms: int = _DEFAULT_PAD_MS,
) -> list[dict[str, Any]]:
    """GetFaceSearch 的稀疏 ``Persons[]`` → 合併後的人物出現區間。

    輸出：``[{start_ms,end_ms,person_id,face_track_id,similarity(0–1),visible_ratio}]``。
    以 ``ExternalImageId`` 為人物身分（非 job-local 的 Person.Index）。
    """
    points: list[dict[str, Any]] = []
    for pm in persons:
        matches = pm.get("FaceMatches") or []
        if not matches:
            continue
        best = max(matches, key=lambda m: m.get("Similarity", 0) or 0)
        ext = (best.get("Face") or {}).get("ExternalImageId")
        if not ext:
            continue
        idx = (pm.get("Person") or {}).get("Index")
        points.append({
            "ts": int(pm["Timestamp"]),
            "person_id": ext,
            "face_track_id": f"track_{idx}" if idx is not None else None,
            "similarity": float(best.get("Similarity", 0) or 0) / 100.0,
        })

    points.sort(key=lambda p: (p["person_id"], p["ts"]))
    intervals: list[dict[str, Any]] = []
    for pt in points:
        if (
            intervals
            and intervals[-1]["person_id"] == pt["person_id"]
            and pt["ts"] - intervals[-1]["_last_ts"] <= gap_ms
        ):
            iv = intervals[-1]
            iv["end_ms"] = pt["ts"] + pad_ms
            iv["_last_ts"] = pt["ts"]
            iv["similarity"] = max(iv["similarity"], pt["similarity"])
        else:
            intervals.append({
                "start_ms": max(0, pt["ts"]),
                "end_ms": pt["ts"] + pad_ms,
                "_last_ts": pt["ts"],
                "person_id": pt["person_id"],
                "face_track_id": pt["face_track_id"],
                "similarity": pt["similarity"],
                "visible_ratio": 1.0,
            })
    for iv in intervals:
        iv.pop("_last_ts", None)
    intervals.sort(key=lambda i: i["start_ms"])
    return intervals


class RealRekognition:
    """boto3 Amazon Rekognition — 同時實作 FaceEnrollmentPort + FaceSearchPort。

    共用一個 client；呼叫端依賴的仍是各自的窄介面（ISP）。
    """

    def __init__(self, settings: Settings, config: AttributionConfig) -> None:
        import boto3  # lazy

        self._settings = settings
        self._config = config
        self._client = boto3.client("rekognition", region_name=settings.aws_region)

    def create_collection(self, project_id: str) -> str:
        from botocore.exceptions import ClientError

        collection_id = f"{self._config.collection_prefix}{project_id}"
        try:
            self._client.create_collection(CollectionId=collection_id)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceAlreadyExistsException":
                raise
        return collection_id

    def index_faces(
        self,
        collection_id: str,
        person_id: str,
        image_refs: list[dict[str, str]],
    ) -> dict[str, Any]:
        indexed: list[str] = []
        unindexed: list[dict[str, Any]] = []
        for ref in image_refs:
            resp = self._client.index_faces(
                CollectionId=collection_id,
                Image={"S3Object": {"Bucket": ref["bucket"], "Name": ref["key"]}},
                ExternalImageId=person_id,
                MaxFaces=1,
                QualityFilter="AUTO",
                DetectionAttributes=["DEFAULT"],
            )
            for rec in resp.get("FaceRecords", []):
                indexed.append(rec["Face"]["FaceId"])
            for un in resp.get("UnindexedFaces", []):
                unindexed.append({"key": ref["key"], "reasons": un.get("Reasons", [])})
        return {"indexed": indexed, "unindexed": unindexed}

    def search_faces(
        self,
        collection_id: str,
        media_uri: str,
        *,
        threshold: float,
    ) -> list[dict[str, Any]]:
        bucket, key = parse_s3_uri(media_uri)
        start = self._client.start_face_search(
            CollectionId=collection_id,
            Video={"S3Object": {"Bucket": bucket, "Name": key}},
            FaceMatchThreshold=float(threshold) * 100.0,
        )
        job_id = start["JobId"]

        persons: list[dict[str, Any]] = []
        for _ in range(self._config.poll_max_attempts):
            resp = self._client.get_face_search(JobId=job_id, MaxResults=1000)
            status = resp["JobStatus"]
            if status == "SUCCEEDED":
                persons.extend(resp.get("Persons", []))
                token = resp.get("NextToken")
                while token:
                    page = self._client.get_face_search(
                        JobId=job_id, MaxResults=1000, NextToken=token
                    )
                    persons.extend(page.get("Persons", []))
                    token = page.get("NextToken")
                break
            if status == "FAILED":
                raise RuntimeError(f"Rekognition face search failed: {resp.get('StatusMessage')}")
            time.sleep(self._config.poll_interval_sec)

        return normalize_face_search(persons)


class StubRekognition:
    """離線替身：不需 AWS。搜尋回傳與 StubTranscriber 對齊的 canned appearances。"""

    def __init__(self, settings: Settings, config: AttributionConfig) -> None:
        self._settings = settings
        self._config = config

    def create_collection(self, project_id: str) -> str:
        return f"{self._config.collection_prefix}{project_id}"

    def index_faces(
        self,
        collection_id: str,
        person_id: str,
        image_refs: list[dict[str, str]],
    ) -> dict[str, Any]:
        return {"indexed": [f"stub-face-{person_id}-{i}" for i in range(len(image_refs))], "unindexed": []}

    def search_faces(
        self,
        collection_id: str,
        media_uri: str,
        *,
        threshold: float,
    ) -> list[dict[str, Any]]:
        # 對齊 StubTranscriber 的 spk_0（person_001）/ spk_1（person_002）敘事
        return [
            {"start_ms": 0, "end_ms": 12500, "person_id": "person_001",
             "face_track_id": "track_1", "similarity": 0.98, "visible_ratio": 0.96},
            {"start_ms": 100000, "end_ms": 108000, "person_id": "person_002",
             "face_track_id": "track_2", "similarity": 0.9, "visible_ratio": 0.8},
        ]
