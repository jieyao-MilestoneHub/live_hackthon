"""Slice 2 校正純函式測試：creation_time→epoch、apply_correction 事件窗校正。"""
from __future__ import annotations

import pytest

from analysis.chatlog.correction import apply_correction, creation_time_to_epoch_ms
from analysis.validate import validate_highlights


# --- creation_time_to_epoch_ms ------------------------------------------

def test_creation_time_utc_z() -> None:
    assert creation_time_to_epoch_ms("1970-01-01T00:00:00Z") == 0
    assert creation_time_to_epoch_ms("1970-01-01T00:00:01Z") == 1000


def test_creation_time_nanoseconds_truncate_to_ms() -> None:
    # OBS 常見奈秒精度 → 截到毫秒。
    assert creation_time_to_epoch_ms("1970-01-01T00:00:00.123456789Z") == 123


def test_creation_time_offset_and_naive() -> None:
    assert creation_time_to_epoch_ms("1970-01-01T08:00:00+08:00") == 0  # 同一瞬間
    assert creation_time_to_epoch_ms("1970-01-01T00:00:00+0000") == 0    # +0000 無冒號
    assert creation_time_to_epoch_ms("1970-01-01T00:00:00") == 0         # 無時區視為 UTC


def test_creation_time_invalid_raises() -> None:
    with pytest.raises(ValueError):
        creation_time_to_epoch_ms("not-a-time")


# --- apply_correction ----------------------------------------------------

def _hl(**over) -> dict:
    h = {
        "highlight_id": "hl-001",
        "start_ms": 23000,
        "end_ms": 78000,
        "score": 0.9,
        "signal": "chat_volume",
        "status": "candidate",
        "chat_window": {"start_ms": 23000, "end_ms": 78000},
        "selected": True,
        "locked": False,
    }
    h.update(over)
    return h


def test_offset_shifts_window_and_marks_shifted() -> None:
    out = apply_correction(_hl(), offset_ms=-20000, corrected_by="editor-1", note="往前抓 20s", source_duration_ms=240000)
    assert out["start_ms"] == 3000 and out["end_ms"] == 58000
    assert out["status"] == "shifted"
    assert out["correction"]["applied"] is True
    assert out["correction"]["offset_ms"] == -20000
    assert out["correction"]["corrected_by"] == "editor-1"


def test_offset_clamps_lower_bound_preserving_length() -> None:
    out = apply_correction(_hl(start_ms=5000, end_ms=20000), offset_ms=-20000, source_duration_ms=240000)
    assert out["start_ms"] == 0 and out["end_ms"] == 15000  # length 15000 preserved


def test_offset_clamps_upper_bound() -> None:
    out = apply_correction(_hl(start_ms=200000, end_ms=230000), offset_ms=50000, source_duration_ms=240000)
    assert out["end_ms"] == 240000 and out["start_ms"] == 210000  # length 30000 preserved


def test_offset_accumulates() -> None:
    once = apply_correction(_hl(), offset_ms=-10000, source_duration_ms=240000)   # 23000 -> 13000
    twice = apply_correction(once, offset_ms=-5000, source_duration_ms=240000)    # 13000 -> 8000
    assert twice["correction"]["offset_ms"] == -15000  # 累加
    assert twice["start_ms"] == 8000 and twice["end_ms"] == 63000


def test_exclude_and_unexclude() -> None:
    excluded = apply_correction(_hl(), exclude=True, note="僅為開場自我介紹")
    assert excluded["status"] == "excluded"
    assert excluded["selected"] is False
    assert excluded["excluded_reason"] == "僅為開場自我介紹"

    back = apply_correction(excluded, exclude=False)
    assert back["status"] == "included"
    assert back["selected"] is True
    assert "excluded_reason" not in back


def test_lock_and_select_passthrough() -> None:
    out = apply_correction(_hl(), locked=True, selected=False)
    assert out["locked"] is True and out["selected"] is False


def test_input_not_mutated() -> None:
    original = _hl()
    snapshot = dict(original)
    apply_correction(original, offset_ms=-20000, source_duration_ms=240000)
    assert original == snapshot  # 純函式：不動輸入


def test_corrected_highlight_still_valid_contract() -> None:
    out = apply_correction(_hl(), offset_ms=-20000, corrected_by="e", note="n", source_duration_ms=240000)
    envelope = {
        "schema_version": "highlights.v1",
        "project_id": "project-123",
        "source_duration_ms": 240000,
        "highlights": [out],
    }
    validate_highlights(envelope)  # raises if invalid
