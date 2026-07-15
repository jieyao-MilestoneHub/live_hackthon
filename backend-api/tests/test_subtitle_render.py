"""ASS 字幕 renderer：樣式（字型/顏色/邊框/位置）+ keyword 出現動畫（不跑 FFmpeg）。"""
from __future__ import annotations

from analysis.validate import load_sample
from workers.render.subtitle_render import _ass_time, _hex_to_ass, to_ass


def test_hex_to_ass_color() -> None:
    assert _hex_to_ass("#FFFFFF") == "&H00FFFFFF"
    assert _hex_to_ass("#FFE23D") == "&H003DE2FF"  # RGB → &H00 BGR


def test_ass_time_centiseconds() -> None:
    assert _ass_time(0) == "0:00:00.00"
    assert _ass_time(11500) == "0:00:11.50"
    assert _ass_time(3_661_230) == "1:01:01.23"


def test_ass_has_both_styles_and_resolution() -> None:
    sub = load_sample("subtitle.sample.json")
    ass = to_ass(sub, 1080, 1920)
    assert "[V4+ Styles]" in ass
    assert "Style: Caption," in ass and "Style: Keyword," in ass
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    # keyword preset 強調黃 → &H003DE2FF 出現在 Keyword 樣式行。
    assert "&H003DE2FF" in ass


def test_keyword_dialogue_carries_animation_tag() -> None:
    sub = load_sample("subtitle.sample.json")
    ass = to_ass(sub, 1080, 1920)
    kw_lines = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:") and ",Keyword," in ln]
    cap_lines = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:") and ",Caption," in ln]
    assert kw_lines and cap_lines
    assert all("\\fad" in ln for ln in kw_lines)          # 有出現動畫
    assert all("\\fad" not in ln for ln in cap_lines)     # caption 無動畫


def test_ass_falls_back_to_preset_when_style_missing() -> None:
    sub = {"schema_version": "subtitle.v1", "language": "zh-TW",
           "cues": [{"start_ms": 0, "end_ms": 1000, "text": "嗨", "kind": "caption"}]}
    ass = to_ass(sub, 1080, 1920)
    assert "Style: Caption," in ass and "Style: Keyword," in ass  # preset 補齊
    assert "Dialogue: 0,0:00:00.00,0:00:01.00,Caption,,0,0,0,,嗨" in ass
