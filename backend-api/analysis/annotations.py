"""規則式結構化標註：highlights.v1 (+chatlog.v1) → annotations.v1（分析流程階段 7–8）。

把每個納入的高光事件窗，依敘事結構切成 5 維度標註
（埋梗 setup → 反應-一開始 reaction_start → 反應-轉折 reaction_turn → 笑點爆點 punchline
＋ 聊天室精彩留言 chat_highlights）與節拍 cut-list（beats），輸出 annotations.v1。

「便宜可重現的規則式初篩」：先用比例切分產生確定性草稿，`dimension.text` / `beat.line`
（台詞）留白，待 AI 精修 seam（Transcribe 逐字稿 + Bedrock 敘事）填入——本層不需 AWS。
時間一律影片相對毫秒（ms）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from analysis import emotion
from analysis.validate import validate_annotations

# 4 段敘事維度的預設時長比例（可經 params["dimension_ratios"] 覆寫）。
_BEAT_DIMENSIONS: tuple[str, ...] = ("setup", "reaction_start", "reaction_turn", "punchline")
_HEAD_DIMENSIONS: tuple[str, ...] = ("setup", "reaction_start", "reaction_turn")
DEFAULT_DIMENSION_RATIOS: dict[str, float] = {
    "setup": 0.30,
    "reaction_start": 0.25,
    "reaction_turn": 0.25,
    "punchline": 0.20,
}
CHAT_HIGHLIGHT_LIMIT = 3
MIN_PUNCH_MS = 3000  # 訊號對齊時，punchline 至少保留的長度


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_included(h: dict[str, Any]) -> bool:
    """納入標註的高光：未被排除、未取消選取。"""
    return h.get("status") != "excluded" and h.get("selected") is not False


def _signal_punch_start(highlight: dict[str, Any], start_ms: int, end_ms: int) -> int | None:
    """以 chat_window（聊天尖峰＝觀眾看到爆點的反應）對齊 punchline 起點；無訊號回 None。

    夾在 [中點, end−MIN_PUNCH] 內：確保埋梗/反應至少拿到前半、且 punchline 有最短長度。
    純訊號、決定性；沒有 chat_window 時退回比例切分（維持既有行為）。
    """
    cw = highlight.get("chat_window") or {}
    cw_start = cw.get("start_ms")
    if cw_start is None:
        return None
    length = end_ms - start_ms
    if length <= 0:
        return None
    lo = start_ms + length // 2
    hi = max(lo, end_ms - MIN_PUNCH_MS)
    return max(lo, min(int(cw_start), hi))


def _split_spans(
    start_ms: int,
    end_ms: int,
    ratios: dict[str, float],
    punch_start: int | None = None,
) -> list[tuple[str, int, int]]:
    """把 [start,end] 切成連續 4 段（整數 ms、不重疊、最後一段對齊 end）。

    ``punch_start`` 給定（訊號對齊）時：setup/reaction_* 依比例填 [start, punch_start]、
    punchline = [punch_start, end]；否則退回四段比例切分（既有行為）。
    """
    start_ms, end_ms = int(start_ms), int(end_ms)

    if punch_start is not None:
        punch_start = max(start_ms, min(int(punch_start), end_ms))
        head_total = punch_start - start_ms
        head_ratio_sum = sum(ratios.get(d, 0.0) for d in _HEAD_DIMENSIONS) or 1.0
        spans: list[tuple[str, int, int]] = []
        cursor = start_ms
        for i, dim in enumerate(_HEAD_DIMENSIONS):
            if i == len(_HEAD_DIMENSIONS) - 1:
                seg_end = punch_start  # 最後一段對齊 punch_start
            else:
                seg_end = min(punch_start, cursor + int(round(head_total * ratios.get(dim, 0.0) / head_ratio_sum)))
                seg_end = max(seg_end, cursor)
            spans.append((dim, cursor, seg_end))
            cursor = seg_end
        spans.append(("punchline", punch_start, end_ms))
        return spans

    total = max(0, end_ms - start_ms)
    spans = []
    cursor = start_ms
    for i, dim in enumerate(_BEAT_DIMENSIONS):
        if i == len(_BEAT_DIMENSIONS) - 1:
            seg_end = end_ms  # 最後一段吃掉餘數，確保對齊
        else:
            seg_end = min(end_ms, cursor + int(round(total * ratios.get(dim, 0.0))))
            seg_end = max(seg_end, cursor)  # 不倒退
        spans.append((dim, cursor, seg_end))
        cursor = seg_end
    return spans


def _transcript_index(transcript: dict[str, Any] | None) -> list[tuple[int, int, str]]:
    """transcript.v1 → [(start_ms, end_ms, text)]（依時間排），供回填台詞。"""
    if not transcript:
        return []
    segs = [
        (int(s["start_ms"]), int(s["end_ms"]), s.get("text") or "")
        for s in transcript.get("segments", [])
        if s.get("text")
    ]
    return sorted(segs, key=lambda t: t[0])


def _line_for_span(seg_index: list[tuple[int, int, str]], start_ms: int, end_ms: int) -> str | None:
    """回傳與 [start,end] 時間重疊的逐字稿文字（串接）；無則 None。"""
    if not seg_index:
        return None
    parts = [t for (s, e, t) in seg_index if not (e <= start_ms or s >= end_ms)]
    joined = "".join(parts).strip()
    return joined or None


def _chat_highlight_messages(
    highlight: dict[str, Any],
    chatlog_index: dict[str, dict[str, Any]] | None,
    limit: int = CHAT_HIGHLIGHT_LIMIT,
) -> list[dict[str, Any]]:
    """由 highlight.provenance.chat_message_ids 取該段聊天留言，依情緒強度排序取前 N。"""
    if not chatlog_index:
        return []
    ids = ((highlight.get("provenance") or {}).get("chat_message_ids")) or []
    msgs = [chatlog_index[mid] for mid in ids if mid in chatlog_index]

    def _emo(m: dict[str, Any]) -> int:
        t = m.get("text") or ""
        return emotion.count_keywords(t) * 2 + emotion.count_emojis(t) + emotion.count_exclaims(t)

    msgs.sort(key=_emo, reverse=True)
    picked = []
    for m in msgs[:limit]:
        picked.append(
            {
                "message_id": m.get("message_id"),
                "username": m.get("username"),
                "text": m.get("text") or "",
            }
        )
    return picked


def _annotate_one(
    highlight: dict[str, Any],
    chatlog_index: dict[str, dict[str, Any]] | None,
    ratios: dict[str, float],
    seg_index: list[tuple[int, int, str]] | None = None,
) -> dict[str, Any]:
    start_ms, end_ms = int(highlight["start_ms"]), int(highlight["end_ms"])
    # 有 chat_window（聊天尖峰）→ 以訊號對齊 punchline 起點；否則比例切分（既有行為）。
    punch_start = _signal_punch_start(highlight, start_ms, end_ms)
    spans = _split_spans(start_ms, end_ms, ratios, punch_start)
    seg_index = seg_index or []

    dimensions: list[dict[str, Any]] = [
        {"dimension": dim, "start_ms": s, "end_ms": e, "text": _line_for_span(seg_index, s, e)}
        for dim, s, e in spans
    ]
    # 第 5 維：聊天室精彩留言，span = punchline 段（payoff 區）。
    punch = next((sp for sp in spans if sp[0] == "punchline"), spans[-1])
    dimensions.append(
        {
            "dimension": "chat_highlights",
            "start_ms": punch[1],
            "end_ms": punch[2],
            "text": None,
            "messages": _chat_highlight_messages(highlight, chatlog_index),
        }
    )

    beats = [
        {
            "order": i + 1,
            "beat": dim,
            "line": _line_for_span(seg_index, s, e),  # 有逐字稿則回填台詞，否則 None（待 AI 精修）
            "start_ms": s,
            "end_ms": e,
            "duration_ms": e - s,
        }
        for i, (dim, s, e) in enumerate(spans)
    ]

    return {
        "highlight_id": highlight["highlight_id"],
        "title": highlight.get("suggested_title"),
        "description": highlight.get("description"),
        "dimensions": dimensions,
        "beats": beats,
    }


def build_annotations(
    highlights: list[dict[str, Any]],
    chatlog: dict[str, Any] | None = None,
    *,
    project_id: str = "",
    params: dict[str, Any] | None = None,
    transcript: dict[str, Any] | None = None,
    annotation_version: str = "annotation-rule-1.0.0",
) -> dict[str, Any]:
    """回傳符合 annotations.v1 的 dict（只標註納入的高光）。

    ``project_id`` 由呼叫端提供（highlight 項本身不帶 project_id）；未給時退回 chatlog。
    ``transcript``（transcript.v1）給定時，依時間重疊回填 dimension.text / beat.line（台詞）。
    """
    p = params or {}
    ratios = {**DEFAULT_DIMENSION_RATIOS, **(p.get("dimension_ratios") or {})}
    if not project_id and chatlog:
        project_id = chatlog.get("project_id", "")

    chatlog_index: dict[str, dict[str, Any]] | None = None
    if chatlog:
        chatlog_index = {m["message_id"]: m for m in (chatlog.get("messages") or [])}
    seg_index = _transcript_index(transcript)

    annotations = [
        _annotate_one(h, chatlog_index, ratios, seg_index) for h in highlights if _is_included(h)
    ]

    doc = {
        "schema_version": "annotations.v1",
        "project_id": project_id,
        "annotation_version": annotation_version,
        "annotations": annotations,
        "created_at": _now_iso(),
    }
    validate_annotations(doc)
    return doc
