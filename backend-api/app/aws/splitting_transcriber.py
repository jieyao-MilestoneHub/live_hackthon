"""SplittingTranscriber — transparent large-file support for Amazon Transcribe.

Decorates any ``TranscriberPort`` (Liskov-substitutable, same 3 methods) so the
Step Functions ``transcribe`` / ``poll_transcription`` handlers stay unchanged.
When the source exceeds Transcribe's 2GB limit it is split (via ``MediaSegmenter``)
into per-segment Transcribe jobs; the child transcripts are merged back into one
``transcript.v1`` with absolute time offsets. Small sources delegate to the inner
transcriber untouched, so the fast path is preserved.

The child jobs reuse the *inner* transcriber by deriving a per-segment pseudo
project id (``{project_id}-seg{i}``) — that gives each child a unique Transcribe
job name + output key for free, so this decorator holds no boto3 itself.

``poll_transcription`` only receives ``project_id``, so ``start_transcription``
persists a small manifest (segment offsets + child ids) to the work bucket keyed
by ``project_id``; poll reads it to know how to fan out and merge.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from app.aws.config import AttributionConfig
from app.aws.media_segmenter import DEFAULT_MAX_BYTES, DEFAULT_SEGMENT_CAP_SEC, MediaSegmenter
from app.aws.ports import TranscriberPort
from app.settings import Settings
from app.storage import Storage


def _parse_s3_uri(media_uri: str) -> tuple[str, str]:
    if not media_uri.startswith("s3://"):
        raise ValueError(f"expected an s3:// media uri, got: {media_uri}")
    bucket, _, key = media_uri[len("s3://"):].partition("/")
    if not bucket or not key:
        raise ValueError(f"malformed s3 uri: {media_uri}")
    return bucket, key


def _shift_segment(seg: dict[str, Any], offset_ms: int) -> dict[str, Any]:
    """Return a copy of ``seg`` with every time field shifted by ``offset_ms``.

    Immutable: never mutates the input (per coding-style)."""
    shifted = dict(seg)
    shifted["start_ms"] = int(seg["start_ms"]) + offset_ms
    shifted["end_ms"] = int(seg["end_ms"]) + offset_ms
    items = seg.get("items")
    if items:
        shifted["items"] = [
            {
                **it,
                "start_ms": (None if it.get("start_ms") is None else int(it["start_ms"]) + offset_ms),
                "end_ms": (None if it.get("end_ms") is None else int(it["end_ms"]) + offset_ms),
            }
            for it in items
        ]
    return shifted


def merge_transcripts(
    project_id: str,
    language_code: str,
    parts: list[tuple[int, dict[str, Any]]],
) -> dict[str, Any]:
    """Merge ``(offset_ms, transcript.v1)`` parts into one ``transcript.v1``.

    Parts are ordered by offset; each part's segment/word times are shifted into
    the absolute source timeline, segment ids re-sequenced, duration recomputed.
    """
    segments: list[dict[str, Any]] = []
    duration_ms = 0
    for offset_ms, transcript in sorted(parts, key=lambda p: p[0]):
        for seg in transcript.get("segments", []):
            shifted = _shift_segment(seg, offset_ms)
            shifted["segment_id"] = f"seg_{len(segments) + 1:04d}"
            segments.append(shifted)
            duration_ms = max(duration_ms, shifted["end_ms"])
        duration_ms = max(duration_ms, offset_ms + int(transcript.get("duration_ms", 0)))
    return {
        "schema_version": "transcript.v1",
        "project_id": project_id,
        "language_code": language_code,
        "duration_ms": duration_ms,
        "segments": segments,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


class SplittingTranscriber:
    """A ``TranscriberPort`` that splits oversized sources, else delegates."""

    def __init__(
        self,
        inner: TranscriberPort,
        segmenter: MediaSegmenter,
        storage: Storage,
        settings: Settings,
        config: AttributionConfig,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        segment_cap_sec: int = DEFAULT_SEGMENT_CAP_SEC,
    ) -> None:
        self._inner = inner
        self._segmenter = segmenter
        self._storage = storage
        self._settings = settings
        self._config = config
        self._max_bytes = max_bytes
        self._segment_cap_sec = segment_cap_sec

    def _manifest_key(self, project_id: str) -> str:
        return f"transcript/{project_id}/split_manifest.json"

    @staticmethod
    def _child_id(project_id: str, index: int) -> str:
        return f"{project_id}-seg{index:03d}"

    def start_transcription(
        self, project_id: str, media_uri: str, *, language_code: str, max_speakers: int
    ) -> None:
        bucket, key = _parse_s3_uri(media_uri)
        segments = self._segmenter.segment(
            bucket, key,
            project_id=project_id,
            max_bytes=self._max_bytes,
            segment_cap_sec=self._segment_cap_sec,
        )
        if not segments:  # fits the limit → transcribe the whole file, no split
            self._inner.start_transcription(
                project_id, media_uri, language_code=language_code, max_speakers=max_speakers
            )
            self._write_manifest(project_id, {"split": False})
            return

        children = []
        for seg in segments:
            child_id = self._child_id(project_id, seg.index)
            self._inner.start_transcription(
                child_id, seg.media_uri, language_code=language_code, max_speakers=max_speakers
            )
            children.append({"child_id": child_id, "offset_ms": seg.offset_ms, "duration_ms": seg.duration_ms})
        self._write_manifest(project_id, {"split": True, "language_code": language_code, "children": children})

    def poll_transcription(self, project_id: str, *, language_code: str) -> dict[str, Any]:
        manifest = self._read_manifest(project_id)
        if manifest is None or not manifest.get("split"):
            return self._inner.poll_transcription(project_id, language_code=language_code)

        parts: list[tuple[int, dict[str, Any]]] = []
        for child in manifest["children"]:
            result = self._inner.poll_transcription(child["child_id"], language_code=language_code)
            status = result["status"]
            if status == "FAILED":
                return {"status": "FAILED", "transcript": None,
                        "reason": f"segment {child['child_id']} failed: {result.get('reason')}"}
            if status != "COMPLETED":
                return {"status": "IN_PROGRESS", "transcript": None}
            parts.append((int(child["offset_ms"]), result["transcript"]))

        merged = merge_transcripts(project_id, language_code, parts)
        return {"status": "COMPLETED", "transcript": merged}

    def transcribe(
        self, project_id: str, media_uri: str, *, language_code: str, max_speakers: int
    ) -> dict[str, Any]:
        """Synchronous helper (local runs / tests): start then poll in-process."""
        self.start_transcription(
            project_id, media_uri, language_code=language_code, max_speakers=max_speakers
        )
        for _ in range(self._config.poll_max_attempts):
            result = self.poll_transcription(project_id, language_code=language_code)
            if result["status"] == "COMPLETED":
                return result["transcript"]
            if result["status"] == "FAILED":
                raise RuntimeError(f"split transcribe failed: {result.get('reason')}")
            time.sleep(self._config.poll_interval_sec)
        raise TimeoutError(f"split transcribe for {project_id} did not complete in time")

    def _write_manifest(self, project_id: str, doc: dict[str, Any]) -> None:
        self._storage.put_json(self._settings.work_bucket, self._manifest_key(project_id), doc)

    def _read_manifest(self, project_id: str) -> dict[str, Any] | None:
        try:
            return self._storage.get_json(self._settings.work_bucket, self._manifest_key(project_id))
        except KeyError:
            return None
