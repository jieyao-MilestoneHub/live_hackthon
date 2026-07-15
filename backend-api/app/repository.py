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
_HIGHLIGHT_SK_PREFIX = "HIGHLIGHT#"
_TIMELINE_SK_PREFIX = "TIMELINE#VERSION#"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _project_pk(project_id: str) -> str:
    return f"PROJECT#{project_id}"


def _timeline_sk(version: int) -> str:
    # zero-padded so lexicographic SK order == numeric version order
    return f"{_TIMELINE_SK_PREFIX}{int(version):06d}"


_RENDER_SK_PREFIX = "RENDER#"
_ARTIFACT_SK_PREFIX = "ARTIFACT#"
_MODERATION_SK_PREFIX = "MODERATION#"
_POINTER_SK = "POINTER"


def _moderation_sk(event: dict[str, Any]) -> str:
    """Time-ordered, unique SK so a Query returns the audit trail chronologically
    and a conditional-put never collides (immutability)."""
    return f"{_MODERATION_SK_PREFIX}{event.get('decided_at', '')}#{event['moderation_id']}"


def _render_pk(render_id: str) -> str:
    return f"RENDER#{render_id}"


def _render_sk(render_id: str) -> str:
    return f"{_RENDER_SK_PREFIX}{render_id}"


def _artifact_pk(artifact_id: str) -> str:
    return f"ARTIFACT#{artifact_id}"


