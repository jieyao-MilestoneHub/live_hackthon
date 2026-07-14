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
DEFAULT_DIMENSION_RATIOS: dict[str, float] = {
    "setup": 0.30,
    "reaction_start": 0.25,
    "reaction_turn": 0.25,
    "punchline": 0.20,
}
CHAT_HIGHLIGHT_LIMIT = 3


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_included(h: dict[str, Any]) -> bool:
    """納入標註的高光：未被排除、未取消選取。"""
    return h.get("status") != "excluded" and h.get("selected") is not False


def _split_spans(start_ms: int, end_ms: int, ratios: dict[str, float]) -> list[tuple[str, int, int]]:
    """把 [start,end] 依比例切成連續 4 段（整數 ms、不重疊、最後一段對齊 end）。"""
    start_ms, end_ms = int(start_ms), int(end_ms)
    total = max(0, end_ms - start_ms)
    spans: list[tuple[str, int, int]] = []
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
) -> dict[str, Any]:
    start_ms, end_ms = int(highlight["start_ms"]), int(highlight["end_ms"])
    spans = _split_spans(start_ms, end_ms, ratios)

    dimensions: list[dict[str, Any]] = [
        {"dimension": dim, "start_ms": s, "end_ms": e, "text": None} for dim, s, e in spans
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
            "line": None,  # 台詞待 AI 精修（逐字稿）填
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
    annotation_version: str = "annotation-rule-1.0.0",
) -> dict[str, Any]:
    """回傳符合 annotations.v1 的 dict（只標註納入的高光）。

    ``project_id`` 由呼叫端提供（highlight 項本身不帶 project_id）；未給時退回 chatlog。
    """
    p = params or {}
    ratios = {**DEFAULT_DIMENSION_RATIOS, **(p.get("dimension_ratios") or {})}
    if not project_id and chatlog:
        project_id = chatlog.get("project_id", "")

    chatlog_index: dict[str, dict[str, Any]] | None = None
    if chatlog:
        chatlog_index = {m["message_id"]: m for m in (chatlog.get("messages") or [])}

    annotations = [
        _annotate_one(h, chatlog_index, ratios) for h in highlights if _is_included(h)
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
