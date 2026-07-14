"""Tests for the analysis bit-detection seam (stub + adapter + injectability)."""
from __future__ import annotations

import pytest

from analysis import BitDetector, StubBitDetector, bits_to_highlights, get_bit_detector
from analysis.validate import load_sample, validate_highlights
from app.state import ProjectState, assert_project_transition


@pytest.fixture()
def transcript() -> dict:
    return load_sample("transcript.sample.json")


def test_stub_bit_package_shape(transcript) -> None:
    pkg = StubBitDetector().detect(transcript)
    assert pkg["project_id"] == transcript["project_id"]
    assert pkg["source_duration_ms"] == transcript["duration_ms"]
    assert pkg["metadata"]["generated_by"] == "bit-stub-1.0.0"

    bits = pkg["bits"]
    assert bits, "stub should yield >=1 bit for the sample"
    ids = [b["bit_id"] for b in bits]
    assert len(ids) == len(set(ids))  # unique bit_ids
    for b in bits:
        s = b["setup"]
        assert isinstance(s["start_ms"], int) and isinstance(s["end_ms"], int)
        assert 0 <= s["start_ms"] < s["end_ms"]
        assert isinstance(b["payoffs"], list)
        for p in b["payoffs"]:
            assert isinstance(p["start_ms"], int) and p["start_ms"] < p["end_ms"]
        assert 0.0 <= b["score"] <= 1.0


def test_log_info_ignored_by_stub(transcript) -> None:
    a = StubBitDetector().detect(transcript)
    b = StubBitDetector().detect(transcript, log_info={"chat": "irrelevant"})
    assert a == b


def test_adapter_produces_valid_highlights(transcript) -> None:
    pkg = StubBitDetector().detect(transcript)
    hl = bits_to_highlights(pkg)
    validate_highlights(hl)
    assert hl["project_id"] == pkg["project_id"]
    assert hl["source_duration_ms"] == pkg["source_duration_ms"]
    assert len(hl["highlights"]) == len(pkg["bits"])
    for h in hl["highlights"]:
        assert h["start_ms"] < h["end_ms"]


def test_factory_returns_a_bit_detector() -> None:
    assert isinstance(get_bit_detector(), BitDetector)  # runtime_checkable Protocol


class _FakeDetector:
    """Stands in for the engineer's real detector (drop-in via the seam)."""

    def detect(self, transcript: dict, log_info=None) -> dict:  # noqa: ARG002
        return {
            "project_id": transcript["project_id"],
            "source_duration_ms": 100000,
            "bits": [{
                "bit_id": "bit-x",
                "setup": {"start_ms": 1000, "end_ms": 20000},
                "payoffs": [],
                "score": 0.7,
                "metadata": {"theme": "t", "transcript": "hello", "suggested_title": "T"},
            }],
            "metadata": {"generated_by": "fake-1.0.0"},
        }


def test_worker_uses_injected_detector(aws) -> None:
    from app.repository import get_repository
    from workers import analysis_worker

    repo = get_repository()
    pid = "project-inject"
    repo.create_project({
        "project_id": pid, "tenant_id": "demo", "user_id": "u",
        "status": ProjectState.CREATED.value, "target_duration_ms": 30000,
        "source_bucket": "b", "source_key": "k", "latest_timeline_version": 0,
    })
    for state in (ProjectState.UPLOAD_PENDING, ProjectState.UPLOADING, ProjectState.ANALYZING):
        assert_project_transition(ProjectState(repo.get_project(pid)["status"]), state)
        repo.update_project(pid, {"status": state.value})

    result = analysis_worker.run(
        repo, pid, load_sample("transcript.sample.json"), detector=_FakeDetector()
    )

    assert [h["highlight_id"] for h in result["highlights"]] == ["bit-x"]
    assert repo.get_project(pid)["source_duration_ms"] == 100000
    stored = repo.list_highlights(pid)
    assert len(stored) == 1 and stored[0]["highlight_id"] == "bit-x"
    assert repo.get_project(pid)["status"] == ProjectState.COMPOSING.value
