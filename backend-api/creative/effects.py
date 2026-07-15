"""特效計畫：timeline.v1 + effect_seed → effects.v1（Creative Planning 第二步）。

薄殼 orchestrator：依 ``effect_settings.intensity``（low/med/high → 強度）與
``random.Random(effect_seed)`` 向 ``creative/effects_registry.py`` 的 registry 要效果——
每個 clip 起點放一個區間特效（zoom_in/pan/shake），clip 之間邊界放一個點狀轉場
（flash_transition）。新增特效類型只要 ``@register`` 一個策略，本檔不需修改（OCP）。

**deterministic**：同一個 ``effect_seed`` 永遠得到相同輸出——重試同一個 Render 不會重新隨機
（demand.md §十二）。預設 intensity=medium 時輸出與重構前逐位元相同（相容既有測試）。
"""
from __future__ import annotations

import random
from typing import Any

from analysis.validate import validate_effects
from creative.effects_registry import EFFECT_REGISTRY, ranged_types

# 既有相容常數（等同 registry 的 ranged 類型；供既有 import）。
RANGED_EFFECT_TYPES = ("zoom_in", "pan", "shake")
_RANGED_MAX_MS = 1600
_FLASH_MS = 240

# intensity → 區間特效強度範圍（medium 維持重構前的 0.03–0.12）。
INTENSITY_STRENGTH: dict[str, tuple[float, float]] = {
    "low": (0.02, 0.06),
    "medium": (0.03, 0.12),
    "high": (0.06, 0.18),
}


def plan_effects(
    timeline: dict[str, Any],
    effect_seed: int,
    project_id: str,
    render_id: str,
    *,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """回傳符合 effects.v1 的 dict（決定性:同 seed 同輸出）。

    ``settings`` = timeline.effect_settings（``enabled`` 關則不出特效；``intensity`` 調強度）。
    """
    settings = settings or {}
    if settings.get("enabled") is False:
        plan = {"schema_version": "effects.v1", "effect_seed": int(effect_seed),
                "project_id": project_id, "render_id": render_id, "effects": []}
        validate_effects(plan)
        return plan

    lo, hi = INTENSITY_STRENGTH.get(
        (settings.get("intensity") or "medium").strip().lower(), INTENSITY_STRENGTH["medium"]
    )
    ranged = ranged_types() or RANGED_EFFECT_TYPES
    rng = random.Random(effect_seed)
    clips = sorted(timeline.get("clips", []), key=lambda c: c["timeline_order"])

    effects: list[dict[str, Any]] = []
    for i, clip in enumerate(clips):
        start = int(clip["timeline_start_ms"])
        end = int(clip["timeline_end_ms"])
        etype = rng.choice(ranged)
        strength = rng.uniform(lo, hi)
        # Ranged effect over the clip's opening (never past the clip end).
        effects.append(EFFECT_REGISTRY[etype].make_ranged(start, min(start + _RANGED_MAX_MS, end), strength))
        # Point transition at each internal boundary（降卡點的視覺閃白）。
        if i > 0:
            effects.append(EFFECT_REGISTRY["flash_transition"].make_point(start, _FLASH_MS))

    plan = {
        "schema_version": "effects.v1",
        "effect_seed": int(effect_seed),
        "project_id": project_id,
        "render_id": render_id,
        "effects": effects,
    }
    validate_effects(plan)
    return plan
