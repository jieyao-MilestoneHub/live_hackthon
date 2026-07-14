"""Contract + invariant tests for the rule-based highlight detector."""
from __future__ import annotations

import pytest

from analysis import detect_highlights
from analysis.validate import load_sample, validate_highlights, validate_transcript

EPS = 0.01


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
    duration = transcript["duration_sec"]
    for h in result["highlights"]:
        assert 0 <= h["start_sec"] < h["end_sec"] <= duration + EPS


def test_clip_duration_within_params(result: dict) -> None:
    lo = result["parameters"]["min_duration_sec"]
    hi = result["parameters"]["max_duration_sec"]
    for h in result["highlights"]:
        dur = h["end_sec"] - h["start_sec"]
        assert lo - EPS <= dur <= hi + EPS


def test_scores_sorted_descending(result: dict) -> None:
    scores = [h["score"] for h in result["highlights"]]
    assert scores == sorted(scores, reverse=True)


def test_clip_ids_unique(result: dict) -> None:
    ids = [h["clip_id"] for h in result["highlights"]]
    assert len(ids) == len(set(ids))


def test_job_id_carried_over(transcript: dict, result: dict) -> None:
    assert result["job_id"] == transcript["job_id"]


def test_clip_count_within_bounds(result: dict) -> None:
    n = len(result["highlights"])
    assert 1 <= n <= result["parameters"]["max_clips"]


def test_climax_region_covered(result: dict) -> None:
    # The sample's climax ("來了來了 / 成功了") spans ~150-188s.
    climax_start, climax_end = 150.0, 188.0
    covered = any(
        h["start_sec"] <= climax_start and h["end_sec"] >= climax_end - 20
        for h in result["highlights"]
    )
    # At minimum some clip must overlap the climax window.
    overlaps = any(
        h["start_sec"] < climax_end and h["end_sec"] > climax_start
        for h in result["highlights"]
    )
    assert overlaps
    assert covered
