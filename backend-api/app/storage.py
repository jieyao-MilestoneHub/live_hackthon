"""S3 storage helpers — presigned multipart upload + presigned GET.

Real path uses boto3 against the Raw bucket (``create_multipart_upload`` +
per-part ``generate_presigned_url('upload_part')``); the browser uploads each
part directly to S3, bypassing the API (demand.md §五). A stub implementation
keeps local uvicorn / offline tests working without AWS credentials.
"""
from __future__ import annotations

import abc
import json
import math
import uuid
from functools import lru_cache
from typing import Any

from app.settings import Settings, get_settings

# S3 requires every part except the last to be >= 5 MiB. Use 8 MiB as the
# default chunk when deriving a part count from a file size.
_PART_SIZE_BYTES = 8 * 1024 * 1024
_MAX_PARTS = 10_000


def resolve_part_count(part_count: int | None, size_bytes: int | None) -> int:
    """Decide how many multipart parts to presign."""
    if part_count and part_count > 0:
        return min(part_count, _MAX_PARTS)
    if size_bytes and size_bytes > 0:
        return min(max(1, math.ceil(size_bytes / _PART_SIZE_BYTES)), _MAX_PARTS)
    return 1


class Storage(abc.ABC):
    @abc.abstractmethod
    def create_upload_session(
        self, key: str, part_count: int, content_type: str | None = None
    ) -> dict[str, Any]:
        """Return {upload_id, bucket, key, parts:[{part_number,url}], expires_in_sec}."""

    @abc.abstractmethod
    def presigned_get(self, bucket: str, key: str) -> str:
        """Return a presigned GET URL for downloading an object."""

    @abc.abstractmethod
    def put_json(self, bucket: str, key: str, doc: dict[str, Any]) -> str:
        """Write ``doc`` as JSON to ``bucket/key``. Returns the key."""

    @abc.abstractmethod
    def get_json(self, bucket: str, key: str) -> dict[str, Any]:
        """Read+parse the JSON object at ``bucket/key``. Raises ``KeyError`` if absent."""

    @abc.abstractmethod
    def put_bytes(self, bucket: str, key: str, data: bytes, content_type: str) -> str:
        """Write raw bytes to ``bucket/key``. Returns the key."""


class StubStorage(Storage):
    """No-AWS stub: fabricates local placeholder URLs, keeps objects in-process."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._objects: dict[tuple[str, str], str] = {}
        self._blobs: dict[tuple[str, str], bytes] = {}

    def create_upload_session(
        self, key: str, part_count: int, content_type: str | None = None
    ) -> dict[str, Any]:
        upload_id = f"stub-upload-{uuid.uuid4().hex}"
        parts = [
            {
                "part_number": n,
                "url": f"http://localhost:8080/stub-upload/{key}?upload_id={upload_id}&part={n}",
            }
            for n in range(1, part_count + 1)
        ]
        return {
            "upload_id": upload_id,
            "bucket": self._settings.raw_bucket,
            "key": key,
            "parts": parts,
            "expires_in_sec": self._settings.presign_expiry_sec,
        }

    def presigned_get(self, bucket: str, key: str) -> str:
        return f"http://localhost:8080/stub-download/{bucket}/{key}"

    def put_json(self, bucket: str, key: str, doc: dict[str, Any]) -> str:
        self._objects[(bucket, key)] = json.dumps(doc, ensure_ascii=False)
        return key

    def get_json(self, bucket: str, key: str) -> dict[str, Any]:
        try:
            return json.loads(self._objects[(bucket, key)])
        except KeyError:
            raise KeyError(f"no object at {bucket}/{key}") from None

    def put_bytes(self, bucket: str, key: str, data: bytes, content_type: str) -> str:
        self._blobs[(bucket, key)] = data
        return key


class S3Storage(Storage):
    def __init__(self, settings: Settings) -> None:
        import boto3  # lazy import

        self._settings = settings
        self._client = boto3.client("s3", region_name=settings.aws_region)

    def create_upload_session(
        self, key: str, part_count: int, content_type: str | None = None
    ) -> dict[str, Any]:
        bucket = self._settings.raw_bucket
        create_args: dict[str, Any] = {"Bucket": bucket, "Key": key}
        if content_type:
            create_args["ContentType"] = content_type
        upload_id = self._client.create_multipart_upload(**create_args)["UploadId"]

        parts = []
        for n in range(1, part_count + 1):
            url = self._client.generate_presigned_url(
                "upload_part",
                Params={"Bucket": bucket, "Key": key, "UploadId": upload_id, "PartNumber": n},
                ExpiresIn=self._settings.presign_expiry_sec,
            )
            parts.append({"part_number": n, "url": url})

        return {
            "upload_id": upload_id,
            "bucket": bucket,
            "key": key,
            "parts": parts,
            "expires_in_sec": self._settings.presign_expiry_sec,
        }

    def presigned_get(self, bucket: str, key: str) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=self._settings.presign_expiry_sec,
        )

    def put_json(self, bucket: str, key: str, doc: dict[str, Any]) -> str:
        self._client.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(doc, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )
        return key

    def get_json(self, bucket: str, key: str) -> dict[str, Any]:
        from botocore.exceptions import ClientError

        try:
            resp = self._client.get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                raise KeyError(f"no object at {bucket}/{key}") from exc
            raise
        return json.loads(resp["Body"].read().decode("utf-8"))

    def put_bytes(self, bucket: str, key: str, data: bytes, content_type: str) -> str:
        self._client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
        return key


@lru_cache(maxsize=1)
def get_storage() -> Storage:
    """FastAPI dependency: pick storage per settings. Cached as a singleton.

    Tests set env then call ``get_storage.cache_clear()``.
    """
    settings = get_settings()
    if settings.use_inmemory:
        return StubStorage(settings)
    return S3Storage(settings)
