"""每分鐘留言量熱區偵測（分析流程階段三 a）。

方法一（便宜、可重現的規則式初篩）：把整場切成每分鐘桶，只計真人自發留言
（非 spam 且 kind==human），以「全場均值 + sigma 個標準差」為熱區門檻（預設
sigma=1.0 → prototype 得到 ≈6 則/分鐘），連續（含小間隔）熱分鐘合併成熱區窗。

全程在 epoch 毫秒空間運算（聊天原生時間）；換算成影片相對毫秒是後續 candidates
→ highlights 的事情（見 sync.py）。
"""
from __future__ import annotations

import statistics
from typing import Any

from analysis.chatlog import spam

MINUTE_MS = 60_000


def _stream_start_epoch_ms(chatlog: dict[str, Any], human_msgs: list[dict[str, Any]]) -> int:
    started = chatlog.get("started_at_epoch_ms")
    if started is not None:
        return int(started)
    if human_msgs:
        return int(min(m["time_ms"] for m in human_msgs))
    all_msgs = chatlog.get("messages") or []
    return int(min((m["time_ms"] for m in all_msgs), default=0))


def minute_buckets(chatlog: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """回傳 (每分鐘桶 list, 起點 epoch ms)。桶涵蓋 0..最後一則訊息所在分鐘（含空桶）。"""
    messages = chatlog.get("messages") or []
    human = [m for m in messages if spam.is_human_message(m)]
    started = _stream_start_epoch_ms(chatlog, human)

    counts: dict[int, int] = {}
    last_index = 0
    for m in human:
        idx = max(0, (int(m["time_ms"]) - started) // MINUTE_MS)
        counts[idx] = counts.get(idx, 0) + 1
        last_index = max(last_index, idx)

    # 也把非 human 的最後時間納入桶範圍，避免尾段空桶被截掉（影響均值）。
    for m in messages:
        idx = max(0, (int(m["time_ms"]) - started) // MINUTE_MS)
        last_index = max(last_index, idx)

    buckets = [
        {
            "minute_index": i,
            "start_epoch_ms": started + i * MINUTE_MS,
            "human_count": counts.get(i, 0),
            "is_hot": None,  # 由 hot_windows 回填
        }
        for i in range(last_index + 1)
    ]
    return buckets, started


def hot_windows(
    chatlog: dict[str, Any],
    sigma: float = 1.0,
    merge_gap_minutes: int = 1,
) -> dict[str, Any]:
    """計算每分鐘熱區並合併成窗。

    - 門檻 threshold = mean + sigma * pstdev（全場每分鐘 human 量，含空桶）。
    - 熱分鐘 = human_count >= threshold（且 >= 1，避免全零場把 0 也當熱區）。
    - 相鄰熱分鐘間隔 <= merge_gap_minutes 者合併成一個窗。

    回傳 dict：minute_buckets（已回填 is_hot）、mean、sigma_value、threshold、windows。
    每個 window：{start_epoch_ms, end_epoch_ms, minute_indices, human_count, peak_minute_volume}。
    """
    buckets, started = minute_buckets(chatlog)
    values = [b["human_count"] for b in buckets]

    mean = statistics.fmean(values) if values else 0.0
    sd = statistics.pstdev(values) if len(values) > 1 else 0.0
    threshold = mean + sigma * sd

    hot_indices: list[int] = []
    for b in buckets:
        is_hot = b["human_count"] >= threshold and b["human_count"] >= 1
        b["is_hot"] = is_hot
        if is_hot:
            hot_indices.append(b["minute_index"])

    windows: list[dict[str, Any]] = []
    for idx in hot_indices:
        if windows and idx - windows[-1]["_last_idx"] <= merge_gap_minutes:
            w = windows[-1]
            w["_last_idx"] = idx
            w["minute_indices"].append(idx)
        else:
            windows.append({"_first_idx": idx, "_last_idx": idx, "minute_indices": [idx]})

    count_by_index = {b["minute_index"]: b["human_count"] for b in buckets}
    result_windows: list[dict[str, Any]] = []
    for w in windows:
        first, last = w["_first_idx"], w["_last_idx"]
        idxs = w["minute_indices"]
        result_windows.append(
            {
                "start_epoch_ms": started + first * MINUTE_MS,
                "end_epoch_ms": started + (last + 1) * MINUTE_MS,
                "minute_indices": idxs,
                "human_count": sum(count_by_index[i] for i in idxs),
                "peak_minute_volume": max(count_by_index[i] for i in idxs),
            }
        )

    return {
        "minute_buckets": buckets,
        "mean": mean,
        "sigma_value": sd,
        "threshold": threshold,
        "windows": result_windows,
    }


# 每 5 秒重算一次、視窗仍是 60 秒（見 sliding_hot_windows）。
SLIDING_STEP_MS = 5_000
SLIDING_WINDOW_MS = MINUTE_MS


def sliding_hot_windows(
    chatlog: dict[str, Any],
    sigma: float = 1.0,
    window_ms: int = SLIDING_WINDOW_MS,
    step_ms: int = SLIDING_STEP_MS,
    merge_gap_ms: int = MINUTE_MS,
) -> dict[str, Any]:
    """滑動視窗版熱區偵測，修正固定日曆分鐘桶的邊界切斷問題。

    背景：hot_windows() 用固定日曆分鐘桶（00-59 秒一組），如果一波連續反應
    剛好卡在分鐘交界（例如前一分鐘 4 則、後一分鐘 5 則，實際 90 秒內共 9 則
    連續留言），兩邊都可能各自低於 mean+sigma 門檻而被漏掉整波。

    做法：每 step_ms（預設 5 秒）重新計算一次「回看 window_ms（預設 60 秒）內
    的真人留言數」，取代固定分鐘桶，門檻與合併規則沿用方法一的精神：
    - threshold = mean + sigma * pstdev（對滾動計數序列本身取統計量）。
    - 熱窗 = 滾動計數 >= threshold（且 >= 1）。
    - 相鄰熱窗間隔 <= merge_gap_ms 者合併，最終窗的起訖時間取該範圍內
      實際訊息的最早/最晚時間戳，不是分鐘桶邊界。

    回傳形狀與 hot_windows() 相容（mean / sigma_value / threshold / windows，
    每個 window 含 start_epoch_ms / end_epoch_ms / human_count /
    peak_minute_volume），可直接餵給 candidates.build_candidates()，
    是 hot_windows() 的替代方案，不影響既有呼叫端與測試。
    """
    messages = chatlog.get("messages") or []
    human = [m for m in messages if spam.is_human_message(m)]
    started = _stream_start_epoch_ms(chatlog, human)

    all_times = [int(m["time_ms"]) for m in messages] or [started]
    last_offset_ms = max(0, max(all_times) - started)
    n_bins = last_offset_ms // step_ms + 1

    bin_counts = [0] * (n_bins + 1)
    for m in human:
        idx = max(0, (int(m["time_ms"]) - started) // step_ms)
        if idx < len(bin_counts):
            bin_counts[idx] += 1

    window_bins = max(1, window_ms // step_ms)
    rolling: list[int] = []
    running = 0
    for i in range(len(bin_counts)):
        running += bin_counts[i]
        if i >= window_bins:
            running -= bin_counts[i - window_bins]
        rolling.append(running)

    mean = statistics.fmean(rolling) if rolling else 0.0
    sd = statistics.pstdev(rolling) if len(rolling) > 1 else 0.0
    threshold = mean + sigma * sd

    hot_idx = [i for i, r in enumerate(rolling) if r >= threshold and r >= 1]

    merge_gap_bins = max(1, merge_gap_ms // step_ms)
    raw_ranges: list[tuple[int, int]] = []
    if hot_idx:
        start_i = prev_i = hot_idx[0]
        for i in hot_idx[1:]:
            if i - prev_i <= merge_gap_bins:
                prev_i = i
            else:
                raw_ranges.append((start_i, prev_i))
                start_i = prev_i = i
        raw_ranges.append((start_i, prev_i))

    result_windows: list[dict[str, Any]] = []
    for i_start, i_end in raw_ranges:
        range_start_ms = started + max(0, i_start - window_bins + 1) * step_ms
        range_end_ms = started + (i_end + 1) * step_ms
        msgs = [m for m in human if range_start_ms <= int(m["time_ms"]) < range_end_ms]
        if not msgs:
            continue
        real_start = min(int(m["time_ms"]) for m in msgs)
        real_end = max(int(m["time_ms"]) for m in msgs)
        per_minute: dict[int, int] = {}
        for m in msgs:
            mi = (int(m["time_ms"]) - started) // MINUTE_MS
            per_minute[mi] = per_minute.get(mi, 0) + 1
        result_windows.append(
            {
                "start_epoch_ms": real_start,
                "end_epoch_ms": real_end,
                "human_count": len(msgs),
                "peak_minute_volume": max(per_minute.values()),
            }
        )

    return {
        "mean": mean,
        "sigma_value": sd,
        "threshold": threshold,
        "windows": result_windows,
    }
