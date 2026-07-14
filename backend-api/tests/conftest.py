"""Shared pytest fixtures.

The API tests exercise the *real* DynamoDB + S3 code paths via ``moto``
(USE_INMEMORY=0), so the ``VideoEditor`` table and Raw bucket are created in a
mocked AWS account. The settings / repository / storage singletons are lru_cached,
so we clear them around each test to bind them to the active mock backend.
"""
from __future__ import annotations

import pytest

TABLE = "VideoEditor"
RAW_BUCKET = "video-editor-raw-test"
REGION = "us-east-1"


def _clear_caches() -> None:
    from app.repository import get_repository
    from app.settings import get_settings
    from app.storage import get_storage

    for fn in (get_settings, get_repository, get_storage):
        fn.cache_clear()


@pytest.fixture()
def aws(monkeypatch):
    monkeypatch.setenv("USE_INMEMORY", "0")
    monkeypatch.setenv("AWS_REGION", REGION)
    monkeypatch.setenv("DYNAMODB_TABLE", TABLE)
    monkeypatch.setenv("RAW_BUCKET", RAW_BUCKET)
    monkeypatch.setenv("ENV", "test")
    # Fake creds so boto3/moto never touches real AWS.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)

    _clear_caches()

    from moto import mock_aws

    with mock_aws():
        import boto3

        ddb = boto3.client("dynamodb", region_name=REGION)
        ddb.create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        boto3.client("s3", region_name=REGION).create_bucket(Bucket=RAW_BUCKET)
        yield

    _clear_caches()


@pytest.fixture()
def client(aws):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
