"""確認 attribution router 已掛載到 app.main:app（基本功能端到端可服務）。

以真正的 app.main:app 建 TestClient，離線（USE_INMEMORY=1）打人物登錄→產生→讀取，
確保端點真的被服務（非僅在獨立 router 測試中）。
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("USE_INMEMORY", "1")
    monkeypatch.setenv("ENV", "test")

    from app.settings import get_settings
    from app.repository import get_repository
    from app.storage import get_storage
    from app.attribution_repository import get_attribution_repository
    from app.aws import factory
    from app.aws.config import get_attribution_config

    for clr in (get_settings.cache_clear, get_repository.cache_clear, get_storage.cache_clear,
                get_attribution_repository.cache_clear, get_attribution_config.cache_clear,
                factory.cache_clear):
        clr()

    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        yield c

    get_settings.cache_clear()
    get_attribution_repository.cache_clear()
    factory.cache_clear()


def test_attribution_routes_registered():
    from app.main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/projects/{project_id}/people" in paths
    assert "/projects/{project_id}/attribution" in paths
    assert "/projects/{project_id}/transcript" in paths


def test_mounted_flow_served(client):
    r = client.post("/projects/pmount/people", json={"display_name": "主播 A", "role": "host"})
    assert r.status_code == 201, r.text

    run = client.post("/projects/pmount/attribution", json={"use_asd": True})
    assert run.status_code == 201, run.text
    assert run.json()["schema_version"] == "attributed_transcript.v1"

    got = client.get("/projects/pmount/transcript")
    assert got.status_code == 200
    assert got.json()["project_id"] == "pmount"
