"""Active Speaker Detection worker（Speaker Attribution）。

判斷某時間區間內畫面中「當下正在說話」的臉部 track 與嘴型同步信心，產出 asd_result.v1，
作為 Fusion Worker 的一等視聽對齊證據。

MVP：``heuristic`` 純幾何/打分函式 + ``HeuristicASD`` provider（以現有臉部出現區間為代理）。
真模型（TalkNet/Light-ASD on Batch/SageMaker）以 ``estimate_from_landmarks`` 的接縫換入。
"""
from __future__ import annotations

from workers.asd.worker import HeuristicASD, StubASD, build_asd_result, run_asd

__all__ = ["HeuristicASD", "StubASD", "build_asd_result", "run_asd"]
