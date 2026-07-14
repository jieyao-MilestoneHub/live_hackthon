"""規則式高光偵測：transcript.v1 → highlights.v1。

MVP 以逐字稿訊號打分（情緒/驚呼關鍵詞、驚嘆號、疊字、語速），
合併相鄰高分段落、套用時長與 padding 規則，輸出排序後的高光片段。
多模態融合（彈幕、視覺）與 LLM 打分列為 Phase 2（見 ROADMAP #22）。

對應 issue #5（偵測邏輯）、#6（highlights.v1 契約）。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

DEFAULT_PARAMS: dict[str, Any] = {
    "max_clips": 5,
    "min_duration_sec": 15.0,
    "max_duration_sec": 60.0,
    "padding_before_sec": 2.0,
    "padding_after_sec": 3.0,
}

# 情緒 / 高光關鍵詞（可擴充；LLM 模式可改用模型打分）
EMOTION_KEYWORDS: tuple[str, ...] = (
    "太扯", "扯", "太神", "神操作", "厲害", "超級", "精彩", "誇張", "天啊", "哇",
    "起雞皮疙瘩", "衝", "太爽", "爽", "成功", "做到了", "感謝", "應援", "絕對",
    "沒想到", "快看", "來了", "最精彩", "神",
)

_EXCLAIM_RE = re.compile(r"[！!]")
_REPEAT_RE = re.compile(r"(.)\1{1,}")  # 疊字：啊啊啊、欸欸欸、來了來了

# 分數權重
_W_KEYWORD = 1.5
_W_EXCLAIM = 2.0
_W_REPEAT = 1.0
_W_RATE = 0.15  # 語速（字/秒）代理興奮度

SCORE_THRESHOLD = 0.45  # 正規化後的熱度門檻
MERGE_GAP_SEC = 12.0     # 相鄰熱段間距 <= 此值則合併


def _raw_score(seg: dict[str, Any]) -> float:
    text: str = seg.get("text") or ""
    dur = max(0.001, float(seg["end_sec"]) - float(seg["start_sec"]))
    keywords = sum(text.count(k) for k in EMOTION_KEYWORDS)
    exclaims = len(_EXCLAIM_RE.findall(text))
    repeats = len(_REPEAT_RE.findall(text))
    rate = len(text) / dur
    return _W_KEYWORD * keywords + _W_EXCLAIM * exclaims + _W_REPEAT * repeats + _W_RATE * rate


def _matched_keywords(texts: list[str], limit: int = 4) -> list[str]:
    found: list[str] = []
    joined = "".join(texts)
    for k in EMOTION_KEYWORDS:
        if k in joined and k not in found:
            found.append(k)
        if len(found) >= limit:
            break
    return found


def _clamp_duration(start: float, end: float, duration: float, params: dict[str, Any]) -> tuple[float, float]:
    lo, hi = params["min_duration_sec"], params["max_duration_sec"]
    start = max(0.0, start)
    end = min(duration, end)
    # 補足最短時長（對稱擴張，邊界夾擠）
    if end - start < lo:
        need = lo - (end - start)
        start = max(0.0, start - need / 2)
        end = min(duration, end + need / 2)
        if end - start < lo:  # 影片太短，盡量拉滿
            start = max(0.0, end - lo)
    # 限制最長時長
    if end - start > hi:
        end = start + hi
        if end > duration:
            end = duration
            start = max(0.0, end - hi)
    return round(start, 2), round(end, 2)


def detect_highlights(
    transcript: dict[str, Any],
    params: dict[str, Any] | None = None,
    analysis_version: str = "highlight-rule-1.0.0",
) -> dict[str, Any]:
    """回傳符合 highlights.v1 的 dict。"""
    p = {**DEFAULT_PARAMS, **(params or {})}
    segments = sorted(transcript.get("segments", []), key=lambda s: s["start_sec"])
    duration = float(transcript.get("duration_sec") or (segments[-1]["end_sec"] if segments else 0.0))

    raws = [_raw_score(s) for s in segments]
    top = max(raws) if raws else 0.0
    norms = [(r / top if top > 0 else 0.0) for r in raws]

    # 合併相鄰熱段
    clusters: list[dict[str, Any]] = []
    for seg, norm in zip(segments, norms):
        if norm < SCORE_THRESHOLD:
            continue
        if clusters and seg["start_sec"] - clusters[-1]["end"] <= MERGE_GAP_SEC:
            c = clusters[-1]
            c["end"] = max(c["end"], float(seg["end_sec"]))
            c["score"] = max(c["score"], norm)
            c["segs"].append(seg["segment_id"])
            c["texts"].append(seg.get("text") or "")
        else:
            clusters.append({
                "start": float(seg["start_sec"]),
                "end": float(seg["end_sec"]),
                "score": norm,
                "segs": [seg["segment_id"]],
                "texts": [seg.get("text") or ""],
            })

    highlights: list[dict[str, Any]] = []
    for c in clusters:
        start, end = _clamp_duration(
            c["start"] - p["padding_before_sec"],
            c["end"] + p["padding_after_sec"],
            duration,
            p,
        )
        if end <= start:
            continue
        kws = _matched_keywords(c["texts"])
        reason = "情緒/驚呼密集：" + "、".join(kws) if kws else "語速與情緒高峰"
        highlights.append({
            "clip_id": "",  # 排序後回填
            "start_sec": start,
            "end_sec": end,
            "score": round(c["score"], 3),
            "reason": reason,
            "title": (c["texts"][0][:12] if c["texts"] else "高光片段"),
            "source_segment_ids": c["segs"],
            "render_profile": "vertical-1080x1920",
        })

    highlights.sort(key=lambda h: h["score"], reverse=True)
    highlights = highlights[: p["max_clips"]]
    for i, h in enumerate(highlights, start=1):
        h["clip_id"] = f"clip_{i:03d}"

    return {
        "schema_version": "highlights.v1",
        "job_id": transcript.get("job_id", ""),
        "analysis_version": analysis_version,
        "parameters": {
            "max_clips": p["max_clips"],
            "min_duration_sec": p["min_duration_sec"],
            "max_duration_sec": p["max_duration_sec"],
            "padding_before_sec": p["padding_before_sec"],
            "padding_after_sec": p["padding_after_sec"],
        },
        "highlights": highlights,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
