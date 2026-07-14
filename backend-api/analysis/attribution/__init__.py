"""Speaker Attribution — 具名說話者逐字稿融合（analysis 子套件）。

純函式融合核心（``fuse``）與打分常數（``scoring``）；不含 AWS/IO。編排與 AWS
adapter 分別在 ``analysis/attribution/pipeline.py`` 與 ``app/aws/``。
"""
from __future__ import annotations

from analysis.attribution.fusion import (
    ATTRIBUTION_VERSION,
    DEFAULT_ATTRIBUTION_PARAMS,
    fuse,
)

__all__ = ["fuse", "DEFAULT_ATTRIBUTION_PARAMS", "ATTRIBUTION_VERSION"]
