"""聊天時間（epoch 毫秒）↔ 影片相對毫秒 的換算工具（分析流程階段四基礎）。

聊天 log 的 time_ms 是牆鐘 Unix epoch 毫秒；其他所有契約都是影片相對毫秒
（0 = 影片起點）。兩個時間世界唯一的橋樑是 Project.video_start_epoch_ms
（來自 MP4 OBS creation_time）：

    video_relative_ms = clamp(chat_epoch_ms − video_start_epoch_ms, 0, source_duration_ms)

觀眾反應落後（chat lag）的事件校正**不在這裡**：它是疊在換算結果之上、每個
highlight 各自的 correction.offset_ms（Slice 2 人工/AI 校正），保持基礎換算無損。
"""
from __future__ import annotations


def video_ms(
    epoch_ms: int,
    video_start_epoch_ms: int,
    source_duration_ms: int | None = None,
) -> int:
    """把 epoch 毫秒換算成影片相對毫秒，clamp 到 [0, source_duration_ms]。

    影片開始前的訊息 → 夾到 0；超出影片長度 → 夾到 source_duration_ms。
    """
    rel = int(epoch_ms) - int(video_start_epoch_ms)
    if rel < 0:
        rel = 0
    if source_duration_ms is not None and rel > int(source_duration_ms):
        rel = int(source_duration_ms)
    return rel


def is_before_video(epoch_ms: int, video_start_epoch_ms: int) -> bool:
    """該訊息是否早於影片 0:00（換算後會被夾到 0，呼叫端可選擇丟棄）。"""
    return int(epoch_ms) < int(video_start_epoch_ms)
