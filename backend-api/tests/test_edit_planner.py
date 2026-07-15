"""Edit planner（Step 2）：閘門、正規化、fail-open —— 全離線，不打 Bedrock。

Real 路徑用假的 bedrock client（注入 planner._client），驗證 Converse tool 輸出
如何正規化回 effects.v1 + 套 emphasis，以及任何錯誤 fail-open 退回 baseline。
"""
from __future__ import annotations

from analysis.validate import validate_effects, validate_subtitle
from app.aws import bedrock_edit_planner as bep
from creative import plan_effects


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
    plan = bep.StubEditPlanner().plan_edit(
        instruction="x", timeline=tl, highlights=hl, effect_seed=7, project_id="p", render_id="r"
    )
    validate_effects(plan["effects"])
    validate_subtitle(plan["subtitle"])
    assert plan["effects"]["effects"] == plan_effects(tl, 7, "p", "r")["effects"]


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
    # fail-open → 確定性 baseline
    assert plan["effects"]["effects"] == plan_effects(tl, 7, "p", "r")["effects"]
