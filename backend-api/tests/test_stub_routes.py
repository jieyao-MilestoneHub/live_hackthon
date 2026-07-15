"""Stub object-store routes (dev-mode upload/download for the in-memory backend).

Security-critical: they must round-trip in in-memory mode but be **inert (404) in
real-AWS mode** — otherwise they'd be an unauthenticated arbitrary read/write to
real S3.
"""
from __future__ import annotations

import pytest

BUCKET = "video-editor-raw-test"
KEY = "tenant=demo/project=p1/source/chat.csv"


def _clear_caches() -> None:
    from app.repository import get_repository
    from app.settings import get_settings
    from app.storage import get_storage

    for fn in (get_settings, get_repository, get_storage):
        fn.cache_clear()


@pytest.fixture()
def inmemory_client(monkeypatch):
    monkeypatch.setenv("USE_INMEMORY", "1")
    monkeypatch.setenv("ENV", "test")
    _clear_caches()
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client
    _clear_caches()


def test_stub_roundtrip_in_memory(inmemory_client):
    put = inmemory_client.put(
        f"/stub-upload/{BUCKET}/{KEY}",
        content=b"time,user,msg\n1,alice,hi\n",
        headers={"content-type": "text/csv"},
    )
    assert put.status_code == 200
    assert put.headers.get("etag")  # S3-like ETag for multipart clients

    got = inmemory_client.get(f"/stub-download/{BUCKET}/{KEY}")
    assert got.status_code == 200
    assert got.content == b"time,user,msg\n1,alice,hi\n"


def test_stub_download_404_when_absent(inmemory_client):
    assert inmemory_client.get(f"/stub-download/{BUCKET}/missing.bin").status_code == 404


def test_stub_routes_disabled_in_real_aws(client):
    """`client` runs with USE_INMEMORY=0 (moto). The guard must 404 both routes so
    real S3 is never exposed."""
    assert client.put(f"/stub-upload/{BUCKET}/evil.bin", content=b"x").status_code == 404
    assert client.get(f"/stub-download/{BUCKET}/{KEY}").status_code == 404
