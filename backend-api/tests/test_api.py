"""API round-trip tests for the Editor API (Project/millisecond, M1).

Uses the moto-backed ``client`` fixture (conftest.py), exercising the real
DynamoDB ``VideoEditor`` + S3 multipart code paths.
"""
from __future__ import annotations

import re

from app.main import _new_project_id, _slugify_title

# project-<YYYYMMDD>-<HHMMSS>[-<ascii-slug>]-<8 hex>. The slug segment is
# optional (Chinese-only titles drop it), and the whole id stays [a-z0-9-] so it
# is safe as a Transcribe job name / Rekognition collection id and needs no
# URL-encoding in the EventBridge S3-event key.
_PROJECT_ID_RE = re.compile(r"^project-\d{8}-\d{6}(?:-[a-z0-9-]+?)?-[0-9a-f]{8}$")


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


def test_slugify_title_keeps_ascii_drops_cjk() -> None:
    # Mixed CJK/ASCII title: keep the ASCII runs, drop CJK + punctuation.
    assert _slugify_title("我的直播精華 — stream1.mp4") == "stream1-mp4"
    # Pure-Chinese title slugs to empty -> caller falls back to timestamp-only.
    assert _slugify_title("純中文標題") == ""
    assert _slugify_title(None) == ""
    assert _slugify_title("") == ""
    # Separators collapse; dangling separators are trimmed; truncation applies.
    assert _slugify_title("A_B  C!!!") == "a-b-c"
    assert _slugify_title("x" * 50, max_len=8) == "x" * 8


def test_new_project_id_is_traceable_and_url_safe() -> None:
    with_slug = _new_project_id("我的直播精華 — stream1.mp4")
    assert _PROJECT_ID_RE.match(with_slug), with_slug
    assert "-stream1-mp4-" in with_slug  # video filename is visible when listing S3

    # Chinese-only title -> timestamp-only id, still matches and stays url-safe.
    zh_only = _new_project_id("純中文標題")
    assert _PROJECT_ID_RE.match(zh_only), zh_only
    assert re.fullmatch(r"[a-z0-9-]+", zh_only), zh_only

    # No title at all (offline pipeline path) still yields a valid id.
    assert _PROJECT_ID_RE.match(_new_project_id())


def test_create_project_returns_traceable_id(client) -> None:
    resp = client.post(
        "/projects", json={"title": "我的直播精華 — stream1.mp4", "target_duration_ms": 30000}
    )
    assert resp.status_code == 201
    body = resp.json()
    project_id = body["project_id"]
    assert _PROJECT_ID_RE.match(project_id), project_id
    # The traceable id nests into the S3 source key.
    assert f"project={project_id}/" in body["source_key"]


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
    assert body["expires_in_sec"] == 21600

    # State advanced CREATED -> UPLOAD_PENDING.
    assert client.get(f"/projects/{project_id}").json()["status"] == "UPLOAD_PENDING"


def test_upload_session_default_single_part(client) -> None:
    project_id = client.post("/projects", json={"target_duration_ms": 15000}).json()["project_id"]
    resp = client.post(f"/projects/{project_id}/upload-session", json={"filename": "a.mp4"})
    assert resp.status_code == 201
    assert len(resp.json()["parts"]) == 1


def test_upload_session_derives_part_count_from_size(client) -> None:
    """0.5.0 batch path: client sends size_bytes (no part_count) and the server
    derives >1 parts from the 16 MiB chunk size."""
    project_id = client.post("/projects", json={"target_duration_ms": 15000}).json()["project_id"]
    size = 100 * 1024 * 1024  # 100 MiB -> ceil(100/16) = 7 parts
    resp = client.post(
        f"/projects/{project_id}/upload-session",
        json={"filename": "big.mp4", "content_type": "video/mp4", "size_bytes": size},
    )
    assert resp.status_code == 201
    assert len(resp.json()["parts"]) == 7


def test_upload_session_rejects_oversize_413(client) -> None:
    project_id = client.post("/projects", json={"target_duration_ms": 15000}).json()["project_id"]
    too_big = 10 * 1024**3 + 1  # 1 byte over the 10GB default cap
    resp = client.post(
        f"/projects/{project_id}/upload-session",
        json={"filename": "huge.mp4", "content_type": "video/mp4", "size_bytes": too_big},
    )
    assert resp.status_code == 413


def test_upload_session_rejects_non_video_415(client) -> None:
    project_id = client.post("/projects", json={"target_duration_ms": 15000}).json()["project_id"]
    resp = client.post(
        f"/projects/{project_id}/upload-session",
        json={"filename": "notes.txt", "content_type": "text/plain", "size_bytes": 1024},
    )
    assert resp.status_code == 415


def test_unknown_project_404(client) -> None:
    assert client.get("/projects/does-not-exist").status_code == 404
    assert (
        client.post("/projects/does-not-exist/upload-session", json={"filename": "a.mp4"}).status_code
        == 404
    )


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


# --- M3 render submission -------------------------------------------------


def test_create_render(client, ready_project) -> None:
    r = client.post(f"/projects/{ready_project}/renders", json={})
    assert r.status_code == 202
    body = r.json()
    assert body["render_id"].startswith("render-")
    assert body["status"] == "QUEUED"
    # Project advanced to RENDER_REQUESTED.
    assert client.get(f"/projects/{ready_project}").json()["status"] == "RENDER_REQUESTED"


def test_get_render(client, ready_render) -> None:
    project_id, render_id = ready_render
    r = client.get(f"/renders/{render_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["render_id"] == render_id
    assert body["project_id"] == project_id
    assert body["status"] == "QUEUED"
    assert body["timeline_version"] == 1
    assert isinstance(body["effect_seed"], int)


def test_render_on_non_ready_project_409(client) -> None:
    pid = client.post("/projects", json={"target_duration_ms": 30000}).json()["project_id"]
    assert client.post(f"/projects/{pid}/renders", json={}).status_code == 409


def test_get_render_unknown_404(client) -> None:
    assert client.get("/renders/render-does-not-exist").status_code == 404


# --- M4 render worker + download -----------------------------------------


def test_render_to_download(client, published_artifact) -> None:
    project_id, render_id, artifact_id = published_artifact
    # Render finished, Project ready.
    render = client.get(f"/renders/{render_id}").json()
    assert render["status"] == "SUCCEEDED"
    assert render["artifact_id"] == artifact_id
    assert client.get(f"/projects/{project_id}").json()["status"] == "ARTIFACT_READY"
    # Download URL issued.
    d = client.get(f"/artifacts/{artifact_id}/download")
    assert d.status_code == 200
    body = d.json()
    assert body["url"]
    assert body["expires_in_sec"] == 21600


def test_download_unknown_artifact_404(client) -> None:
    assert client.get("/artifacts/artifact-does-not-exist/download").status_code == 404
