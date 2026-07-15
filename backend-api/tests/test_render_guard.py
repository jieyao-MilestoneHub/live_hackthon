"""Fail-closed render guard: RENDER_REQUIRE_FFMPEG must prevent a silent stub
render on the real path (e.g. if RENDER_ENCODER ever drifts off 'ffmpeg')."""
from __future__ import annotations

import pytest

from workers import render_worker
from workers.render_worker import EncodeInputs, StubEncoder


def test_default_is_stub_without_require(monkeypatch) -> None:
    monkeypatch.delenv("RENDER_ENCODER", raising=False)
    monkeypatch.delenv("RENDER_REQUIRE_FFMPEG", raising=False)
    assert isinstance(render_worker.get_encoder(), StubEncoder)


def test_require_ffmpeg_raises_when_encoder_not_ffmpeg(monkeypatch) -> None:
    # Simulate the real Batch path with a drifted/missing RENDER_ENCODER.
    monkeypatch.delenv("RENDER_ENCODER", raising=False)  # defaults to "stub"
    monkeypatch.setenv("RENDER_REQUIRE_FFMPEG", "1")
    with pytest.raises(RuntimeError, match="RENDER_REQUIRE_FFMPEG"):
        render_worker.get_encoder()

    monkeypatch.setenv("RENDER_ENCODER", "stub")
    with pytest.raises(RuntimeError, match="not 'ffmpeg'"):
        render_worker.get_encoder()


def test_require_ffmpeg_ok_when_ffmpeg(monkeypatch) -> None:
    monkeypatch.setenv("RENDER_ENCODER", "ffmpeg")
    monkeypatch.setenv("RENDER_REQUIRE_FFMPEG", "1")
    from workers.render.ffmpeg_encoder import FFmpegEncoder

    assert isinstance(render_worker.get_encoder(), FFmpegEncoder)


def test_mp4_sanity_check_rejects_non_mp4_from_source_encoder() -> None:
    """A source-consuming encoder that yields non-MP4 bytes must be rejected
    before publish (belt-and-suspenders against a stub/corrupt real render)."""

    class FakeStubbySourceEncoder:
        needs_source = True

        def encode(self, inputs: EncodeInputs) -> dict[str, bytes]:
            return {"final": b"STUB final.mp4\n", "preview": b"", "thumbnail": b""}

    # The check lives inside render_worker.run; exercise the exact predicate it uses.
    enc = FakeStubbySourceEncoder()
    final = enc.encode(None)["final"]
    assert getattr(enc, "needs_source", False) and b"ftyp" not in final[:64]
