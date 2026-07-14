"""Bedrock highlight enrichment — gated, additive, fail-open.

Given highlights.v1 items (already scored/segmented by the deterministic
``analysis.highlights`` detector), ask Amazon Bedrock (Converse + forced tool
use, ``temperature=0``) to write a punchy ``suggested_title`` + ``reason`` for
the top-N highlights — the copy the editor displays.

Design:
  * Gated by ``HIGHLIGHT_LLM_ENRICH`` (default OFF) so the deterministic path
    stays authoritative and offline/pytest never call AWS.
  * Fail-open: any error (no creds, throttling, bad model access) returns the
    input unchanged — the pipeline never breaks because copy generation failed.
  * Orthogonal to the (unmerged) BitDetector seam: it enriches whatever
    highlights.v1 the scorer produced, so it survives either detector.

Note: this uses its own tiny Converse call (title/reason), NOT
``bedrock_nova.review_speaker`` which is speaker-attribution-specific.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from app.aws.config import get_attribution_config

_TOOL_NAME = "write_highlight_copy"
_SYSTEM_PROMPT = (
    "你是短影音的中文文案助手。針對提供的逐字稿片段，寫出吸睛的繁體中文標題"
    "（不超過 14 字）與一句話理由。只能根據內容本身，不得杜撰未出現的資訊。"
)


def enrich_enabled() -> bool:
    return os.environ.get("HIGHLIGHT_LLM_ENRICH", "").strip().lower() in {"1", "true", "yes", "on"}


def _top_n() -> int:
    try:
        return int(os.environ.get("HIGHLIGHT_LLM_TOP_N", "5"))
    except ValueError:
        return 5


def _tool_config() -> dict[str, Any]:
    return {
        "toolChoice": {"tool": {"name": _TOOL_NAME}},
        "tools": [{
            "toolSpec": {
                "name": _TOOL_NAME,
                "description": "為短影音高光片段寫標題與理由。",
                "inputSchema": {"json": {
                    "type": "object",
                    "properties": {
                        "suggested_title": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["suggested_title", "reason"],
                }},
            }
        }],
    }


class _RealEnricher:
    """boto3 Bedrock Converse (forced tool use)."""

    def __init__(self, region: str, model_id: str) -> None:
        import boto3  # lazy

        self._client = boto3.client("bedrock-runtime", region_name=region)
        self._model_id = model_id

    def title_and_reason(self, text: str) -> tuple[str | None, str | None]:
        resp = self._client.converse(
            modelId=self._model_id,
            system=[{"text": _SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": text}]}],
            inferenceConfig={"temperature": 0, "topP": 1, "maxTokens": 256},
            toolConfig=_tool_config(),
        )
        content = (resp.get("output", {}).get("message", {}) or {}).get("content", []) or []
        for block in content:
            tool = block.get("toolUse")
            if tool and tool.get("name") == _TOOL_NAME:
                inp = tool.get("input") or {}
                return inp.get("suggested_title"), inp.get("reason")
        return None, None


@lru_cache(maxsize=1)
def _get_enricher() -> _RealEnricher:
    config = get_attribution_config()
    # Reuse the Nova review model id / bedrock region already configured for
    # speaker-attribution (Micro: cheap in-region text on us-east-1).
    return _RealEnricher(config.bedrock_region, config.nova_review_model_id)


def enrich(highlights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return highlights with LLM-written title/reason on the top-N by score.

    Never raises: on any failure the original list is returned unchanged.
    """
    if not highlights:
        return highlights
    try:
        enricher = _get_enricher()
    except Exception:  # noqa: BLE001 — fail-open (no creds / import error)
        return highlights

    ranked = sorted(
        range(len(highlights)),
        key=lambda i: highlights[i].get("score", 0),
        reverse=True,
    )[: _top_n()]

    out = [dict(h) for h in highlights]
    for i in ranked:
        text = out[i].get("transcript") or out[i].get("text") or ""
        if not text:
            continue
        try:
            title, reason = enricher.title_and_reason(text)
        except Exception:  # noqa: BLE001 — skip this one, keep the rest
            continue
        if title:
            out[i]["suggested_title"] = title
        if reason:
            out[i]["reason"] = reason
    return out
