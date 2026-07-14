"""規則式高光偵測：transcript.v1 → highlights.v1（M1 Project/毫秒版）。

MVP 以逐字稿訊號打分（情緒/驚呼關鍵詞、驚嘆號、疊字、語速），
合併相鄰高分段落、套用時長與 padding 規則，輸出排序後的高光片段。
時間一律毫秒（ms），輸出以 project_id / highlight_id 為鍵。

多模態融合（彈幕、視覺）與 LLM 打分列為 Phase 2；完整 Analysis Worker
（Bedrock、SQS）為 M2。對應 issue #5（偵測邏輯）、#6（highlights.v1 契約）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from analysis import emotion

DEFAULT_PARAMS: dict[str, Any] = {
    "max_clips": 5,
    "min_duration_ms": 15000,
    "max_duration_ms": 60000,
    "padding_before_ms": 2000,
    "padding_after_ms": 3000,
}

# 情緒詞彙、regex、權重已移至 analysis.emotion（與聊天 Level-1 overlay 共用）。
# 為相容既有 import，保留關鍵詞表別名。
EMOTION_KEYWORDS = emotion.EMOTION_KEYWORDS

SCORE_THRESHOLD = 0.45  # 正規化後的熱度門檻
MERGE_GAP_MS = 12000     # 相鄰熱段間距 <= 此值（毫秒）則合併


def _raw_score(seg: dict[str, Any]) -> float:
    text: str = seg.get("text") or ""
    dur_ms = max(1.0, float(seg["end_ms"]) - float(seg["start_ms"]))
    keywords = emotion.count_keywords(text)
    exclaims = emotion.count_exclaims(text)
    repeats = emotion.count_repeats(text)
    rate = len(text) / (dur_ms / 1000.0)  # 字/秒
    return (
        emotion.W_KEYWORD * keywords
        + emotion.W_EXCLAIM * exclaims
        + emotion.W_REPEAT * repeats
        + emotion.W_RATE * rate
    )


def _matched_keywords(texts: list[str], limit: int = 4) -> list[str]:
    return emotion.matched_keywords(texts, limit)


def _clamp_duration(start_ms: float, end_ms: float, duration_ms: float, params: dict[str, Any]) -> tuple[int, int]:
    lo, hi = params["min_duration_ms"], params["max_duration_ms"]
    start_ms = max(0.0, start_ms)
    end_ms = min(duration_ms, end_ms)
    # 補足最短時長（對稱擴張，邊界夾擠）
    if end_ms - start_ms < lo:
        need = lo - (end_ms - start_ms)
        start_ms = max(0.0, start_ms - need / 2)
        end_ms = min(duration_ms, end_ms + need / 2)
        if end_ms - start_ms < lo:  # 影片太短，盡量拉滿
            start_ms = max(0.0, end_ms - lo)
    # 限制最長時長
    if end_ms - start_ms > hi:
        end_ms = start_ms + hi
        if end_ms > duration_ms:
            end_ms = duration_ms
            start_ms = max(0.0, end_ms - hi)
    return int(round(start_ms)), int(round(end_ms))


def detect_highlights(
    transcript: dict[str, Any],
    params: dict[str, Any] | None = None,
    analysis_version: str = "highlight-rule-1.0.0",
) -> dict[str, Any]:
    """回傳符合 highlights.v1（Project/毫秒版）的 dict。"""
    p = {**DEFAULT_PARAMS, **(params or {})}
    segments = sorted(transcript.get("segments", []), key=lambda s: s["start_ms"])
    duration_ms = float(
        transcript.get("duration_ms") or (segments[-1]["end_ms"] if segments else 0.0)
    )

    raws = [_raw_score(s) for s in segments]
    top = max(raws) if raws else 0.0
    norms = [(r / top if top > 0 else 0.0) for r in raws]

    # 合併相鄰熱段
    clusters: list[dict[str, Any]] = []
    for seg, norm in zip(segments, norms):
        if norm < SCORE_THRESHOLD:
            continue
        if clusters and seg["start_ms"] - clusters[-1]["end"] <= MERGE_GAP_MS:
            c = clusters[-1]
            c["end"] = max(c["end"], float(seg["end_ms"]))
            c["score"] = max(c["score"], norm)
            c["segs"].append(seg["segment_id"])
            c["texts"].append(seg.get("text") or "")
        else:
            clusters.append({
                "start": float(seg["start_ms"]),
                "end": float(seg["end_ms"]),
                "score": norm,
                "segs": [seg["segment_id"]],
                "texts": [seg.get("text") or ""],
            })

    highlights: list[dict[str, Any]] = []
    for c in clusters:
        start_ms, end_ms = _clamp_duration(
            c["start"] - p["padding_before_ms"],
            c["end"] + p["padding_after_ms"],
            duration_ms,
            p,
        )
        if end_ms <= start_ms:
            continue
        kws = _matched_keywords(c["texts"])
        reason = "情緒/驚呼密集：" + "、".join(kws) if kws else "語速與情緒高峰"
        joined_text = "".join(c["texts"])
        highlights.append({
            "highlight_id": "",  # 排序後回填
            "start_ms": start_ms,
            "end_ms": end_ms,
            "score": round(c["score"], 3),
            "reason": reason,
            "transcript": joined_text,
            "suggested_title": (joined_text[:12] if joined_text else "高光片段"),
            "source_segment_ids": c["segs"],
            "selected": True,
            "locked": False,
        })

    highlights.sort(key=lambda h: h["score"], reverse=True)
    highlights = highlights[: p["max_clips"]]
    for i, h in enumerate(highlights, start=1):
        h["highlight_id"] = f"hl-{i:03d}"

    return {
        "schema_version": "highlights.v1",
        "project_id": transcript.get("project_id", ""),
        "source_duration_ms": int(duration_ms),
        "analysis_version": analysis_version,
        "parameters": {
            "max_clips": p["max_clips"],
            "min_duration_ms": p["min_duration_ms"],
            "max_duration_ms": p["max_duration_ms"],
            "padding_before_ms": p["padding_before_ms"],
            "padding_after_ms": p["padding_after_ms"],
        },
        "highlights": highlights,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
