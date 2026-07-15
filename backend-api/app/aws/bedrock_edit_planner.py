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
from creative import plan_subtitles

_TOOL_NAME = "plan_edit"
_RANGED_TYPES = {"zoom_in", "zoom_out", "pan", "shake"}
_POINT_TYPES = {"flash_transition", "cut"}
_EMPHASIS_LIMIT = 3

# 平衡強度區 + punch-in 上限（依真實聊天高光「又尖又窄」的形狀，短促最貼切）。
_STRENGTH_LO, _STRENGTH_HI = 0.06, 0.12
_STRENGTH_CLAMP_LO, _STRENGTH_CLAMP_HI = 0.03, 0.15  # 硬護欄：無論 NL 打什麼都收進此區
_RANGED_MAX_MS = 1600
_FLASH_MS = 240
_MAX_RANGED_PER_CLIP = 1  # 硬護欄：每 clip 至多 1 個 ranged（防過動）

# 「聊天驅動」直播的爆點字候選：真實資料的高頻反應詞（emoji + 疊字笑聲 + 導流問候），
# 逐字稿情緒詞（analysis/emotion.py::EMOTION_KEYWORDS）在此類聊天幾乎不出現。此清單
# 僅供本旁路功能挑 emphasis，不動共用 pipeline。
_CHAT_EMPHASIS_KEYWORDS = (
    "笑死", "哈哈哈", "哈哈", "www", "笑", "恭喜", "888", "88",
    "太扯", "好扯", "誇張", "神", "厲害", "可愛", "喜歡", "推", "讚", "狗勾",
    "❤", "🤣", "😂", "👏",
)

