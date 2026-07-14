"""Contract tests: every schema is valid Draft 2020-12, every sample validates,
and no legacy seconds / job-keys leak into the Project/millisecond contracts.
"""
from __future__ import annotations

import json
import re

import pytest
from jsonschema import Draft202012Validator

from analysis.validate import (
    contracts_dir,
    load_sample,
    validate_artifact,
    validate_effects,
    validate_highlights,
    validate_render_spec,
    validate_subtitle,
    validate_timeline,
    validate_transcript,
)

SCHEMAS = [
    "transcript.v1",
    "highlights.v1",
    "timeline.v1",
    "subtitle.v1",
    "effects.v1",
    "render_spec.v1",
    "artifact.v1",
]

# (sample filename, validator) — the sample must validate against its schema.
SAMPLE_CASES = [
    ("transcript.sample.json", validate_transcript),
    ("highlights.sample.json", validate_highlights),
    ("timeline.sample.json", validate_timeline),
    ("subtitle.sample.json", validate_subtitle),
    ("effects.sample.json", validate_effects),
    ("render_spec.sample.json", validate_render_spec),
    ("artifact.sample.json", validate_artifact),
]


@pytest.mark.parametrize("name", SCHEMAS)
def test_schema_is_valid_draft202012(name: str) -> None:
    schema = json.loads((contracts_dir() / f"{name}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["schema_version"]["const"] == name


@pytest.mark.parametrize("filename,validator", SAMPLE_CASES)
def test_sample_validates_against_schema(filename: str, validator) -> None:
    validator(load_sample(filename))


_SEC_RE = re.compile(r"\b(\w+)_sec\b")
_BANNED = {
    "job_id": re.compile(r"\bjob_id\b"),
    "clip_id": re.compile(r"\bclip_id\b"),
    "VideoJobs": re.compile(r"\bVideoJobs\b"),
}


def test_no_seconds_or_legacy_keys_in_contracts() -> None:
    """Guardrail: media time must be *_ms; entity key must be project_id/highlight_id.

    ``expires_in_sec`` (URL TTL) and ``batch_job_id`` (AWS Batch id) are allowed.
    """
    root = contracts_dir()
    offenders: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir() or path.name == "README.md":
            continue
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(root)
        for m in _SEC_RE.finditer(text):
            if m.group(0) != "expires_in_sec":
                offenders.append(f"{rel}: media-seconds field '{m.group(0)}'")
        for label, rx in _BANNED.items():
            if rx.search(text):
                offenders.append(f"{rel}: banned token '{label}'")
    assert not offenders, "legacy tokens leaked into contracts/:\n  " + "\n  ".join(offenders)
