"""Data-plane transfers: direct PUT/GET against presigned URLs.

The CLI never names S3 — it only follows the presigned URL the API mints, exactly
like the browser. That keeps it storage-agnostic (swap S3 for GCS/MinIO and this
file is the only thing that could care). Uses stdlib ``urllib`` — no boto3.
"""
from __future__ import annotations

import math
import os
import shutil
import urllib.error
import urllib.request
from typing import Any, Callable

from .api import EditorApi
from .errors import BackendError


def _put(url: str, data: bytes, content_type: str) -> str:
    """PUT bytes to a presigned URL; return the ETag header (S3 sends one)."""
    req = urllib.request.Request(url, data=data, method="PUT")
    req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return (resp.headers.get("ETag") or "").strip()
    except urllib.error.URLError as exc:
        raise BackendError(f"upload PUT failed: {getattr(exc, 'reason', exc)}") from None


def upload_video(
    api: EditorApi,
    project_id: str,
    file_path: str,
    *,
    content_type: str = "video/mp4",
    on_progress: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    """Presigned multipart upload of a video, then the complete handshake."""
    size = os.path.getsize(file_path)
    session = api.create_upload_session(
        project_id,
        {
            "filename": os.path.basename(file_path),
            "content_type": content_type,
            "size_bytes": size,
            "part_count": 1,
        },
    )
    parts = sorted(session["parts"], key=lambda p: p["part_number"])
    part_size = max(1, math.ceil(size / len(parts)))
    completed: list[dict[str, Any]] = []
    done = 0
    with open(file_path, "rb") as fh:
        for part in parts:
            chunk = fh.read(part_size)
            etag = _put(part["url"], chunk, content_type)
            completed.append({"part_number": part["part_number"], "etag": etag})
            done += len(chunk)
            if on_progress and size:
                on_progress(min(100, round(done * 100 / size)))
    return api.complete_upload_session(project_id, session["upload_id"], completed)


def upload_chat(api: EditorApi, project_id: str, csv_path: str) -> dict[str, Any]:
    """Presign + PUT the chat-log CSV to the Raw bucket."""
    session = api.create_chat_upload(project_id)
    with open(csv_path, "rb") as fh:
        _put(session["url"], fh.read(), "text/csv")
    return session


def download_artifact(api: EditorApi, artifact_id: str, out_path: str) -> str:
    """Resolve a presigned GET and stream the finished clip to ``out_path``."""
    signed = api.get_download_url(artifact_id)
    url = signed["url"]
    directory = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(directory, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=120) as resp, open(out_path, "wb") as out:
            shutil.copyfileobj(resp, out)
    except urllib.error.URLError as exc:
        raise BackendError(f"download failed: {getattr(exc, 'reason', exc)}") from None
    return out_path
