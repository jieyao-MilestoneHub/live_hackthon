"""Chat Analysis Worker：chatlog.v1 → highlights.v1，落地並推進狀態。

聊天優先分析流程的 worker，與 analysis_worker（逐字稿路徑）平行：讀入正規化聊天
log，跑規則式熱區+情緒偵測，寫進 repository，把 Project 自 ANALYZING 推進到 COMPOSING。
演算法（analysis.chatlog.detect_highlights_from_chat）為 pure function；真部署時包成
ai-task Lambda，輸入改自 S3（chatlog.v1）/DynamoDB，邏輯不變。

時間換算需要兩個影片座標：video_start_epoch_ms（影片 0:00 的 epoch 毫秒，來自 MP4
OBS creation_time）與 source_duration_ms（影片長度）。優先用傳入值，其次讀 Project
META。兩者若都缺（影片尚未連結，Slice 2 才 ffprobe），退回「聊天相對時間」模式：以
聊天起訖當作影片起點與長度，並在 analysis_version 標記 -chattime 以示不可直接對到影片。
"""
from __future__ import annotations

from typing import Any

from analysis.chatlog import detect_highlights_from_chat
from app.repository import ProjectRepository
from app.state import ProjectState, assert_project_transition


def _resolve_timebase(
    project: dict[str, Any],
    chatlog: dict[str, Any],
    video_start_epoch_ms: int | None,
    source_duration_ms: int | None,
) -> tuple[int, int, bool]:
    """回傳 (video_start_epoch_ms, source_duration_ms, is_chat_relative_fallback)。"""
    vs = video_start_epoch_ms if video_start_epoch_ms is not None else project.get("video_start_epoch_ms")
    dur = source_duration_ms if source_duration_ms is not None else project.get("source_duration_ms")
    if vs is not None and dur is not None:
        return int(vs), int(dur), False

    # Fallback：影片未連結 → 用聊天起訖當替身（時間軸為聊天相對，非影片相對）。
    msgs = chatlog.get("messages") or []
    times = [int(m["time_ms"]) for m in msgs]
    started = chatlog.get("started_at_epoch_ms")
    started = int(started) if started is not None else (min(times) if times else 0)
    ended = chatlog.get("ended_at_epoch_ms")
    ended = int(ended) if ended is not None else (max(times) if times else started)
    vs = int(vs) if vs is not None else started
    dur = int(dur) if dur is not None else max(0, ended - started)
    return vs, dur, True


def run(
    repo: ProjectRepository,
    project_id: str,
    chatlog: dict[str, Any],
    *,
    video_start_epoch_ms: int | None = None,
    source_duration_ms: int | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect chat-driven highlights for a project and persist them.

    Precondition: Project status == ANALYZING. Postcondition: highlights stored,
    status advanced to COMPOSING (and ``source_duration_ms`` recorded when it is a
    real video duration, not the chat-relative fallback).
    Returns the highlights.v1 document.
    """
    project = repo.get_project(project_id)
    if project is None:
        raise KeyError(f"project {project_id} not found")
    assert_project_transition(ProjectState(project["status"]), ProjectState.COMPOSING)

    vs, dur, fallback = _resolve_timebase(project, chatlog, video_start_epoch_ms, source_duration_ms)
    analysis_version = "highlight-chat-1.0.0" + ("-chattime" if fallback else "")

    # Tie the analysis output to this project (chatlog may carry a different id).
    scoped = {**chatlog, "project_id": project_id}
    result = detect_highlights_from_chat(scoped, vs, dur, params, analysis_version=analysis_version)

    repo.put_highlights(project_id, result["highlights"])
    updates: dict[str, Any] = {"status": ProjectState.COMPOSING.value}
    if not fallback:
        updates["source_duration_ms"] = result["source_duration_ms"]
    repo.update_project(project_id, updates)
    return result
