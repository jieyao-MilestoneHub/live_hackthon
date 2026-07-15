"""Media segmenter — cut an oversized source into Transcribe-digestible pieces.

Amazon Transcribe rejects any input file over **2 GB** (``BadRequestException:
The input file ... exceeds the maximum size of 2048.00 Mb``). A long livestream
VOD easily exceeds that, so this tool splits the S3 source into ``≤ max_bytes``
segments that each transcribe independently; ``SplittingTranscriber`` then merges
the per-segment transcripts back with absolute time offsets.

SOLID / testability:
  * Single responsibility — turn one S3 object into an ordered list of segment
    objects in the work bucket (``SegmentInfo``), or ``[]`` when no split is needed.
  * ffmpeg exec and the duration probe are **injected** (``runner`` / ``probe``),
    so the planning + upload logic unit-tests with no ffmpeg binary and no AWS.
  * The source is read by ffmpeg via an S3 **presigned GET URL** (HTTP range
    seeks), so a multi-GB source is never fully downloaded to the Lambda; only
    one-pass stream-copy segments land on ``/tmp``.
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable

from app.settings import Settings
from app.storage import Storage

# Leave headroom under the hard 2GB Transcribe limit so a segment that lands a
# little larger than planned (keyframe alignment) still clears the limit.
DEFAULT_MAX_BYTES = int(1.8 * 1024**3)  # 1.8 GiB
# Also cap segment length: Transcribe batch tops out at 4h; stay well under.
DEFAULT_SEGMENT_CAP_SEC = 3300  # 55 min


@dataclass(frozen=True)
class SegmentInfo:
    """One Transcribe-ready segment sitting in the work bucket."""

    index: int
    media_uri: str  # s3://work-bucket/...
    offset_ms: int  # absolute start of this segment in the original source
    duration_ms: int


def _default_ffmpeg_bin() -> str:
    override = os.environ.get("FFMPEG_BINARY")
    if override:
        return override
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:  # the backend image ships a static binary via imageio-ffmpeg
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return "ffmpeg"


def _run_ffmpeg(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "ignore")[-2000:]
        raise RuntimeError(f"ffmpeg segment failed ({proc.returncode}): {tail}")


def _probe_duration_ms(ffmpeg_bin: str, media_url: str) -> int:
    """Read the container duration (ms) from ffmpeg's stderr banner.

    ``ffmpeg -i <input>`` with no output exits non-zero *by design* ("At least
    one output file must be specified") after printing ``Duration: HH:MM:SS.ss``;
    we parse that line rather than decode the whole file (fast; header only)."""
    proc = subprocess.run([ffmpeg_bin, "-i", media_url], capture_output=True)
    text = proc.stderr.decode("utf-8", "ignore")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            token = line.split("Duration:", 1)[1].split(",", 1)[0].strip()
            if token.upper().startswith("N/A"):
                break
            hh, mm, ss = token.split(":")
            total = int(hh) * 3600 + int(mm) * 60 + float(ss)
            return int(round(total * 1000))
    raise RuntimeError(f"could not determine media duration from: {media_url}")


def plan_segment_count(size_bytes: int, max_bytes: int) -> int:
    """How many equal time-slices keep every segment safely under ``max_bytes``.

    Pure: assumes ~constant bitrate, so N = ceil(size / max_bytes). Returns ≥ 1."""
    if size_bytes <= 0 or max_bytes <= 0:
        return 1
    return max(1, math.ceil(size_bytes / max_bytes))


class MediaSegmenter:
    def __init__(
        self,
        storage: Storage,
        settings: Settings,
        *,
        ffmpeg_bin: str | None = None,
        runner: Callable[[list[str]], None] | None = None,
        probe: Callable[[str, str], int] | None = None,
    ) -> None:
        self._storage = storage
        self._settings = settings
        self._ffmpeg = ffmpeg_bin or _default_ffmpeg_bin()
        self._run = runner or _run_ffmpeg
        self._probe = probe or _probe_duration_ms

    def segment(
        self,
        bucket: str,
        key: str,
        *,
        project_id: str,
        max_bytes: int = DEFAULT_MAX_BYTES,
        segment_cap_sec: int = DEFAULT_SEGMENT_CAP_SEC,
    ) -> list[SegmentInfo]:
        """Split ``bucket/key`` into ``≤ max_bytes`` segments in the work bucket.

        Returns ``[]`` when the source already fits (caller transcribes it whole).
        """
        size = self._storage.head_size(bucket, key)
        if size <= max_bytes:
            return []

        media_url = self._storage.presigned_get(bucket, key)
        duration_ms = self._probe(self._ffmpeg, media_url)
        count = plan_segment_count(size, max_bytes)
        segment_sec = max(1, min(segment_cap_sec, math.ceil((duration_ms / 1000) / count)))

        work_dir = tempfile.mkdtemp(prefix=f"seg-{project_id}-")
        try:
            csv_path = os.path.join(work_dir, "segments.csv")
            self._run([
                self._ffmpeg, "-y", "-i", media_url,
                "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy",
                "-f", "segment", "-segment_time", str(segment_sec),
                "-reset_timestamps", "1",
                "-segment_list", csv_path, "-segment_list_type", "csv",
                os.path.join(work_dir, "seg_%03d.mp4"),
            ])
            return self._upload_segments(work_dir, csv_path, project_id)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _upload_segments(self, work_dir: str, csv_path: str, project_id: str) -> list[SegmentInfo]:
        segments: list[SegmentInfo] = []
        for index, (filename, start_sec, end_sec) in enumerate(_parse_segment_csv(csv_path)):
            local = os.path.join(work_dir, filename)
            seg_key = f"transcript/{project_id}/segments/{filename}"
            self._storage.upload_file(
                self._settings.work_bucket, seg_key, local, content_type="video/mp4"
            )
            os.remove(local)  # drain /tmp as we go — peak disk stays ~source size
            segments.append(SegmentInfo(
                index=index,
                media_uri=f"s3://{self._settings.work_bucket}/{seg_key}",
                offset_ms=int(round(start_sec * 1000)),
                duration_ms=int(round((end_sec - start_sec) * 1000)),
            ))
        return segments


def _parse_segment_csv(csv_path: str) -> list[tuple[str, float, float]]:
    """Parse ffmpeg's segment_list CSV (``filename,start_time,end_time``)."""
    rows: list[tuple[str, float, float]] = []
    with open(csv_path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) < 3:
                continue
            rows.append((parts[0], float(parts[1]), float(parts[2])))
    return rows
