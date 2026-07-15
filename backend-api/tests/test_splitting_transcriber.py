"""Unit tests for SplittingTranscriber — delegate vs split, manifest, poll/merge.

All collaborators (inner transcriber, segmenter, storage) are fakes; no AWS, no
network. Covers logic / boundary / error / object-state.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.aws.media_segmenter import SegmentInfo
from app.aws.splitting_transcriber import SplittingTranscriber


class _FakeInner:
    def __init__(self, poll_map=None):
        self.start_calls: list[tuple[str, str]] = []
        self.poll_calls: list[str] = []
        self._poll_map = poll_map or {}

    def start_transcription(self, project_id, media_uri, *, language_code, max_speakers):
        self.start_calls.append((project_id, media_uri))

    def poll_transcription(self, project_id, *, language_code):
        self.poll_calls.append(project_id)
        return self._poll_map.get(project_id, {"status": "IN_PROGRESS", "transcript": None})

    def transcribe(self, *a, **k):  # unused here
        raise NotImplementedError


class _FakeSegmenter:
    def __init__(self, segments):
        self._segments = segments

    def segment(self, bucket, key, *, project_id, max_bytes, segment_cap_sec):
        return self._segments


class _FakeStorage:
    def __init__(self):
        self.jsons: dict[tuple[str, str], dict] = {}

    def put_json(self, bucket, key, doc):
        self.jsons[(bucket, key)] = doc
        return key

    def get_json(self, bucket, key):
        try:
            return self.jsons[(bucket, key)]
        except KeyError:
            raise KeyError(key) from None


def _seg(start_ms, end_ms, text="hi"):
    return {"segment_id": "seg_0001", "start_ms": start_ms, "end_ms": end_ms,
            "speaker": "spk_0", "text": text, "confidence": 0.9}


def _completed(segments):
    return {"status": "COMPLETED", "transcript": {
        "schema_version": "transcript.v1", "project_id": "child", "language_code": "zh-TW",
        "duration_ms": max((s["end_ms"] for s in segments), default=0),
        "segments": segments, "created_at": "2026-01-01T00:00:00Z"}}


def _build(inner, segments):
    return SplittingTranscriber(
        inner, _FakeSegmenter(segments), _FakeStorage(),
        SimpleNamespace(work_bucket="work"),
        SimpleNamespace(poll_max_attempts=3, poll_interval_sec=0),
    )


def _two_segments():
    return [
        SegmentInfo(0, "s3://work/transcript/p/segments/seg_000.mp4", 0, 600_000),
        SegmentInfo(1, "s3://work/transcript/p/segments/seg_001.mp4", 600_000, 600_000),
    ]


# --- logic -----------------------------------------------------------------

def test_large_source_starts_a_job_per_segment():
    inner = _FakeInner()
    st = _build(inner, _two_segments())

    st.start_transcription("p", "s3://raw/src.mp4", language_code="zh-TW", max_speakers=5)

    assert len(inner.start_calls) == 2


def test_child_jobs_use_derived_segment_ids():
    inner = _FakeInner()
    st = _build(inner, _two_segments())

    st.start_transcription("p", "s3://raw/src.mp4", language_code="zh-TW", max_speakers=5)

    assert inner.start_calls[0][0] == "p-seg000"


def test_split_manifest_is_persisted():
    inner = _FakeInner()
    st = _build(inner, _two_segments())

    st.start_transcription("p", "s3://raw/src.mp4", language_code="zh-TW", max_speakers=5)

    assert st._read_manifest("p")["split"] is True


def test_poll_merges_completed_segments():
    inner = _FakeInner()
    st = _build(inner, _two_segments())
    st.start_transcription("p", "s3://raw/src.mp4", language_code="zh-TW", max_speakers=5)
    inner._poll_map = {
        "p-seg000": _completed([_seg(0, 1000, "first")]),
        "p-seg001": _completed([_seg(0, 2000, "second")]),
    }

    result = st.poll_transcription("p", language_code="zh-TW")

    assert result["transcript"]["segments"][1]["start_ms"] == 600_000


# --- boundary --------------------------------------------------------------

def test_small_source_delegates_whole_file():
    inner = _FakeInner()
    st = _build(inner, [])  # segmenter says: no split

    st.start_transcription("p", "s3://raw/src.mp4", language_code="zh-TW", max_speakers=5)

    assert inner.start_calls == [("p", "s3://raw/src.mp4")]


def test_no_manifest_delegates_poll_to_inner():
    inner = _FakeInner(poll_map={"p": {"status": "IN_PROGRESS", "transcript": None}})
    st = _build(inner, [])

    st.poll_transcription("p", language_code="zh-TW")

    assert inner.poll_calls == ["p"]


# --- error / status aggregation -------------------------------------------

def test_any_segment_in_progress_keeps_whole_in_progress():
    inner = _FakeInner()
    st = _build(inner, _two_segments())
    st.start_transcription("p", "s3://raw/src.mp4", language_code="zh-TW", max_speakers=5)
    inner._poll_map = {"p-seg000": _completed([_seg(0, 1000)])}  # seg001 defaults IN_PROGRESS

    result = st.poll_transcription("p", language_code="zh-TW")

    assert result["status"] == "IN_PROGRESS"


def test_any_segment_failed_fails_the_whole():
    inner = _FakeInner()
    st = _build(inner, _two_segments())
    st.start_transcription("p", "s3://raw/src.mp4", language_code="zh-TW", max_speakers=5)
    inner._poll_map = {
        "p-seg000": _completed([_seg(0, 1000)]),
        "p-seg001": {"status": "FAILED", "transcript": None, "reason": "boom"},
    }

    result = st.poll_transcription("p", language_code="zh-TW")

    assert result["status"] == "FAILED"


# --- object-state ----------------------------------------------------------

def test_merged_transcript_carries_real_project_id():
    inner = _FakeInner()
    st = _build(inner, _two_segments())
    st.start_transcription("p", "s3://raw/src.mp4", language_code="zh-TW", max_speakers=5)
    inner._poll_map = {
        "p-seg000": _completed([_seg(0, 1000)]),
        "p-seg001": _completed([_seg(0, 2000)]),
    }

    result = st.poll_transcription("p", language_code="zh-TW")

    assert result["transcript"]["project_id"] == "p"
