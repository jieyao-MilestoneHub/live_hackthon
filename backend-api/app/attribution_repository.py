"""Speaker-Attribution 持久化（自成一檔，避免與其他 session 的 repository.py 編輯衝突）。

沿用既有單表 ``VideoEditor``（PK=``PROJECT#{id}``）的樣式，但用**自己的** boto3 資源，
不 import 也不修改 ``app/repository.py`` / ``app/storage.py``：
  * ``PERSON#{person_id}``   — people.v1 名冊項
  * ``SPEAKER#{cluster_id}`` — 使用者對整個 diarization 群組的手動標記
  * 具名逐字稿本體 → work bucket S3 物件（大物件不入 DynamoDB）

InMemory / Dynamo 兩實作對稱；``get_attribution_repository()`` 依 ``settings.use_inmemory`` 選擇。
"""
from __future__ import annotations

import abc
import copy
import json
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from typing import Any

from app.settings import Settings, get_settings

_PK = "PK"
_SK = "SK"
_PERSON_SK_PREFIX = "PERSON#"
_SPEAKER_SK_PREFIX = "SPEAKER#"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _project_pk(project_id: str) -> str:
    return f"PROJECT#{project_id}"


def _attributed_key(project_id: str) -> str:
    return f"attribution/{project_id}/attributed_transcript.v1.json"


