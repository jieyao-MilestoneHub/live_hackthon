"""規則式標註產生器單元測試（階段 7–8）：build_annotations → annotations.v1。"""
from __future__ import annotations

from analysis.annotations import build_annotations
from analysis.validate import validate_annotations

DIMS = ["setup", "reaction_start", "reaction_turn", "punchline", "chat_highlights"]


def _highlights() -> list[dict]:
    return [
        {
            "highlight_id": "hl-001",
            "start_ms": 100000,
            "end_ms": 160000,  # 60s
            "score": 0.9,
            "status": "shifted",
            "selected": True,
            "suggested_title": "神操作",
            "description": "深層脈絡：粽子開箱",
            "provenance": {"chat_message_ids": ["m-1", "m-2", "m-3", "m-4"]},
        },
        {"highlight_id": "hl-002", "start_ms": 0, "end_ms": 30000, "score": 0.3, "status": "excluded", "selected": False},
        {"highlight_id": "hl-003", "start_ms": 5000, "end_ms": 20000, "score": 0.4, "selected": False},
    ]


def _chatlog() -> dict:
    return {
        "schema_version": "chatlog.v1",
        "project_id": "project-123",
        "time_base": "epoch_ms",
        "messages": [
            {"message_id": "m-1", "time_ms": 1, "username": "A", "text": "先卡位", "kind": "human"},
            {"message_id": "m-2", "time_ms": 2, "username": "B", "text": "太扯了吧哈哈哈！", "kind": "human"},
            {"message_id": "m-3", "time_ms": 3, "username": "C", "text": "太神了 起雞皮疙瘩 🤣🤣", "kind": "human"},
            {"message_id": "m-4", "time_ms": 4, "username": "D", "text": "嗯嗯", "kind": "human"},
        ],
    }


def test_output_validates_and_skips_excluded_deselected() -> None:
    doc = build_annotations(_highlights(), _chatlog(), project_id="project-123")
    validate_annotations(doc)
    assert doc["project_id"] == "project-123"
    # 只標註納入的 hl-001（excluded 的 hl-002、deselected 的 hl-003 被跳過）。
    assert [a["highlight_id"] for a in doc["annotations"]] == ["hl-001"]


def test_five_dimensions_in_order() -> None:
    ann = build_annotations(_highlights(), _chatlog(), project_id="p")["annotations"][0]
    assert [d["dimension"] for d in ann["dimensions"]] == DIMS
    assert ann["title"] == "神操作"
    assert ann["description"] == "深層脈絡：粽子開箱"


def test_spans_contiguous_within_event_window() -> None:
    ann = build_annotations(_highlights(), _chatlog(), project_id="p")["annotations"][0]
    narrative = [d for d in ann["dimensions"] if d["dimension"] != "chat_highlights"]
    assert narrative[0]["start_ms"] == 100000
    assert narrative[-1]["end_ms"] == 160000
    for a, b in zip(narrative, narrative[1:]):
        assert a["end_ms"] == b["start_ms"]  # 連續不重疊
    # 預設比例 .30/.25/.25/.20 於 60s
    assert [(d["start_ms"], d["end_ms"]) for d in narrative] == [
        (100000, 118000), (118000, 133000), (133000, 148000), (148000, 160000)
    ]
    # chat_highlights span == punchline span
    chat = next(d for d in ann["dimensions"] if d["dimension"] == "chat_highlights")
    assert (chat["start_ms"], chat["end_ms"]) == (148000, 160000)


def test_chat_highlights_from_provenance_ranked() -> None:
    ann = build_annotations(_highlights(), _chatlog(), project_id="p")["annotations"][0]
    chat = next(d for d in ann["dimensions"] if d["dimension"] == "chat_highlights")
    msgs = chat["messages"]
    assert 1 <= len(msgs) <= 3
    # 高情緒（m-3 有關鍵詞+emoji、m-2 有關鍵詞+驚嘆）排在低情緒（m-4 "嗯嗯"）前面。
    texts = [m["text"] for m in msgs]
    assert "太神了 起雞皮疙瘩 🤣🤣" in texts and "太扯了吧哈哈哈！" in texts
    assert all(m["message_id"] in {"m-1", "m-2", "m-3", "m-4"} for m in msgs)


def test_beats_cut_list() -> None:
    ann = build_annotations(_highlights(), _chatlog(), project_id="p")["annotations"][0]
    beats = ann["beats"]
    assert [b["order"] for b in beats] == [1, 2, 3, 4]
    assert [b["beat"] for b in beats] == ["setup", "reaction_start", "reaction_turn", "punchline"]
    for b in beats:
        assert b["duration_ms"] == b["end_ms"] - b["start_ms"]
        assert b["line"] is None  # 台詞待 AI 精修填


def test_dimension_ratios_override() -> None:
    doc = build_annotations(
        _highlights(), _chatlog(), project_id="p",
        params={"dimension_ratios": {"setup": 0.5, "reaction_start": 0.2, "reaction_turn": 0.2, "punchline": 0.1}},
    )
    setup = doc["annotations"][0]["dimensions"][0]
    assert setup["end_ms"] == 100000 + 30000  # 50% of 60s


def test_no_chatlog_omits_chat_messages() -> None:
    doc = build_annotations(_highlights(), None, project_id="p")
    validate_annotations(doc)
    chat = next(d for d in doc["annotations"][0]["dimensions"] if d["dimension"] == "chat_highlights")
    assert chat.get("messages") in ([], None)
