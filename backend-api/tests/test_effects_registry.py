"""特效 registry / strategy（OCP）：註冊、intensity、決定性、fragment 套用。"""
from __future__ import annotations

from analysis.validate import load_sample, validate_effects
from creative import plan_effects
from creative.effects_registry import (
    EFFECT_REGISTRY,
    BaseEffect,
    EffectContext,
    get_effect,
    point_types,
    ranged_types,
    register,
)
from workers.render.effects_apply import clip_effect_fragments


def _timeline() -> dict:
    return load_sample("timeline.sample.json")


def test_builtin_types_registered() -> None:
    for t in ("zoom_in", "pan", "shake", "flash_transition", "crossfade", "fade", "cut"):
        assert get_effect(t) is not None
    assert set(ranged_types()) >= {"zoom_in", "pan", "shake"}
    assert "flash_transition" in point_types()


def test_register_is_open_for_extension() -> None:
    @register
    class _Sparkle(BaseEffect):
        type = "sparkle_test"
        kind = "ranged"

        def fragment(self, effect, ctx):  # noqa: ANN001, ARG002
            return "eq=saturation=1.4"

    try:
        assert get_effect("sparkle_test") is not None
        assert "sparkle_test" in ranged_types()
    finally:
        EFFECT_REGISTRY.pop("sparkle_test", None)  # 清掉測試污染


def test_intensity_scales_strength() -> None:
    tl = _timeline()
    low = plan_effects(tl, 42, "p", "r", settings={"intensity": "low"})
    high = plan_effects(tl, 42, "p", "r", settings={"intensity": "high"})
    validate_effects(low)
    validate_effects(high)
    low_str = [e["strength"] for e in low["effects"] if "strength" in e]
    high_str = [e["strength"] for e in high["effects"] if "strength" in e]
    assert low_str and high_str
    assert max(low_str) <= 0.06 + 1e-9
    assert min(high_str) >= 0.06 - 1e-9


def test_deterministic_and_disabled() -> None:
    tl = _timeline()
    assert plan_effects(tl, 7, "p", "r") == plan_effects(tl, 7, "p", "r")
    off = plan_effects(tl, 7, "p", "r", settings={"enabled": False})
    validate_effects(off)
    assert off["effects"] == []


def test_zoom_and_flash_fragments() -> None:
    ctx = EffectContext(1080, 1920, 30)
    zoom = get_effect("zoom_in").fragment({"type": "zoom_in", "strength": 0.08}, ctx)
    flash = get_effect("flash_transition").fragment({"type": "flash_transition", "duration_ms": 240}, ctx)
    assert "zoompan" in zoom and "1080x1920" in zoom
    assert "fade=t=in" in flash and "white" in flash
    # 尚未實作視覺的策略回傳空片段（安全，不弄壞編碼）。
    assert get_effect("pan").fragment({"type": "pan"}, ctx) == ""


def test_apply_selects_effects_within_clip() -> None:
    ctx = EffectContext(1080, 1920, 30)
    clip = {"timeline_order": 1, "highlight_id": "h", "source_start_ms": 0, "source_end_ms": 5000,
            "timeline_start_ms": 0, "timeline_end_ms": 5000}
    effects = [
        {"type": "zoom_in", "start_ms": 0, "end_ms": 1600, "strength": 0.08},
        {"type": "flash_transition", "at_ms": 0, "duration_ms": 240},
        {"type": "zoom_in", "start_ms": 9000, "end_ms": 9600, "strength": 0.08},  # 屬別的 clip
    ]
    frags = clip_effect_fragments(clip, effects, ctx)
    assert any("zoompan" in f for f in frags)
    assert any("white" in f for f in frags)
    assert len(frags) == 2  # clip 外的 zoom 被排除
