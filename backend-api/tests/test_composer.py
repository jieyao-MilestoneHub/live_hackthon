"""Contract + invariant tests for the Composer (compose_timeline)."""
from __future__ import annotations

import pytest

from analysis.validate import load_sample, validate_timeline
from composer import MAX_DURATION_MS, compose_timeline

PID = "project-123"


@pytest.fixture()
def highlights() -> list[dict]:
    return load_sample("highlights.sample.json")["highlights"]


def test_output_validates_against_contract(highlights) -> None:
    validate_timeline(compose_timeline(PID, highlights, 30000))


def test_actual_within_half_second_of_target(highlights) -> None:
    tl = compose_timeline(PID, highlights, 30000)  # enough source material
    assert abs(tl["actual_duration_ms"] - 30000) <= 500


def test_never_exceeds_60s(highlights) -> None:
    tl = compose_timeline(PID, highlights, 60000)
    assert tl["actual_duration_ms"] <= MAX_DURATION_MS


def test_excluded_never_included(highlights) -> None:
    tl = compose_timeline(PID, highlights, 60000, excluded_ids=["hl-001"])
    assert "hl-001" not in [c["highlight_id"] for c in tl["clips"]]


def test_locked_always_included(highlights) -> None:
    # hl-002 is lower-scored; without lock a tight target would drop it.
    tl = compose_timeline(PID, highlights, 20000, locked_ids=["hl-002"])
    assert "hl-002" in [c["highlight_id"] for c in tl["clips"]]


def test_clips_chronological_sequential_contiguous(highlights) -> None:
    tl = compose_timeline(PID, highlights, 60000)
    clips = tl["clips"]
    assert len(clips) >= 2
    assert [c["timeline_order"] for c in clips] == list(range(1, len(clips) + 1))
    starts = [c["source_start_ms"] for c in clips]
    assert starts == sorted(starts)  # chronological by source time
    cursor = 0
    for c in clips:
        assert c["timeline_start_ms"] == cursor
        assert c["timeline_end_ms"] > c["timeline_start_ms"]
        cursor = c["timeline_end_ms"]
    assert cursor == tl["actual_duration_ms"]


def test_version_and_metadata_passthrough(highlights) -> None:
    tl = compose_timeline(PID, highlights, 30000, version=3, aspect_ratio="1:1")
    assert tl["version"] == 3
    assert tl["project_id"] == PID
    assert tl["aspect_ratio"] == "1:1"
    assert tl["created_by"] == "composer"


def test_empty_highlights_yields_empty_timeline() -> None:
    tl = compose_timeline(PID, [], 30000)
    validate_timeline(tl)
    assert tl["clips"] == []
    assert tl["actual_duration_ms"] == 0


def test_deterministic(highlights) -> None:
    a = compose_timeline(PID, highlights, 45000, created_at="2026-07-14T00:00:00Z")
    b = compose_timeline(PID, highlights, 45000, created_at="2026-07-14T00:00:00Z")
    assert a == b