_SYSTEM_PROMPT = (
    "你是短影片剪接師。輸入是一支已排好的短影片（clip 清單，時間為【輸出剪輯時間軸】"
    "毫秒、0 起算）＋每個 clip 的逐字稿、聊天爆量峰值 peak_anchor_ms、emotion 明細。"
    "你的任務：依使用者的中文剪接需求，決定 (1) 每個高光內哪些【子片段】套哪種鏡頭特效"
    "（zoom_in / zoom_out / pan / shake），(2) clip 邊界要不要 flash 轉場，(3) 字幕裡"
    "哪些關鍵字做爆點放大動畫。"
    "房規：把短促 punch-in 特效【對齊 peak_anchor_ms】（觀眾此刻在反應），長度 ≤1.6 秒；"
    "該 clip 的 emotion.emoji 高於 keyword 時用 shake，否則 zoom_in；"
    "每個高潮至多 1 個 ranged 特效、寧缺勿濫；strength 用平衡幅度 0.06–0.12；"
    "爆點字從 emphasis_candidates 或逐字稿挑真正會引起反應的詞。"
    "規則：只呼叫 plan_edit 工具、不要輸出任何散文；所有時間為輸出時間軸毫秒且需落在"
    "對應 clip 的 [timeline_start_ms, timeline_end_ms] 內、不得超過 actual_duration_ms。"
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


def _highlight_index(highlights: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {h["highlight_id"]: h for h in highlights if h.get("highlight_id")}


def _peak_anchor_ms(clip: dict[str, Any], highlight: dict[str, Any] | None) -> int:
    """該 clip 的聊天爆量峰值 anchor（**輸出時間軸座標**）。

    chat_window 是 source(影片相對) ms → 映射到輸出：
    ``anchor = timeline_start_ms + (chat_window中心 − source_start_ms)``，clamp 進 clip。
    缺 chat_window → 退回 clip 開頭（現行行為）。
    """
    cs, ce = int(clip["timeline_start_ms"]), int(clip["timeline_end_ms"])
    cw = (highlight or {}).get("chat_window") or {}
    ws, we = cw.get("start_ms"), cw.get("end_ms")
    if ws is None or we is None:
        return cs
    peak_source = (int(ws) + int(we)) / 2.0
    src_start = int(clip.get("source_start_ms", 0))
    anchor = cs + int(peak_source - src_start)
    return max(cs, min(anchor, ce - 1))


def _emoji_dominant(highlight: dict[str, Any] | None) -> bool:
    bd = ((highlight or {}).get("emotion") or {}).get("breakdown") or {}
    emoji = float(bd.get("emoji", 0) or 0)
    keyword = float(bd.get("keyword", 0) or 0)
    return emoji > 0 and emoji >= keyword


def _peak_strength(highlight: dict[str, Any] | None) -> float:
    """由 detection.minute_volume/threshold 映射到平衡強度區 [0.06,0.12]。"""
    det = (highlight or {}).get("detection") or {}
    vol, thr = det.get("minute_volume"), det.get("threshold")
    mid = round((_STRENGTH_LO + _STRENGTH_HI) / 2, 3)
    if not vol or not thr or float(thr) <= 0:
        return mid
    ratio = float(vol) / float(thr)  # 高光 ⇒ ≥1
    return round(max(_STRENGTH_LO, min(_STRENGTH_HI, _STRENGTH_LO + (ratio - 1.0) * 0.04)), 3)


def _clamp_strength(value: Any) -> float:
    """硬護欄：strength 收進 [0.03,0.15]（None → 平衡中值）。"""
    if value is None:
        return round((_STRENGTH_LO + _STRENGTH_HI) / 2, 3)
    try:
        s = float(value)
    except (TypeError, ValueError):
        return round((_STRENGTH_LO + _STRENGTH_HI) / 2, 3)
    return round(max(_STRENGTH_CLAMP_LO, min(_STRENGTH_CLAMP_HI, s)), 3)


def _peak_effects(
    timeline: dict[str, Any],
    highlights: list[dict[str, Any]],
    effect_seed: int,
    project_id: str,
    render_id: str,
) -> dict[str, Any]:
    """確定性、**對齊聊天爆量峰值**的 effects.v1（取代 clip-開頭 的 plan_effects）。

    每 clip 一個短促 punch-in（zoom_in；emoji 主導→shake）落在 peak anchor；內部 clip
    邊界放 flash。純資料驅動 → 同 highlights 同輸出；effect_seed 仍隨 plan 供 encoder。
    """
    hidx = _highlight_index(highlights)
    clips = sorted(timeline.get("clips", []), key=lambda c: c["timeline_order"])
    effects: list[dict[str, Any]] = []
    for i, clip in enumerate(clips):
        cs, ce = int(clip["timeline_start_ms"]), int(clip["timeline_end_ms"])
        hl = hidx.get(clip["highlight_id"])
        anchor = _peak_anchor_ms(clip, hl)
        start = max(cs, anchor - 300)
        end = min(ce, start + _RANGED_MAX_MS, anchor + 1000)
        if end <= start:
            end = min(ce, start + 200)
        etype = "shake" if _emoji_dominant(hl) else "zoom_in"
        effects.append({"type": etype, "start_ms": start, "end_ms": end, "strength": _peak_strength(hl)})
        if i > 0:
            effects.append({"type": "flash_transition", "at_ms": cs, "duration_ms": _FLASH_MS})
    plan = {
        "schema_version": "effects.v1",
        "effect_seed": int(effect_seed),
        "project_id": project_id,
        "render_id": render_id,
        "effects": effects,
    }
    validate_effects(plan)
    return plan


def _overlay_chat_emphasis(subtitle: dict[str, Any]) -> dict[str, Any]:
    """把聊天高頻詞疊到 cue 的 emphasis_words（不覆蓋既有，最多 3 個）。"""
    for cue in subtitle.get("cues", []):
        text = cue.get("text", "")
        words = list(cue.get("emphasis_words", []) or [])
        for kw in _CHAT_EMPHASIS_KEYWORDS:
            if len(words) >= _EMPHASIS_LIMIT:
                break
            if kw in text and kw not in words:
                words.append(kw)
        if words:
            cue["emphasis_words"] = words[:_EMPHASIS_LIMIT]
    validate_subtitle(subtitle)
    return subtitle


def _baseline(
    timeline: dict[str, Any],
    highlights: list[dict[str, Any]],
    effect_seed: int,
    project_id: str,
    render_id: str,
) -> dict[str, Any]:
    """確定性 baseline（也是 Real 的 fail-open 落點）：峰值對齊特效 + 聊天爆點字。

    效果從 clip 開頭改為對齊聊天爆量峰值；字幕在 plan_subtitles 之上疊聊天高頻詞
    emphasis。兩者皆只在本旁路功能生效，不動 creative/plan_effects 與共用 pipeline。
    """
    subtitle = _overlay_chat_emphasis(plan_subtitles(timeline, highlights, project_id, render_id))
    return {
        "effects": _peak_effects(timeline, highlights, effect_seed, project_id, render_id),
        "subtitle": subtitle,
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
    """把剪接需求 + clip 清單（含逐字稿 + **聊天爆量峰值/emotion**）攤成一段給模型精修。"""
    import json

    hidx = _highlight_index(highlights)
    clips = []
    for c in sorted(timeline.get("clips", []), key=lambda c: c["timeline_order"]):
        hl = hidx.get(c["highlight_id"])
        clips.append({
            "timeline_order": c["timeline_order"],
            "highlight_id": c["highlight_id"],
            "timeline_start_ms": c["timeline_start_ms"],
            "timeline_end_ms": c["timeline_end_ms"],
            "peak_anchor_ms": _peak_anchor_ms(c, hl),  # 聊天爆量峰值（輸出座標）
            "emotion": ((hl or {}).get("emotion") or {}).get("breakdown"),
            "transcript": (hl or {}).get("transcript") or (hl or {}).get("suggested_title") or "",
        })
    payload = {
        "actual_duration_ms": _timeline_span(timeline),
        "aspect_ratio": timeline.get("aspect_ratio"),
        "emphasis_candidates": list(_CHAT_EMPHASIS_KEYWORDS),
        "clips": clips,
    }
    return (
        f"剪接需求：{instruction}\n\n"
        f"短影片結構（時間為輸出剪輯時間軸毫秒；peak_anchor_ms=聊天爆量峰值，"
        f"建議把 punch-in 對齊此處）：\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _cap_ranged_per_clip(
    ranged: list[dict[str, Any]], clips: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """硬護欄：每 clip 至多 _MAX_RANGED_PER_CLIP 個 ranged（保最強），防過動。

    無 clip 資訊時退回「總量上限 2」。同一效果可能跨 clip → 去重保序。
    """
    if not clips:
        ranged.sort(key=lambda e: e.get("strength", 0), reverse=True)
        return ranged[:2]
    kept: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for clip in clips:
        cs, ce = int(clip["timeline_start_ms"]), int(clip["timeline_end_ms"])
        overlapping = [e for e in ranged if e["start_ms"] < ce and e["end_ms"] > cs]
        overlapping.sort(key=lambda e: e.get("strength", 0), reverse=True)
        for e in overlapping[:_MAX_RANGED_PER_CLIP]:
            key = (e["type"], e["start_ms"], e["end_ms"])
            if key not in seen:
                seen.add(key)
                kept.append(e)
    kept.sort(key=lambda e: e["start_ms"])
    return kept


def _normalize_effects(
    raw: dict[str, Any],
    timeline: dict[str, Any],
    effect_seed: int,
    project_id: str,
    render_id: str,
) -> dict[str, Any]:
    """把 Claude tool 輸出正規化成 effects.v1（硬護欄：enum + 時間 clamp + strength clamp
    + 每 clip ranged 上限 + 邊界數上限）——**穩定性靠這裡，不靠信任 prompt**。"""
    span_ms = _timeline_span(timeline)
    clips = sorted(timeline.get("clips", []), key=lambda c: c["timeline_order"])

    ranged: list[dict[str, Any]] = []
    for e in raw.get("effects") or []:
        etype = e.get("type")
        if etype not in _RANGED_TYPES:
            continue
        start = max(0, int(e.get("start_ms", 0)))
        end = min(span_ms, int(e.get("end_ms", 0)))
        if end <= start:
            continue
        ranged.append({
            "type": etype, "start_ms": start, "end_ms": end,
            "strength": _clamp_strength(e.get("strength")),
        })
    ranged = _cap_ranged_per_clip(ranged, clips)

    points: list[dict[str, Any]] = []
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
        points.append(item)
    points = points[: max(1, len(clips))]  # 邊界數上限

    plan = {
        "schema_version": "effects.v1",
        "effect_seed": int(effect_seed),
        "project_id": project_id,
        "render_id": render_id,
        "effects": ranged + points,
    }
    validate_effects(plan)
    return plan


def _apply_emphasis(subtitle: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    """把 Claude 選出的爆點關鍵字**疊加**到各 cue（additive：保留既有聊天 emphasis，
    再加 Claude 命中且出現在 cue.text 的字，最多 3 個）。"""
    words: list[str] = []
    for group in raw.get("emphasis") or []:
        for w in group.get("words") or []:
            w = str(w).strip()
            if w and w not in words:
                words.append(w)
    if not words:
        return subtitle  # 保留 baseline 疊好的聊天 emphasis，不清空
    for cue in subtitle.get("cues", []):
        text = cue.get("text", "")
        existing = list(cue.get("emphasis_words", []) or [])
        for w in words:
            if len(existing) >= _EMPHASIS_LIMIT:
                break
            if w in text and w not in existing:
                existing.append(w)
        if existing:
            cue["emphasis_words"] = existing[:_EMPHASIS_LIMIT]
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
            effects = _normalize_effects(raw, timeline, effect_seed, project_id, render_id)
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
