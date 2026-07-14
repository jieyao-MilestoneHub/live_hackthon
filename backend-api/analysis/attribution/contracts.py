"""Speaker-Attribution 契約驗證（自成一檔，不編輯共用的 analysis/validate.py）。

自帶 contracts 目錄解析 + Draft 2020-12 驗證器，涵蓋 people.v1 /
attributed_transcript.v1 / asd_result.v1（皆為本功能新增的 additive 契約）。
既有 7 個契約仍用 ``analysis.validate``；此處不重複、不覆蓋。
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_THIS = Path(__file__).resolve()


def contracts_dir() -> Path:
    """解析共用 contracts 目錄（與 analysis/validate.py 同策略，自成一份避免耦合）。"""
    candidates: list[Path] = []
    env = os.environ.get("CONTRACTS_DIR")
    if env:
        candidates.append(Path(env))
    # analysis/attribution/contracts.py -> parents[2] == backend-api/ ；parents[3] == repo root
    candidates.append(_THIS.parents[3] / "contracts")
    candidates.append(_THIS.parents[2] / "contracts")
    candidates.append(Path.cwd() / "contracts")
    for c in candidates:
        if (c / "attributed_transcript.v1.schema.json").is_file():
            return c.resolve()
    tried = "\n  - ".join(str(c) for c in candidates)
    raise FileNotFoundError(
        "Could not locate contracts/ (attributed_transcript.v1.schema.json). "
        f"Set CONTRACTS_DIR.\nTried:\n  - {tried}"
    )


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=None)
def _validator(schema_filename: str) -> Draft202012Validator:
    schema = _load_json(contracts_dir() / schema_filename)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_people(doc: dict[str, Any]) -> None:
    """Validate against people.v1. Raises jsonschema.ValidationError."""
    _validator("people.v1.schema.json").validate(doc)


def validate_attributed_transcript(doc: dict[str, Any]) -> None:
    """Validate against attributed_transcript.v1. Raises jsonschema.ValidationError."""
    _validator("attributed_transcript.v1.schema.json").validate(doc)


def validate_asd_result(doc: dict[str, Any]) -> None:
    """Validate against asd_result.v1. Raises jsonschema.ValidationError."""
    _validator("asd_result.v1.schema.json").validate(doc)


def load_sample(name: str) -> dict[str, Any]:
    """Load a JSON sample from contracts/samples/name."""
    return _load_json(contracts_dir() / "samples" / name)
