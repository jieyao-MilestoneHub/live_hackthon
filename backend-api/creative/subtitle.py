"""字幕計畫：timeline.v1 (+ highlights) → subtitle.v1（Creative Planning 第一步）。

純函式：以每個 timeline clip 對應的 highlight 逐字內容,在該 clip 的輸出時間區間
[timeline_start_ms, timeline_end_ms] 內切出字幕 cue（長句依標點分句、依字數比例均分
時間）。`emphasis_words` 以情緒關鍵詞比對。demand.md §十二。

限制:highlights.v1 無逐句時間,cue 時間為 clip 內比例估算(MVP);待有 segment/word
時間時再精修。真部署由 Creative Planning Worker(Lambda+Bedrock)產出。
"""
from __future__ import annotations

import re
from typing import Any

from analysis.highlights import EMOTION_KEYWORDS
from analysis.validate import validate_subtitle

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?])")
_EMPHASIS_LIMIT = 3


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _emphasis(text: str) -> list[str]:
    found: list[str] = []
    for k in EMOTION_KEYWORDS:
        if k in text and k not in found:
            found.append(k)
        if len(found) >= _EMPHASIS_LIMIT:
            break
    return found


def _clip_cues(text: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    sentences = _split_sentences(text)
    if not sentences:
        return []
    span = max(1, end_ms - start_ms)
    total_chars = sum(len(s) for s in sentences) or 1
    cues: list[dict[str, Any]] = []
    cursor = start_ms
    for i, sentence in enumerate(sentences):
        if i == len(sentences) - 1:
            cue_end = end_ms  # snap last cue to the clip end
        else:
            cue_end = min(end_ms, cursor + max(1, round(span * len(sentence) / total_chars)))
        if cue_end <= cursor:
            cue_end = min(end_ms, cursor + 1)
        cue: dict[str, Any] = {"start_ms": cursor, "end_ms": cue_end, "text": sentence}
        emph = _emphasis(sentence)
        if emph:
            cue["emphasis_words"] = emph
        cues.append(cue)
        cursor = cue_end
    return cues


def plan_subtitles(
    timeline: dict[str, Any],
    highlights: list[dict[str, Any]],
    project_id: str,
    render_id: str,
    language: str = "zh-TW",
) -> dict[str, Any]:
    """回傳符合 subtitle.v1 的 dict（cue 對齊 timeline 輸出時間）。"""
    text_by_hl = {
        h["highlight_id"]: (h.get("transcript") or h.get("suggested_title") or "")
        for h in highlights
    }

    cues: list[dict[str, Any]] = []
    for clip in sorted(timeline.get("clips", []), key=lambda c: c["timeline_order"]):
        text = text_by_hl.get(clip["highlight_id"], "")
        cues.extend(_clip_cues(text, clip["timeline_start_ms"], clip["timeline_end_ms"]))

    subtitle = {
        "schema_version": "subtitle.v1",
        "language": language,
        "project_id": project_id,
        "render_id": render_id,
        "cues": cues,
    }
    validate_subtitle(subtitle)
    return subtitle
