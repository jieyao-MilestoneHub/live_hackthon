"""兩層字幕：逐字稿 caption + 爆點 keyword（含樣式/動畫），對齊 timeline。"""
from __future__ import annotations

from analysis.annotations import build_annotations
from analysis.validate import load_sample, validate_subtitle
from composer import compose_timeline
from creative import plan_subtitles

PID = "project-123"


def _fixture():
    hls = load_sample("highlights.sample.json")["highlights"]
    ann = build_annotations(hls, project_id=PID)
    tl = compose_timeline(PID, hls, 60000, annotations=ann)  # 新組片：clips 含 punchline
    return hls, ann, tl


def test_two_layers_present_and_valid() -> None:
    hls, ann, tl = _fixture()
    sub = plan_subtitles(tl, hls, PID, "render-1", annotations=ann)
    validate_subtitle(sub)
    kinds = {c.get("kind") for c in sub["cues"]}
    assert "caption" in kinds and "keyword" in kinds
    # 兩組樣式 preset 都在。
    assert "caption" in sub["style"] and "keyword" in sub["style"]


def test_cues_within_timeline_and_nondecreasing() -> None:
    hls, ann, tl = _fixture()
    total = tl["actual_duration_ms"]
    sub = plan_subtitles(tl, hls, PID, "render-1", annotations=ann)
    prev = -1
    for c in sub["cues"]:
        assert 0 <= c["start_ms"] < c["end_ms"] <= total
        assert c["start_ms"] >= prev
        prev = c["start_ms"]


def test_keyword_cue_has_animation_and_lands_on_punchline() -> None:
    hls, ann, tl = _fixture()
    sub = plan_subtitles(tl, hls, PID, "render-1", annotations=ann)
    kw = [c for c in sub["cues"] if c.get("kind") == "keyword"]
    assert kw
    for c in kw:
        assert c.get("animation", {}).get("type")   # 有出現動畫
        assert len(c["text"]) <= 6                    # 精簡
        # 落在某個「含 punchline」的 clip 的 timeline 區間內。
        assert any(
            clip["timeline_start_ms"] <= c["start_ms"] < clip["timeline_end_ms"]
            for clip in tl["clips"]
        )


def test_mode_switches_layers() -> None:
    hls, ann, tl = _fixture()
    only_cap = plan_subtitles(tl, hls, PID, "r", annotations=ann, settings={"enabled": True, "mode": "caption"})
    only_kw = plan_subtitles(tl, hls, PID, "r", annotations=ann, settings={"enabled": True, "mode": "keyword"})
    assert {c["kind"] for c in only_cap["cues"]} == {"caption"}
    assert {c["kind"] for c in only_kw["cues"]} == {"keyword"}


def test_disabled_yields_no_cues() -> None:
    hls, ann, tl = _fixture()
    sub = plan_subtitles(tl, hls, PID, "r", annotations=ann, settings={"enabled": False})
    validate_subtitle(sub)
    assert sub["cues"] == []


def test_style_override_merges() -> None:
    hls, ann, tl = _fixture()
    sub = plan_subtitles(
        tl, hls, PID, "r", annotations=ann,
        settings={"enabled": True, "style": {"caption": {"font_size": 60}, "font_family": "Some Font"}},
    )
    assert sub["style"]["caption"]["font_size"] == 60          # per-kind 覆寫
    assert sub["style"]["caption"]["font_family"] == "Some Font"  # flat 覆寫套用兩層
    assert sub["style"]["keyword"]["font_family"] == "Some Font"


def test_default_extractor_no_annotations_still_places_keyword() -> None:
    # 無 annotations 時 keyword 落在 clip 後段（fallback），仍為兩層。
    hls = load_sample("highlights.sample.json")["highlights"]
    tl = compose_timeline(PID, hls, 30000)
    sub = plan_subtitles(tl, hls, PID, "r")
    validate_subtitle(sub)
    assert any(c.get("kind") == "keyword" for c in sub["cues"])
