"""高光分析模組：transcript.v1 → highlights.v1（規則式，LLM 可選）。"""
from .highlights import detect_highlights, DEFAULT_PARAMS

__all__ = ["detect_highlights", "DEFAULT_PARAMS"]
