"""Edit planner（Step 2）：閘門、正規化、fail-open —— 全離線，不打 Bedrock。

Real 路徑用假的 bedrock client（注入 planner._client），驗證 Converse tool 輸出
如何正規化回 effects.v1 + 套 emphasis，以及任何錯誤 fail-open 退回 baseline。
"""
from __future__ import annotations

from analysis.validate import validate_effects, validate_subtitle
from app.aws import bedrock_edit_planner as bep


def _synthetic_chat():
    """合成 timeline(2 clip) + 帶 chat_window/detection/emotion 的 highlights，
    用來測峰值對齊（不倚賴 transcript fixture，那條路徑無 chat_window）。"""
    timeline = {
        "actual_duration_ms": 8000,
        "aspect_ratio": "9:16",
        "clips": [
            {"timeline_order": 1, "highlight_id": "h1", "source_start_ms": 10000,
             "source_end_ms": 14000, "timeline_start_ms": 0, "timeline_end_ms": 4000},
            {"timeline_order": 2, "highlight_id": "h2", "source_start_ms": 30000,
             "source_end_ms": 34000, "timeline_start_ms": 4000, "timeline_end_ms": 8000},
        ],
    }
    highlights = [
        {"highlight_id": "h1", "start_ms": 10000, "end_ms": 14000,
         "chat_window": {"start_ms": 12000, "end_ms": 13000},          # 中心 12500 → 輸出 anchor 2500
         "detection": {"minute_volume": 10, "threshold": 5},           # ratio 2 → strength 0.10
         "emotion": {"breakdown": {"keyword": 1, "emoji": 0.2, "volume": 3}}},  # zoom_in
        {"highlight_id": "h2", "start_ms": 30000, "end_ms": 34000,
         "chat_window": {"start_ms": 31000, "end_ms": 31500},          # 中心 31250 → 輸出 anchor 5250
         "detection": {"minute_volume": 8, "threshold": 4},
         "emotion": {"breakdown": {"keyword": 0.1, "emoji": 5, "volume": 2}}},   # shake（emoji 主導）
    ]
    return timeline, highlights


def _timeline_highlights(ready_project):
    from app.repository import get_repository

    repo = get_repository()
    pid = ready_project
    tv = int(repo.get_project(pid)["latest_timeline_version"])
    return repo.get_timeline(pid, tv), repo.list_highlights(pid)


class _FakeBedrock:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc

    def converse(self, **_kwargs):
        if self._exc:
            raise self._exc
        return self._response


def _canned(span: int) -> dict:
    return {"output": {"message": {"content": [{"toolUse": {"name": "plan_edit", "input": {
        "effects": [
            {"type": "zoom_in", "start_ms": 0, "end_ms": min(1200, span), "strength": 0.1},
            {"type": "bogus", "start_ms": 0, "end_ms": 100},                    # 丟棄：非法 type
            {"type": "pan", "start_ms": span + 9999, "end_ms": span + 10000},   # 丟棄：超出範圍
        ],
        "transitions": [{"type": "flash_transition", "at_ms": min(500, span), "duration_ms": 240}],
        "emphasis": [{"words": ["爆笑", "太扯"]}],
    }}}]}}}


def test_gate_default_off_returns_stub():
    assert isinstance(bep.get_edit_planner(), bep.StubEditPlanner)


def test_gate_on_returns_real(monkeypatch):
    monkeypatch.setenv("EDIT_PLANNER_LLM", "1")
    bep.cache_clear()
    try:
        assert isinstance(bep.get_edit_planner(), bep.RealClaudeEditPlanner)
    finally:
        bep.cache_clear()


def test_stub_planner_is_deterministic_baseline(client, ready_project):
    tl, hl = _timeline_highlights(ready_project)
    p1 = bep.StubEditPlanner().plan_edit(
        instruction="x", timeline=tl, highlights=hl, effect_seed=7, project_id="p", render_id="r"
    )
    p2 = bep.StubEditPlanner().plan_edit(
        instruction="x", timeline=tl, highlights=hl, effect_seed=7, project_id="p", render_id="r"
    )
    validate_effects(p1["effects"])
    validate_subtitle(p1["subtitle"])
    assert p1["effects"]["effects"]  # 每 clip ≥1 特效
    assert p1 == p2  # 決定性（同 seed+highlights 同輸出）


