"""Project persistence — DynamoDB single-table ``VideoEditor`` + in-memory fallback.

Table layout (demand.md §十七): PK = ``PROJECT#{project_id}``, SK = ``META`` for
the Project item. Other item types (HIGHLIGHT#, TIMELINE#VERSION#, RENDER#,
ARTIFACT#) share the same PK and arrive in M2+.

The repository speaks plain ``dict`` (domain attributes, no PK/SK) so it stays
decoupled from the pydantic API models. ``create_project`` is idempotent via a
conditional put; ``update_project`` bumps an optimistic-lock ``version``.
"""
from __future__ import annotations

import abc
import copy
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from app.settings import Settings, get_settings

_PK = "PK"
_SK = "SK"
_META_SK = "META"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _project_pk(project_id: str) -> str:
    return f"PROJECT#{project_id}"


class ProjectRepository(abc.ABC):
    """Persistence port for the Project META item."""

    @abc.abstractmethod
    def create_project(self, item: dict[str, Any]) -> dict[str, Any]:
        """Persist a new Project. ``item`` must contain ``project_id``.

        Raises ``KeyError`` if a project with that id already exists.
        Returns the stored domain dict (with created_at/updated_at/version set).
        """

    @abc.abstractmethod
    def get_project(self, project_id: str) -> dict[str, Any] | None:
        """Return the Project domain dict, or ``None`` if absent."""

    @abc.abstractmethod
    def update_project(self, project_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Apply ``patch``, bump ``version`` + ``updated_at``. Returns updated dict.

        Raises ``KeyError`` if the project does not exist.
        """


class InMemoryProjectRepository(ProjectRepository):
    """Process-local store for offline dev / tests."""

    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    def create_project(self, item: dict[str, Any]) -> dict[str, Any]:
        project_id = item["project_id"]
        if project_id in self._items:
            raise KeyError(f"project {project_id} already exists")
        now = _now_iso()
        stored = {**item, "created_at": now, "updated_at": now, "version": 0}
        self._items[project_id] = stored
        return copy.deepcopy(stored)

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        found = self._items.get(project_id)
        return copy.deepcopy(found) if found is not None else None

    def update_project(self, project_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = self._items.get(project_id)
        if current is None:
            raise KeyError(f"project {project_id} not found")
        current.update(patch)
        current["version"] = int(current.get("version", 0)) + 1
        current["updated_at"] = _now_iso()
        return copy.deepcopy(current)


def _coerce_numbers(value: Any) -> Any:
    """Convert DynamoDB Decimals back to int/float for JSON-friendly output."""
    from decimal import Decimal

    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [_coerce_numbers(v) for v in value]
    if isinstance(value, dict):
        return {k: _coerce_numbers(v) for k, v in value.items()}
    return value


class DynamoProjectRepository(ProjectRepository):
    """DynamoDB-backed implementation (single table ``VideoEditor``)."""

    def __init__(self, settings: Settings) -> None:
        import boto3  # lazy: only import when actually using AWS

        self._table = boto3.resource(
            "dynamodb", region_name=settings.aws_region
        ).Table(settings.dynamodb_table)

    @staticmethod
    def _strip_keys(item: dict[str, Any]) -> dict[str, Any]:
        return _coerce_numbers({k: v for k, v in item.items() if k not in (_PK, _SK)})

    def create_project(self, item: dict[str, Any]) -> dict[str, Any]:
        from botocore.exceptions import ClientError

        project_id = item["project_id"]
        now = _now_iso()
        record = {
            _PK: _project_pk(project_id),
            _SK: _META_SK,
            **{k: v for k, v in item.items() if v is not None},
            "created_at": now,
            "updated_at": now,
            "version": 0,
        }
        try:
            self._table.put_item(
                Item=record,
                ConditionExpression="attribute_not_exists(#pk)",
                ExpressionAttributeNames={"#pk": _PK},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise KeyError(f"project {project_id} already exists") from exc
            raise
        return self._strip_keys(record)

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        resp = self._table.get_item(Key={_PK: _project_pk(project_id), _SK: _META_SK})
        item = resp.get("Item")
        return self._strip_keys(item) if item else None

    def update_project(self, project_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        # Build a dynamic SET expression: patch fields + version + updated_at.
        set_parts = ["#version = #version + :one", "updated_at = :now"]
        names: dict[str, str] = {"#version": "version"}
        values: dict[str, Any] = {":one": 1, ":now": _now_iso()}
        for i, (k, v) in enumerate(patch.items()):
            names[f"#f{i}"] = k
            values[f":v{i}"] = v
            set_parts.append(f"#f{i} = :v{i}")

        from botocore.exceptions import ClientError

        try:
            resp = self._table.update_item(
                Key={_PK: _project_pk(project_id), _SK: _META_SK},
                UpdateExpression="SET " + ", ".join(set_parts),
                ConditionExpression="attribute_exists(#pk)",
                ExpressionAttributeNames={**names, "#pk": _PK},
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise KeyError(f"project {project_id} not found") from exc
            raise
        return self._strip_keys(resp["Attributes"])


@lru_cache(maxsize=1)
def get_repository() -> ProjectRepository:
    """FastAPI dependency: pick the repo per settings. Cached as a singleton.

    Tests set env then call ``get_repository.cache_clear()``.
    """
    settings = get_settings()
    if settings.use_inmemory:
        return InMemoryProjectRepository()
    return DynamoProjectRepository(settings)
