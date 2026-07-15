"""Duration Composer：highlights.v1 (+annotations.v1) → timeline.v1（剪輯決策表 / EDL）。

外殼＋時間軸定位＋契約驗證留在此；「選哪些段、怎麼裁」委派給可替換的 ``ClipPlanner``
策略（``composer/strategies.py``）：有 annotations 的起承轉合 ``beats`` → ``NarrativeBeatPlanner``
（保留埋梗+爆梗、超長捨中段、**永不砍 punchline**）；否則 ``ScoreGreedyPlanner``（分數貪婪、
前段裁切保爆點）。降卡點的接點轉場由 encoder 依 ``composer/transitions.py`` 套用。

deterministic：clips 依 source 時間排（自然敘事）、actual 逼近 target（≤60 秒、±0.5 秒）。
只做排序/裁切/秒數最佳化，不碰 FFmpeg。輸出以 highlights.v1 的 highlight_id 為外鍵。對應 issue #6。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from analysis.validate import validate_timeline
from composer.strategies import (
    MAX_DURATION_MS,
    MIN_CLIP_MS,
    ClipPlanner,
    SelectedClip,
    default_planner,
)

DEFAULT_ASPECT_RATIO = "9:16"

__all__ = ["compose_timeline", "MAX_DURATION_MS", "MIN_CLIP_MS", "DEFAULT_ASPECT_RATIO"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    *,
    annotations: dict[str, Any] | None = None,
    planner: ClipPlanner | None = None,
) -> dict[str, Any]:
    """回傳符合 timeline.v1 的 dict（clips 依時間順序、actual 逼近 target）。

    ``annotations``（annotations.v1）有 beats 時預設走起承轉合拼接；``planner`` 可顯式覆寫策略。
    """
    chosen = planner or default_planner(annotations)
    selected: list[SelectedClip] = chosen.plan(
        highlights,
        annotations,
        target_duration_ms,
        locked_ids=locked_ids,
        excluded_ids=excluded_ids,
    )

    # 依 source 時間排列（自然敘事），指派 order 與連續的 timeline 位置。
    selected = sorted(selected, key=lambda c: c.source_start_ms)
    clips: list[dict[str, Any]] = []
    cursor = 0
    for order, sel in enumerate(selected, start=1):
        dur = sel.source_end_ms - sel.source_start_ms
        clips.append({
            "timeline_order": order,
            "highlight_id": sel.highlight_id,
            "source_start_ms": sel.source_start_ms,
            "source_end_ms": sel.source_end_ms,
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
