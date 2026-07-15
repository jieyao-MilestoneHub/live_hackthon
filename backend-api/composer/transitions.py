"""降低拼接「卡點」：接點轉場策略（JoinStrategy）＋ 切點吸附（snap）。

兩個層次：
  1. **切點吸附** ``snap_cut_points``：把 source 裁切點吸附到最近的 beat/句界，避免句中硬切。
     預設 ``tolerance_ms=0``＝不吸附（維持 composer 秒數精確、既有測試不動）；呼叫端可開。
  2. **接點轉場** ``JoinStrategy``：由 encoder 在每個內部接點套用，柔化硬切。
     * ``HardCut``：無轉場（現況）。
     * ``MicroFade``（**預設**）：每刀邊界 ~90ms 影音微淡（video ``fade`` + audio ``afade``），
       **總長不變**、與 concat demuxer 相容——安全的降卡點。
     * ``VideoXfade``：可見交叉溶接（需 filter_complex，opt-in，之後做）。

轉場以 effects.v1 point 效果（``fade``/``crossfade``）當 marker 記錄，實際套用在 encoder。
純函式、決定性。時間一律毫秒（ms）。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from composer.strategies import MIN_CLIP_MS, SelectedClip

JOIN_FADE_MS = 90  # MicroFade 每接點的微淡時長


# --- 切點吸附 -----------------------------------------------------------------

def _snap(value: int, boundaries: list[int], tolerance_ms: int) -> int:
    best = value
    best_d = tolerance_ms + 1
    for b in boundaries:
        d = abs(b - value)
        if d <= tolerance_ms and d < best_d:
            best, best_d = b, d
    return best


def snap_cut_points(
    clips: list[SelectedClip],
    boundaries: list[int],
    tolerance_ms: int = 0,
) -> list[SelectedClip]:
    """把每刀 source_start/end 吸附到 ``tolerance_ms`` 內最近的 boundary（beat/句界）。

    ``tolerance_ms<=0`` 或無 boundary 時原樣返回（預設關閉，維持秒數精確）。吸附後若不足
    ``MIN_CLIP_MS`` 則還原該刀，避免產生過短片段。
    """
    if tolerance_ms <= 0 or not boundaries:
        return list(clips)
    bs = sorted({int(b) for b in boundaries})
    out: list[SelectedClip] = []
    for c in clips:
        s = _snap(c.source_start_ms, bs, tolerance_ms)
        e = _snap(c.source_end_ms, bs, tolerance_ms)
        if e - s < MIN_CLIP_MS:
            s, e = c.source_start_ms, c.source_end_ms
        out.append(SelectedClip(c.highlight_id, s, e))
    return out


def beat_boundaries(annotations: dict[str, Any] | None) -> list[int]:
    """從 annotations 蒐集所有 beat 邊界（供 snap_cut_points 用）。"""
    bounds: list[int] = []
    for a in (annotations or {}).get("annotations", []):
        for b in a.get("beats", []) or []:
            bounds.append(int(b["start_ms"]))
            bounds.append(int(b["end_ms"]))
    return sorted(set(bounds))


# --- 接點轉場策略 -------------------------------------------------------------

@runtime_checkable
class JoinStrategy(Protocol):
    name: str
    fade_ms: int

    def markers(self, clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """回傳內部接點的 effects.v1 point marker（供 provenance / encoder 參考）。"""
        ...


def _internal_boundaries(clips: list[dict[str, Any]]) -> list[int]:
    ordered = sorted(clips, key=lambda c: c["timeline_order"])
    return [int(c["timeline_start_ms"]) for c in ordered[1:]]  # 除第一刀外的起點


class HardCut:
    name = "hard_cut"
    fade_ms = 0

    def markers(self, clips: list[dict[str, Any]]) -> list[dict[str, Any]]:  # noqa: ARG002
        return []


class MicroFade:
    name = "micro_fade"
    fade_ms = JOIN_FADE_MS

    def markers(self, clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"type": "fade", "at_ms": b, "duration_ms": self.fade_ms} for b in _internal_boundaries(clips)]


class VideoXfade:
    name = "video_xfade"
    fade_ms = 250

    def markers(self, clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"type": "crossfade", "at_ms": b, "duration_ms": self.fade_ms} for b in _internal_boundaries(clips)]


_JOINS: dict[str, JoinStrategy] = {s.name: s for s in (HardCut(), MicroFade(), VideoXfade())}


def get_join_strategy(name: str | None) -> JoinStrategy:
    """依名稱取接點策略（未知/None → MicroFade 預設）。"""
    return _JOINS.get((name or "").strip().lower(), MicroFade())
