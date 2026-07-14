"""Attribution 領域層抽象 Port（Protocol）。

AWS 服務介面在 ``app/aws/ports.py``；此處僅放與 domain 相關、pipeline 需要注入的
額外抽象（如 Active Speaker Detection provider），維持 DIP：pipeline 不 import 具體 worker。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ActiveSpeakerProvider(Protocol):
    """Active Speaker Detection（嘴型—音訊同步）證據來源。"""

    def detect(
        self,
        project_id: str,
        media_uri: str,
        *,
        transcript: dict[str, Any],
        face_appearances: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """回傳 asd_result.v1 的 ``segments``（可空）。"""
        ...
