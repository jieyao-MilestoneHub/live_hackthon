"""API round-trip tests for the Editor API (Project/millisecond, M1).

Uses the moto-backed ``client`` fixture (conftest.py), exercising the real
DynamoDB ``VideoEditor`` + S3 multipart code paths.
"""
from __future__ import annotations


def test_health(client) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.2.0"


def test_create_project_roundtrip(client) -> None:
    resp = client.post("/projects", json={"title": "測試", "target_duration_ms": 30000})
    assert resp.status_code == 201
    body = resp.json()
    project_id = body["project_id"]
    assert project_id.startswith("project-")
    assert body["status"] == "CREATED"
    assert body["target_duration_ms"] == 30000
    assert project_id in body["source_key"]
    assert body["source_key"].endswith("source/source.mp4")

    # Round-trip GET.
    got = client.get(f"/projects/{project_id}")
    assert got.status_code == 200
    gb = got.json()
    assert gb["project_id"] == project_id
    assert gb["status"] == "CREATED"
    assert gb["target_duration_ms"] == 30000


def test_target_duration_validation(client) -> None:
    # Over the 60s ceiling -> 422.
    assert client.post("/projects", json={"target_duration_ms": 60001}).status_code == 422
    # Missing required field -> 422.
    assert client.post("/projects", json={"title": "x"}).status_code == 422


def test_upload_session_presigns_parts_and_advances_state(client) -> None:
    project_id = client.post("/projects", json={"target_duration_ms": 15000}).json()["project_id"]

    resp = client.post(
        f"/projects/{project_id}/upload-session",
        json={"filename": "source.mp4", "content_type": "video/mp4", "part_count": 3},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["upload_id"]
    assert body["bucket"] == "video-editor-raw-test"
    assert body["key"].endswith("source/source.mp4")
    assert len(body["parts"]) == 3
    assert [p["part_number"] for p in body["parts"]] == [1, 2, 3]
    assert all(p["url"].startswith("http") for p in body["parts"])
    assert body["expires_in_sec"] == 900

    # State advanced CREATED -> UPLOAD_PENDING.
    assert client.get(f"/projects/{project_id}").json()["status"] == "UPLOAD_PENDING"


def test_upload_session_default_single_part(client) -> None:
    project_id = client.post("/projects", json={"target_duration_ms": 15000}).json()["project_id"]
    resp = client.post(f"/projects/{project_id}/upload-session", json={"filename": "a.mp4"})
    assert resp.status_code == 201
    assert len(resp.json()["parts"]) == 1


def test_unknown_project_404(client) -> None:
    assert client.get("/projects/does-not-exist").status_code == 404
    assert (
        client.post("/projects/does-not-exist/upload-session", json={"filename": "a.mp4"}).status_code
        == 404
    )


def test_unimplemented_endpoints_return_501(client) -> None:
    project_id = client.post("/projects", json={"target_duration_ms": 15000}).json()["project_id"]
    assert client.get(f"/projects/{project_id}/highlights").status_code == 501
    assert client.get(f"/projects/{project_id}/timeline").status_code == 501
    assert client.post(f"/projects/{project_id}/renders", json={}).status_code == 501
    assert client.get("/renders/render-xyz").status_code == 501
    assert client.get("/artifacts/artifact-xyz/download").status_code == 501
