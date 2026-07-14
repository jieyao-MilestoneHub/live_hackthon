"""Amazon Bedrock — Amazon Nova adapter（語意複核，SemanticReviewerPort）。

查證結論（見 plan「AWS 服務查證結論 §C」）：
  * Nova **只看視覺影格、不分析音訊、不命名人物** → 只做低信心片段的**文字**語意複核，
    絕不可當 speaker-ID 來源。逐字稿以純文字餵入。
  * 用 **Converse API + 強制 tool use**（toolChoice.tool）以 JSON Schema ``enum`` 取受約束
    輸出；``temperature=0`` 求決定性。
  * 模型：預設 Micro（us-east-1 in-region、最便宜文字）；複雜片段用 Premier。
    Nova 2 Lite 需 ``us.`` 跨區 inference profile（見 config）。

Real 走 boto3 ``bedrock-runtime``；Stub 一律回 ``unknown``（不覆蓋高信心臉部結果）。
"""
from __future__ import annotations

from typing import Any

from app.aws.config import AttributionConfig
from app.settings import Settings

_TOOL_NAME = "resolve_speaker"
_SYSTEM_PROMPT = (
    "你是說話者歸屬的語意複核助手。只能從提供的候選 person_id 或 'unknown' 中，"
    "依逐字稿上下文（前後輪、角色）選出最合理的一個。不得臆造未提供的 id；"
    "證據不足時回 'unknown'。你不得也無法從影像辨識或命名任何真實人物。"
)


def build_tool_config(candidate_person_ids: list[str]) -> dict[str, Any]:
    """組出 Converse 的 toolConfig：強制呼叫單一工具、person_id 限縮為候選 + unknown。"""
    enum = [*candidate_person_ids, "unknown"]
    return {
        "toolChoice": {"tool": {"name": _TOOL_NAME}},
        "tools": [{
            "toolSpec": {
                "name": _TOOL_NAME,
                "description": "為目標 utterance 選出說話者。",
                "inputSchema": {"json": {
                    "type": "object",
                    "properties": {
                        "person_id": {"type": "string", "enum": enum},
                        "confidence": {"type": "number"},
                    },
                    "required": ["person_id"],
                }},
            }
        }],
    }


def extract_person_id(response: dict[str, Any], candidate_person_ids: list[str]) -> str:
    """從 Converse 回應取出 toolUse.input.person_id；不合法則 unknown。"""
    allowed = {*candidate_person_ids, "unknown"}
    content = (response.get("output", {}).get("message", {}) or {}).get("content", []) or []
    for block in content:
        tool = block.get("toolUse")
        if tool and tool.get("name") == _TOOL_NAME:
            pid = (tool.get("input") or {}).get("person_id")
            if pid in allowed:
                return pid
    return "unknown"


