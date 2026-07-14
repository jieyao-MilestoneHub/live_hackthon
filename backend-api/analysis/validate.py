"""JSON Schema validation helpers for the transcript.v1 / highlights.v1 contracts.

Resolves the shared ``contracts/`` directory in a way that works both in the
monorepo (worktree layout) and inside the container image, then exposes
Draft 2020-12 validators and a sample loader.
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
    """Resolve the contracts directory.

    Tries, in order:
      1. env ``CONTRACTS_DIR``
      2. ``<repo_root>/contracts``  (parents[2] of this file: backend-api/analysis/validate.py)
      3. ``<backend-api>/contracts`` (parents[1]; used inside the container image)
      4. ``<cwd>/contracts``

    Raises a clear error if none contains the expected schema files.
    """
    candidates: list[Path] = []

    env = os.environ.get("CONTRACTS_DIR")
    if env:
        candidates.append(Path(env))

    # backend-api/analysis/validate.py -> parents[2] == repo root
    candidates.append(_THIS.parents[2] / "contracts")
    # parents[1] == backend-api/ (container copies contracts/ under /app/contracts)
    candidates.append(_THIS.parents[1] / "contracts")
    candidates.append(Path.cwd() / "contracts")

    for c in candidates:
        if (c / "transcript.v1.schema.json").is_file():
            return c.resolve()

    tried = "\n  - ".join(str(c) for c in candidates)
    raise FileNotFoundError(
        "Could not locate the contracts directory. Set the CONTRACTS_DIR "
        "environment variable to the folder containing transcript.v1.schema.json.\n"
        f"Tried:\n  - {tried}"
    )


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=None)
def _validator(schema_filename: str) -> Draft202012Validator:
    schema = _load_json(contracts_dir() / schema_filename)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_transcript(doc: dict[str, Any]) -> None:
    """Validate a doc against transcript.v1. Raises jsonschema.ValidationError."""
    _validator("transcript.v1.schema.json").validate(doc)


def validate_highlights(doc: dict[str, Any]) -> None:
    """Validate a doc against highlights.v1. Raises jsonschema.ValidationError."""
    _validator("highlights.v1.schema.json").validate(doc)


def load_sample(name: str) -> dict[str, Any]:
    """Load and parse a JSON sample from ``contracts_dir()/samples/name``."""
    return _load_json(contracts_dir() / "samples" / name)
