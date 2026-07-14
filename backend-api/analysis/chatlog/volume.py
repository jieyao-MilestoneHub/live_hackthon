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