def _to_dynamo(value: Any) -> Any:
    """float → Decimal（DynamoDB 不吃 float）。自成一份，避免耦合他人 repository。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [_to_dynamo(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items()}
    return value


def _coerce_numbers(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [_coerce_numbers(v) for v in value]
    if isinstance(value, dict):
        return {k: _coerce_numbers(v) for k, v in value.items()}
    return value


def _apply_correction(doc: dict[str, Any], utterance_id: str, person_id: str | None,
                      roster: dict[str, dict[str, Any]], corrected_by: str) -> dict[str, Any] | None:
    """在具名逐字稿 doc 上就地套用單句更正；回傳被改的 utterance（找不到回 None）。"""
    for utt in doc.get("utterances", []):
        if utt["utterance_id"] != utterance_id:
            continue
        person = roster.get(person_id or "", {})
        utt["person_id"] = person_id
        utt["display_name"] = person.get("display_name") or (person_id or "未知說話者")
        utt["role"] = person.get("role") if person_id else None
        utt["attribution"]["status"] = "confirmed" if person_id else "unknown"
        utt["attribution"]["method"] = "user_label"
        utt["attribution"]["confidence"] = 1.0 if person_id else 0.0
        utt["corrected_by"] = corrected_by
        utt["corrected_at"] = _now_iso()
        return utt
    return None


class AttributionRepository(abc.ABC):
    """人物名冊 / 群組標記 / 具名逐字稿 的持久化 port。"""

    @abc.abstractmethod
    def put_people(self, project_id: str, participants: list[dict[str, Any]]) -> None: ...

    @abc.abstractmethod
    def list_people(self, project_id: str) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    def set_cluster_label(self, project_id: str, cluster_id: str, person_id: str, corrected_by: str) -> None: ...

    @abc.abstractmethod
    def get_cluster_labels(self, project_id: str) -> dict[str, str]: ...

    @abc.abstractmethod
    def put_attributed_transcript(self, project_id: str, doc: dict[str, Any]) -> None: ...

    @abc.abstractmethod
    def get_attributed_transcript(self, project_id: str) -> dict[str, Any] | None: ...

    def correct_utterance(self, project_id: str, utterance_id: str, person_id: str | None,
                          corrected_by: str) -> dict[str, Any] | None:
        """更正單句人物；回傳更新後的 utterance（找不到逐字稿/句子回 None）。"""
        doc = self.get_attributed_transcript(project_id)
        if doc is None:
            return None
        roster = {p["person_id"]: p for p in self.list_people(project_id) if p.get("person_id")}
        updated = _apply_correction(doc, utterance_id, person_id, roster, corrected_by)
        if updated is None:
            return None
        self.put_attributed_transcript(project_id, doc)
        return updated

    def label_cluster(self, project_id: str, cluster_id: str, person_id: str,
                      corrected_by: str) -> int:
        """把整個 diarization 群組標成某人物；傳播到已存逐字稿的所有該群組 utterance。

        回傳被更新的 utterance 數（無已存逐字稿則 0，但群組標記仍會被記下供下次 fuse 使用）。
        """
        self.set_cluster_label(project_id, cluster_id, person_id, corrected_by)
        doc = self.get_attributed_transcript(project_id)
        if doc is None:
            return 0
        roster = {p["person_id"]: p for p in self.list_people(project_id) if p.get("person_id")}
        person = roster.get(person_id, {})
        count = 0
        for utt in doc.get("utterances", []):
            if utt.get("speaker_cluster_id") != cluster_id:
                continue
            utt["person_id"] = person_id
            utt["display_name"] = person.get("display_name") or person_id
            utt["role"] = person.get("role")
            utt["attribution"]["status"] = "confirmed"
            utt["attribution"]["method"] = "user_label"
            utt["attribution"]["confidence"] = 1.0
            utt["corrected_by"] = corrected_by
            utt["corrected_at"] = _now_iso()
            count += 1
        if count:
            self.put_attributed_transcript(project_id, doc)
        return count


class InMemoryAttributionRepository(AttributionRepository):
    def __init__(self) -> None:
        self._people: dict[str, list[dict[str, Any]]] = {}
        self._labels: dict[str, dict[str, dict[str, Any]]] = {}
        self._transcripts: dict[str, dict[str, Any]] = {}

    def put_people(self, project_id: str, participants: list[dict[str, Any]]) -> None:
        existing = {p["person_id"]: p for p in self._people.get(project_id, [])}
        for p in participants:
            existing[p["person_id"]] = copy.deepcopy(p)
        self._people[project_id] = list(existing.values())

    def list_people(self, project_id: str) -> list[dict[str, Any]]:
        return copy.deepcopy(self._people.get(project_id, []))

    def set_cluster_label(self, project_id: str, cluster_id: str, person_id: str, corrected_by: str) -> None:
        self._labels.setdefault(project_id, {})[cluster_id] = {
            "person_id": person_id, "corrected_by": corrected_by, "corrected_at": _now_iso(),
        }

    def get_cluster_labels(self, project_id: str) -> dict[str, str]:
        return {c: v["person_id"] for c, v in self._labels.get(project_id, {}).items()}

    def put_attributed_transcript(self, project_id: str, doc: dict[str, Any]) -> None:
        self._transcripts[project_id] = copy.deepcopy(doc)

    def get_attributed_transcript(self, project_id: str) -> dict[str, Any] | None:
        found = self._transcripts.get(project_id)
        return copy.deepcopy(found) if found is not None else None


class DynamoAttributionRepository(AttributionRepository):
    """DynamoDB（名冊/群組標記）+ S3 work bucket（逐字稿 JSON）。自帶 boto3 資源。"""

    def __init__(self, settings: Settings) -> None:
        import boto3  # lazy

        self._settings = settings
        self._table = boto3.resource("dynamodb", region_name=settings.aws_region).Table(settings.dynamodb_table)
        self._s3 = boto3.client("s3", region_name=settings.aws_region)

    def put_people(self, project_id: str, participants: list[dict[str, Any]]) -> None:
        pk = _project_pk(project_id)
        with self._table.batch_writer() as batch:
            for p in participants:
                item = {_PK: pk, _SK: f"{_PERSON_SK_PREFIX}{p['person_id']}",
                        **{k: v for k, v in p.items() if v is not None}}
                batch.put_item(Item=_to_dynamo(item))

    def list_people(self, project_id: str) -> list[dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        resp = self._table.query(
            KeyConditionExpression=Key(_PK).eq(_project_pk(project_id))
            & Key(_SK).begins_with(_PERSON_SK_PREFIX),
        )
        out = []
        for it in resp.get("Items", []):
            out.append(_coerce_numbers({k: v for k, v in it.items() if k not in (_PK, _SK)}))
        return out

    def set_cluster_label(self, project_id: str, cluster_id: str, person_id: str, corrected_by: str) -> None:
        self._table.put_item(Item=_to_dynamo({
            _PK: _project_pk(project_id), _SK: f"{_SPEAKER_SK_PREFIX}{cluster_id}",
            "cluster_id": cluster_id, "person_id": person_id,
            "corrected_by": corrected_by, "corrected_at": _now_iso(),
        }))

    def get_cluster_labels(self, project_id: str) -> dict[str, str]:
        from boto3.dynamodb.conditions import Key

        resp = self._table.query(
            KeyConditionExpression=Key(_PK).eq(_project_pk(project_id))
            & Key(_SK).begins_with(_SPEAKER_SK_PREFIX),
        )
        return {it["cluster_id"]: it["person_id"] for it in resp.get("Items", []) if it.get("person_id")}

    def put_attributed_transcript(self, project_id: str, doc: dict[str, Any]) -> None:
        self._s3.put_object(
            Bucket=self._settings.work_bucket,
            Key=_attributed_key(project_id),
            Body=json.dumps(doc, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )

    def get_attributed_transcript(self, project_id: str) -> dict[str, Any] | None:
        from botocore.exceptions import ClientError

        try:
            resp = self._s3.get_object(Bucket=self._settings.work_bucket, Key=_attributed_key(project_id))
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise
        return json.loads(resp["Body"].read().decode("utf-8"))


@lru_cache(maxsize=1)
def get_attribution_repository() -> AttributionRepository:
    settings = get_settings()
    if settings.use_inmemory:
        return InMemoryAttributionRepository()
    return DynamoAttributionRepository(settings)
