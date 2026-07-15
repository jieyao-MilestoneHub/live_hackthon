"""組片選段策略：highlights(+annotations) → 選定片段清單（SOLID 的 DIP/OCP）。

`compose_timeline` 只負責外殼/時間軸定位/驗證；「選哪些段、怎麼裁」抽成可替換的
``ClipPlanner`` 策略：

  * ``ScoreGreedyPlanner``：等同既有行為（無 annotations 時的預設），**但修正保爆點**——
    片段填不下時從**前段**裁切、保留結尾 payoff（不再從結尾砍掉 punchline）。
  * ``NarrativeBeatPlanner``：吃 annotations.v1 的起承轉合 ``beats``。對每個高光保留
    **埋梗(setup)＋爆梗(punchline)**；長度不足時**捨棄中間反應段**，輸出「setup clip ＋
    punchline clip」兩刀（timeline.clips 本就允許同 highlight 多刀），punchline 永不被裁
    （極端超長才取其尾段 payoff）。多個高光依 source 時間拼接成敘事順序。

純函式、決定性（穩定排序、無 RNG）。時間一律毫秒（ms）。對應 issue #6。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol, runtime_checkable

MAX_DURATION_MS = 60_000       # 最終短片上限（demand.md §九）
MIN_CLIP_MS = 2_000            # 單刀最短，避免過碎裁切


@dataclass(frozen=True)
class SelectedClip:
    """選定的一刀（source 裁切點）；timeline 定位由 compose_timeline 指派。"""

    highlight_id: str
    source_start_ms: int
    source_end_ms: int


@runtime_checkable
class ClipPlanner(Protocol):
    def plan(
        self,
        highlights: list[dict[str, Any]],
        annotations: dict[str, Any] | None,
        target_duration_ms: int,
        *,
        locked_ids: Iterable[str] = (),
        excluded_ids: Iterable[str] = (),
    ) -> list[SelectedClip]:
        """回傳依 source 時間排序前的選定片段（compose_timeline 再排序/定位）。"""
        ...


# --- 共用原語 -----------------------------------------------------------------

def _overlaps(a_start: int, a_end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(not (a_end <= s or a_start >= e) for s, e in ranges)


def _rank(
    highlights: list[dict[str, Any]],
    locked: set[str],
    excluded: set[str],
) -> list[dict[str, Any]]:
    """排除剔除段/status=excluded；鎖定優先，其餘分數高→低（穩定排序、可重現）。"""
    def included(h: dict[str, Any]) -> bool:
        hid = h["highlight_id"]
        return hid not in excluded and h.get("status") != "excluded"

    def is_locked(h: dict[str, Any]) -> bool:
        return h["highlight_id"] in locked or bool(h.get("locked"))

    cands = [h for h in highlights if included(h)]
    locked_first = sorted((h for h in cands if is_locked(h)), key=lambda h: h.get("score", 0.0), reverse=True)
    rest = sorted((h for h in cands if not is_locked(h)), key=lambda h: h.get("score", 0.0), reverse=True)
    return locked_first + rest


def _front_trim(hid: str, start: int, end: int, remaining: int) -> tuple[list[SelectedClip], int]:
    """整段放不下時，從**前段**裁切、保留結尾 payoff（保爆點）。"""
    start, end = int(start), int(end)
    length = end - start
    if length <= remaining:
        return [SelectedClip(hid, start, end)], length
    if remaining < MIN_CLIP_MS:
        return [], 0
    return [SelectedClip(hid, end - remaining, end)], remaining  # 保留尾段


def _greedy(
    highlights: list[dict[str, Any]],
    target_duration_ms: int,
    locked: set[str],
    excluded: set[str],
    carve,
) -> list[SelectedClip]:
    """共用貪婪迴圈；每段如何裁由 ``carve(highlight, remaining)`` 決定。"""
    remaining = min(int(target_duration_ms), MAX_DURATION_MS)
    used: list[tuple[int, int]] = []
    selected: list[SelectedClip] = []
    for h in _rank(highlights, locked, excluded):
        if remaining < MIN_CLIP_MS:
            break
        window = (int(h["start_ms"]), int(h["end_ms"]))
        if _overlaps(*window, used):
            continue  # 語意重複 MVP 啟發式：跳過 source 重疊者
        clips, consumed = carve(h, remaining)
        if not clips:
            continue
        selected.extend(clips)
        used.append(window)
        remaining -= consumed
    return selected


# --- 策略：分數貪婪（保爆點）--------------------------------------------------

class ScoreGreedyPlanner:
    """既有行為 + 保爆點（前段裁切）。無 annotations 時的預設。"""

    def plan(self, highlights, annotations, target_duration_ms, *, locked_ids=(), excluded_ids=()):
        def carve(h, remaining):
            return _front_trim(h["highlight_id"], h["start_ms"], h["end_ms"], remaining)

        return _greedy(highlights, target_duration_ms, set(locked_ids), set(excluded_ids), carve)


# --- 策略：起承轉合 beat-aware（埋梗+爆梗拼接）--------------------------------

def _beats_for(annotations: dict[str, Any] | None, highlight_id: str) -> list[dict[str, Any]]:
    for a in (annotations or {}).get("annotations", []):
        if a.get("highlight_id") == highlight_id:
            return sorted(a.get("beats", []) or [], key=lambda b: b.get("order", 0))
    return []


def _arc(beats: list[dict[str, Any]]) -> tuple[int, int, int, int] | None:
    """由 beats 取 (setup_start, setup_end, punch_start, punch_end)；無 beats 回 None。"""
    if not beats:
        return None
    setup = [b for b in beats if b.get("beat") == "setup"]
    punch = [b for b in beats if b.get("beat") == "punchline"]
    setup_start = min((b["start_ms"] for b in setup), default=beats[0]["start_ms"])
    setup_end = max((b["end_ms"] for b in setup), default=beats[0]["end_ms"])
    if punch:
        punch_start = min(b["start_ms"] for b in punch)
        punch_end = max(b["end_ms"] for b in punch)
    else:  # 沒標 punchline：以最後一拍為爆點
        punch_start, punch_end = beats[-1]["start_ms"], beats[-1]["end_ms"]
    return int(setup_start), int(setup_end), int(punch_start), int(punch_end)


class NarrativeBeatPlanner:
    """依起承轉合 beats 拼接埋梗+爆梗；保爆點、超長捨中段。有 annotations 時的預設。"""

    def plan(self, highlights, annotations, target_duration_ms, *, locked_ids=(), excluded_ids=()):
        def carve(h, remaining):
            hid = h["highlight_id"]
            arc = _arc(_beats_for(annotations, hid))
            if arc is None:  # 該高光無 beats：退回整段前段裁切（保爆點）
                return _front_trim(hid, h["start_ms"], h["end_ms"], remaining)
            s_start, s_end, p_start, p_end = arc
            full_len = p_end - s_start
            punch_len = p_end - p_start
            if full_len <= remaining:  # 整段起承轉合放得下：一刀
                return [SelectedClip(hid, s_start, p_end)], full_len
            if punch_len >= remaining:  # 連 punchline 都超長：取其尾段 payoff（不砍爆點）
                return [SelectedClip(hid, p_end - remaining, p_end)], remaining
            # 放不下：捨中間反應段 → setup 刀 + punchline 刀
            setup_budget = remaining - punch_len
            setup_len = min(s_end - s_start, setup_budget)
            clips: list[SelectedClip] = []
            consumed = 0
            if setup_len >= MIN_CLIP_MS:  # setup 太短就整個省略、只留爆梗
                clips.append(SelectedClip(hid, s_start, s_start + setup_len))
                consumed += setup_len
            clips.append(SelectedClip(hid, p_start, p_end))
            consumed += punch_len
            return clips, consumed

        return _greedy(highlights, target_duration_ms, set(locked_ids), set(excluded_ids), carve)


def default_planner(annotations: dict[str, Any] | None) -> ClipPlanner:
    """有 annotations（且含 beats）→ NarrativeBeat；否則 ScoreGreedy。"""
    has_beats = bool(
        annotations
        and any(a.get("beats") for a in annotations.get("annotations", []))
    )
    return NarrativeBeatPlanner() if has_beats else ScoreGreedyPlanner()
