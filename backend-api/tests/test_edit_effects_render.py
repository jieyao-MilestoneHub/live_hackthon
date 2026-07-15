"""Step 3：effects.v1 / emphasis → filtergraph 接線。

純字串測試（座標映射、確定性、子片段 gate）＋一個真 ffmpeg 煙霧測試（合成小影片
實跑我手寫的 crop/eq/ASS filtergraph，抓語法錯誤；ffmpeg 不可用時 skip）。
"""
from __future__ import annotations

import subprocess

import pytest

from workers.render import ffmpeg_encoder as fe
from workers.render_worker import EncodeInputs, _fmt_ts_ass, _to_ass


# --- pure-string helpers ---------------------------------------------------

def test_fmt_ts_ass():
    assert _fmt_ts_ass(0) == "0:00:00.00"
    assert _fmt_ts_ass(3_661_120) == "1:01:01.12"  # 1h1m1s120ms → cs=12


def test_to_ass_animates_emphasis_only():
    ass = _to_ass({"cues": [{"start_ms": 0, "end_ms": 1500, "text": "這太扯了吧", "emphasis_words": ["太扯"]}]})
    assert "[Script Info]" in ass and "PlayResX: 1080" in ass
    assert "Dialogue: 0,0:00:00.00,0:00:01.50,Default" in ass
    assert "\\fscx145" in ass and "太扯" in ass          # emphasis 有 pop 動畫
    # 非爆點字不被包 override
    plain = _to_ass({"cues": [{"start_ms": 0, "end_ms": 500, "text": "普通句子"}]})
    assert "\\fscx145" not in plain


def test_geometry_vf_shapes():
    z = fe._geometry_vf("zoom_in", 0.1, 0.3, 1.2, 0.9, 1080, 1920, 7, 1)
    assert z.startswith("crop=") and z.endswith(",scale=1080:1920")
    assert "between(t,0.300,1.200)" in z and "0.1000" in z
    sh = fe._geometry_vf("shake", 0.08, 0.0, 0.6, 0.6, 1080, 1920, 7, 2)
    assert "sin(2*PI*12" in sh


def test_flash_vf_shape():
    f = fe._flash_vf(1.5, 0.24)
    assert "eq=brightness=" in f and "eval=frame" in f and "between(t,1.500,1.500+0.240)" in f


def test_pan_direction_deterministic():
    assert fe._pan_direction(7, 1) == fe._pan_direction(7, 1)
    dirs = {fe._pan_direction(s, o) for s in range(20) for o in range(4)}
    assert dirs <= {"lr", "rl", "tb", "bt"} and len(dirs) > 1


def test_clip_filters_maps_subsegment_and_gates_boundary():
    clip = {"timeline_order": 2, "timeline_start_ms": 1500, "timeline_end_ms": 3000}
    effects = {"effects": [
        {"type": "zoom_in", "start_ms": 1800, "end_ms": 2400, "strength": 0.1},  # 子片段（中段）
        {"type": "pan", "start_ms": 0, "end_ms": 1400},                          # 別的 clip → 跳過
        {"type": "flash_transition", "at_ms": 1500, "duration_ms": 240},         # 本 clip 開頭
        {"type": "flash_transition", "at_ms": 5000},                             # 超出 → 跳過
    ]}
    out = fe._clip_filters(effects, clip, 1080, 1920, 7)
    assert len(out) == 2                       # zoom（子片段）+ 一個 flash
    geom = next(x for x in out if x.startswith("crop="))
    # 中段特效：clip-local 起點 = (1800-1500)/1000 = 0.300（不是 0）
    assert "between(t,0.300,0.900)" in geom
    assert any("eq=brightness=" in x for x in out)


def test_clip_filters_empty_when_no_effects():
    clip = {"timeline_order": 1, "timeline_start_ms": 0, "timeline_end_ms": 1500}
    assert fe._clip_filters({}, clip, 1080, 1920, 0) == []
    assert fe._clip_filters(None, clip, 1080, 1920, 0) == []


# --- real ffmpeg smoke test ------------------------------------------------

def _ffmpeg_ok() -> bool:
    try:
        subprocess.run([fe._ffmpeg_bin(), "-version"], capture_output=True, timeout=15)
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _ffmpeg_ok(), reason="ffmpeg binary not available")
def test_ffmpeg_encoder_end_to_end_with_effects_and_ass(tmp_path):
    ff = fe._ffmpeg_bin()
    src = str(tmp_path / "source.mp4")
    make = subprocess.run(
        [ff, "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src],
        capture_output=True,
    )
    if make.returncode != 0:
        pytest.skip("cannot synthesize test source")

    timeline = {"clips": [
        {"timeline_order": 1, "highlight_id": "h1", "source_start_ms": 0, "source_end_ms": 1500,
         "timeline_start_ms": 0, "timeline_end_ms": 1500},
        {"timeline_order": 2, "highlight_id": "h2", "source_start_ms": 1500, "source_end_ms": 3000,
         "timeline_start_ms": 1500, "timeline_end_ms": 3000},
    ], "actual_duration_ms": 3000, "aspect_ratio": "9:16"}
    effects = {"schema_version": "effects.v1", "effect_seed": 7, "effects": [
        {"type": "zoom_in", "start_ms": 300, "end_ms": 1200, "strength": 0.1},  # 子片段
        {"type": "shake", "start_ms": 1600, "end_ms": 2200, "strength": 0.08},  # 子片段
        {"type": "flash_transition", "at_ms": 1500, "duration_ms": 240},        # 邊界
    ]}
    subtitle = {"cues": [{"start_ms": 0, "end_ms": 1500, "text": "太扯了", "emphasis_words": ["太扯"]}]}
    render_spec = {"resolution": {"width": 180, "height": 320}, "encode": {"crf": 30, "fps": 24},
                   "audio": {"normalize": False}, "effect_seed": 7}

    inputs = EncodeInputs(
        render_id="r1", source=None, source_path=src, timeline=timeline,
        subtitle_vtt="WEBVTT\n\n", subtitle_ass=_to_ass(subtitle),
        effects=effects, render_spec=render_spec,
    )
    try:
        media = fe.FFmpegEncoder().encode(inputs)
    except RuntimeError as exc:  # limited ffmpeg build (no libass) → skip, not fail
        msg = str(exc).lower()
        if "subtitles" in msg or "libass" in msg or "no such filter" in msg:
            pytest.skip(f"ffmpeg lacks a required filter: {exc}")
        raise
    assert b"ftyp" in media["final"][:64]
    assert len(media["final"]) > 1000
