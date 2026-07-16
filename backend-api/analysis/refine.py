"""AI 精修（分析流程階段 5–6）：用逐字稿定位笑點 + Bedrock 敘事填台詞。

純函式編排（比照 attribution.pipeline.run_attribution）：
  - propose_punchline_offsets：用逐字稿情緒峰提議每個高光的校正 offset（觀眾反應落後，
    真正的哏在聊天尖峰之前）。只提議，交編輯器 PATCH 確認。
  - enrich_annotations：把 annotations.v1 的 description / dimension.text / beat.line（台詞）
    以 NarrativeReviewerPort 填滿（離線走 Stub 罐頭）。

持久化交給呼叫端（workers.refine_worker）；本層不做 I/O。時間一律影片相對毫秒（ms）。
"""
from __future__ import annotations

import copy
from typing import Any

from analysis import emotion
from analysis.validate import validate_annotations


def _seg_emotion(text: str) -> int:
    """逐字稿段落的情緒強度（關鍵詞加權 + emoji + 驚嘆）。"""
    return emotion.count_keywords(text) * 2 + emotion.count_emojis(text) + emotion.count_exclaims(text)


def _is_included(h: dict[str, Any]) -> bool:
    return h.get("status") != "excluded" and h.get("selected") is not False


def _overlaps(seg: dict[str, Any], start_ms: int, end_ms: int) -> bool:
    return int(seg["start_ms"]) < end_ms and int(seg["end_ms"]) > start_ms


def _peak_segment(segments: list[dict[str, Any]]) -> dict[str, Any] | None:
    best, best_score = None, -1
    for s in segments:
        score = _seg_emotion(s.get("text") or "")
        if score > best_score:
            best, best_score = s, score
    return best


def propose_punchline_offsets(
    transcript: dict[str, Any],
    highlights: list[dict[str, Any]],
    *,
    lead_ms: int = 2000,
    buffer_ms: int = 20000,
) -> list[dict[str, Any]]:
    """對每個納入的高光，用逐字稿情緒峰提議校正 offset。

    在 ``[start-buffer, end]`` 內找情緒最高的逐字稿段落當笑點；提議起點 =
    ``max(0, 笑點段起 − lead)``；offset = 提議起點 − 目前起點。

    **無重疊段落 → 不提議（skip）**：逐字稿是影片相對時間，若某高光窗與逐字稿完全不重疊，
    多半是時基不一致（如聊天相對 -chattime 高光）或該處根本沒有語音；此時退回「整片全域
    情緒峰」會把該高光硬拽到影片別處（例如把 20:00 的高光拉到 05:00 的全域峰），破壞成品。
    故無重疊時不提議，交由呼叫端（refine_worker）在時基可信時才啟用。
    """
    segments = transcript.get("segments") or []
    if not segments:
        return []
    proposals: list[dict[str, Any]] = []
    for h in highlights:
        if not _is_included(h):
            continue
        start_ms, end_ms = int(h["start_ms"]), int(h["end_ms"])
        window = [s for s in segments if _overlaps(s, max(0, start_ms - buffer_ms), end_ms)]
        if not window:
            continue  # 無重疊 → 無可靠對齊，不硬拽到全域峰
        peak = _peak_segment(window)
        if peak is None:
            continue
        proposed_start = max(0, int(peak["start_ms"]) - lead_ms)
        proposals.append(
            {
                "highlight_id": h["highlight_id"],
                "current_start_ms": start_ms,
                "proposed_start_ms": proposed_start,
                "offset_ms": proposed_start - start_ms,
                "evidence_text": peak.get("text") or "",
            }
        )
    return proposals


def _window_text(transcript: dict[str, Any], start_ms: int, end_ms: int) -> str:
    """事件窗內逐字稿文字（CJK 無空格串接）；無重疊則退回全域情緒峰段落。"""
    segments = transcript.get("segments") or []
    overlapping = [s for s in segments if _overlaps(s, start_ms, end_ms)]
    if overlapping:
        return "".join(s.get("text") or "" for s in overlapping)
    peak = _peak_segment(segments)
    return (peak.get("text") or "") if peak else ""


def enrich_annotations(
    annotations: dict[str, Any],
    transcript: dict[str, Any],
    highlights: list[dict[str, Any]],
    narrative_reviewer: Any,
) -> dict[str, Any]:
    """回傳 description / dimension.text / beat.line 已填的**新** annotations.v1。"""
    doc = copy.deepcopy(annotations)
    hl_index = {h["highlight_id"]: h for h in highlights}

    for ann in doc.get("annotations", []):
        hl = hl_index.get(ann["highlight_id"])
        if hl is not None:
            start_ms, end_ms = int(hl["start_ms"]), int(hl["end_ms"])
        else:  # 退回維度 span 範圍
            spans = ann.get("dimensions") or []
            start_ms = min((int(d["start_ms"]) for d in spans), default=0)
            end_ms = max((int(d["end_ms"]) for d in spans), default=0)
        transcript_text = _window_text(transcript, start_ms, end_ms)

        chat_texts: list[str] = []
        for d in ann.get("dimensions", []):
            if d.get("dimension") == "chat_highlights":
                chat_texts = [m.get("text") or "" for m in (d.get("messages") or [])]

        context = {
            "highlight_id": ann["highlight_id"],
            "title": ann.get("title"),
            "transcript_text": transcript_text,
            "dimensions": [d["dimension"] for d in ann.get("dimensions", [])],
            "beats": [b["order"] for b in ann.get("beats", [])],
            "chat_texts": chat_texts,
        }
        out = narrative_reviewer.enrich(context) or {}
        dim_texts = out.get("dimension_texts") or {}
        beat_lines = out.get("beat_lines") or {}

        if out.get("description"):
            ann["description"] = out["description"]
        for d in ann.get("dimensions", []):
            filled = dim_texts.get(d["dimension"])
            if filled:
                d["text"] = filled
        for b in ann.get("beats", []):
            filled = beat_lines.get(str(b["order"]))
            if filled:
                b["line"] = filled

    validate_annotations(doc)
    return doc


def run_refine(
    highlights: list[dict[str, Any]],
    annotations: dict[str, Any],
    transcript: dict[str, Any],
    *,
    narrative_reviewer: Any = None,
    lead_ms: int = 2000,
    propose_offsets: bool = True,
) -> dict[str, Any]:
    """純編排：提議笑點 offset + 敘事精修 annotations。未給 reviewer 走 factory。

    ``propose_offsets=False``（時基不可信，如聊天相對高光）→ 不提議 offset，只做敘事精修，
    避免用影片相對逐字稿去校正非影片相對的高光窗。
    """
    if narrative_reviewer is None:
        from app.aws import factory

        narrative_reviewer = factory.get_narrative_reviewer()

    proposed = propose_punchline_offsets(transcript, highlights, lead_ms=lead_ms) if propose_offsets else []
    enriched = enrich_annotations(annotations, transcript, highlights, narrative_reviewer)
    return {"proposed_offsets": proposed, "annotations": enriched}
