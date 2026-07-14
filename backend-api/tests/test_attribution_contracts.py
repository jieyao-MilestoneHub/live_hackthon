"""Speaker-Attribution 新契約測試（獨立檔，不動共用 test_contracts.py）。

驗證 people.v1 / attributed_transcript.v1 / asd_result.v1 皆為合法 Draft 2020-12、
schema_version.const 對應檔名、樣本通過、且無 seconds/legacy token（沿用治理守門規則）。
"""
from __future__ import annotations

import json
import re

import pytest
from jsonschema import Draft202012Validator

from analysis.attribution.contracts import (
    contracts_dir,
    load_sample,
    validate_asd_result,
    validate_attributed_transcript,
    validate_people,
)

SCHEMAS = ["people.v1", "attributed_transcript.v1", "asd_result.v1"]

SAMPLE_CASES = [
    ("people.sample.json", validate_people),
    ("attributed_transcript.sample.json", validate_attributed_transcript),
    ("asd_result.sample.json", validate_asd_result),
]

_FILES = [
    "people.v1.schema.json",
    "attributed_transcript.v1.schema.json",
    "asd_result.v1.schema.json",
    "samples/people.sample.json",
    "samples/attributed_transcript.sample.json",
    "samples/asd_result.sample.json",
]

_SEC_RE = re.compile(r"\b(\w+)_sec\b")
_BANNED = ("job_id", "clip_id", "VideoJobs")


@pytest.mark.parametrize("name", SCHEMAS)
def test_schema_is_valid_draft202012(name: str) -> None:
    schema = json.loads((contracts_dir() / f"{name}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["schema_version"]["const"] == name


@pytest.mark.parametrize("filename,validator", SAMPLE_CASES)
def test_sample_validates_against_schema(filename: str, validator) -> None:
    validator(load_sample(filename))


def test_no_seconds_or_legacy_keys() -> None:
    root = contracts_dir()
    offenders: list[str] = []
    for rel in _FILES:
        text = (root / rel).read_text(encoding="utf-8")
        for m in _SEC_RE.finditer(text):
            if m.group(0) != "expires_in_sec":
                offenders.append(f"{rel}: '{m.group(0)}'")
        for tok in _BANNED:
            if re.search(rf"\b{tok}\b", text):
                offenders.append(f"{rel}: banned '{tok}'")
    assert not offenders, "legacy tokens leaked:\n  " + "\n  ".join(offenders)
