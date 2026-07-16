"""聊天優先高光偵測：chatlog.v1 → highlights.v1（分析流程階段三 收斂輸出）。

串接 volume（每分鐘熱區）→ candidates（情緒排序取前 N）→ sync（epoch→影片相對毫秒），
產出**與逐字稿路徑完全相同形狀**的 highlights.v1，讓下游 composer / timeline 零改動。

Slice 1 只到「候選（candidate）」：start_ms/end_ms 即換算+padding 後的窗，尚無人工
chat-lag 校正（correction 在 Slice 2 疊上）。chat_window 保留未 padding 的原始偵測窗，
供可解釋性。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from analysis.chatlog import candidates, sync, volume

DEFAULT_CHAT_PARAMS: dict[str, Any] = {
    "max_clips": 5,
    "min_duration_ms": 15000,
    "max_duration_ms": 60000,
    "padding_before_ms": 2000,
    "padding_after_ms": 3000,
    "hot_zone_sigma": 1.0,
    "merge_gap_minutes": 1,
}


def _clamp(start_ms: float, end_ms: float, duration_ms: float, p: dict[str, Any]) -> tuple[int, int]:
    """對稱補足最短時長、夾擠邊界、限制最長時長（與逐字稿路徑一致的規則）。"""
    lo, hi = p["min_duration_ms"], p["max_duration_ms"]
    start_ms = max(0.0, start_ms)
    end_ms = min(duration_ms, end_ms)
    if end_ms - start_ms < lo:
        need = lo - (end_ms - start_ms)
        start_ms = max(0.0, start_ms - need / 2)
        end_ms = min(duration_ms, end_ms + need / 2)
        if end_ms - start_ms < lo:
            start_ms = max(0.0, end_ms - lo)
    if end_ms - start_ms > hi:
        end_ms = start_ms + hi
        if end_ms > duration_ms:
            end_ms = duration_ms
            start_ms = max(0.0, end_ms - hi)
    return int(round(start_ms)), int(round(end_ms))


def _suggested_title(texts: list[str]) -> str:
    for t in texts:
        t = (t or "").strip()
        if t:
            return t[:16]
    return "高光片段"


def detect_highlights_from_chat(
    chatlog: dict[str, Any],
    video_start_epoch_ms: int,
    source_duration_ms: int,
    params: dict[str, Any] | None = None,
    analysis_version: str = "highlight-chat-1.0.0",
) -> dict[str, Any]:
    """回傳符合 highlights.v1 的 dict（signal=chat_volume）。"""
    p = {**DEFAULT_CHAT_PARAMS, **(params or {})}
    duration_ms = int(source_duration_ms)

    # sliding_hot_windows()：固定日曆分鐘桶會把剛好卡在分鐘交界的一波連續反應
    # 切成兩半，兩邊都可能各自低於門檻而漏掉整波；滑動視窗（每 5 秒回看 60 秒）
    # 不受分鐘邊界影響。回傳形狀與 hot_windows() 相容，此處為直接替換。
    vol = volume.sliding_hot_windows(
        chatlog,
        sigma=float(p["hot_zone_sigma"]),
        merge_gap_ms=int(p["merge_gap_minutes"]) * 60_000,
    )
    cands = candidates.build_candidates(chatlog, vol, p)

    highlights: list[dict[str, Any]] = []
    for c in cands:
        cs = sync.video_ms(c["chat_start_epoch_ms"], video_start_epoch_ms, duration_ms)
        ce = sync.video_ms(c["chat_end_epoch_ms"], video_start_epoch_ms, duration_ms)
        start_ms, end_ms = _clamp(cs - p["padding_before_ms"], ce + p["padding_after_ms"], duration_ms, p)
        if end_ms <= start_ms:
            continue
        highlights.append(
            {
                "highlight_id": "",  # 排序後回填
                "start_ms": start_ms,
                "end_ms": end_ms,
                "score": c["score"],
                "signal": "chat_volume",
                "status": "candidate",
                "chat_window": {"start_ms": cs, "end_ms": ce},
                "emotion": c["emotion"],
                "detection": c["detection"],
                "reason": c["reason"],
                "suggested_title": _suggested_title(c["texts"]),
                "source_segment_ids": [],  # 逐字稿在 Slice 2 才產生
                "selected": True,
                "locked": False,
                "provenance": {
                    "detected_from": "chatlog.v1",
                    "pipeline": "chat_volume->emotion",
                    "chat_message_ids": c["message_ids"],
                },
            }
        )

    # candidates 已依 score 排序；回填 highlight_id。
    for i, h in enumerate(highlights, start=1):
        h["highlight_id"] = f"hl-{i:03d}"

    return {
        "schema_version": "highlights.v1",
        "project_id": chatlog.get("project_id", ""),
        "source_duration_ms": duration_ms,
        "analysis_version": analysis_version,
        "parameters": {
            "max_clips": p["max_clips"],
            "min_duration_ms": p["min_duration_ms"],
            "max_duration_ms": p["max_duration_ms"],
            "padding_before_ms": p["padding_before_ms"],
            "padding_after_ms": p["padding_after_ms"],
            "hot_zone_sigma": p["hot_zone_sigma"],
            "merge_gap_minutes": p["merge_gap_minutes"],
            "spam_ruleset_version": chatlog.get("filter", {}).get("spam_ruleset_version"),
        },
        "highlights": highlights,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
