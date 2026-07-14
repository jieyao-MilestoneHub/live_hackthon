"""Contract + invariant tests for the Creative Planning pure functions."""
from __future__ import annotations

import pytest

from analysis.validate import (
    load_sample,
    validate_effects,
    validate_render_spec,
    validate_subtitle,
)
from creative import RESOLUTION_BY_ASPECT, build_render_spec, plan_effects, plan_subtitles


@pytest.fixture()
def timeline() -> dict:
    return load_sample("timeline.sample.json")


@pytest.fixture()
def highlights() -> list[dict]:
    return load_sample("highlights.sample.json")["highlights"]


def test_subtitle_valid_and_within_timeline(timeline, highlights) -> None:
    sub = plan_subtitles(timeline, highlights, "project-123", "render-1")
    validate_subtitle(sub)
    assert sub["cues"]
    total = timeline["actual_duration_ms"]
    prev_start = -1
    for c in sub["cues"]:
        assert 0 <= c["start_ms"] < c["end_ms"] <= total
        assert c["start_ms"] >= prev_start  # non-decreasing
        prev_start = c["start_ms"]


def test_effects_valid_and_deterministic(timeline) -> None:
    a = plan_effects(timeline, 834710, "project-123", "render-1")
    b = plan_effects(timeline, 834710, "project-123", "render-1")
    validate_effects(a)
    assert a == b  # same seed -> identical output (reproducible retries)
    assert plan_effects(timeline, 999, "project-123", "render-1") != a  # different seed differs


def test_effect_oneof_shapes(timeline) -> None:
    plan = plan_effects(timeline, 42, "p", "r")
    for e in plan["effects"]:
        if "at_ms" in e:  # point variant
            assert not {"start_ms", "end_ms", "strength"} & e.keys()
        else:  # ranged variant
            assert "start_ms" in e and "end_ms" in e and "at_ms" not in e
    # a >1-clip timeline gets an internal flash transition
    if len(timeline["clips"]) > 1:
        assert any(e["type"] == "flash_transition" for e in plan["effects"])


def test_render_spec_valid_resolution_and_keys(timeline) -> None:
    project = {
        "project_id": "project-123",
        "tenant_id": "demo",
        "source_bucket": "video-editor-raw-dev",
        "source_key": "tenant=demo/project=project-123/source/source.mp4",
    }
    inputs = {"timeline_key": "t.json", "subtitle_key": "s.json", "effect_plan_key": "e.json"}
    outputs = {"video_key": "v.mp4", "preview_key": "p.mp4", "thumbnail_key": "th.jpg"}
    spec = build_render_spec(project, timeline, "render-1", 834710, inputs, outputs)
    validate_render_spec(spec)

    w, h = RESOLUTION_BY_ASPECT[timeline["aspect_ratio"]]
    assert spec["resolution"] == {"width": w, "height": h}
    assert spec["effect_seed"] == 834710
    assert spec["timeline_version"] == timeline["version"]
    assert spec["inputs"] == inputs
    assert spec["outputs"] == outputs
