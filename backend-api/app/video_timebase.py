"""從 MP4 讀 OBS/FFmpeg 寫入的 ``creation_time`` → epoch 毫秒，當作 chat↔影片 時基橋樑。

聊天 LOG 的 time_ms 是牆鐘 epoch，其餘契約都是影片相對毫秒；兩者唯一橋樑是
``Project.video_start_epoch_ms``（= 影片 0:00 的 epoch），而 chat LOG 本身**不帶**任何
對齊影片起點的欄位，唯一可靠來源就是 MP4 的 ``creation_time``。

本模組純 Python 掃 ``mvhd`` box（免 ffprobe/ffmpeg，可在輕量 Lambda 執行）：
``mvhd.creation_time`` 是 1904-01-01 00:00 UTC 起算的秒數；轉成 Unix epoch 毫秒即得。
``moov``/``mvhd`` 可能在檔頭（faststart，經 ffmpeg -movflags +faststart）或檔尾（OBS 直接
錄影的預設），故先讀檔頭、找不到再讀檔尾。任何解析失敗一律回 ``None``（呼叫端 fail-safe）。
"""
from __future__ import annotations

import struct
from typing import Any

# 1904-01-01 → 1970-01-01 的秒差（QuickTime/MP4 epoch → Unix epoch）。
_MAC_EPOCH_OFFSET = 2_082_844_800
_HEAD_BYTES = 256 * 1024        # faststart：moov 在檔頭
_TAIL_BYTES = 4 * 1024 * 1024   # OBS 錄影：moov 在檔尾
# 合理性上下界（epoch ms）：約 2001-09-09 ~ 2100-01-01；擋掉寫成 0 / 本機 1904 之類的怪值。
_MIN_PLAUSIBLE_MS = 1_000_000_000_000
_MAX_PLAUSIBLE_MS = 4_102_444_800_000


def _parse_mvhd_creation_epoch_ms(buf: bytes) -> int | None:
    """從含 ``mvhd`` box 的位元組緩衝解析 creation_time → epoch 毫秒；解析不出回 None。

    掃 ASCII 標記 ``mvhd`` 而非依賴絕對 box 位移，因此對「只讀到檔頭/檔尾片段」也穩健。
    mvhd payload：version(1) + flags(3) + creation_time(v0=4 / v1=8 bytes, big-endian)。
    """
    idx = buf.find(b"mvhd")
    if idx < 0:
        return None
    p = idx + 4  # 指到 mvhd payload 起點
    if p + 4 > len(buf):
        return None
    version = buf[p]
    try:
        if version == 1:
            if p + 12 > len(buf):
                return None
            secs = struct.unpack(">Q", buf[p + 4 : p + 12])[0]
        else:
            if p + 8 > len(buf):
                return None
            secs = struct.unpack(">I", buf[p + 4 : p + 8])[0]
    except struct.error:
        return None
    if secs <= _MAC_EPOCH_OFFSET:  # 0 或早於 Unix epoch → 未寫 creation_time
        return None
    epoch_ms = (secs - _MAC_EPOCH_OFFSET) * 1000
    if epoch_ms < _MIN_PLAUSIBLE_MS or epoch_ms > _MAX_PLAUSIBLE_MS:
        return None
    return epoch_ms


def extract_creation_epoch_ms(
    storage: Any, bucket: str, key: str, *, file_size: int | None = None
) -> int | None:
    """讀 ``bucket/key`` 的 MP4，回傳 creation_time 的 epoch 毫秒；任何失敗回 ``None``。

    只做兩次 range 讀取（檔頭 256KB → 找不到再讀檔尾 4MB），不下載整片。
    """
    try:
        head = storage.get_range(bucket, key, 0, _HEAD_BYTES)
    except KeyError:
        return None
    except Exception:  # noqa: BLE001 — 抽時基是最佳努力，任何 IO 例外都退回 None
        head = b""
    epoch = _parse_mvhd_creation_epoch_ms(head)
    if epoch is not None:
        return epoch

    # 檔頭沒有（moov 在檔尾）→ 讀檔尾。
    if file_size is None:
        try:
            file_size = storage.head_size(bucket, key)
        except KeyError:
            return None
        except Exception:  # noqa: BLE001
            return None
    if not file_size or file_size <= len(head):
        return None
    start = max(0, int(file_size) - _TAIL_BYTES)
    try:
        tail = storage.get_range(bucket, key, start, min(_TAIL_BYTES, int(file_size)))
    except Exception:  # noqa: BLE001
        return None
    return _parse_mvhd_creation_epoch_ms(tail)