def _artifact_sk(artifact_id: str) -> str:
    return f"{_ARTIFACT_SK_PREFIX}{artifact_id}"


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

    @abc.abstractmethod
    def put_highlights(self, project_id: str, highlights: list[dict[str, Any]]) -> None:
        """Persist the project's highlight items (SK ``HIGHLIGHT#{highlight_id}``)."""

    @abc.abstractmethod
    def list_highlights(self, project_id: str) -> list[dict[str, Any]]:
        """Return all highlight items for the project (empty list if none)."""

    @abc.abstractmethod
    def get_highlight(self, project_id: str, highlight_id: str) -> dict[str, Any] | None:
        """Return a single highlight item, or ``None`` if absent."""

    @abc.abstractmethod
    def update_highlight(
        self, project_id: str, highlight_id: str, highlight: dict[str, Any]
    ) -> dict[str, Any]:
        """Replace a single highlight item (editor correction). Raises ``KeyError`` if absent."""

    @abc.abstractmethod
    def put_timeline(self, project_id: str, timeline: dict[str, Any]) -> int:
        """Persist a timeline version (append-only). Returns the version number.

        Raises ``KeyError`` if that version already exists (never overwrite).
        """

    @abc.abstractmethod
    def get_timeline(self, project_id: str, version: int | None = None) -> dict[str, Any] | None:
        """Return a timeline version (latest if ``version`` is None), or ``None``."""

    @abc.abstractmethod
    def put_render(self, project_id: str, render: dict[str, Any]) -> None:
        """Persist a Render item (SK ``RENDER#{render_id}``) + a render_id pointer."""

    @abc.abstractmethod
    def get_render(self, project_id: str, render_id: str) -> dict[str, Any] | None:
        """Return the Render item, or ``None`` if absent."""

    @abc.abstractmethod
    def get_render_by_id(self, render_id: str) -> dict[str, Any] | None:
        """Resolve a render_id (via pointer) to its Render item, or ``None``."""

    @abc.abstractmethod
    def update_render(self, project_id: str, render_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Apply ``patch`` to a Render item. Raises ``KeyError`` if absent."""

    @abc.abstractmethod
    def put_artifact(self, project_id: str, artifact: dict[str, Any]) -> None:
        """Persist an Artifact item (SK ``ARTIFACT#{artifact_id}``) + a pointer."""

    @abc.abstractmethod
    def get_artifact(self, project_id: str, artifact_id: str) -> dict[str, Any] | None:
        """Return the Artifact item, or ``None`` if absent."""

    @abc.abstractmethod
    def get_artifact_by_id(self, artifact_id: str) -> dict[str, Any] | None:
        """Resolve an artifact_id (via pointer) to its Artifact item, or ``None``."""

    @abc.abstractmethod
    def list_artifacts(self, project_id: str) -> list[dict[str, Any]]:
        """Return all Artifact items for a project (dual-track: one per route)."""

    @abc.abstractmethod
    def put_moderation_event(self, project_id: str, event: dict[str, Any]) -> None:
        """Append an IMMUTABLE moderation audit record (SK ``MODERATION#{ts}#{id}``).

        Compliance requirement: existing records are never overwritten. ``event``
        must contain ``moderation_id`` + ``decided_at``. The Project's mutable
        ``moderation_status`` field is set separately via ``update_project``.
        """

    @abc.abstractmethod
    def list_moderation_events(self, project_id: str) -> list[dict[str, Any]]:
        """Return the project's moderation audit trail, oldest→newest (empty if none)."""


class InMemoryProjectRepository(ProjectRepository):
    """Process-local store for offline dev / tests."""

    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}
        self._highlights: dict[str, list[dict[str, Any]]] = {}
        self._timelines: dict[str, dict[int, dict[str, Any]]] = {}
        self._renders: dict[tuple[str, str], dict[str, Any]] = {}
        self._render_pointers: dict[str, str] = {}
        self._artifacts: dict[tuple[str, str], dict[str, Any]] = {}
        self._artifact_pointers: dict[str, str] = {}
        self._moderation: dict[str, list[dict[str, Any]]] = {}

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

    def put_highlights(self, project_id: str, highlights: list[dict[str, Any]]) -> None:
        self._highlights[project_id] = copy.deepcopy(highlights)

    def list_highlights(self, project_id: str) -> list[dict[str, Any]]:
        return copy.deepcopy(self._highlights.get(project_id, []))

    def get_highlight(self, project_id: str, highlight_id: str) -> dict[str, Any] | None:
        for h in self._highlights.get(project_id, []):
            if h["highlight_id"] == highlight_id:
                return copy.deepcopy(h)
        return None

    def update_highlight(
        self, project_id: str, highlight_id: str, highlight: dict[str, Any]
    ) -> dict[str, Any]:
        items = self._highlights.get(project_id, [])
        for i, h in enumerate(items):
            if h["highlight_id"] == highlight_id:
                items[i] = copy.deepcopy(highlight)
                return copy.deepcopy(highlight)
        raise KeyError(f"highlight {highlight_id} not found in {project_id}")

    def put_timeline(self, project_id: str, timeline: dict[str, Any]) -> int:
        version = int(timeline["version"])
        versions = self._timelines.setdefault(project_id, {})
        if version in versions:
            raise KeyError(f"timeline version {version} already exists for {project_id}")
        versions[version] = copy.deepcopy(timeline)
        return version

    def get_timeline(self, project_id: str, version: int | None = None) -> dict[str, Any] | None:
        versions = self._timelines.get(project_id)
        if not versions:
            return None
        target = max(versions) if version is None else version
        found = versions.get(target)
        return copy.deepcopy(found) if found is not None else None

    def put_render(self, project_id: str, render: dict[str, Any]) -> None:
        render_id = render["render_id"]
        self._renders[(project_id, render_id)] = copy.deepcopy(render)
        self._render_pointers[render_id] = project_id

    def get_render(self, project_id: str, render_id: str) -> dict[str, Any] | None:
        found = self._renders.get((project_id, render_id))
        return copy.deepcopy(found) if found is not None else None

    def get_render_by_id(self, render_id: str) -> dict[str, Any] | None:
        project_id = self._render_pointers.get(render_id)
        return self.get_render(project_id, render_id) if project_id else None

    def update_render(self, project_id: str, render_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = self._renders.get((project_id, render_id))
        if current is None:
            raise KeyError(f"render {render_id} not found")
        current.update(patch)
        current["updated_at"] = _now_iso()
        return copy.deepcopy(current)

    def put_artifact(self, project_id: str, artifact: dict[str, Any]) -> None:
        artifact_id = artifact["artifact_id"]
        self._artifacts[(project_id, artifact_id)] = copy.deepcopy(artifact)
        self._artifact_pointers[artifact_id] = project_id

    def get_artifact(self, project_id: str, artifact_id: str) -> dict[str, Any] | None:
        found = self._artifacts.get((project_id, artifact_id))
        return copy.deepcopy(found) if found is not None else None

    def get_artifact_by_id(self, artifact_id: str) -> dict[str, Any] | None:
        project_id = self._artifact_pointers.get(artifact_id)
        return self.get_artifact(project_id, artifact_id) if project_id else None

    def list_artifacts(self, project_id: str) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(a)
            for (pid, _aid), a in self._artifacts.items()
            if pid == project_id
        ]

    def put_moderation_event(self, project_id: str, event: dict[str, Any]) -> None:
        self._moderation.setdefault(project_id, []).append(copy.deepcopy(event))

    def list_moderation_events(self, project_id: str) -> list[dict[str, Any]]:
        events = self._moderation.get(project_id, [])
        return copy.deepcopy(sorted(events, key=lambda e: e.get("decided_at", "")))


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


def _to_dynamo(value: Any) -> Any:
    """Convert Python floats to Decimal (boto3 DynamoDB rejects floats)."""
    from decimal import Decimal

    if isinstance(value, bool):  # bool is a subclass of int — keep as-is
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [_to_dynamo(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items()}
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

    def put_highlights(self, project_id: str, highlights: list[dict[str, Any]]) -> None:
        pk = _project_pk(project_id)
        with self._table.batch_writer() as batch:
            for h in highlights:
                item = {
                    _PK: pk,
                    _SK: f"{_HIGHLIGHT_SK_PREFIX}{h['highlight_id']}",
                    **{k: v for k, v in h.items() if v is not None},
                }
                batch.put_item(Item=_to_dynamo(item))

    def list_highlights(self, project_id: str) -> list[dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        resp = self._table.query(
            KeyConditionExpression=Key(_PK).eq(_project_pk(project_id))
            & Key(_SK).begins_with(_HIGHLIGHT_SK_PREFIX),
        )
        return [self._strip_keys(it) for it in resp.get("Items", [])]

    def get_highlight(self, project_id: str, highlight_id: str) -> dict[str, Any] | None:
        resp = self._table.get_item(
            Key={_PK: _project_pk(project_id), _SK: f"{_HIGHLIGHT_SK_PREFIX}{highlight_id}"}
        )
        item = resp.get("Item")
        return self._strip_keys(item) if item else None

    def update_highlight(
        self, project_id: str, highlight_id: str, highlight: dict[str, Any]
    ) -> dict[str, Any]:
        from botocore.exceptions import ClientError

        record = {
            _PK: _project_pk(project_id),
            _SK: f"{_HIGHLIGHT_SK_PREFIX}{highlight_id}",
            **{k: v for k, v in highlight.items() if v is not None},
        }
        try:
            self._table.put_item(
                Item=_to_dynamo(record),
                ConditionExpression="attribute_exists(#pk)",  # only replace an existing highlight
                ExpressionAttributeNames={"#pk": _PK},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise KeyError(f"highlight {highlight_id} not found in {project_id}") from exc
            raise
        return self._strip_keys(record)

    def put_timeline(self, project_id: str, timeline: dict[str, Any]) -> int:
        from botocore.exceptions import ClientError

        version = int(timeline["version"])
        record = {
            _PK: _project_pk(project_id),
            _SK: _timeline_sk(version),
            **{k: v for k, v in timeline.items() if v is not None},
        }
        try:
            self._table.put_item(
                Item=_to_dynamo(record),
                ConditionExpression="attribute_not_exists(#pk)",
                ExpressionAttributeNames={"#pk": _PK},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise KeyError(
                    f"timeline version {version} already exists for {project_id}"
                ) from exc
            raise
        return version

    def get_timeline(self, project_id: str, version: int | None = None) -> dict[str, Any] | None:
        if version is not None:
            resp = self._table.get_item(
                Key={_PK: _project_pk(project_id), _SK: _timeline_sk(version)}
            )
            item = resp.get("Item")
            return self._strip_keys(item) if item else None

        from boto3.dynamodb.conditions import Key

        resp = self._table.query(
            KeyConditionExpression=Key(_PK).eq(_project_pk(project_id))
            & Key(_SK).begins_with(_TIMELINE_SK_PREFIX),
            ScanIndexForward=False,  # latest version first
            Limit=1,
        )
        items = resp.get("Items", [])
        return self._strip_keys(items[0]) if items else None

    def put_render(self, project_id: str, render: dict[str, Any]) -> None:
        render_id = render["render_id"]
        record = {
            _PK: _project_pk(project_id),
            _SK: _render_sk(render_id),
            **{k: v for k, v in render.items() if v is not None},
        }
        self._table.put_item(Item=_to_dynamo(record))
        # Pointer item so a bare render_id (top-level route) resolves to its project.
        self._table.put_item(
            Item={_PK: _render_pk(render_id), _SK: _POINTER_SK, "project_id": project_id}
        )

    def get_render(self, project_id: str, render_id: str) -> dict[str, Any] | None:
        resp = self._table.get_item(Key={_PK: _project_pk(project_id), _SK: _render_sk(render_id)})
        item = resp.get("Item")
        return self._strip_keys(item) if item else None

    def get_render_by_id(self, render_id: str) -> dict[str, Any] | None:
        resp = self._table.get_item(Key={_PK: _render_pk(render_id), _SK: _POINTER_SK})
        pointer = resp.get("Item")
        if not pointer:
            return None
        return self.get_render(pointer["project_id"], render_id)

    def update_render(self, project_id: str, render_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        set_parts = ["updated_at = :now"]
        names: dict[str, str] = {}
        values: dict[str, Any] = {":now": _now_iso()}
        for i, (k, v) in enumerate(patch.items()):
            names[f"#f{i}"] = k
            values[f":v{i}"] = v
            set_parts.append(f"#f{i} = :v{i}")

        from botocore.exceptions import ClientError

        try:
            resp = self._table.update_item(
                Key={_PK: _project_pk(project_id), _SK: _render_sk(render_id)},
                UpdateExpression="SET " + ", ".join(set_parts),
                ConditionExpression="attribute_exists(#pk)",
                ExpressionAttributeNames={**names, "#pk": _PK},
                ExpressionAttributeValues=_to_dynamo(values),
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise KeyError(f"render {render_id} not found") from exc
            raise
        return self._strip_keys(resp["Attributes"])

    def put_artifact(self, project_id: str, artifact: dict[str, Any]) -> None:
        artifact_id = artifact["artifact_id"]
        record = {
            _PK: _project_pk(project_id),
            _SK: _artifact_sk(artifact_id),
            **{k: v for k, v in artifact.items() if v is not None},
        }
        self._table.put_item(Item=_to_dynamo(record))
        self._table.put_item(
            Item={_PK: _artifact_pk(artifact_id), _SK: _POINTER_SK, "project_id": project_id}
        )

    def get_artifact(self, project_id: str, artifact_id: str) -> dict[str, Any] | None:
        resp = self._table.get_item(
            Key={_PK: _project_pk(project_id), _SK: _artifact_sk(artifact_id)}
        )
        item = resp.get("Item")
        return self._strip_keys(item) if item else None

    def get_artifact_by_id(self, artifact_id: str) -> dict[str, Any] | None:
        resp = self._table.get_item(Key={_PK: _artifact_pk(artifact_id), _SK: _POINTER_SK})
        pointer = resp.get("Item")
        if not pointer:
            return None
        return self.get_artifact(pointer["project_id"], artifact_id)

    def list_artifacts(self, project_id: str) -> list[dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        # Artifact items live under PROJECT#{id} with SK ARTIFACT#{artifact_id};
        # POINTER items live under a different PK (ARTIFACT#{id}), so they never match.
        resp = self._table.query(
            KeyConditionExpression=Key(_PK).eq(_project_pk(project_id))
            & Key(_SK).begins_with(_ARTIFACT_SK_PREFIX),
        )
        return [self._strip_keys(it) for it in resp.get("Items", [])]

    def put_moderation_event(self, project_id: str, event: dict[str, Any]) -> None:
        from botocore.exceptions import ClientError

        record = {
            _PK: _project_pk(project_id),
            _SK: _moderation_sk(event),
            **{k: v for k, v in event.items() if v is not None},
        }
        try:
            # attribute_not_exists ⇒ never overwrite an existing audit record (immutable).
            self._table.put_item(
                Item=_to_dynamo(record),
                ConditionExpression="attribute_not_exists(#pk)",
                ExpressionAttributeNames={"#pk": _PK},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise KeyError(
                    f"moderation event {event.get('moderation_id')} already exists for {project_id}"
                ) from exc
            raise

    def list_moderation_events(self, project_id: str) -> list[dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        resp = self._table.query(
            KeyConditionExpression=Key(_PK).eq(_project_pk(project_id))
            & Key(_SK).begins_with(_MODERATION_SK_PREFIX),
        )
        # SK is time-prefixed, so Query already returns oldest→newest.
        return [self._strip_keys(it) for it in resp.get("Items", [])]


@lru_cache(maxsize=1)
def get_repository() -> ProjectRepository:
    """FastAPI dependency: pick the repo per settings. Cached as a singleton.

    Tests set env then call ``get_repository.cache_clear()``.
    """
    settings = get_settings()
    if settings.use_inmemory:
        return InMemoryProjectRepository()
    return DynamoProjectRepository(settings)
