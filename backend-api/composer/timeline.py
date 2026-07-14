"""Duration Composer：highlights.v1 → timeline.v1（剪輯決策表 / EDL）。

純函式、deterministic：依 demand.md §九 規則,從高光候選挑選並裁切片段,組出總長
逼近使用者目標秒數(≤60 秒、±0.5 秒)的 timeline。只做排序/合併/秒數最佳化,不碰
FFmpeg(見 ROADMAP「不要三次編碼」)。輸出以 highlights.v1 的 highlight_id 為外鍵。

限制:highlights.v1 不含逐句時間(只有 source_segment_ids),故「不在句中裁切」目前
以毫秒裁切近似;待輸入帶 segment 邊界時再精修。對應 issue #6（highlights↔timeline）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from analysis.validate import validate_timeline

MAX_DURATION_MS = 60_000       # 最終短片長度上限（demand.md §九：不得超過 60 秒）
MIN_CLIP_MS = 2_000            # 單一片段最短長度，避免產生過碎的裁切
DEFAULT_ASPECT_RATIO = "9:16"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _overlaps(a_start: int, a_end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(not (a_end <= s or a_start >= e) for s, e in ranges)


def compose_timeline(
    project_id: str,
    highlights: list[dict[str, Any]],
    target_duration_ms: int,
    locked_ids: Iterable[str] = (),
    excluded_ids: Iterable[str] = (),
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    version: int = 1,
    created_by: str = "composer",
    created_at: str | None = None,
) -> dict[str, Any]:
    """回傳符合 timeline.v1 的 dict（clips 依時間順序、actual 逼近 target）。"""
    locked = set(locked_ids)
    excluded = set(excluded_ids)

    # 1. 排除使用者剔除的片段。
    candidates = [h for h in highlights if h["highlight_id"] not in excluded]

    # 2. 鎖定片段優先，其餘依分數（高→低）；穩定排序讓輸出可重現。
    def is_locked(h: dict[str, Any]) -> bool:
        return h["highlight_id"] in locked or bool(h.get("locked"))

    locked_first = sorted(
        (h for h in candidates if is_locked(h)),
        key=lambda h: h.get("score", 0.0),
        reverse=True,
    )
    rest = sorted(
        (h for h in candidates if not is_locked(h)),
        key=lambda h: h.get("score", 0.0),
        reverse=True,
    )
    ordered = locked_first + rest

    # 3. 貪婪填滿 min(target, 60s)，最後一段裁到剛好命中 → actual 落在 ±0.5s。
    ceiling = min(int(target_duration_ms), MAX_DURATION_MS)
    remaining = ceiling
    selected: list[dict[str, int]] = []
    used: list[tuple[int, int]] = []

    for h in ordered:
        if remaining < MIN_CLIP_MS:
            break
        start = int(h["start_ms"])
        end = int(h["end_ms"])
        length = end - start
        if length < MIN_CLIP_MS:
            continue
        # 語意重複的 MVP 啟發式：跳過與已選 source 區間重疊者。
        if _overlaps(start, end, used):
            continue
        take = min(length, remaining)
        selected.append({"highlight_id": h["highlight_id"], "source_start_ms": start, "source_end_ms": start + take})
        used.append((start, start + take))
        remaining -= take

    # 4. 依 source 時間排列（自然敘事），指派 order 與連續的 timeline 位置。
    selected.sort(key=lambda c: c["source_start_ms"])
    clips: list[dict[str, Any]] = []
    cursor = 0
    for order, sel in enumerate(selected, start=1):
        dur = sel["source_end_ms"] - sel["source_start_ms"]
        clips.append({
            "timeline_order": order,
            "highlight_id": sel["highlight_id"],
            "source_start_ms": sel["source_start_ms"],
            "source_end_ms": sel["source_end_ms"],
            "timeline_start_ms": cursor,
            "timeline_end_ms": cursor + dur,
        })
        cursor += dur

    timeline = {
        "schema_version": "timeline.v1",
        "project_id": project_id,
        "version": int(version),
        "target_duration_ms": int(target_duration_ms),
        "actual_duration_ms": cursor,
        "aspect_ratio": aspect_ratio,
        "created_by": created_by,
        "created_at": created_at or _now_iso(),
        "clips": clips,
    }
    validate_timeline(timeline)  # 自我驗證：保證輸出永遠符合契約
    return timeline
