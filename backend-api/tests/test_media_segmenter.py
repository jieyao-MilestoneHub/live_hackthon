"""Unit tests for MediaSegmenter — split decision, csv parsing, upload mapping.

ffmpeg exec and the duration probe are injected, so nothing here runs a binary or
touches AWS. Covers logic / boundary / error / object-state.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from app.aws.media_segmenter import (
    MediaSegmenter,
    _parse_segment_csv,
    plan_segment_count,
)

GB = 1024**3


class _FakeStorage:
    """Duck-typed Storage: only the methods MediaSegmenter calls."""

    def __init__(self, size: int) -> None:
        self._size = size
        self.uploaded: list[tuple[str, str]] = []

    def head_size(self, bucket: str, key: str) -> int:
        return self._size

    def download_to_file(self, bucket: str, key: str, dest_path: str) -> None:
        with open(dest_path, "wb") as fh:
            fh.write(b"source-bytes")

    def upload_file(self, bucket, key, src_path, content_type=None) -> str:
        self.uploaded.append((bucket, key))
        return key


def _settings():
    return SimpleNamespace(work_bucket="work-bucket")


def _make_runner(n: int, seg_sec: int = 600):
    """A fake ffmpeg runner that writes n segment files + a segment_list csv."""

    def run(cmd: list[str]) -> None:
        csv_path = cmd[cmd.index("-segment_list") + 1]
        work_dir = os.path.dirname(csv_path)
        lines = []
        for i in range(n):
            filename = f"seg_{i:03d}.mp4"
            with open(os.path.join(work_dir, filename), "wb") as fh:
                fh.write(b"segment-bytes")
            lines.append(f"{filename},{i * seg_sec:.6f},{(i + 1) * seg_sec:.6f}")
        with open(csv_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    return run


def _segmenter(storage, *, runner, probe_ms=1_200_000):
    return MediaSegmenter(
        storage, _settings(),
        ffmpeg_bin="ffmpeg",
        runner=runner,
        probe=lambda _bin, _url: probe_ms,
    )


# --- logic -----------------------------------------------------------------

def test_plan_segment_count_rounds_up():
    assert plan_segment_count(4 * GB, int(1.8 * GB)) == 3


def test_segment_returns_one_info_per_csv_row():
    seg = _segmenter(_FakeStorage(4 * GB), runner=_make_runner(3))

    result = seg.segment("raw", "src.mp4", project_id="p")

    assert len(result) == 3


def test_segment_offsets_come_from_csv_start_times():
    seg = _segmenter(_FakeStorage(4 * GB), runner=_make_runner(3, seg_sec=600))

    result = seg.segment("raw", "src.mp4", project_id="p")

    assert result[1].offset_ms == 600_000


def test_segment_media_uri_points_at_work_bucket():
    seg = _segmenter(_FakeStorage(4 * GB), runner=_make_runner(1))

    result = seg.segment("raw", "src.mp4", project_id="p")

    assert result[0].media_uri == "s3://work-bucket/transcript/p/segments/seg_000.mp4"


def test_each_segment_is_uploaded():
    storage = _FakeStorage(4 * GB)
    seg = _segmenter(storage, runner=_make_runner(2))

    seg.segment("raw", "src.mp4", project_id="p")

    assert len(storage.uploaded) == 2


# --- boundary --------------------------------------------------------------

def test_small_source_is_not_split():
    seg = _segmenter(_FakeStorage(500 * 1024**2), runner=_make_runner(3))

    result = seg.segment("raw", "src.mp4", project_id="p", max_bytes=int(1.8 * GB))

    assert result == []


def test_parse_csv_skips_blank_and_short_lines(tmp_path):
    csv = tmp_path / "segments.csv"
    csv.write_text("seg_000.mp4,0.000000,600.000000\n\nbad-line\n", encoding="utf-8")

    rows = _parse_segment_csv(str(csv))

    assert rows == [("seg_000.mp4", 0.0, 600.0)]


# --- error -----------------------------------------------------------------

def test_ffmpeg_failure_propagates():
    def boom(cmd):
        raise RuntimeError("ffmpeg segment failed (1)")

    seg = _segmenter(_FakeStorage(4 * GB), runner=boom)

    with pytest.raises(RuntimeError):
        seg.segment("raw", "src.mp4", project_id="p")


# --- object-state ----------------------------------------------------------

def test_tmp_segment_files_are_cleaned_up():
    # Capture the work dir the runner wrote into; it must be gone afterwards.
    seen: dict[str, str] = {}
    base_runner = _make_runner(2)

    def run(cmd):
        seen["dir"] = os.path.dirname(cmd[cmd.index("-segment_list") + 1])
        base_runner(cmd)

    seg = _segmenter(_FakeStorage(4 * GB), runner=run)
    seg.segment("raw", "src.mp4", project_id="p")

    assert not os.path.exists(seen["dir"])
