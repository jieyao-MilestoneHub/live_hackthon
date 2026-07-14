"""特效計畫：timeline.v1 + effect_seed → effects.v1（Creative Planning 第二步）。

純函式且 **deterministic**：以 `random.Random(effect_seed)` 產生特效,同一個
`effect_seed` 永遠得到相同輸出——重試同一個 Render 不會重新隨機,確保影片可重現
（demand.md §十二）。每個 clip 起點放一個區間特效(zoom_in/pan/shake),clip 之間
的邊界放一個點狀轉場(flash_transition)。
"""
from __future__ import annotations

import random
from typing import Any

from analysis.validate import validate_effects

RANGED_EFFECT_TYPES = ("zoom_in", "pan", "shake")
_RANGED_MAX_MS = 1600
_FLASH_MS = 240


def plan_effects(
    timeline: dict[str, Any],
    effect_seed: int,
    project_id: str,
    render_id: str,
) -> dict[str, Any]:
    """回傳符合 effects.v1 的 dict（決定性:同 seed 同輸出）。"""
    rng = random.Random(effect_seed)
    clips = sorted(timeline.get("clips", []), key=lambda c: c["timeline_order"])

    effects: list[dict[str, Any]] = []
    for i, clip in enumerate(clips):
        start = int(clip["timeline_start_ms"])
        end = int(clip["timeline_end_ms"])
        # Ranged effect over the clip's opening (never past the clip end).
        effects.append({
            "type": rng.choice(RANGED_EFFECT_TYPES),
            "start_ms": start,
            "end_ms": min(start + _RANGED_MAX_MS, end),
            "strength": round(rng.uniform(0.03, 0.12), 3),
        })
        # Point transition at each internal boundary.
        if i > 0:
            effects.append({"type": "flash_transition", "at_ms": start, "duration_ms": _FLASH_MS})

    plan = {
        "schema_version": "effects.v1",
        "effect_seed": int(effect_seed),
        "project_id": project_id,
        "render_id": render_id,
        "effects": effects,
    }
    validate_effects(plan)
    return plan
