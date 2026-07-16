"""Regression tests for the pipeline timing / input-routing fixes.

Covers the four logic holes that degraded the final artifact:
  A  video timebase bridge (MP4 creation_time → video_start_epoch_ms)
  B  composer no longer lets the fake fixed-ratio annotation split drive cuts
     when there is no real punch signal (chat_window)
  D  subtitles slice per clip's source sub-range (no duplicate / no stretch)
  E  refine only proposes offsets when the highlight window aligns with the
     video-relative transcript (no global-peak yank; disabled when chat-relative)
"""
from __future__ import annotations

import struct

from analysis.refine import propose_punchline_offsets, run_refine
from app.repository import InMemoryProjectRepository
from app.state import ProjectState
from app.storage import StubStorage
from app.settings import get_settings
from app.video_timebase import (
    _MAC_EPOCH_OFFSET,
    _parse_mvhd_creation_epoch_ms,
    extract_creation_epoch_ms,
)
from creative.subtitle import CHAT_CAPTION_MAX_MS, plan_subtitles
from workers import composer_worker

VIDEO_START_MS = 1752487200000  # 2025-07-14T14:00:00Z


# --- A: MP4 creation_time extraction ---------------------------------------

def _mvhd_box(unix_ms: int, version: int = 0) -> bytes:
    secs = unix_ms // 1000 + _MAC_EPOCH_OFFSET
    if version == 1:
        payload = bytes([1, 0, 0, 0]) + struct.pack(">Q", secs)
    else:
        payload = bytes([0, 0, 0, 0]) + struct.pack(">I", secs)
    return struct.pack(">I", 8 + len(payload)) + b"mvhd" + payload


def _mp4(unix_ms: int, version: int = 0, prefix: bytes = b"") -> bytes:
    ftyp = struct.pack(">I", 16) + b"ftyp" + b"isom" + b"\x00\x00\x00\x00"
    return prefix + ftyp + _mvhd_box(unix_ms, version)


def test_mvhd_v0_and_v1_parse_to_epoch_ms() -> None:
    assert _parse_mvhd_creation_epoch_ms(_mp4(VIDEO_START_MS, 0)) == VIDEO_START_MS
    assert _parse_mvhd_creation_epoch_ms(_mp4(VIDEO_START_MS, 1)) == VIDEO_START_MS


def test_mvhd_absent_or_zero_returns_none() -> None:
    assert _parse_mvhd_creation_epoch_ms(b"no moov here at all") is None
    # creation_time == 0 (some muxers) → unusable, not epoch 0.
    zero_box = struct.pack(">I", 20) + b"mvhd" + bytes([0, 0, 0, 0]) + struct.pack(">I", 0)
    assert _parse_mvhd_creation_epoch_ms(zero_box) is None


def test_extract_creation_epoch_head_and_tail() -> None:
    settings = get_settings()
    storage = StubStorage(settings)
    bucket, key = "raw", "tenant=demo/project=p/source/source.mp4"

    # faststart: moov near the head.
    storage.put_bytes(bucket, key, _mp4(VIDEO_START_MS), "video/mp4")
    assert extract_creation_epoch_ms(storage, bucket, key) == VIDEO_START_MS

    # OBS recording: moov at the tail, past the 256KB head window.
    big = _mp4(VIDEO_START_MS, prefix=b"\x00" * (300 * 1024))
    storage.put_bytes(bucket, key, big, "video/mp4")
    assert extract_creation_epoch_ms(storage, bucket, key) == VIDEO_START_MS


def test_get_range_stub_reads_slice() -> None:
    storage = StubStorage(get_settings())
    storage.put_bytes("b", "k", b"0123456789", "application/octet-stream")
    assert storage.get_range("b", "k", 2, 4) == b"2345"
    assert storage.get_range("b", "k", 8, 100) == b"89"  # clipped at EOF


# --- B: composer does not let fake structure drive cuts --------------------

def _seed_project(repo: InMemoryProjectRepository, pid: str, target_ms: int) -> None:
    repo.create_project({
        "project_id": pid,
        "tenant_id": "demo",
        "user_id": "t",
        "status": ProjectState.COMPOSING.value,
        "target_duration_ms": target_ms,
        "latest_timeline_version": 0,
    })


def test_no_signal_highlights_use_scoregreedy_not_beat_split() -> None:
    # Transcribe-style highlights (NO chat_window). With a tight target the top
    # highlight must be trimmed. The fix: fall back to ScoreGreedy (front-trim,
    # ONE clip keeping the tail payoff) — NOT the NarrativeBeat setup+dead-tail
    # split that the fixed-ratio annotation would otherwise force.
    repo = InMemoryProjectRepository()
    pid = "proj-nosignal"
    _seed_project(repo, pid, target_ms=20000)
    repo.put_highlights(pid, [
        {"highlight_id": "hl-001", "start_ms": 0, "end_ms": 30000, "score": 0.9,
         "selected": True, "transcript": "abc"},
        {"highlight_id": "hl-002", "start_ms": 40000, "end_ms": 70000, "score": 0.5,
         "selected": True, "transcript": "def"},
    ])

    tl = composer_worker.run(repo, pid)
    top = [c for c in tl["clips"] if c["highlight_id"] == "hl-001"]
    assert len(top) == 1, "no-signal highlight must not be split into setup+tail"
    assert top[0]["source_end_ms"] == 30000  # tail payoff preserved
    assert top[0]["source_start_ms"] == 10000  # front-trimmed to fit 20s


