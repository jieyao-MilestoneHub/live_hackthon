"""Edit-by-language planner — NL 剪接需求 → effects.v1 + subtitle.v1（爆點字）。

「短影片剪接師」角色：拿著已組好的 timeline.v1（輸出時間軸座標）+ 逐字稿/高光，
依使用者自然語言需求，決定**高光內哪些子片段套哪種特效**（zoom_in/zoom_out/pan/
shake + flash 轉場）以及**哪些關鍵字要爆點動畫**，輸出兩份契約 dict：

    {"effects": effects.v1, "subtitle": subtitle.v1}

設計（比照 analysis/highlights_llm.py，非 pipeline 綁定）：
  * self-contained：不編輯 app/aws/factory.py；旁路 API 直接叫 get_edit_planner()。
  * 閘門 EDIT_PLANNER_LLM（預設 OFF）：關掉時一律走 StubEditPlanner（=確定性
    plan_effects + plan_subtitles baseline），離線/pytest 永不打 Bedrock。
    （對齊 demand：DEMO 前 default-off 關 Bedrock。）
  * Real 走 Bedrock Converse + 強制 tool-use(plan_edit) + temperature=0，**fail-open**：
    任何錯誤（無憑證、throttling、model access、schema 不合）→ 退回 baseline，
    端點永不因規劃失敗而壞。
  * 時間一律**輸出剪輯時間軸毫秒**（0 起算），與 effects.v1 / subtitle.v1 一致。

model id 走 env（Bedrock 前綴 anthropic. / 需跨區時 us.anthropic.）：
  EDIT_PLANNER_MODEL_ID          預設 us.anthropic.claude-haiku-4-5   （fast，高併發）
  EDIT_PLANNER_QUALITY_MODEL_ID  預設 us.anthropic.claude-sonnet-5    （quality）
實際可用 id 由部署前的 probe 確認（見計畫 §IAM 與 probe）。
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Protocol, runtime_checkable

from analysis.validate import validate_effects, validate_subtitle
from app.aws.config import get_attribution_config
from creative import plan_effects, plan_subtitles

_TOOL_NAME = "plan_edit"
_RANGED_TYPES = {"zoom_in", "zoom_out", "pan", "shake"}
_POINT_TYPES = {"flash_transition", "cut"}
_EMPHASIS_LIMIT = 3

_SYSTEM_PROMPT = (
    "你是短影片剪接師。輸入是一支已排好的短影片（clip 清單，時間為【輸出剪輯時間軸】"
    "毫秒、0 起算）與其逐字稿。你的任務：依使用者的中文剪接需求，決定"
    "(1) 每個高光片段內哪些【子片段】要套哪種鏡頭特效（zoom_in / zoom_out / pan / shake），"
    "(2) clip 邊界要不要 flash 轉場，(3) 字幕裡哪些關鍵字要做爆點放大動畫。"
    "規則：只呼叫 plan_edit 工具、不要輸出任何散文；所有時間為輸出時間軸毫秒且需落在"
    "對應 clip 的 [timeline_start_ms, timeline_end_ms] 內、不得超過 actual_duration_ms；"
    "特效寧缺勿濫（每個高潮 1–2 個即可）；strength 介於 0–1（建議 0.03–0.15 的細膩幅度）。"
)


@runtime_checkable
class EditPlannerPort(Protocol):
    """NL + timeline → {effects, subtitle} 的窄介面（DIP）。"""

    def plan_edit(
        self,
        *,
        instruction: str,
        timeline: dict[str, Any],
        highlights: list[dict[str, Any]],
        effect_seed: int,
        project_id: str,
        render_id: str,
        model_tier: str = "fast",
    ) -> dict[str, Any]:
        """回傳 ``{"effects": effects.v1, "subtitle": subtitle.v1}``（皆已 schema 驗證）。"""
        ...


def llm_enabled() -> bool:
    return os.environ.get("EDIT_PLANNER_LLM", "").strip().lower() in {"1", "true", "yes", "on"}


def _model_id(model_tier: str) -> str:
    if model_tier == "quality":
        return os.environ.get("EDIT_PLANNER_QUALITY_MODEL_ID", "us.anthropic.claude-sonnet-5")
    return os.environ.get("EDIT_PLANNER_MODEL_ID", "us.anthropic.claude-haiku-4-5")


def _baseline(
    timeline: dict[str, Any],
    highlights: list[dict[str, Any]],
    effect_seed: int,
    project_id: str,
    render_id: str,
) -> dict[str, Any]:
    """確定性 baseline：既有 plan_effects + plan_subtitles（也是 Real 的 fail-open 落點）。"""
    return {
        "effects": plan_effects(timeline, effect_seed, project_id, render_id),
        "subtitle": plan_subtitles(timeline, highlights, project_id, render_id),
    }


def _timeline_span(timeline: dict[str, Any]) -> int:
    span = timeline.get("actual_duration_ms")
    if span:
        return int(span)
    clips = timeline.get("clips", [])
    return max((int(c["timeline_end_ms"]) for c in clips), default=0)


def _tool_config() -> dict[str, Any]:
    return {
        "toolChoice": {"tool": {"name": _TOOL_NAME}},
        "tools": [{
            "toolSpec": {
                "name": _TOOL_NAME,
                "description": "決定高光內每個子片段套哪種特效與爆點關鍵字（時間為輸出剪輯時間軸毫秒）。",
                "inputSchema": {"json": {
                    "type": "object",
                    "properties": {
                        "effects": {"type": "array", "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": sorted(_RANGED_TYPES)},
                                "start_ms": {"type": "integer", "minimum": 0},
                                "end_ms": {"type": "integer", "minimum": 0},
                                "strength": {"type": "number", "minimum": 0, "maximum": 1},
                            },
                            "required": ["type", "start_ms", "end_ms"],
                        }},
                        "transitions": {"type": "array", "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": sorted(_POINT_TYPES)},
                                "at_ms": {"type": "integer", "minimum": 0},
                                "duration_ms": {"type": "integer", "minimum": 0},
                            },
                            "required": ["type", "at_ms"],
                        }},
                        "emphasis": {"type": "array", "items": {
                            "type": "object",
                            "properties": {
                                "highlight_id": {"type": "string"},
                                "words": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["words"],
                        }},
                    },
                    "required": ["effects"],
                }},
            }
        }],
    }


def _user_message(instruction: str, timeline: dict[str, Any], highlights: list[dict[str, Any]]) -> str:
    """把剪接需求 + clip 清單（含逐字稿）+ baseline 種子攤成一段給模型精修。"""
    import json

    text_by_hl = {h["highlight_id"]: (h.get("transcript") or h.get("suggested_title") or "") for h in highlights}
    clips = [
        {
            "timeline_order": c["timeline_order"],
            "highlight_id": c["highlight_id"],
            "timeline_start_ms": c["timeline_start_ms"],
            "timeline_end_ms": c["timeline_end_ms"],
            "transcript": text_by_hl.get(c["highlight_id"], ""),
        }
        for c in sorted(timeline.get("clips", []), key=lambda c: c["timeline_order"])
    ]
    payload = {
        "actual_duration_ms": _timeline_span(timeline),
        "aspect_ratio": timeline.get("aspect_ratio"),
        "clips": clips,
    }
    return (
        f"剪接需求：{instruction}\n\n"
        f"短影片結構（時間為輸出剪輯時間軸毫秒）：\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _normalize_effects(
    raw: dict[str, Any],
    span_ms: int,
    effect_seed: int,
    project_id: str,
    render_id: str,
) -> dict[str, Any]:
    """把 Claude tool 輸出（effects + transitions）正規化成 effects.v1。"""
    out: list[dict[str, Any]] = []
    for e in raw.get("effects") or []:
        etype = e.get("type")
        if etype not in _RANGED_TYPES:
            continue
        start = max(0, int(e.get("start_ms", 0)))
        end = min(span_ms, int(e.get("end_ms", 0)))
        if end <= start:
            continue
        item: dict[str, Any] = {"type": etype, "start_ms": start, "end_ms": end}
        if e.get("strength") is not None:
            item["strength"] = max(0.0, min(1.0, float(e["strength"])))
        out.append(item)
    for t in raw.get("transitions") or []:
        ttype = t.get("type")
        if ttype not in _POINT_TYPES:
            continue
        at = int(t.get("at_ms", 0))
        if at < 0 or at > span_ms:
            continue
        item = {"type": ttype, "at_ms": at}
        if t.get("duration_ms") is not None:
            item["duration_ms"] = max(0, int(t["duration_ms"]))
        out.append(item)

    plan = {
        "schema_version": "effects.v1",
        "effect_seed": int(effect_seed),
        "project_id": project_id,
        "render_id": render_id,
        "effects": out,
    }
    validate_effects(plan)
    return plan


def _apply_emphasis(subtitle: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    """把 Claude 選出的爆點關鍵字套到各 cue（該字出現在 cue.text 才標）。"""
    words: list[str] = []
    for group in raw.get("emphasis") or []:
        for w in group.get("words") or []:
            w = str(w).strip()
            if w and w not in words:
                words.append(w)
    if not words:
        return subtitle
    for cue in subtitle.get("cues", []):
        text = cue.get("text", "")
        hit = [w for w in words if w in text][:_EMPHASIS_LIMIT]
        if hit:
            cue["emphasis_words"] = hit
        else:
            cue.pop("emphasis_words", None)
    validate_subtitle(subtitle)
    return subtitle


class StubEditPlanner:
    """離線/預設：確定性 baseline，無 AWS。"""

    def plan_edit(
        self,
        *,
        instruction: str,
        timeline: dict[str, Any],
        highlights: list[dict[str, Any]],
        effect_seed: int,
        project_id: str,
        render_id: str,
        model_tier: str = "fast",
    ) -> dict[str, Any]:
        return _baseline(timeline, highlights, effect_seed, project_id, render_id)


class RealClaudeEditPlanner:
    """Claude on Bedrock（Converse + 強制 tool-use, temperature=0）。fail-open 退回 baseline。"""

    def __init__(self, region: str) -> None:
        self._region = region
        self._client = None  # lazy

    def _bedrock(self):
        if self._client is None:
            import boto3  # lazy
            from botocore.config import Config

            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self._region,
                config=Config(retries={"mode": "adaptive", "max_attempts": 4}),
            )
        return self._client

    def _converse(self, instruction, timeline, highlights, model_tier) -> dict[str, Any]:
        resp = self._bedrock().converse(
            modelId=_model_id(model_tier),
            system=[{"text": _SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": _user_message(instruction, timeline, highlights)}]}],
            inferenceConfig={"temperature": 0, "topP": 1, "maxTokens": 2048},
            toolConfig=_tool_config(),
        )
        content = (resp.get("output", {}).get("message", {}) or {}).get("content", []) or []
        for block in content:
            tool = block.get("toolUse")
            if tool and tool.get("name") == _TOOL_NAME:
                return tool.get("input") or {}
        raise ValueError("Claude 未回 plan_edit toolUse block")

    def plan_edit(
        self,
        *,
        instruction: str,
        timeline: dict[str, Any],
        highlights: list[dict[str, Any]],
        effect_seed: int,
        project_id: str,
        render_id: str,
        model_tier: str = "fast",
    ) -> dict[str, Any]:
        baseline = _baseline(timeline, highlights, effect_seed, project_id, render_id)
        try:
            raw = self._converse(instruction, timeline, highlights, model_tier)
            effects = _normalize_effects(raw, _timeline_span(timeline), effect_seed, project_id, render_id)
            subtitle = _apply_emphasis(baseline["subtitle"], raw)
            # 空計畫（模型什麼都沒選）→ 用 baseline 的特效，避免產出無特效影片。
            if not effects["effects"]:
                effects = baseline["effects"]
            return {"effects": effects, "subtitle": subtitle}
        except Exception:  # noqa: BLE001 — fail-open：規劃失敗不壞端點
            return baseline


@lru_cache(maxsize=1)
def _get_real() -> RealClaudeEditPlanner:
    return RealClaudeEditPlanner(get_attribution_config().bedrock_region)


def get_edit_planner() -> EditPlannerPort:
    """閘門 EDIT_PLANNER_LLM 開才用 Claude；預設/離線走 Stub。"""
    return _get_real() if llm_enabled() else StubEditPlanner()


def cache_clear() -> None:
    _get_real.cache_clear()
