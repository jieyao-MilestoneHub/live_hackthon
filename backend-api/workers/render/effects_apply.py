"""特效套用：effects.v1 → per-clip FFmpeg vf 片段（透過 registry 的 EffectStrategy）。

encoder 逐 clip（Phase 1，畫面已正規化為目標 WxH、時間 clip-local 起於 0）呼叫
``clip_effect_fragments`` 取該 clip 適用的 filter 片段。哪個 type 產生什麼片段由
``creative/effects_registry.py`` 的策略決定（OCP：新增特效不改此檔）。

特效以 timeline 輸出毫秒定位；每個特效歸屬「其錨點落入的 clip」（ranged 用 start_ms、
point 用 at_ms）。gated：由 encoder 依 ``RENDER_APPLY_EFFECTS`` 決定是否呼叫。
"""
from __future__ import annotations

from typing import Any

from creative.effects_registry import EffectContext, get_effect


def clip_effect_fragments(
    clip: dict[str, Any],
    effects: list[dict[str, Any]] | None,
    ctx: EffectContext,
) -> list[str]:
    """回傳此 clip 適用的非空 vf 片段（依 effects 順序）。"""
    tl_s = int(clip["timeline_start_ms"])
    tl_e = int(clip["timeline_end_ms"])
    frags: list[str] = []
    for effect in effects or []:
        anchor = effect.get("start_ms", effect.get("at_ms"))
        if anchor is None:
            continue
        if not (tl_s <= int(anchor) < tl_e):
            continue  # 特效不屬於此 clip
        strategy = get_effect(effect.get("type", ""))
        if strategy is None:
            continue
        # ranged 特效換算成 clip-local 毫秒（fragment 的時間窗運算式假設 t=0 為 clip 起點）；
        # end_ms 夾在 clip 自己的長度內，避免時間窗跨到下一個 clip 的畫面範圍。
        local_effect = effect
        if "start_ms" in effect and "end_ms" in effect:
            local_effect = {
                **effect,
                "start_ms": int(effect["start_ms"]) - tl_s,
                "end_ms": min(int(effect["end_ms"]), tl_e) - tl_s,
            }
        fragment = strategy.fragment(local_effect, ctx)
        if fragment:
            frags.append(fragment)
    return frags
