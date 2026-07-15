"""refine 端點測試（離線 Stub Transcribe/Bedrock）。

用 USE_INMEMORY=1 的自有 client fixture（比照 test_attribution_api）：refine 會呼叫
factory.get_transcriber()/get_narrative_reviewer()，唯有 USE_INMEMORY=1 才綁 Stub（罐頭、
無 AWS）；conftest 的 client 是 USE_INMEMORY=0（moto，真 adapter 會去 poll 真 Transcribe）。
"""
from __future__ import annotations

import csv
import io
import json

import pytest

VIDEO_START = 1752487200000


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("USE_INMEMORY", "1")
    from app.aws import factory
    from app.repository import get_repository
    from app.settings import get_settings
    from app.storage import get_storage

    clears = (
        get_settings.cache_clear,
        get_repository.cache_clear,
        get_storage.cache_clear,
        factory.cache_clear,
    )
    for clr in clears:
        clr()

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c

    for clr in clears:
        clr()


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


def test_refine_proposes_and_enriches(client) -> None:
    pid = _analyzed(client)

    r = client.post(f"/projects/{pid}/refine")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["transcript_segment_count"] == 3  # StubTranscriber
    assert body["applied"] == 0  # 預設只提議
    assert len(body["proposed_offsets"]) >= 1
    off = body["proposed_offsets"][0]
    assert {"highlight_id", "current_start_ms", "proposed_start_ms", "offset_ms"} <= set(off)

    # annotations 已 enriched：description / dimension.text / beat.line 皆非 null。
    ann = body["annotations"]["annotations"][0]
    assert ann["description"]
    assert all(d.get("text") for d in ann["dimensions"])
    assert all(b.get("line") for b in ann["beats"])

    # GET /annotations 反映 enriched（已落地 work bucket）。
    got = client.get(f"/projects/{pid}/annotations").json()
    assert got["annotations"][0]["description"] == ann["description"]


def test_refine_apply_offsets_patches_highlight(client) -> None:
    pid = _analyzed(client)
    r = client.post(f"/projects/{pid}/refine", json={"apply_offsets": True})
    assert r.status_code == 200, r.text
    assert r.json()["applied"] >= 1
    # 高光被自動校正（correction.applied / status=shifted）。
    hls = client.get(f"/projects/{pid}/highlights").json()["highlights"]
    assert any((h.get("correction") or {}).get("applied") for h in hls)


def test_refine_before_analysis_conflicts(client) -> None:
    pid = client.post("/projects", json={"target_duration_ms": 30000}).json()["project_id"]
    r = client.post(f"/projects/{pid}/refine")
    assert r.status_code == 409


def test_refine_persists_transcript(client) -> None:
    from app.settings import get_settings
    from app.storage import get_storage

    pid = _analyzed(client)
    client.post(f"/projects/{pid}/refine")
    settings = get_settings()
    doc = get_storage().get_json(settings.work_bucket, settings.transcript_key("demo", pid))
    assert doc["schema_version"] == "transcript.v1"
