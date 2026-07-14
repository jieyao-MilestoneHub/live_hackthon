"""Contract + invariant tests for the rule-based highlight detector (ms/Project)."""
from __future__ import annotations

import pytest

from analysis import detect_highlights
from analysis.validate import load_sample, validate_highlights, validate_transcript

EPS_MS = 1  # integer-ms tolerance


@pytest.fixture(scope="module")
def transcript() -> dict:
    return load_sample("transcript.sample.json")


@pytest.fixture(scope="module")
def result(transcript: dict) -> dict:
    return detect_highlights(transcript)


def test_sample_transcript_validates(transcript: dict) -> None:
    validate_transcript(transcript)


def test_highlights_output_validates(result: dict) -> None:
    validate_highlights(result)


def test_time_bounds_within_duration(transcript: dict, result: dict) -> None:
    duration = transcript["duration_ms"]
    for h in result["highlights"]:
        assert 0 <= h["start_ms"] < h["end_ms"] <= duration + EPS_MS


def test_clip_duration_within_params(result: dict) -> None:
    lo = result["parameters"]["min_duration_ms"]
    hi = result["parameters"]["max_duration_ms"]
    for h in result["highlights"]:
        dur = h["end_ms"] - h["start_ms"]
        assert lo - EPS_MS <= dur <= hi + EPS_MS


def test_scores_sorted_descending(result: dict) -> None:
    scores = [h["score"] for h in result["highlights"]]
    assert scores == sorted(scores, reverse=True)


def test_highlight_ids_unique(result: dict) -> None:
    ids = [h["highlight_id"] for h in result["highlights"]]
    assert len(ids) == len(set(ids))


def test_project_id_carried_over(transcript: dict, result: dict) -> None:
    assert result["project_id"] == transcript["project_id"]


def test_source_duration_carried_over(transcript: dict, result: dict) -> None:
    assert result["source_duration_ms"] == transcript["duration_ms"]


def test_clip_count_within_bounds(result: dict) -> None:
    n = len(result["highlights"])
    assert 1 <= n <= result["parameters"]["max_clips"]


def test_climax_region_covered(result: dict) -> None:
    # The sample's climax ("來了來了 / 成功了") spans ~150000-188000 ms.
    climax_start, climax_end = 150000, 188000
    covered = any(
        h["start_ms"] <= climax_start and h["end_ms"] >= climax_end - 20000
        for h in result["highlights"]
    )
    overlaps = any(
        h["start_ms"] < climax_end and h["end_ms"] > climax_start
        for h in result["highlights"]
    )
    assert overlaps
    assert covered
