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
    # highlights/timeline/compose are wired in M2; renders/artifacts stay stubs.
    assert client.post("/projects/proj/renders", json={}).status_code == 501
    assert client.get("/renders/render-xyz").status_code == 501
    assert client.get("/artifacts/artifact-xyz/download").status_code == 501


# --- M2 editor loop -------------------------------------------------------


def test_highlights_empty_and_timeline_404_before_analysis(client) -> None:
    pid = client.post("/projects", json={"target_duration_ms": 30000}).json()["project_id"]
    hl = client.get(f"/projects/{pid}/highlights")
    assert hl.status_code == 200
    assert hl.json()["highlights"] == []
    assert client.get(f"/projects/{pid}/timeline").status_code == 404


def test_compose_without_highlights_409(client) -> None:
    pid = client.post("/projects", json={"target_duration_ms": 30000}).json()["project_id"]
    assert client.post(f"/projects/{pid}/compose", json={}).status_code == 409


def test_get_highlights(client, ready_project) -> None:
    r = client.get(f"/projects/{ready_project}/highlights")
    assert r.status_code == 200
    body = r.json()
    assert body["project_id"] == ready_project
    assert body["source_duration_ms"] == 240000
    assert len(body["highlights"]) >= 1
    assert all(h["start_ms"] < h["end_ms"] for h in body["highlights"])


def test_get_timeline(client, ready_project) -> None:
    r = client.get(f"/projects/{ready_project}/timeline")
    assert r.status_code == 200
    tl = r.json()
    assert tl["version"] == 1
    assert tl["actual_duration_ms"] <= 60000
    assert len(tl["clips"]) >= 1


def test_compose_appends_new_version(client, ready_project) -> None:
    r = client.post(f"/projects/{ready_project}/compose", json={"target_duration_ms": 20000})
    assert r.status_code == 202
    assert r.json()["timeline_version"] == 2
    # latest is now v2; v1 is still retrievable (append-only).
    assert client.get(f"/projects/{ready_project}/timeline").json()["version"] == 2
    assert client.get(f"/projects/{ready_project}/timeline?version=1").json()["version"] == 1


def test_put_timeline_appends_new_version(client, ready_project) -> None:
    current = client.get(f"/projects/{ready_project}/timeline").json()
    edited = {"target_duration_ms": current["target_duration_ms"], "clips": current["clips"][:1]}
    r = client.put(f"/projects/{ready_project}/timeline", json=edited)
    assert r.status_code == 200
    assert r.json()["timeline_version"] == current["version"] + 1
