"""Contract client — methods map 1:1 to ``contracts/openapi.yaml`` (the 浪 LIVE
Editor API). This is a Python port of the frontend's ``lib/api.ts``: two clients,
one governed contract. It holds no business logic — just the endpoint surface —
so it stays trivially maintainable and could be regenerated from the OpenAPI spec.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from .transport import Transport


def _pp(value: str) -> str:
    """Path-segment encode an id (keep it opaque; never assume its shape)."""
    return quote(str(value), safe="")


class EditorApi:
    def __init__(self, transport: Transport):
        self.t = transport

    # -- meta ---------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        return self.t.request("GET", "/health")

    def openapi(self) -> dict[str, Any]:
        return self.t.request("GET", "/openapi.json")

    # -- project ------------------------------------------------------------
    def create_project(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.t.request("POST", "/projects", json_body=body)

    def get_project(self, project_id: str) -> dict[str, Any]:
        return self.t.request("GET", f"/projects/{_pp(project_id)}")

    def set_video_timebase(self, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.t.request("PUT", f"/projects/{_pp(project_id)}/video-timebase", json_body=body)

    # -- upload (video multipart) ------------------------------------------
    def create_upload_session(self, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.t.request("POST", f"/projects/{_pp(project_id)}/upload-session", json_body=body)

    def complete_upload_session(
        self, project_id: str, upload_id: str, parts: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return self.t.request(
            "POST",
            f"/projects/{_pp(project_id)}/upload-session/complete",
            json_body={"upload_id": upload_id, "parts": parts},
        )

    # -- chat-log analysis --------------------------------------------------
    def create_chat_upload(self, project_id: str) -> dict[str, Any]:
        return self.t.request("POST", f"/projects/{_pp(project_id)}/chat-upload")

    def analyze(self, project_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.t.request("POST", f"/projects/{_pp(project_id)}/analyze", json_body=body or {})

    # -- highlights ---------------------------------------------------------
    def get_highlights(self, project_id: str) -> dict[str, Any]:
        return self.t.request("GET", f"/projects/{_pp(project_id)}/highlights")

    def patch_highlight(
        self, project_id: str, highlight_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return self.t.request(
            "PATCH",
            f"/projects/{_pp(project_id)}/highlights/{_pp(highlight_id)}",
            json_body=body,
        )

    # -- annotations --------------------------------------------------------
    def generate_annotations(self, project_id: str) -> dict[str, Any]:
        return self.t.request("POST", f"/projects/{_pp(project_id)}/annotations")

    def get_annotations(self, project_id: str) -> dict[str, Any]:
        return self.t.request("GET", f"/projects/{_pp(project_id)}/annotations")

    # -- timeline / compose -------------------------------------------------
    def get_timeline(self, project_id: str, version: int | None = None) -> dict[str, Any]:
        return self.t.request(
            "GET", f"/projects/{_pp(project_id)}/timeline", query={"version": version}
        )

    def update_timeline(self, project_id: str, timeline: dict[str, Any]) -> dict[str, Any]:
        return self.t.request("PUT", f"/projects/{_pp(project_id)}/timeline", json_body=timeline)

    def compose(self, project_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.t.request("POST", f"/projects/{_pp(project_id)}/compose", json_body=body or {})

    # -- render / artifact --------------------------------------------------
    def create_render(self, project_id: str, timeline_version: int | None = None) -> dict[str, Any]:
        body = {"timeline_version": timeline_version} if timeline_version is not None else {}
        return self.t.request("POST", f"/projects/{_pp(project_id)}/renders", json_body=body)

    def get_render(self, render_id: str) -> dict[str, Any]:
        return self.t.request("GET", f"/renders/{_pp(render_id)}")

    def get_download_url(self, artifact_id: str) -> dict[str, Any]:
        return self.t.request("GET", f"/artifacts/{_pp(artifact_id)}/download")