def test_chat_signal_highlights_still_use_beats() -> None:
    # WITH chat_window → NarrativeBeat is allowed (punchline aligned to the signal).
    repo = InMemoryProjectRepository()
    pid = "proj-signal"
    _seed_project(repo, pid, target_ms=20000)
    repo.put_highlights(pid, [
        {"highlight_id": "hl-001", "start_ms": 0, "end_ms": 30000, "score": 0.9,
         "selected": True, "signal": "chat_volume",
         "chat_window": {"start_ms": 22000, "end_ms": 27000}},
    ])
    tl = composer_worker.run(repo, pid)
    clips = [c for c in tl["clips"] if c["highlight_id"] == "hl-001"]
    assert clips  # composes
    # punchline region (chat_window) is covered by some clip (never truncated).
    assert any(c["source_start_ms"] <= 22000 and c["source_end_ms"] >= 27000 for c in clips)


# --- D: subtitle per-clip source sub-range ---------------------------------

def _sub_settings() -> dict:
    return {"enabled": True, "mode": "caption"}  # isolate Tier-1 captions


def test_split_highlight_caption_not_duplicated() -> None:
    # One highlight (5 sentences) cut into a setup clip + a punchline clip with a
    # dropped middle. Each clip must caption only its own source sub-range; the
    # dropped-middle lines appear in neither, and nothing is duplicated.
    highlights = [{
        "highlight_id": "hl-001", "start_ms": 0, "end_ms": 20000,
        "transcript": "第一句。第二句。第三句。第四句。第五句。",
    }]
    timeline = {"clips": [
        {"timeline_order": 1, "highlight_id": "hl-001",
         "source_start_ms": 0, "source_end_ms": 6000,
         "timeline_start_ms": 0, "timeline_end_ms": 6000},
        {"timeline_order": 2, "highlight_id": "hl-001",
         "source_start_ms": 14000, "source_end_ms": 20000,
         "timeline_start_ms": 6000, "timeline_end_ms": 12000},
    ]}
    sub = plan_subtitles(timeline, highlights, "p", "r", settings=_sub_settings())
    captions = [c["text"] for c in sub["cues"] if c["kind"] == "caption"]

    assert len(captions) == len(set(captions)), "captions must not be duplicated"
    assert "第二句。" not in captions and "第三句。" not in captions  # dropped middle
    assert "第一句。" in captions  # setup portion
    assert "第五句。" in captions  # punchline portion


def test_chat_only_caption_is_brief_not_stretched() -> None:
    # No transcript (chat-driven highlight): the suggested_title is shown briefly
    # at the clip start, NOT stretched across the whole 15s clip.
    highlights = [{"highlight_id": "hl-001", "start_ms": 0, "end_ms": 15000,
                   "suggested_title": "太扯了吧"}]
    timeline = {"clips": [
        {"timeline_order": 1, "highlight_id": "hl-001",
         "source_start_ms": 0, "source_end_ms": 15000,
         "timeline_start_ms": 0, "timeline_end_ms": 15000},
    ]}
    sub = plan_subtitles(timeline, highlights, "p", "r", settings=_sub_settings())
    caps = [c for c in sub["cues"] if c["kind"] == "caption"]
    assert len(caps) == 1
    assert caps[0]["start_ms"] == 0
    assert caps[0]["end_ms"] - caps[0]["start_ms"] <= CHAT_CAPTION_MAX_MS


def test_full_clip_caption_lays_out_all_sentences() -> None:
    # Single clip covering the whole highlight → all sentences laid out (backward
    # compatible with the pre-fix behaviour).
    highlights = [{"highlight_id": "hl-001", "start_ms": 0, "end_ms": 10000,
                   "transcript": "甲甲甲。乙乙乙。"}]
    timeline = {"clips": [
        {"timeline_order": 1, "highlight_id": "hl-001",
         "source_start_ms": 0, "source_end_ms": 10000,
         "timeline_start_ms": 0, "timeline_end_ms": 10000},
    ]}
    sub = plan_subtitles(timeline, highlights, "p", "r", settings=_sub_settings())
    caps = [c for c in sub["cues"] if c["kind"] == "caption"]
    assert [c["text"] for c in caps] == ["甲甲甲。", "乙乙乙。"]
    assert caps[0]["start_ms"] == 0
    assert caps[-1]["end_ms"] == 10000


# --- E: refine offset gating ------------------------------------------------

def _transcript() -> dict:
    return {"segments": [
        {"segment_id": "s1", "start_ms": 0, "end_ms": 10000, "text": "嗨大家好"},
        {"segment_id": "s2", "start_ms": 30000, "end_ms": 40000, "text": "太扯了太神了！哈哈"},
    ]}


def test_refine_skips_offsets_when_disabled() -> None:
    hls = [{"highlight_id": "hl-001", "start_ms": 25000, "end_ms": 45000, "selected": True}]
    ann = {"schema_version": "annotations.v1", "project_id": "p",
           "annotation_version": "annotation-rule-1.0.0", "annotations": [], "created_at": "2026-01-01T00:00:00Z"}
    res = run_refine(hls, ann, _transcript(), narrative_reviewer=_FakeReviewer(), propose_offsets=False)
    assert res["proposed_offsets"] == []


def test_refine_proposes_when_overlapping_and_enabled() -> None:
    hls = [{"highlight_id": "hl-001", "start_ms": 25000, "end_ms": 45000, "selected": True}]
    props = propose_punchline_offsets(_transcript(), hls)
    assert len(props) == 1  # s2 overlaps → local peak proposal (no global yank)


class _FakeReviewer:
    def enrich(self, context: dict) -> dict:
        return {}
