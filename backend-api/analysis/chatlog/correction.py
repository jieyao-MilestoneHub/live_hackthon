"""影片時基換算 + 事件窗人工校正（分析流程階段四/五，人工確認層）。

兩個純函式，供 Slice 2 編輯器端點使用：
  - creation_time_to_epoch_ms：把 MP4 OBS `creation_time`（ISO-8601，可能到奈秒）換成
    epoch 毫秒，寫入 Project.video_start_epoch_ms 當 chat↔影片 換算基準。
  - apply_correction：對單一 highlight 套用「聊天落後」校正（往前抓為負 offset）、排除
    開場（exclude）、鎖定（locked）、選取（selected），回傳更新後的 highlight（不變更輸入）。

時間一律影片相對毫秒；offset 為事件窗相對目前窗的位移，累加進 correction.offset_ms 供稽核。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

_ISO_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<frac>\d+))?(?P<tz>Z|[+-]\d{2}:?\d{2})?$"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def creation_time_to_epoch_ms(value: str) -> int:
    """ISO-8601 / OBS `creation_time` → epoch 毫秒（UTC）。

    容忍：'Z' 或 ±hh:mm / ±hhmm 時區、無時區（視為 UTC）、以及超過 6 位的小數秒
    （奈秒，OBS 常見），一律截到微秒再解析。
    """
    s = str(value).strip()
    m = _ISO_RE.match(s)
    if not m:
        raise ValueError(f"unrecognized creation_time: {value!r}")
    frac = (m.group("frac") or "")[:6].ljust(6, "0")  # 截/補到微秒
    tz = m.group("tz") or "Z"
    if tz == "Z":
        tz = "+00:00"
    elif len(tz) == 5 and ":" not in tz:  # +0800 → +08:00
        tz = tz[:3] + ":" + tz[3:]
    dt = datetime.fromisoformat(f"{m.group('date')}T{m.group('time')}.{frac}{tz}")
    return int(dt.timestamp() * 1000)


def apply_correction(
    highlight: dict[str, Any],
    *,
    offset_ms: int | None = None,
    exclude: bool | None = None,
    selected: bool | None = None,
    locked: bool | None = None,
    corrected_by: str | None = None,
    note: str | None = None,
    source_duration_ms: int | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """回傳套用校正後的**新** highlight dict（不修改輸入）。"""
    h: dict[str, Any] = dict(highlight)
    correction_applied = bool((h.get("correction") or {}).get("applied"))

    if offset_ms:  # 非零位移才動窗
        length = int(h["end_ms"]) - int(h["start_ms"])
        new_start = int(h["start_ms"]) + int(offset_ms)
        new_end = int(h["end_ms"]) + int(offset_ms)
        if new_start < 0:  # 夾下界，保持窗長
            new_start, new_end = 0, length
        if source_duration_ms is not None and new_end > int(source_duration_ms):
            new_end = int(source_duration_ms)
            new_start = max(0, new_end - length)
        h["start_ms"], h["end_ms"] = new_start, new_end

        prev = int((h.get("correction") or {}).get("offset_ms", 0))
        corr: dict[str, Any] = {"applied": True, "offset_ms": prev + int(offset_ms)}
        if corrected_by:
            corr["corrected_by"] = corrected_by
        corr["corrected_at"] = now_iso or _now_iso()
        if note:
            corr["note"] = note
        h["correction"] = corr
        h["status"] = "shifted"
        correction_applied = True

    if exclude is True:
        h["status"] = "excluded"
        h["selected"] = False
        h["excluded_reason"] = note or h.get("excluded_reason") or "編輯排除"
    elif exclude is False:
        if h.get("status") == "excluded":
            h["status"] = "shifted" if correction_applied else "included"
        h["selected"] = True
        h.pop("excluded_reason", None)

    if selected is not None:
        h["selected"] = bool(selected)
    if locked is not None:
        h["locked"] = bool(locked)

    return h
