"""兩層字幕計畫：timeline.v1 (+highlights +annotations) → subtitle.v1（Creative Planning 第一步）。

兩層（demand.md §十二 延伸）：
  * **Tier 1 `caption`（逐字稿基底）**：每個 clip 對應 highlight 的逐字內容，在該 clip 輸出時間
    區間內依標點分句上字（好位置＝下三分之一）。emphasis_words 以情緒關鍵詞比對。
  * **Tier 2 `keyword`（爆點關鍵字）**：在 punchline 位置疊一個「字數精簡、代表性高」的關鍵字
    （含出現動畫，如 pop 彈入），位置置中偏上、與 caption 錯開。關鍵字由可注入的
    ``KeywordExtractor`` 抽取（預設規則式）。

樣式（字型/字體/顏色/邊框/位置）由 ``creative/style.py`` 的 preset + ``subtitle_settings.style``
覆寫合併，序列化進 subtitle.v1 的開放 `style`（render 層再轉 ASS）。純函式、時間為 timeline
輸出毫秒（ms）。真部署由 Creative Planning Worker（Lambda）產出。
"""
from __future__ import annotations

import re
from typing import Any

from analysis.highlights import EMOTION_KEYWORDS
from analysis.keywords import KeywordExtractor, get_keyword_extractor
from analysis.validate import validate_subtitle
from creative.style import merge_style, resolve_animation, resolve_styles

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?])")
_EMPHASIS_LIMIT = 3
KEYWORD_MAX_MS = 2200   # 爆點字卡最長顯示
KEYWORD_MIN_MS = 700    # 爆點字卡最短顯示
_KEYWORD_TAIL_RATIO = 0.65  # 無 annotations 時，字卡落在 clip 後段的起點比例


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
    """Tier 1 逐字稿 cue（依標點分句、依字數比例均分時間）。"""
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
        cue: dict[str, Any] = {"start_ms": cursor, "end_ms": cue_end, "text": sentence, "kind": "caption"}
        emph = _emphasis(sentence)
        if emph:
            cue["emphasis_words"] = emph
        cues.append(cue)
        cursor = cue_end
    return cues


