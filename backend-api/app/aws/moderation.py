"""Content-moderation adapters — visual (Rekognition) + text (Bedrock zh-TW).

One ``RealContentModeration`` implements BOTH ``VisualModerationPort`` and
``TextModerationPort`` (it holds a Rekognition client + a Bedrock-runtime client),
exactly like ``RealRekognition`` implements two face ports — callers still depend
only on their narrow port (ISP), and the factory exposes two typed getters (DIP).

查證結論 (see the batch-upload/moderation plan):
  * 視覺：Rekognition ``StartContentModeration`` (video, async) → ``GetContentModeration``；
    語言無關，適合 zh-TW 直播影格 (裸露/暴力/武器…)。start/poll 拆分讓 Step Functions 等待。
  * 文字：Transcribe ToxicityDetection 只支援 en-US、Comprehend toxicity 不支援 zh-TW，
    皆不可用 → 改用 Bedrock Converse + 強制 tool use 做 zh-TW 受約束分類 (mirror bedrock_nova)。

Real 走 boto3；Stub 一律回「安全」(無標籤/無命中) 供離線與測試。
"""
from __future__ import annotations

from typing import Any

from app.aws.config import AttributionConfig
from app.aws.rekognition import parse_s3_uri
from app.settings import Settings

_TOOL_NAME = "report_moderation"
_TEXT_SYSTEM_PROMPT = (
    "你是繁體中文內容安全審核員。閱讀提供的文字片段，找出違反社群規範的內容："
    "仇恨/歧視(hate)、騷擾/霸凌(harassment)、色情/性(sexual)、暴力/血腥(violence)、"
    "自我傷害(self_harm)、違法/危險(illegal)。只回報真正命中的片段，"
    "每筆給 category 與 severity(0–1，越高越嚴重) 與簡短 quote。無命中則回空清單。"
    "不得臆造；正常的直播聊天、遊戲用語、口語誇飾不算違規。"
)

# Rekognition top-level moderation categories worth surfacing (ParentName). We keep
# all returned labels above MinConfidence; the pure policy decides FLAG/BLOCK.


def _text_tool_config() -> dict[str, Any]:
    """Converse toolConfig forcing a single structured moderation report."""
    return {
        "toolChoice": {"tool": {"name": _TOOL_NAME}},
        "tools": [{
            "toolSpec": {
                "name": _TOOL_NAME,
                "description": "回報命中的內容安全問題清單。",
                "inputSchema": {"json": {
                    "type": "object",
                    "properties": {
                        "findings": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "index": {"type": "integer", "description": "命中的 item 索引 (0-based)"},
                                    "category": {
                                        "type": "string",
                                        "enum": ["hate", "harassment", "sexual", "violence", "self_harm", "illegal"],
                                    },
                                    "severity": {"type": "number"},
                                    "quote": {"type": "string"},
                                },
                                "required": ["index", "category", "severity"],
                            },
                        }
                    },
                    "required": ["findings"],
                }},
            }
        }],
    }


def parse_text_findings(
    response: dict[str, Any], items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Extract toolUse.input.findings from a Converse response → normalized findings
    carrying the original item's ``source``. Pure (offline-testable)."""
    content = (response.get("output", {}).get("message", {}) or {}).get("content", []) or []
    out: list[dict[str, Any]] = []
    for block in content:
        tool = block.get("toolUse")
        if not tool or tool.get("name") != _TOOL_NAME:
            continue
        for f in (tool.get("input") or {}).get("findings", []) or []:
            idx = f.get("index")
            source = items[idx]["source"] if isinstance(idx, int) and 0 <= idx < len(items) else None
            try:
                sev = max(0.0, min(1.0, float(f.get("severity", 0.0))))
            except (TypeError, ValueError):
                sev = 0.0
            out.append({
                "source": source,
                "category": f.get("category"),
                "severity": sev,
                "quote": (f.get("quote") or "")[:200],
            })
    return out


def normalize_moderation_labels(raw_labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """GetContentModeration ``ModerationLabels[]`` → normalized labels. Pure."""
    labels: list[dict[str, Any]] = []
    for ml in raw_labels or []:
        lbl = ml.get("ModerationLabel") or {}
        name = lbl.get("Name")
        if not name:
            continue
        labels.append({
            "name": name,
            "parent_name": lbl.get("ParentName") or None,
            "confidence": float(lbl.get("Confidence", 0.0) or 0.0),
            "timestamp_ms": int(ml.get("Timestamp", 0) or 0),
        })
    return labels


class RealContentModeration:
    """boto3 Rekognition (visual) + Bedrock (text) — implements both ports."""

    def __init__(self, settings: Settings, config: AttributionConfig) -> None:
        import boto3  # lazy

        self._settings = settings
        self._config = config
        self._rek = boto3.client("rekognition", region_name=settings.aws_region)
        self._bedrock = boto3.client("bedrock-runtime", region_name=config.bedrock_region)

    # --- VisualModerationPort ---
    def start_visual_moderation(
        self, project_id: str, media_uri: str, *, min_confidence: float
    ) -> str:
        bucket, key = parse_s3_uri(media_uri)
        resp = self._rek.start_content_moderation(
            Video={"S3Object": {"Bucket": bucket, "Name": key}},
            MinConfidence=float(min_confidence),
            # Idempotency: retried starts for the same project reuse the job.
            ClientRequestToken=f"mod-{project_id}"[:64],
        )
        return resp["JobId"]

    def poll_visual_moderation(self, job_id: str) -> dict[str, Any]:
        resp = self._rek.get_content_moderation(JobId=job_id, MaxResults=1000)
        status = resp["JobStatus"]
        if status == "SUCCEEDED":
            labels = list(resp.get("ModerationLabels", []))
            token = resp.get("NextToken")
            while token:
                page = self._rek.get_content_moderation(
                    JobId=job_id, MaxResults=1000, NextToken=token
                )
                labels.extend(page.get("ModerationLabels", []))
                token = page.get("NextToken")
            return {"status": "COMPLETED", "labels": normalize_moderation_labels(labels)}
        if status == "FAILED":
            return {"status": "FAILED", "labels": [], "reason": resp.get("StatusMessage")}
        return {"status": "IN_PROGRESS", "labels": []}

    # --- TextModerationPort ---
    def moderate_text(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        import json

        clean = [it for it in items if (it.get("text") or "").strip()]
        if not clean:
            return []
        payload = [{"index": i, "source": it.get("source"), "text": (it["text"] or "")[:1000]}
                   for i, it in enumerate(clean)]
        resp = self._bedrock.converse(
            modelId=self._config.moderation_model_id,
            system=[{"text": _TEXT_SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": json.dumps(payload, ensure_ascii=False)}]}],
            inferenceConfig={"temperature": 0, "topP": 1, "maxTokens": 1024},
            toolConfig=_text_tool_config(),
        )
        return parse_text_findings(resp, clean)


class StubContentModeration:
    """離線替身：一律「安全」(無視覺標籤、無文字命中)。供本機/測試。"""

    def __init__(self, settings: Settings, config: AttributionConfig) -> None:
        self._settings = settings
        self._config = config

    def start_visual_moderation(
        self, project_id: str, media_uri: str, *, min_confidence: float
    ) -> str:
        return f"stub-mod-{project_id}"

    def poll_visual_moderation(self, job_id: str) -> dict[str, Any]:
        return {"status": "COMPLETED", "labels": []}

    def moderate_text(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return []
