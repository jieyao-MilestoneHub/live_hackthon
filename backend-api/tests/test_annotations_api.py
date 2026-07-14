"""annotations 端點測試（moto client）：POST 產生 / GET 讀取 / PUT 人工編輯。"""
from __future__ import annotations

import csv
import io
import json

VIDEO_START = 1752487200000


def _chat_csv() -> bytes:
    rows = [
        {"msg": "msg", "time": 1752487265500, "nickname": "B", "content": "哇這個真的太扯了！"},
        {"msg": "msg", "time": 1752487212000, "nickname": "A", "content": "先卡位"},
        {"msg": "msg", "time": 1752487266200, "nickname": "C", "content": "太神了吧 起雞皮疙瘩 🤣🤣"},
        {"msg": "msg", "time": 1752487353000, "nickname": "E", "content": "成功了！太爽了吧 感謝應援！"},
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["seq", "message"])
    for i, r in enumerate(rows):
        w.writerow([i, json.dumps(r, ensure_ascii=False)])
    return buf.getvalue().encode("utf-8")


def _analyzed(client) -> str:
    from app.storage import get_storage

    pid = client.post("/projects", json={"target_duration_ms": 30000}).json()["project_id"]
    up = client.post(f"/projects/{pid}/chat-upload").json()
    get_storage().put_bytes(up["bucket"], up["key"], _chat_csv(), "text/csv")
    client.post(
        f"/projects/{pid}/analyze",
        json={"video_start_epoch_ms": VIDEO_START, "source_duration_ms": 240000},
    )
    return pid


def test_generate_get_and_put(client) -> None:
    pid = _analyzed(client)

    # 產生
    r = client.post(f"/projects/{pid}/annotations")
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["annotation_version"] == "annotation-rule-1.0.0"
    assert len(doc["annotations"]) >= 1
    dims = [d["dimension"] for d in doc["annotations"][0]["dimensions"]]
    assert dims == ["setup", "reaction_start", "reaction_turn", "punchline", "chat_highlights"]

    # 讀取一致
    g = client.get(f"/projects/{pid}/annotations")
    assert g.status_code == 200
    assert g.json()["annotations"][0]["highlight_id"] == doc["annotations"][0]["highlight_id"]

    # 人工編輯：改 description → 存 → 讀回反映
    edited = g.json()
    edited["annotations"][0]["description"] = "端午節粽子開箱：冰的無調味豆沙減重粽"
    edited["annotations"][0]["corrected_by"] = "editor-1"
    p = client.put(f"/projects/{pid}/annotations", json=edited)
    assert p.status_code == 200, p.text
    back = client.get(f"/projects/{pid}/annotations").json()
    assert back["annotations"][0]["description"] == "端午節粽子開箱：冰的無調味豆沙減重粽"


def test_get_before_generate_returns_404(client) -> None:
    pid = _analyzed(client)
    r = client.get(f"/projects/{pid}/annotations")
    assert r.status_code == 404


def test_generate_before_analysis_conflicts(client) -> None:
    # 剛建立（CREATED）尚未分析 → 不允許標註。
    pid = client.post("/projects", json={"target_duration_ms": 30000}).json()["project_id"]
    r = client.post(f"/projects/{pid}/annotations")
    assert r.status_code == 409


def test_put_invalid_annotations_422(client) -> None:
    pid = _analyzed(client)
    client.post(f"/projects/{pid}/annotations")
    # dimension enum 非法 → 違反 annotations.v1。
    bad = {
        "project_id": pid,
        "annotations": [
            {"highlight_id": "hl-001", "dimensions": [{"dimension": "bogus", "start_ms": 0, "end_ms": 1000}]}
        ],
    }
    r = client.put(f"/projects/{pid}/annotations", json=bad)
    assert r.status_code == 422
