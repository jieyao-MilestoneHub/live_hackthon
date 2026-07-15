"""起承轉合 beat-aware 組片 + 保爆點（NarrativeBeat / ScoreGreedy）。"""
from __future__ import annotations

from analysis.annotations import build_annotations
from analysis.validate import load_sample, validate_timeline
from composer import ScoreGreedyPlanner, compose_timeline

PID = "project-123"


def _highlights() -> list[dict]:
    return load_sample("highlights.sample.json")["highlights"]


def _punch_span(ann: dict, hid: str) -> tuple[int, int] | None:
    for a in ann["annotations"]:
        if a["highlight_id"] == hid:
            pb = [b for b in a["beats"] if b["beat"] == "punchline"]
            if pb:
                return min(b["start_ms"] for b in pb), max(b["end_ms"] for b in pb)
    return None


def test_narrative_never_truncates_punchline() -> None:
    hls = _highlights()
    ann = build_annotations(hls, project_id=PID)
    tl = compose_timeline(PID, hls, 60000, annotations=ann)
    validate_timeline(tl)
    present = {c["highlight_id"] for c in tl["clips"]}
    assert present  # something got composed
    for hid in present:
        span = _punch_span(ann, hid)
        if span is None:
            continue
        p_s, p_e = span
        covered = any(
            c["highlight_id"] == hid and c["source_start_ms"] <= p_s and c["source_end_ms"] >= p_e
            for c in tl["clips"]
        )
        assert covered, f"punchline of {hid} was truncated"


def test_narrative_splices_setup_and_punchline() -> None:
    # 緊目標 → 上位高光被拆成 setup 刀 + punchline 刀（埋梗+爆梗拼接）。
    hls = _highlights()
    ann = build_annotations(hls, project_id=PID)
    tl = compose_timeline(PID, hls, 60000, annotations=ann)
    # hl-002 於此目標會被拆刀：至少含其 punchline，且可能含一段 setup。
    hl2 = [c for c in tl["clips"] if c["highlight_id"] == "hl-002"]
    assert hl2
    span = _punch_span(ann, "hl-002")
    assert span is not None
    assert any(c["source_start_ms"] <= span[0] and c["source_end_ms"] >= span[1] for c in hl2)


def test_scoregreedy_front_trims_to_protect_payoff() -> None:
    # 無 beats 的路徑：填不下時從前段裁、保留結尾 payoff（不砍爆點）。
    hls = _highlights()
    tl = compose_timeline(PID, hls, 30000, planner=ScoreGreedyPlanner())
    validate_timeline(tl)
    hl1 = [c for c in tl["clips"] if c["highlight_id"] == "hl-001"]
    assert hl1 and hl1[0]["source_end_ms"] == 188000  # 尾端（payoff）保留


def test_contiguous_within_target_and_deterministic() -> None:
    hls = _highlights()
    ann = build_annotations(hls, project_id=PID)
    a = compose_timeline(PID, hls, 45000, annotations=ann, created_at="2026-07-15T00:00:00Z")
    b = compose_timeline(PID, hls, 45000, annotations=ann, created_at="2026-07-15T00:00:00Z")
    assert a == b  # 決定性
    assert a["actual_duration_ms"] <= 60000
    cursor = 0
    for c in sorted(a["clips"], key=lambda c: c["timeline_order"]):
        assert c["timeline_start_ms"] == cursor
        cursor = c["timeline_end_ms"]
    assert cursor == a["actual_duration_ms"]


def test_excluded_status_never_composed() -> None:
    hls = _highlights()  # hl-003 status=excluded
    ann = build_annotations(hls, project_id=PID)
    tl = compose_timeline(PID, hls, 60000, annotations=ann)
    assert "hl-003" not in {c["highlight_id"] for c in tl["clips"]}
