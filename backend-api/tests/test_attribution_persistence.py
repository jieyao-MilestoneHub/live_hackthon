"""Attribution 持久化測試（自帶 moto fixture，不動共用 conftest）。

InMemory 全面測 + Dynamo/S3（moto）往返；涵蓋名冊、群組標記、具名逐字稿讀寫、單句更正。
"""
from __future__ import annotations

import pytest

from analysis.attribution import fuse
from app.attribution_repository import (
    InMemoryAttributionRepository,
    get_attribution_repository,
)

PEOPLE = [
    {"person_id": "person_001", "display_name": "主播 A", "role": "protagonist", "identity_source": "rekognition_collection"},
    {"person_id": "person_002", "display_name": "來賓 B", "role": "guest", "identity_source": "user_label"},
]

TABLE = "VideoEditor"
WORK_BUCKET = "video-editor-work-test"
REGION = "us-east-1"


def _sample_attributed():
    t = {
        "schema_version": "transcript.v1", "project_id": "project-123",
        "language_code": "zh-TW", "duration_ms": 20000,
        "segments": [
            {"segment_id": "seg_1", "start_ms": 0, "end_ms": 10000, "speaker": "spk_1", "text": "誰做的？"},
        ],
    }
    return fuse(t, [], [], PEOPLE)  # 無證據 → unknown，供更正


def _roundtrip_checks(repo):
    repo.put_people("project-123", PEOPLE)
    assert {p["person_id"] for p in repo.list_people("project-123")} == {"person_001", "person_002"}

    repo.set_cluster_label("project-123", "spk_1", "person_002", corrected_by="user-x")
    assert repo.get_cluster_labels("project-123") == {"spk_1": "person_002"}

    doc = _sample_attributed()
    repo.put_attributed_transcript("project-123", doc)
    got = repo.get_attributed_transcript("project-123")
    assert got is not None and got["project_id"] == "project-123"
    assert repo.get_attributed_transcript("no-such") is None

    # 單句更正：unknown → person_002
    utt_id = doc["utterances"][0]["utterance_id"]
    updated = repo.correct_utterance("project-123", utt_id, "person_002", corrected_by="user-x")
    assert updated is not None
    assert updated["person_id"] == "person_002"
    assert updated["display_name"] == "來賓 B"
    assert updated["attribution"]["method"] == "user_label"
    assert updated["corrected_by"] == "user-x"
    # 落地確認
    reread = repo.get_attributed_transcript("project-123")
    assert reread["utterances"][0]["person_id"] == "person_002"


def test_inmemory_repo_roundtrip():
    _roundtrip_checks(InMemoryAttributionRepository())


@pytest.fixture()
def dynamo(monkeypatch):
    monkeypatch.setenv("USE_INMEMORY", "0")
    monkeypatch.setenv("AWS_REGION", REGION)
    monkeypatch.setenv("DYNAMODB_TABLE", TABLE)
    monkeypatch.setenv("WORK_BUCKET", WORK_BUCKET)
    monkeypatch.setenv("ENV", "test")
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(k, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)

    from app.settings import get_settings
    get_settings.cache_clear()
    get_attribution_repository.cache_clear()

    from moto import mock_aws

    with mock_aws():
        import boto3

        ddb = boto3.client("dynamodb", region_name=REGION)
        ddb.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}, {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        boto3.client("s3", region_name=REGION).create_bucket(Bucket=WORK_BUCKET)
        yield

    get_settings.cache_clear()
    get_attribution_repository.cache_clear()


def test_dynamo_repo_roundtrip(dynamo):
    from app.attribution_repository import DynamoAttributionRepository
    from app.settings import get_settings

    _roundtrip_checks(DynamoAttributionRepository(get_settings()))