def test_real_planner_normalizes_tool_output(client, ready_project):
    tl, hl = _timeline_highlights(ready_project)
    span = int(tl["actual_duration_ms"])
    planner = bep.RealClaudeEditPlanner("us-east-1")
    planner._client = _FakeBedrock(response=_canned(span))  # 跳過 lazy boto3

    plan = planner.plan_edit(
        instruction="加特效", timeline=tl, highlights=hl, effect_seed=7, project_id="p", render_id="r"
    )
    validate_effects(plan["effects"])
    validate_subtitle(plan["subtitle"])

    types = [e["type"] for e in plan["effects"]["effects"]]
    assert "zoom_in" in types
    assert "flash_transition" in types
    assert "bogus" not in types  # 非法 type 被丟棄
    # 所有 ranged 特效落在 [0, span]
    for e in plan["effects"]["effects"]:
        if "start_ms" in e:
            assert 0 <= e["start_ms"] < e["end_ms"] <= span


def test_real_planner_fail_open_on_error(client, ready_project):
    tl, hl = _timeline_highlights(ready_project)
    planner = bep.RealClaudeEditPlanner("us-east-1")
    planner._client = _FakeBedrock(exc=RuntimeError("throttled"))

    plan = planner.plan_edit(
        instruction="x", timeline=tl, highlights=hl, effect_seed=7, project_id="p", render_id="r"
    )
    validate_effects(plan["effects"])
    # fail-open → 退回同一份確定性 baseline
    assert plan["effects"]["effects"] == bep._baseline(tl, hl, 7, "p", "r")["effects"]["effects"]


# --- Step 5 精修：峰值對齊 / 硬護欄 / 聊天 emphasis（純函式，離線）------------

def test_peak_anchor_maps_chat_window_to_output_coords():
    tl, hl = _synthetic_chat()
    clip1, clip2 = tl["clips"]
    hidx = {h["highlight_id"]: h for h in hl}
    assert bep._peak_anchor_ms(clip1, hidx["h1"]) == 2500   # 0 + (12500 − 10000)
    assert bep._peak_anchor_ms(clip2, hidx["h2"]) == 5250   # 4000 + (31250 − 30000)
    assert bep._peak_anchor_ms(clip1, {}) == clip1["timeline_start_ms"]  # 缺訊號→clip 開頭


def test_peak_effects_aligns_to_peak_and_emoji_uses_shake():
    tl, hl = _synthetic_chat()
    plan = bep._peak_effects(tl, hl, 7, "p", "r")
    validate_effects(plan)
    ranged = sorted((e for e in plan["effects"] if e["type"] in bep._RANGED_TYPES),
                    key=lambda e: e["start_ms"])
    assert len(ranged) == 2                                  # 每 clip 一個
    assert ranged[0]["type"] == "zoom_in" and ranged[0]["start_ms"] > 1000  # 對齊峰值,非開頭 0
    assert ranged[1]["type"] == "shake"                     # emoji 主導
    assert ranged[0]["strength"] == 0.1 and ranged[1]["strength"] == 0.1    # ratio 2 → 0.10
    assert any(e["type"] == "flash_transition" and e["at_ms"] == 4000 for e in plan["effects"])
    assert bep._peak_effects(tl, hl, 7, "p", "r") == plan   # 決定性


def test_clamp_strength_hard_range():
    assert bep._clamp_strength(0.5) == 0.15
    assert bep._clamp_strength(0.001) == 0.03
    assert bep._clamp_strength(None) == 0.09


def test_cap_ranged_per_clip_bounds_flood():
    tl, _ = _synthetic_chat()
    flood = [{"type": "zoom_in", "start_ms": 100 * i, "end_ms": 100 * i + 500,
              "strength": 0.05 + 0.001 * i} for i in range(10)]  # 全落在 clip1
    assert len(bep._cap_ranged_per_clip(flood, tl["clips"])) <= 1


def test_normalize_effects_caps_and_clamps():
    tl, _ = _synthetic_chat()
    raw = {"effects": [
        {"type": "zoom_in", "start_ms": 100 * i, "end_ms": 100 * i + 400, "strength": 0.9}
        for i in range(10)
    ]}
    plan = bep._normalize_effects(raw, tl, 7, "p", "r")
    validate_effects(plan)
    ranged = [e for e in plan["effects"] if e["type"] in bep._RANGED_TYPES]
    assert len(ranged) <= 1                                  # 每 clip ≤1（此例全在 clip1）
    assert all(0.03 <= e["strength"] <= 0.15 for e in ranged)  # strength clamp


def test_overlay_chat_emphasis_picks_chat_tokens():
    sub = {"schema_version": "subtitle.v1", "language": "zh-TW", "project_id": "p",
           "render_id": "r", "cues": [{"start_ms": 0, "end_ms": 1000, "text": "笑死我了www"}]}
    bep._overlay_chat_emphasis(sub)
    emph = sub["cues"][0].get("emphasis_words", [])
    assert "笑死" in emph and "www" in emph
