"""Chat Analysis Worker + /analyze API 測試（moto-backed DynamoDB/S3）。

涵蓋：正常路徑（有影片時基）落地+推進狀態、fallback（未連結影片）標記 -chattime、
以及 chat-upload → analyze → highlights → compose 的 API 端到端。
"""
from __future__ import annotations

import csv
import io
import json

import pytest

from analysis.validate import load_sample
from app.repository import get_repository
from app.state import ProjectState, assert_project_transition
from workers import chat_analysis_worker, composer_worker

VIDEO_START = 1752487200000


def _seed_project(repo, *, with_video: bool, target_ms: int = 30000) -> str:
    project_id = f"project-chat-{'v' if with_video else 'novideo'}-{target_ms}"
    item = {
        "project_id": project_id,
        "tenant_id": "demo",
        "user_id": "tester",
        "status": ProjectState.CREATED.value,
        "target_duration_ms": target_ms,
        "source_bucket": "video-editor-raw-test",
        "source_key": f"tenant=demo/project={project_id}/source/source.mp4",
        "latest_timeline_version": 0,
    }
    if with_video:
        item["video_start_epoch_ms"] = VIDEO_START
        item["source_duration_ms"] = 240000
    repo.create_project(item)
    for state in (ProjectState.UPLOAD_PENDING, ProjectState.UPLOADING, ProjectState.ANALYZING):
        assert_project_transition(ProjectState(repo.get_project(project_id)["status"]), state)
        repo.update_project(project_id, {"status": state.value})
    return project_id


def test_chat_worker_persists_and_advances(aws) -> None:
    repo = get_repository()
    pid = _seed_project(repo, with_video=True)

    result = chat_analysis_worker.run(repo, pid, load_sample("chatlog.sample.json"))

    assert result["project_id"] == pid
    assert result["analysis_version"] == "highlight-chat-1.0.0"
    assert all(h["signal"] == "chat_volume" for h in result["highlights"])
    stored = repo.list_highlights(pid)
    assert len(stored) == len(result["highlights"]) >= 1
    project = repo.get_project(pid)
    assert project["status"] == ProjectState.COMPOSING.value
    assert project["source_duration_ms"] == 240000


def test_chat_worker_fallback_when_no_video(aws) -> None:
    repo = get_repository()
    pid = _seed_project(repo, with_video=False)

    result = chat_analysis_worker.run(repo, pid, load_sample("chatlog.sample.json"))

    # 未連結影片 → 聊天相對時間模式，analysis_version 標記，且不亂寫 source_duration_ms。
    assert result["analysis_version"].endswith("-chattime")
    assert repo.get_project(pid).get("source_duration_ms") is None
    assert repo.get_project(pid)["status"] == ProjectState.COMPOSING.value


def test_chat_worker_then_compose_ready(aws) -> None:
    repo = get_repository()
    pid = _seed_project(repo, with_video=True)
    chat_analysis_worker.run(repo, pid, load_sample("chatlog.sample.json"))

    timeline = composer_worker.run(repo, pid)

    assert timeline["version"] == 1
    assert repo.get_project(pid)["status"] == ProjectState.READY_TO_EDIT.value


def _chat_csv() -> bytes:
    rows = [
        {"msg": "msg", "time": 1752487265500, "nickname": "B", "content": "哇這個真的太扯了！"},
        {"msg": "join", "time": 1752487201000, "nickname": "sys", "content": "B 進入"},
        {"msg": "msg", "time": 1752487212000, "nickname": "A", "content": "先卡位"},
        {"msg": "msg", "time": 1752487233000, "nickname": "bot", "content": "輸入浪🌊ID 抽好禮"},
        {"msg": "msg", "time": 1752487266200, "nickname": "C", "content": "太神了吧 起雞皮疙瘩 XDDD"},
        {"msg": "msg", "time": 1752487353000, "nickname": "E", "content": "成功了！太爽了吧 感謝應援！"},
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["seq", "message"])
    for i, r in enumerate(rows):
        w.writerow([i, json.dumps(r, ensure_ascii=False)])
    return buf.getvalue().encode("utf-8")


