"""AI 精修純函式測試（階段 5–6）：propose_punchline_offsets / enrich_annotations。"""
from __future__ import annotations

from analysis.annotations import build_annotations
from analysis.refine import enrich_annotations, propose_punchline_offsets, run_refine
from analysis.validate import validate_annotations


class FakeNarrative:
    """確定性假 reviewer（注入 DIP）。"""

    def enrich(self, context: dict) -> dict:
        return {
            "description": f"D:{context['highlight_id']}",
            "dimension_texts": {d: f"T-{d}" for d in context["dimensions"]},
            "beat_lines": {str(o): f"L-{o}" for o in context["beats"]},
        }


def _transcript() -> dict:
    return {
        "schema_version": "transcript.v1",
        "project_id": "p",
        "language_code": "zh-TW",
        "duration_ms": 200000,
        "segments": [
            {"segment_id": "s1", "start_ms": 0, "end_ms": 10000, "speaker": "spk_0", "text": "嗨大家好"},
            {"segment_id": "s2", "start_ms": 30000, "end_ms": 40000, "speaker": "spk_0", "text": "太扯了太神了！哈哈"},
            {"segment_id": "s3", "start_ms": 80000, "end_ms": 90000, "speaker": "spk_1", "text": "嗯好的"},
        ],
    }


def _highlights() -> list[dict]:
    return [
        {"highlight_id": "hl-001", "start_ms": 25000, "end_ms": 60000, "score": 0.9,
         "status": "candidate", "selected": True, "suggested_title": "神操作",
         "provenance": {"chat_message_ids": []}},
        {"highlight_id": "hl-x", "start_ms": 0, "end_ms": 30000, "score": 0.3, "status": "excluded"},
    ]


# --- propose_punchline_offsets ------------------------------------------

def test_offset_picks_emotion_peak_segment() -> None:
    props = propose_punchline_offsets(_transcript(), _highlights())
    assert len(props) == 1  # excluded 被跳過
    p = props[0]
    assert p["highlight_id"] == "hl-001"
    # 峰為 s2（太扯太神！），proposed = 30000 - lead(2000) = 28000；offset = 28000-25000
    assert p["proposed_start_ms"] == 28000
    assert p["offset_ms"] == 3000
    assert "太扯" in p["evidence_text"]


def test_offset_custom_lead() -> None:
    p = propose_punchline_offsets(_transcript(), _highlights(), lead_ms=5000)[0]
    assert p["proposed_start_ms"] == 25000  # 30000-5000


def test_offset_no_overlap_uses_global_peak() -> None:
    hls = [{"highlight_id": "hl-far", "start_ms": 150000, "end_ms": 170000, "selected": True}]
    p = propose_punchline_offsets(_transcript(), hls)[0]
    # 無重疊 → 全域峰 s2 → proposed 28000
    assert p["proposed_start_ms"] == 28000
    assert p["offset_ms"] == 28000 - 150000


def test_offset_empty_transcript() -> None:
    assert propose_punchline_offsets({"segments": []}, _highlights()) == []


# --- enrich_annotations --------------------------------------------------

def _annotations() -> dict:
    hls = [{"highlight_id": "hl-001", "start_ms": 0, "end_ms": 90000, "score": 0.9,
            "status": "candidate", "selected": True, "suggested_title": "神操作",
            "provenance": {"chat_message_ids": []}}]
    return build_annotations(hls, None, project_id="p")


def test_enrich_fills_description_text_and_lines() -> None:
    ann_doc = _annotations()
    hls = [{"highlight_id": "hl-001", "start_ms": 0, "end_ms": 90000}]
    out = enrich_annotations(ann_doc, _transcript(), hls, FakeNarrative())
    validate_annotations(out)
    a = out["annotations"][0]
    assert a["description"] == "D:hl-001"
    assert all(d["text"] == f"T-{d['dimension']}" for d in a["dimensions"])
    assert all(b["line"] == f"L-{b['order']}" for b in a["beats"])


def test_enrich_does_not_mutate_input() -> None:
    ann_doc = _annotations()
    before_desc = ann_doc["annotations"][0]["description"]
    enrich_annotations(ann_doc, _transcript(), [{"highlight_id": "hl-001", "start_ms": 0, "end_ms": 90000}], FakeNarrative())
    assert ann_doc["annotations"][0]["description"] == before_desc  # 純函式不動輸入


def test_run_refine_combines_both() -> None:
    hls = _highlights()
    ann_doc = build_annotations(hls, None, project_id="p")
    result = run_refine(hls, ann_doc, _transcript(), narrative_reviewer=FakeNarrative())
    assert result["proposed_offsets"][0]["highlight_id"] == "hl-001"
    assert result["annotations"]["annotations"][0]["description"] == "D:hl-001"
