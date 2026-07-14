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
WORK_BUCKET = "video-editor-work-test"
OUTPUT_BUCKET = "video-editor-output-test"
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
    monkeypatch.setenv("WORK_BUCKET", WORK_BUCKET)
    monkeypatch.setenv("OUTPUT_BUCKET", OUTPUT_BUCKET)
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
        s3 = boto3.client("s3", region_name=REGION)
        for bucket in (RAW_BUCKET, WORK_BUCKET, OUTPUT_BUCKET):
            s3.create_bucket(Bucket=bucket)
        yield

    _clear_caches()


@pytest.fixture()
def client(aws):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def ready_project(client):
    """A project seeded to READY_TO_EDIT with highlights + timeline v1.

    Runs the analysis + composer workers against the SAME repo singleton the
    app uses (get_repository() is lru_cached), so HTTP reads see the data.
    Target 60000ms yields a 2-clip timeline for richer assertions.
    """
    from analysis.validate import load_sample
    from app.repository import get_repository
    from app.state import ProjectState, assert_project_transition
    from workers import analysis_worker, composer_worker

    project_id = client.post("/projects", json={"target_duration_ms": 60000}).json()["project_id"]
    repo = get_repository()
    for state in (ProjectState.UPLOAD_PENDING, ProjectState.UPLOADING, ProjectState.ANALYZING):
        current = ProjectState(repo.get_project(project_id)["status"])
        assert_project_transition(current, state)
        repo.update_project(project_id, {"status": state.value})
    analysis_worker.run(repo, project_id, load_sample("transcript.sample.json"))
    composer_worker.run(repo, project_id)
    return project_id


@pytest.fixture()
def ready_render(ready_project):
    """A submitted render (status QUEUED) on a READY_TO_EDIT project.

    Returns (project_id, render_id).
    """
    from app.repository import get_repository
    from app.storage import get_storage
    from workers import creative_worker

    render = creative_worker.submit_render(get_repository(), get_storage(), ready_project)
    return ready_project, render["render_id"]
