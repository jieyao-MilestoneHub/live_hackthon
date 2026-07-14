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