def _annotation_by_hl(annotations: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {a["highlight_id"]: a for a in (annotations or {}).get("annotations", [])}


def _punch_source_span(ann: dict[str, Any] | None) -> tuple[int, int] | None:
    """由 annotation 的 punchline beat/dimension 取 source 區間（爆點）。"""
    if not ann:
        return None
    punch_beats = [b for b in ann.get("beats", []) if b.get("beat") == "punchline"]
    if punch_beats:
        return min(b["start_ms"] for b in punch_beats), max(b["end_ms"] for b in punch_beats)
    punch_dims = [d for d in ann.get("dimensions", []) if d.get("dimension") == "punchline"]
    if punch_dims:
        return min(d["start_ms"] for d in punch_dims), max(d["end_ms"] for d in punch_dims)
    return None


def _punch_text(ann: dict[str, Any] | None, highlight: dict[str, Any]) -> str:
    if ann:
        for b in ann.get("beats", []):
            if b.get("beat") == "punchline" and b.get("line"):
                return b["line"]
        for d in ann.get("dimensions", []):
            if d.get("dimension") == "punchline" and d.get("text"):
                return d["text"]
    return highlight.get("transcript") or highlight.get("suggested_title") or ""


def _keyword_cue(
    clip: dict[str, Any],
    highlight: dict[str, Any],
    ann: dict[str, Any] | None,
    extractor: KeywordExtractor,
    animation: dict[str, Any],
) -> dict[str, Any] | None:
    """Tier 2 爆點字卡：在 punchline 位置放一個精簡關鍵字（source→timeline 線性映射）。"""
    src_s, src_e = int(clip["source_start_ms"]), int(clip["source_end_ms"])
    tl_s, tl_e = int(clip["timeline_start_ms"]), int(clip["timeline_end_ms"])

    region: tuple[int, int] | None = None
    punch_src = _punch_source_span(ann)
    if punch_src is not None:
        a = max(src_s, punch_src[0])
        b = min(src_e, punch_src[1])
        if b > a:  # punchline 落在此 clip：映射到 timeline
            region = (tl_s + (a - src_s), tl_s + (b - src_s))
        else:
            return None  # 此 clip 不含爆點（如 setup 刀）→ 不放字卡
    else:
        span = tl_e - tl_s  # 無 annotations：落在 clip 後段
        region = (tl_s + int(span * _KEYWORD_TAIL_RATIO), tl_e)

    text = _punch_text(ann, highlight)
    hits = extractor.extract(
        text,
        emphasis_words=tuple(_emphasis(text)),
        reason=highlight.get("reason"),
        limit=1,
    )
    if not hits:
        return None
    keyword = hits[0].text

    start = region[0]
    end = min(region[1], start + KEYWORD_MAX_MS)
    if end - start < KEYWORD_MIN_MS:
        end = min(tl_e, start + KEYWORD_MIN_MS)
    if end <= start:
        return None
    return {
        "start_ms": int(start),
        "end_ms": int(end),
        "text": keyword,
        "kind": "keyword",
        "emphasis_words": [keyword],
        "animation": dict(animation),
    }


def plan_subtitles(
    timeline: dict[str, Any],
    highlights: list[dict[str, Any]],
    project_id: str,
    render_id: str,
    *,
    annotations: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    style: dict[str, Any] | None = None,
    extractor: KeywordExtractor | None = None,
    language: str = "zh-TW",
) -> dict[str, Any]:
    """回傳符合 subtitle.v1 的兩層字幕 dict（cue 對齊 timeline 輸出時間）。

    ``settings`` = timeline.subtitle_settings（``enabled`` 關則不上字幕；``mode`` ∈
    {both(預設)/caption/keyword} 決定層次）。``style`` 為額外 per-kind 樣式覆寫。
    """
    settings = settings or {}
    if settings.get("enabled") is False:  # 使用者關閉字幕
        subtitle = {"schema_version": "subtitle.v1", "language": language,
                    "project_id": project_id, "render_id": render_id, "style": {}, "cues": []}
        validate_subtitle(subtitle)
        return subtitle

    mode = (settings.get("mode") or "both").strip().lower()
    want_caption = mode in {"both", "caption", "full", "auto"}
    want_keyword = mode in {"both", "keyword", "auto"}

    styles = resolve_styles(settings)
    if style:  # 額外覆寫（可 per-kind 或 flat）
        for kind in ("caption", "keyword"):
            styles[kind] = merge_style(styles[kind], style.get(kind) or style)
    animation = resolve_animation((settings.get("keyword") or {}).get("animation"))
    extractor = extractor or get_keyword_extractor()

    text_by_hl = {
        h["highlight_id"]: (h.get("transcript") or h.get("suggested_title") or "")
        for h in highlights
    }
    hl_by_id = {h["highlight_id"]: h for h in highlights}
    ann_by_hl = _annotation_by_hl(annotations)

    cues: list[dict[str, Any]] = []
    for clip in sorted(timeline.get("clips", []), key=lambda c: c["timeline_order"]):
        hid = clip["highlight_id"]
        if want_caption:
            cues.extend(_clip_cues(text_by_hl.get(hid, ""), clip["timeline_start_ms"], clip["timeline_end_ms"]))
        if want_keyword and hid in hl_by_id:
            kw = _keyword_cue(clip, hl_by_id[hid], ann_by_hl.get(hid), extractor, animation)
            if kw:
                cues.append(kw)

    # caption 先於同起點的 keyword，確保 start 非遞減且穩定可重現。
    cues.sort(key=lambda c: (c["start_ms"], 0 if c.get("kind") == "caption" else 1))

    subtitle = {
        "schema_version": "subtitle.v1",
        "language": language,
        "project_id": project_id,
        "render_id": render_id,
        "style": styles,
        "cues": cues,
    }
    validate_subtitle(subtitle)
    return subtitle
