"""API smoke tests for the FastAPI walking skeleton."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_create_job_returns_succeeded_with_clips(client: TestClient) -> None:
    resp = client.post("/jobs", json={"filename": "stream.mp4"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["job_id"]
    assert body["status"] == "SUCCEEDED"
    assert "upload" in body

    # The created job is retrievable and matches, with at least one clip.
    job_id = body["job_id"]
    got = client.get(f"/jobs/{job_id}")
    assert got.status_code == 200
    got_body = got.json()
    assert got_body["job_id"] == job_id
    assert got_body["status"] == "SUCCEEDED"
    assert len(got_body["highlights"]) >= 1
    clip = got_body["highlights"][0]
    assert clip["clip_id"]
    assert clip["start_sec"] < clip["end_sec"]


def test_get_unknown_job_returns_404(client: TestClient) -> None:
    resp = client.get("/jobs/does-not-exist")
    assert resp.status_code == 404


def test_download_url_stub(client: TestClient) -> None:
    created = client.post("/jobs", json={"filename": "stream.mp4"}).json()
    job_id = created["job_id"]
    clip_id = client.get(f"/jobs/{job_id}").json()["highlights"][0]["clip_id"]

    resp = client.get(f"/jobs/{job_id}/artifacts/{clip_id}/download")
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"]
    assert body["expires_in_sec"] == 900

    # Unknown clip -> 404
    assert client.get(f"/jobs/{job_id}/artifacts/nope/download").status_code == 404
    # Unknown job -> 404
    assert client.get("/jobs/nope/artifacts/x/download").status_code == 404
