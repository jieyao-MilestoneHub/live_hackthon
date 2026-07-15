"""Unit tests for the pure transcript merger (splitting_transcriber.merge_transcripts).

Pure functions — no AWS, no network. Covers logic / boundary / error / object-state.
"""
from __future__ import annotations

from app.aws.splitting_transcriber import _shift_segment, merge_transcripts


def _seg(segment_id, start_ms, end_ms, *, text="hi", speaker="spk_0", conf=0.9, items=None):
    seg = {
        "segment_id": segment_id,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "speaker": speaker,
        "text": text,
        "confidence": conf,
    }
    if items is not None:
        seg["items"] = items
    return seg


def _transcript(segments, *, duration_ms=0, project_id="p"):
    return {
        "schema_version": "transcript.v1",
        "project_id": project_id,
        "language_code": "zh-TW",
        "duration_ms": duration_ms,
        "segments": segments,
        "created_at": "2026-01-01T00:00:00Z",
    }


# --- logic -----------------------------------------------------------------

def test_second_part_segment_shifted_by_offset():
    # Arrange
    parts = [
        (0, _transcript([_seg("seg_0001", 0, 1000)], duration_ms=1000)),
        (60000, _transcript([_seg("seg_0001", 0, 2000)], duration_ms=2000)),
    ]
    # Act
    merged = merge_transcripts("p", "zh-TW", parts)
    # Assert
    assert merged["segments"][1]["start_ms"] == 60000


def test_segments_are_resequenced():
    parts = [
        (0, _transcript([_seg("seg_0001", 0, 1000)])),
        (60000, _transcript([_seg("seg_0001", 0, 2000)])),
    ]

    merged = merge_transcripts("p", "zh-TW", parts)

    assert merged["segments"][1]["segment_id"] == "seg_0002"


def test_duration_is_max_absolute_end():
    parts = [
        (0, _transcript([_seg("seg_0001", 0, 1000)], duration_ms=1000)),
        (60000, _transcript([_seg("seg_0001", 0, 2000)], duration_ms=2000)),
    ]

    merged = merge_transcripts("p", "zh-TW", parts)

    assert merged["duration_ms"] == 62000


def test_parts_merged_in_offset_order_regardless_of_input_order():
    parts = [
        (60000, _transcript([_seg("seg_0001", 0, 500, text="second")])),
        (0, _transcript([_seg("seg_0001", 0, 500, text="first")])),
    ]

    merged = merge_transcripts("p", "zh-TW", parts)

    assert merged["segments"][0]["text"] == "first"


def test_word_items_are_shifted():
    items = [{"type": "pronunciation", "start_ms": 100, "end_ms": 200, "text": "哈", "confidence": 0.9}]
    parts = [(5000, _transcript([_seg("seg_0001", 0, 500, items=items)]))]

    merged = merge_transcripts("p", "zh-TW", parts)

    assert merged["segments"][0]["items"][0]["start_ms"] == 5100


# --- boundary --------------------------------------------------------------

def test_single_part_offset_zero_leaves_times_unchanged():
    parts = [(0, _transcript([_seg("seg_0001", 100, 900)]))]

    merged = merge_transcripts("p", "zh-TW", parts)

    assert merged["segments"][0]["end_ms"] == 900


def test_empty_parts_yield_no_segments():
    merged = merge_transcripts("p", "zh-TW", [])

    assert merged["segments"] == []


def test_punctuation_item_with_null_time_stays_null():
    items = [{"type": "punctuation", "start_ms": None, "end_ms": None, "text": "。", "confidence": None}]
    parts = [(5000, _transcript([_seg("seg_0001", 0, 500, items=items)]))]

    merged = merge_transcripts("p", "zh-TW", parts)

    assert merged["segments"][0]["items"][0]["start_ms"] is None


# --- object-state (immutability) ------------------------------------------

def test_input_transcript_is_not_mutated():
    original = _seg("seg_0001", 0, 2000)
    parts = [(60000, _transcript([original]))]

    merge_transcripts("p", "zh-TW", parts)

    assert original["start_ms"] == 0


def test_shift_segment_returns_new_object():
    seg = _seg("seg_0001", 0, 1000)

    shifted = _shift_segment(seg, 500)

    assert shifted is not seg