class RealNovaReviewer:
    """boto3 Bedrock Nova（Converse + 強制 tool use）。"""

    def __init__(self, settings: Settings, config: AttributionConfig) -> None:
        import boto3  # lazy

        self._settings = settings
        self._config = config
        self._client = boto3.client("bedrock-runtime", region_name=config.bedrock_region)

    def review_speaker(
        self,
        candidate_person_ids: list[str],
        context: dict[str, Any],
        *,
        complex_case: bool = False,
    ) -> str:
        import json

        if not candidate_person_ids:
            return "unknown"
        model_id = (
            self._config.nova_premier_model_id if complex_case
            else self._config.nova_review_model_id
        )
        resp = self._client.converse(
            modelId=model_id,
            system=[{"text": _SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": json.dumps(context, ensure_ascii=False)}]}],
            inferenceConfig={"temperature": 0, "topP": 1, "maxTokens": 256},
            toolConfig=build_tool_config(candidate_person_ids),
        )
        return extract_person_id(resp, candidate_person_ids)


class StubNovaReviewer:
    """離線替身：一律回 unknown（Nova 僅輔助、不覆蓋高信心結果）。"""

    def __init__(self, settings: Settings, config: AttributionConfig) -> None:
        self._settings = settings
        self._config = config

    def review_speaker(
        self,
        candidate_person_ids: list[str],
        context: dict[str, Any],
        *,
        complex_case: bool = False,
    ) -> str:
        return "unknown"


# --- Narrative enrichment（高光敘事精修，NarrativeReviewerPort）---------------

_NARRATIVE_TOOL_NAME = "write_highlight_narrative"
_NARRATIVE_SYSTEM_PROMPT = (
    "你是短影音剪輯的敘事助手。依提供的逐字稿與聊天室摘要，為這個高光片段寫出："
    "(1) 一句話描述『這橋段在幹嘛、為什麼好笑』；(2) 每個敘事維度（埋梗/反應/笑點）的簡短說明；"
    "(3) 每個節拍的代表台詞（可引用逐字稿）。用繁體中文，精簡、口語、忠於逐字稿，不得杜撰事實。"
)


def build_narrative_tool_config() -> dict[str, Any]:
    """組出敘事精修的 toolConfig：強制單一工具、輸出 description/dimension_texts/beat_lines。"""
    return {
        "toolChoice": {"tool": {"name": _NARRATIVE_TOOL_NAME}},
        "tools": [{
            "toolSpec": {
                "name": _NARRATIVE_TOOL_NAME,
                "description": "為一個高光片段產出敘事文字（描述、各維度說明、各節拍台詞）。",
                "inputSchema": {"json": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "dimension_texts": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                        "beat_lines": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "required": ["description"],
                }},
            }
        }],
    }


def extract_narrative(response: dict[str, Any]) -> dict[str, Any]:
    """從 Converse 回應取出 toolUse.input（description/dimension_texts/beat_lines）；缺則給保底。"""
    content = (response.get("output", {}).get("message", {}) or {}).get("content", []) or []
    for block in content:
        tool = block.get("toolUse")
        if tool and tool.get("name") == _NARRATIVE_TOOL_NAME:
            data = tool.get("input") or {}
            return {
                "description": data.get("description") or "",
                "dimension_texts": data.get("dimension_texts") or {},
                "beat_lines": data.get("beat_lines") or {},
            }
    return {"description": "", "dimension_texts": {}, "beat_lines": {}}


class RealNarrativeReviewer:
    """boto3 Bedrock Nova（Converse + 強制 tool use）產生高光敘事。"""

    def __init__(self, settings: Settings, config: AttributionConfig) -> None:
        import boto3  # lazy

        self._settings = settings
        self._config = config
        self._client = boto3.client("bedrock-runtime", region_name=config.bedrock_region)

    def enrich(self, context: dict[str, Any]) -> dict[str, Any]:
        import json

        resp = self._client.converse(
            modelId=self._config.nova_reasoning_model_id,
            system=[{"text": _NARRATIVE_SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": json.dumps(context, ensure_ascii=False)}]}],
            inferenceConfig={"temperature": 0, "topP": 1, "maxTokens": 800},
            toolConfig=build_narrative_tool_config(),
        )
        return extract_narrative(resp)


class StubNarrativeReviewer:
    """離線替身：由 context 的逐字稿確定性回填（供離線端到端測試；真值上 AWS）。"""

    def __init__(self, settings: Settings, config: AttributionConfig) -> None:
        self._settings = settings
        self._config = config

    def enrich(self, context: dict[str, Any]) -> dict[str, Any]:
        text = (context.get("transcript_text") or "").strip()
        title = context.get("title") or "高光片段"
        snippet = text[:24] if text else "（無逐字稿）"
        description = f"（AI草稿）{title}：{text[:40]}" if text else f"（AI草稿）{title}"
        dimension_texts = {dim: f"{dim}：{snippet}" for dim in (context.get("dimensions") or [])}
        beat_lines = {str(order): snippet for order in (context.get("beats") or [])}
        return {"description": description, "dimension_texts": dimension_texts, "beat_lines": beat_lines}