def test_analyze_api_end_to_end(client) -> None:
    from app.settings import get_settings
    from app.storage import get_storage

    pid = client.post("/projects", json={"target_duration_ms": 30000}).json()["project_id"]

    up = client.post(f"/projects/{pid}/chat-upload").json()
    assert up["key"].endswith("chat.csv")
    # 模擬瀏覽器 presigned PUT：直接把 chat.csv 寫進（moto）S3。
    get_storage().put_bytes(up["bucket"], up["key"], _chat_csv(), "text/csv")

    r = client.post(
        f"/projects/{pid}/analyze",
        json={"video_start_epoch_ms": VIDEO_START, "source_duration_ms": 240000},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "COMPOSING"
    assert body["highlight_count"] >= 1

    hls = client.get(f"/projects/{pid}/highlights").json()["highlights"]
    assert hls[0]["signal"] == "chat_volume"
    assert hls[0]["emotion"]["score"] >= 0

    # chatlog.v1 已落地 Work bucket。
    settings = get_settings()
    doc = get_storage().get_json(settings.work_bucket, settings.chatlog_key("demo", pid))
    assert doc["schema_version"] == "chatlog.v1"

    assert client.post(f"/projects/{pid}/compose").status_code == 202
    assert client.get(f"/projects/{pid}").json()["status"] == "READY_TO_EDIT"


def test_analyze_missing_chat_csv_returns_404(client) -> None:
    pid = client.post("/projects", json={"target_duration_ms": 30000}).json()["project_id"]
    r = client.post(f"/projects/{pid}/analyze", json={})
    assert r.status_code == 404


# --- Slice 2 校正端點 ----------------------------------------------------

def test_video_timebase_via_creation_time(client) -> None:
    pid = client.post("/projects", json={"target_duration_ms": 30000}).json()["project_id"]
    r = client.put(
        f"/projects/{pid}/video-timebase",
        json={"creation_time": "2025-07-14T04:00:00.000000000Z", "source_duration_ms": 240000},
    )
    assert r.status_code == 200, r.text
    proj = r.json()
    assert proj["video_start_epoch_ms"] == 1752465600000  # 2025-07-14T04:00:00Z
    assert proj["source_duration_ms"] == 240000


def _analyzed_project(client) -> tuple[str, str]:
    """Create a project, upload chat.csv, analyze → return (project_id, first highlight_id)."""
    from app.storage import get_storage

    pid = client.post("/projects", json={"target_duration_ms": 30000}).json()["project_id"]
    up = client.post(f"/projects/{pid}/chat-upload").json()
    get_storage().put_bytes(up["bucket"], up["key"], _chat_csv(), "text/csv")
    client.post(
        f"/projects/{pid}/analyze",
        json={"video_start_epoch_ms": VIDEO_START, "source_duration_ms": 240000},
    )
    hid = client.get(f"/projects/{pid}/highlights").json()["highlights"][0]["highlight_id"]
    return pid, hid


def test_patch_highlight_offset_then_exclude(client) -> None:
    pid, hid = _analyzed_project(client)
    before = next(h for h in client.get(f"/projects/{pid}/highlights").json()["highlights"] if h["highlight_id"] == hid)

    # 聊天落後 → 往前抓 5s（此段起點僅 10s，抓 20s 會夾到 0）。
    r = client.patch(f"/projects/{pid}/highlights/{hid}", json={"correction_offset_ms": -5000, "note": "chat lag"})
    assert r.status_code == 200, r.text
    shifted = r.json()
    assert shifted["status"] == "shifted"
    assert shifted["start_ms"] == before["start_ms"] - 5000
    assert shifted["correction"]["offset_ms"] == -5000

    # 排除開場。
    r = client.patch(f"/projects/{pid}/highlights/{hid}", json={"exclude": True, "note": "開場自我介紹"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "excluded"
    assert r.json()["selected"] is False

    # 讀回持久化結果。
    stored = next(h for h in client.get(f"/projects/{pid}/highlights").json()["highlights"] if h["highlight_id"] == hid)
    assert stored["status"] == "excluded" and stored["selected"] is False


def test_patch_highlight_lock(client) -> None:
    pid, hid = _analyzed_project(client)
    r = client.patch(f"/projects/{pid}/highlights/{hid}", json={"locked": True})
    assert r.status_code == 200 and r.json()["locked"] is True


def test_patch_missing_highlight_404(client) -> None:
    pid, _ = _analyzed_project(client)
    r = client.patch(f"/projects/{pid}/highlights/hl-999", json={"locked": True})
    assert r.status_code == 404
