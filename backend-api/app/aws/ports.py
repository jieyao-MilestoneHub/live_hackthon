"""AWS 服務抽象 Port（Protocol）— Speaker Attribution。

依 SOLID 的 ISP/DIP：把外部服務切成小而專的介面，呼叫端（pipeline / API）只依賴
自己用到的方法，具體 Real*/Stub* 由 ``app/aws/factory.py`` 依 ``settings.use_inmemory``
綁定。boto3 只出現在具體 Real 實作內。

回傳型別一律是「已正規化、契約友善」的純 dict/list（時間毫秒、相似度 0–1），
讓下游 fusion 不需認得任何 AWS 原生格式。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TranscriberPort(Protocol):
    """語音轉文字 + 說話者分離（Amazon Transcribe）。"""

    def transcribe(
        self,
        project_id: str,
        media_uri: str,
        *,
        language_code: str,
        max_speakers: int,
    ) -> dict[str, Any]:
        """回傳 transcript.v1 dict（segments 帶匿名 ``speaker=spk_N``、時間毫秒）。"""
        ...


@runtime_checkable
class FaceEnrollmentPort(Protocol):
    """人物臉部登錄（Amazon Rekognition Collection）。"""

    def create_collection(self, project_id: str) -> str:
        """建立 project-specific Face Collection，回傳 collection_id。"""
        ...

    def index_faces(
        self,
        collection_id: str,
        person_id: str,
        image_refs: list[dict[str, str]],
    ) -> dict[str, Any]:
        """把 3–10 張參考照片以 ``ExternalImageId=person_id`` 登錄。

        ``image_refs`` = ``[{"bucket":..., "key":...}, ...]``。
        回傳 ``{"indexed": [face_id,...], "unindexed": [{"key":..., "reasons":[...]}, ...]}``。
        """
        ...


@runtime_checkable
class FaceSearchPort(Protocol):
    """影片人物搜尋（Amazon Rekognition Video StartFaceSearch）。"""

    def search_faces(
        self,
        collection_id: str,
        media_uri: str,
        *,
        threshold: float,
    ) -> list[dict[str, Any]]:
        """回傳正規化的人物出現區間（稀疏時間點已合併）。

        ``[{start_ms,end_ms,person_id,face_track_id,similarity(0–1),visible_ratio}]``。
        """
        ...


@runtime_checkable
class SemanticReviewerPort(Protocol):
    """低信心片段語意複核（Amazon Bedrock — Nova）。僅回傳候選之一或 unknown。"""

    def review_speaker(
        self,
        candidate_person_ids: list[str],
        context: dict[str, Any],
        *,
        complex_case: bool = False,
    ) -> str:
        """回傳 ``candidate_person_ids`` 之一或 ``"unknown"``（受約束輸出）。"""
        ...
