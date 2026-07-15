"""特效策略註冊表：可擴充的 EffectStrategy（SOLID 的 OCP/DIP）。

一個特效類型 = 一個策略，知道 (a) 如何被**計畫**（產出 effects.v1 的 dict）與
(b) 如何被**套用**（產出 FFmpeg filter 片段給 encoder）。新增特效只要 ``@register`` 一個
策略類別，`plan_effects`（計畫）與 `apply_effects`（渲染）都不需修改——真正的開放封閉。

形狀對應 effects.v1 的 oneOf：
  * ranged（區間，如 zoom_in / pan / shake）：``{type, start_ms, end_ms, strength}``
  * point（點狀轉場，如 flash_transition / crossfade / fade / cut）：``{type, at_ms, duration_ms}``

具體特效清單稍後定案；本檔先把架構＋既有類型架好，並把最穩的 zoom_in / flash 真正實作
FFmpeg 片段（其餘策略回傳空片段、標 TODO，啟用特效也不會弄壞編碼）。fragment 是 clip-local
（套在每個 clip 正規化後的 -vf 鏈上，畫面已是目標 WxH），時間相對該 clip 起點。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class EffectContext:
    """套用特效時的畫面情境（供 fragment 產生正確參數）。"""

    width: int
    height: int
    fps: int


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

@register
class ZoomIn(BaseEffect):
    """緩慢推近（Ken Burns）。以 zoompan 連續放大到 1+strength，畫面維持目標尺寸。"""

    type = "zoom_in"
    kind = "ranged"

    def fragment(self, effect: dict[str, Any], ctx: EffectContext) -> str:
        strength = max(0.0, min(float(effect.get("strength", self.default_strength)), 0.5))
        zmax = round(1.0 + strength, 4)
        return (
            f"zoompan=z='min(zoom+0.0015,{zmax})':d=1"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":s={ctx.width}x{ctx.height}:fps={ctx.fps}"
        )


@register
class Pan(BaseEffect):
    """橫移運鏡。TODO：需先放大取得裁切餘裕；具體視覺定案後實作，先回傳空片段（安全）。"""

    type = "pan"
    kind = "ranged"


@register
class Shake(BaseEffect):
    """震動。TODO：crop 抖動 + 回縮；具體視覺定案後實作，先回傳空片段（安全）。"""

    type = "shake"
    kind = "ranged"


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
