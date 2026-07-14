"""Attribution API router 測試（自建 app 掛載 router，不碰 main.py）。

離線（USE_INMEMORY=1）：in-memory repo + stub adapters。涵蓋：人物登錄（方案A）、名冊、
產生具名逐字稿、讀取、群組標記傳播、單句更正、未產生前 404。
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("USE_INMEMORY", "1")
    monkeypatch.setenv("ENV", "test")

    from app.settings import get_settings
    from app.attribution_repository import get_attribution_repository
    from app.aws import factory
    from app.aws.config import get_attribution_config

    for clr in (get_settings.cache_clear, get_attribution_repository.cache_clear,
                get_attribution_config.cache_clear, factory.cache_clear):
        clr()

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.attribution_api import router

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        yield c

    get_settings.cache_clear()
    get_attribution_repository.cache_clear()
    factory.cache_clear()


def test_enroll_and_list_people(client):
    r1 = client.post("/projects/p1/people", json={
        "display_name": "主播 A", "role": "host", "reference_image_keys": ["k1.jpg", "k2.jpg"],
    })
    assert r1.status_code == 201, r1.text
    p1 = r1.json()
    assert p1["person_id"].startswith("person_")
    assert p1["identity_source"] == "rekognition_collection"
    assert p1["indexed_faces"] == 2
    assert p1["rekognition_collection_id"] == "lang-live-p1"

    r2 = client.post("/projects/p1/people", json={"display_name": "來賓 B", "role": "guest"})
    assert r2.status_code == 201
    assert r2.json()["identity_source"] == "user_label"
    assert r2.json()["indexed_faces"] == 0

    roster = client.get("/projects/p1/people").json()
    assert len(roster) == 2


def test_run_read_and_correct(client):
    client.post("/projects/p1/people", json={"display_name": "主播 A", "role": "host"})

    # 產生前讀取 → 404
    assert client.get("/projects/p1/transcript").status_code == 404

    run = client.post("/projects/p1/attribution", json={"use_asd": True})
    assert run.status_code == 201, run.text
    doc = run.json()
    assert doc["schema_version"] == "attributed_transcript.v1"
    assert doc["utterances"]

    got = client.get("/projects/p1/transcript")
    assert got.status_code == 200
    assert got.json()["project_id"] == "p1"

    # 單句更正
    utt_id = doc["utterances"][0]["utterance_id"]
    pr = client.patch(f"/projects/p1/utterances/{utt_id}", json={"person_id": "person_xyz"})
    assert pr.status_code == 200
    assert pr.json()["person_id"] == "person_xyz"
    assert pr.json()["attribution"]["method"] == "user_label"

    # 落地確認
    reread = client.get("/projects/p1/transcript").json()
    changed = next(u for u in reread["utterances"] if u["utterance_id"] == utt_id)
    assert changed["person_id"] == "person_xyz"


def test_label_cluster_propagates(client):
    client.post("/projects/p1/people", json={"display_name": "主播 A", "role": "host"})
    client.post("/projects/p1/attribution", json={"use_asd": False})

    resp = client.patch("/projects/p1/speakers/spk_0", json={"person_id": "person_zzz"})
    assert resp.status_code == 200
    assert resp.json()["updated_utterances"] >= 1

    doc = client.get("/projects/p1/transcript").json()
    spk0 = [u for u in doc["utterances"] if u["speaker_cluster_id"] == "spk_0"]
    assert spk0 and all(u["person_id"] == "person_zzz" for u in spk0)


def test_correct_missing_utterance_404(client):
    client.post("/projects/p1/people", json={"display_name": "主播 A", "role": "host"})
    client.post("/projects/p1/attribution", json={})
    r = client.patch("/projects/p1/utterances/utt_9999", json={"person_id": "person_x"})
    assert r.status_code == 404
