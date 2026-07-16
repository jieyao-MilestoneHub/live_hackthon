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
    zoom = get_effect("zoom_in").fragment({"type": "zoom_in", "start_ms": 0, "end_ms": 1600, "strength": 0.08}, ctx)
    flash = get_effect("flash_transition").fragment({"type": "flash_transition", "duration_ms": 240}, ctx)
    assert "crop=" in zoom and "1080:1920" in zoom
    assert "between(t," in zoom  # 時間窗閘門化，非整段 clip 持續生效
    assert "fade=t=in" in flash and "white" in flash


def test_pan_and_shake_are_implemented_and_gated() -> None:
    """pan/shake 曾是 TODO stub（回傳空字串）；現在要真的有內容，且都有時間窗閘門。"""
    ctx = EffectContext(1080, 1920, 30)
    pan = get_effect("pan").fragment({"type": "pan", "start_ms": 0, "end_ms": 1600, "strength": 0.08}, ctx)
    shake = get_effect("shake").fragment({"type": "shake", "start_ms": 0, "end_ms": 1600, "strength": 0.08}, ctx)
    assert pan and shake  # 不再是空字串
    assert "between(t," in pan and "crop=" in pan
    assert "between(t," in shake and "sin(" in shake  # 震動用正弦波抖動


def test_pan_direction_is_deterministic() -> None:
    """同一個 (seed, start_ms) 恆得同方向；不同 seed 或不同 start_ms 可能不同方向。"""
    ctx_a = EffectContext(1080, 1920, 30, seed=7)
    ctx_a2 = EffectContext(1080, 1920, 30, seed=7)
    effect = {"type": "pan", "start_ms": 1000, "end_ms": 2000, "strength": 0.08}
    assert get_effect("pan").fragment(effect, ctx_a) == get_effect("pan").fragment(effect, ctx_a2)

    # 掃過幾個 seed，至少要出現一次以上不同方向，確認不是永遠硬編同一個方向。
    variants = {
        get_effect("pan").fragment(effect, EffectContext(1080, 1920, 30, seed=s))
        for s in range(10)
    }
    assert len(variants) > 1


def test_ranged_effects_are_identity_outside_their_window() -> None:
    """時間窗外要還原成 identity（gate 表達式在效果之外恆為 1/0），不能整段 clip 都生效。"""
    ctx = EffectContext(1080, 1920, 30)
    for etype in ("zoom_in", "pan", "shake"):
        frag = get_effect(etype).fragment(
            {"type": etype, "start_ms": 2000, "end_ms": 3000, "strength": 0.08}, ctx
        )
        assert "between(t,2.000,3.000)" in frag


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
    assert any("crop=" in f for f in frags)
    assert any("white" in f for f in frags)
    assert len(frags) == 2  # clip 外的 zoom 被排除


def test_apply_converts_timeline_ms_to_clip_local() -> None:
    """clip 不是從 timeline 0 開始時，effect 的時間窗要換算成 clip-local，不能直接用 timeline 毫秒。"""
    ctx = EffectContext(1080, 1920, 30)
    clip = {"timeline_order": 2, "highlight_id": "h2", "source_start_ms": 0, "source_end_ms": 5000,
            "timeline_start_ms": 8000, "timeline_end_ms": 13000}
    effects = [{"type": "zoom_in", "start_ms": 8000, "end_ms": 9600, "strength": 0.08}]
    frags = clip_effect_fragments(clip, effects, ctx)
    assert len(frags) == 1
    # 換算後應該是 clip-local 0.000~1.600 秒，不是 timeline 的 8.000~9.600 秒。
    assert "between(t,0.000,1.600)" in frags[0]


def test_apply_clamps_effect_end_to_clip_boundary() -> None:
    """effect 的 end_ms 超出 clip 範圍時要被夾住，不能讓時間窗跨進下一個 clip 的畫面。"""
    ctx = EffectContext(1080, 1920, 30)
    clip = {"timeline_order": 1, "highlight_id": "h", "source_start_ms": 0, "source_end_ms": 3000,
            "timeline_start_ms": 0, "timeline_end_ms": 3000}
    effects = [{"type": "zoom_in", "start_ms": 0, "end_ms": 9999, "strength": 0.08}]
    frags = clip_effect_fragments(clip, effects, ctx)
    assert "between(t,0.000,3.000)" in frags[0]  # 夾在 clip 自己的 3 秒長度內
