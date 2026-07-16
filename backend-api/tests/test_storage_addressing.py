"""S3 presigned-URL addressing style (``S3_ADDRESSING_STYLE`` toggle).

Fixes browser ``ERR_NAME_NOT_RESOLVED`` on multipart part PUT: ``path`` routes
presigned URLs through ``s3.amazonaws.com/<bucket>/`` instead of the per-bucket
virtual-hosted subdomain that some client resolvers fail to resolve. ``auto``
(the default) must keep boto3's existing virtual-hosted behaviour unchanged.

Uses the moto-backed ``aws`` fixture (conftest.py). Presigned-URL generation is
client-side signing (no network), so the host reflects region + addressing style
regardless of moto.
"""
from __future__ import annotations

import pytest

RAW_BUCKET = "video-editor-raw-test"  # created by the conftest ``aws`` fixture


def _settings(style: str):
    from app.settings import Settings

    return Settings(
        env="test",
        aws_region="us-east-1",
        dynamodb_table="VideoEditor",
        raw_bucket=RAW_BUCKET,
        work_bucket="video-editor-work-test",
        output_bucket="video-editor-output-test",
        use_inmemory=False,
        presign_expiry_sec=21600,
        s3_addressing_style=style,
    )


@pytest.mark.parametrize(
    ("style", "expected_host_prefix"),
    [
        # path-style: the fix — canonical host, bucket in the path.
        ("path", f"https://s3.amazonaws.com/{RAW_BUCKET}/"),
        # virtual-hosted: per-bucket subdomain (us-east-1 global endpoint).
        ("virtual", f"https://{RAW_BUCKET}.s3.amazonaws.com/"),
        # auto == current default: must stay virtual-hosted (zero behaviour change on ship).
        ("auto", f"https://{RAW_BUCKET}.s3.amazonaws.com/"),
    ],
)
def test_upload_session_part_url_host_matches_addressing_style(
    aws, style, expected_host_prefix
) -> None:
    from app.storage import S3Storage

    storage = S3Storage(_settings(style))
    session = storage.create_upload_session(
        "tenant=demo/project=p/source/source.mp4", part_count=1
    )

    assert session["parts"][0]["url"].startswith(expected_host_prefix)
