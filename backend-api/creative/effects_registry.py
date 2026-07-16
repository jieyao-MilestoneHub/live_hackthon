"""特效策略註冊表：可擴充的 EffectStrategy（SOLID 的 OCP/DIP）。

一個特效類型 = 一個策略，知道 (a) 如何被**計畫**（產出 effects.v1 的 dict）與
(b) 如何被**套用**（產出 FFmpeg filter 片段給 encoder）。新增特效只要 ``@register`` 一個
策略類別，`plan_effects`（計畫）與 `apply_effects`（渲染）都不需修改——真正的開放封閉。

形狀對應 effects.v1 的 oneOf：
  * ranged（區間，如 zoom_in / pan / shake）：``{type, start_ms, end_ms, strength}``
  * point（點狀轉場，如 flash_transition / crossfade / fade / cut）：``{type, at_ms, duration_ms}``

fragment 是 clip-local（套在每個 clip 正規化後的 -vf 鏈上，畫面已是目標 WxH，t=0 為該 clip
起點）；呼叫端（``workers/render/effects_apply.py``）負責把 effect 的 timeline 毫秒換算成
clip-local 毫秒後才呼叫這裡的 ``fragment``。ranged 特效一律用 ``between(t,ls,le)`` 時間窗
+ ``if(gate,...,1)`` 幾何運算式閘門化：窗內生效、窗外還原成 identity（不影響前後畫面）。
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class EffectContext:
    """套用特效時的畫面情境（供 fragment 產生正確參數）。

    ``seed``：跟 effect_seed 同一份，供需要決定性隨機選擇的策略使用（如 Pan 的方向）；
    預設 0，向後相容既有只給 width/height/fps 的呼叫。
    """

    width: int
    height: int
    fps: int
    seed: int = 0


@runtime_checkable
class EffectStrategy(Protocol):
    """特效策略 Port。``kind`` ∈ {"ranged","point"}；``type`` 為契約 type 字串。"""

    type: str
    kind: str

    def fragment(self, effect: dict[str, Any], ctx: EffectContext) -> str:
        """回傳 FFmpeg filter 片段（可為空字串＝此特效不改畫面/交由他處處理）。"""
        ...


class BaseEffect:
    """策略基底：提供 make_* 建 effects.v1 dict；子類覆寫 ``fragment``。"""

    type: str = ""
    kind: str = "ranged"
    default_strength: float = 0.08
    default_duration_ms: int = 240

    def make_ranged(self, start_ms: int, end_ms: int, strength: float) -> dict[str, Any]:
        return {
            "type": self.type,
            "start_ms": int(start_ms),
            "end_ms": int(end_ms),
            "strength": round(float(strength), 3),
        }

    def make_point(self, at_ms: int, duration_ms: int) -> dict[str, Any]:
        return {"type": self.type, "at_ms": int(at_ms), "duration_ms": int(duration_ms)}

    def fragment(self, effect: dict[str, Any], ctx: EffectContext) -> str:  # noqa: ARG002
        return ""


EFFECT_REGISTRY: dict[str, BaseEffect] = {}


def register(cls: type[BaseEffect]) -> type[BaseEffect]:
    """類別裝飾器：實例化並登記到 ``EFFECT_REGISTRY``（依 ``.type``）。"""
    inst = cls()
    if not inst.type:
        raise ValueError(f"{cls.__name__} must set a non-empty `type`")
    if inst.kind not in ("ranged", "point"):
        raise ValueError(f"{cls.__name__}.kind must be 'ranged' or 'point'")
    EFFECT_REGISTRY[inst.type] = inst
    return cls


def get_effect(effect_type: str) -> BaseEffect | None:
    return EFFECT_REGISTRY.get(effect_type)


def ranged_types() -> tuple[str, ...]:
    return tuple(t for t, e in EFFECT_REGISTRY.items() if e.kind == "ranged")


def point_types() -> tuple[str, ...]:
    return tuple(t for t, e in EFFECT_REGISTRY.items() if e.kind == "point")


# --- 既有既定特效（保留原 type 名，向後相容）------------------------------------

def _ranged_window(effect: dict[str, Any]) -> tuple[float, float, float]:
    """clip-local ranged effect 的 (ls, le, td) 秒數，td 為時長（下限 0.05 秒避免除零）。"""
    start_ms = int(effect.get("start_ms", 0))
    end_ms = max(start_ms, int(effect.get("end_ms", start_ms)))
    ls, le = start_ms / 1000.0, end_ms / 1000.0
    return ls, le, max(0.05, le - ls)


def _clamp_strength(value: Any, lo: float = 0.02, hi: float = 0.3) -> float:
    try:
        s = float(value)
    except (TypeError, ValueError):
        s = BaseEffect.default_strength
    return max(lo, min(s, hi))


def _pan_direction(seed: int, start_ms: int) -> str:
    """決定性橫移方向：純函式，同 (seed, start_ms) 恆得同方向。"""
    return random.Random(f"{int(seed)}:{int(start_ms)}").choice(("lr", "rl", "tb", "bt"))


@register
class ZoomIn(BaseEffect):
    """緩慢推近（Ken Burns）。clip-local 時間窗內從 1 放大到 1+strength，窗外還原 identity。"""

    type = "zoom_in"
    kind = "ranged"

    def fragment(self, effect: dict[str, Any], ctx: EffectContext) -> str:  # noqa: ARG002
        ls, le, td = _ranged_window(effect)
        s = _clamp_strength(effect.get("strength", self.default_strength), 0.0, 0.5)
        gate = f"between(t,{ls:.3f},{le:.3f})"
        ramp = f"min((t-{ls:.3f})/{td:.3f},1)"
        z = f"if({gate},1+{s:.4f}*{ramp},1)"
        return f"crop=w='iw/({z})':h='ih/({z})':x='(iw-ow)/2':y='(ih-oh)/2',scale={ctx.width}:{ctx.height}"


@register
class Pan(BaseEffect):
    """橫移運鏡：clip-local 時間窗內從一側裁切位移到另一側，窗外還原 identity。

    需先用 strength 放大取得裁切餘裕（否則平移時邊緣會露出畫面外，跟 zoom_in 共用同一招）。
    方向由 (effect_seed, 該效果的 start_ms) 決定性決定，四選一（左右/右左/上下/下上）。
    """

    type = "pan"
    kind = "ranged"

    def fragment(self, effect: dict[str, Any], ctx: EffectContext) -> str:
        ls, le, td = _ranged_window(effect)
        s = _clamp_strength(effect.get("strength", self.default_strength))
        gate = f"between(t,{ls:.3f},{le:.3f})"
        ramp = f"min((t-{ls:.3f})/{td:.3f},1)"
        z = f"if({gate},1+{s:.4f},1)"
        direction = _pan_direction(ctx.seed, int(effect.get("start_ms", 0)))
        if direction == "lr":
            x, y = f"(iw-ow)*{ramp}", "(ih-oh)/2"
        elif direction == "rl":
            x, y = f"(iw-ow)*(1-{ramp})", "(ih-oh)/2"
        elif direction == "tb":
            x, y = "(iw-ow)/2", f"(ih-oh)*{ramp}"
        else:  # bt
            x, y = "(iw-ow)/2", f"(ih-oh)*(1-{ramp})"
        return f"crop=w='iw/({z})':h='ih/({z})':x='{x}':y='{y}',scale={ctx.width}:{ctx.height}"


@register
class Shake(BaseEffect):
    """震動：clip-local 時間窗內 x/y 各自用不同頻率的正弦波抖動，振幅隨時間衰減至 0。"""

    type = "shake"
    kind = "ranged"

    def fragment(self, effect: dict[str, Any], ctx: EffectContext) -> str:  # noqa: ARG002
        ls, le, td = _ranged_window(effect)
        s = _clamp_strength(effect.get("strength", self.default_strength))
        gate = f"between(t,{ls:.3f},{le:.3f})"
        decay = f"max(0,1-(t-{ls:.3f})/{td:.3f})"
        z = f"if({gate},1+{s:.4f},1)"
        dx = f"if({gate},(iw-ow)/2*{decay}*sin(2*PI*12*(t-{ls:.3f})),0)"
        dy = f"if({gate},(ih-oh)/2*{decay}*sin(2*PI*13*(t-{ls:.3f})+1.7),0)"
        return f"crop=w='iw/({z})':h='ih/({z})':x='(iw-ow)/2+{dx}':y='(ih-oh)/2+{dy}',scale={ctx.width}:{ctx.height}"


@register
class FlashTransition(BaseEffect):
    """爆點閃白：在 clip 起點由白淡入（fade from white），短促有力。"""

    type = "flash_transition"
    kind = "point"
    default_duration_ms = 240

    def fragment(self, effect: dict[str, Any], ctx: EffectContext) -> str:  # noqa: ARG002
        d = max(1, int(effect.get("duration_ms", self.default_duration_ms))) / 1000.0
        return f"fade=t=in:st=0:d={d:.3f}:color=white"


@register
class Fade(BaseEffect):
    """一般淡入（黑）。多由降卡點 join 使用；此處提供 fragment 供直接套用。"""

    type = "fade"
    kind = "point"
    default_duration_ms = 100

    def fragment(self, effect: dict[str, Any], ctx: EffectContext) -> str:  # noqa: ARG002
        d = max(1, int(effect.get("duration_ms", self.default_duration_ms))) / 1000.0
        return f"fade=t=in:st=0:d={d:.3f}"


@register
class Crossfade(BaseEffect):
    """交叉溶接。屬 join 語意，由 encoder 的接點處理（見 composer/transitions），非 per-clip fragment。"""

    type = "crossfade"
    kind = "point"


@register
class Cut(BaseEffect):
    """純硬切：無 filter。"""

    type = "cut"
    kind = "point"
