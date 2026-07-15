"""Content-moderation decision policy — PURE (no AWS, no IO).

Turns normalized findings (visual labels from Rekognition + text findings from
Bedrock) into a tiered verdict ALLOWED / FLAGGED / BLOCKED, given the configured
severity thresholds. Kept pure + versioned (like ``analysis/chatlog/spam.py``) so
the policy is unit-testable offline and the audit trail can record which
``POLICY_VERSION`` produced a decision.

Severity is normalized to 0–1 for BOTH signals:
  * visual: Rekognition label ``confidence`` (0–100) ÷ 100.
  * text:   Bedrock finding ``severity`` (already 0–1).
The verdict is driven by the single highest-severity signal across both.
"""
from __future__ import annotations

from typing import Any

POLICY_VERSION = "moderation-policy.v1"


def _max_severity(visual_labels: list[dict[str, Any]], text_findings: list[dict[str, Any]]) -> float:
    severities: list[float] = []
    for lbl in visual_labels or []:
        try:
            severities.append(float(lbl.get("confidence", 0.0)) / 100.0)
        except (TypeError, ValueError):
            continue
    for f in text_findings or []:
        try:
            severities.append(float(f.get("severity", 0.0)))
        except (TypeError, ValueError):
            continue
    return max(severities) if severities else 0.0


def decide(
    visual_labels: list[dict[str, Any]] | None,
    text_findings: list[dict[str, Any]] | None,
    *,
    flag_threshold: float,
    block_threshold: float,
) -> dict[str, Any]:
    """Return ``{status, max_severity, policy_version}``.

    status: BLOCKED if max severity ≥ block_threshold; else FLAGGED if ≥
    flag_threshold; else ALLOWED. No findings ⇒ ALLOWED.
    """
    max_sev = _max_severity(visual_labels or [], text_findings or [])
    if max_sev >= block_threshold:
        status = "BLOCKED"
    elif max_sev >= flag_threshold:
        status = "FLAGGED"
    else:
        status = "ALLOWED"
    return {"status": status, "max_severity": round(max_sev, 4), "policy_version": POLICY_VERSION}
