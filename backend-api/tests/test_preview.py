"""GET /artifacts/{id}/preview — inline signed URL for in-page <video> preview.

Shares ``get_artifact_by_id`` + the moderation gate with the download route;
differs only in disposition (inline vs attachment) + a forced ``video/mp4``
content type. The download route is re-asserted here to lock in its upgrade to
an attachment (save-to-disk) disposition.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def _query(url: str, param: str) -> str:
    return parse_qs(urlparse(url).query).get(param, [""])[0]


def _disposition(url: str) -> str:
    return _query(url, "response-content-disposition")


def test_preview_returns_signed_url(client, published_artifact) -> None:
    _pid, _rid, artifact_id = published_artifact

    resp = client.get(f"/artifacts/{artifact_id}/preview")

    assert resp.status_code == 200 and resp.json()["url"]


def test_preview_url_is_inline(client, published_artifact) -> None:
    _pid, _rid, artifact_id = published_artifact

    url = client.get(f"/artifacts/{artifact_id}/preview").json()["url"]

    assert _disposition(url) == "inline"


def test_preview_forces_video_content_type(client, published_artifact) -> None:
    _pid, _rid, artifact_id = published_artifact

    url = client.get(f"/artifacts/{artifact_id}/preview").json()["url"]

    assert _query(url, "response-content-type") == "video/mp4"


def test_preview_404_for_missing_artifact(client) -> None:
    resp = client.get("/artifacts/artifact-does-not-exist/preview")

    assert resp.status_code == 404


def test_preview_403_when_project_blocked(client, published_artifact) -> None:
    from app.repository import get_repository

    project_id, _rid, artifact_id = published_artifact
    get_repository().update_project(project_id, {"moderation_status": "BLOCKED"})

    resp = client.get(f"/artifacts/{artifact_id}/preview")

    assert resp.status_code == 403


def test_download_url_is_attachment(client, published_artifact) -> None:
    _pid, _rid, artifact_id = published_artifact

    url = client.get(f"/artifacts/{artifact_id}/download").json()["url"]

    assert _disposition(url).startswith("attachment")


def test_download_filename_derives_from_project_and_route(client, published_artifact) -> None:
    project_id, _rid, artifact_id = published_artifact
    route = (client.get(f"/projects/{project_id}/artifacts").json()[0].get("route")) or "pipeline"

    disposition = _disposition(client.get(f"/artifacts/{artifact_id}/download").json()["url"])

    assert disposition == f'attachment; filename="{project_id}-{route}.mp4"'
