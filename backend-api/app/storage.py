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

# S3 requires every part except the last to be >= 5 MiB. Use 16 MiB as the
# default chunk when deriving a part count from a file size: a 10GB file →
# 640 parts (well under the 10,000-part cap; headroom to ~156GB), and the
# presign response (~640 URLs) stays well under Lambda's 6MB sync payload.
_PART_SIZE_BYTES = 16 * 1024 * 1024
_MAX_PARTS = 10_000


def resolve_part_count(part_count: int | None, size_bytes: int | None) -> int:
    """Decide how many multipart parts to presign."""
    if part_count and part_count > 0:
        return min(part_count, _MAX_PARTS)
    if size_bytes and size_bytes > 0:
        return min(max(1, math.ceil(size_bytes / _PART_SIZE_BYTES)), _MAX_PARTS)
    return 1


# Filename chars that must never reach a Content-Disposition header: the quote
# that would break out of filename="…", path separators, and every C0/C7F
# control char (covers CR/LF header-injection). Everything else passes through.
_UNSAFE_FILENAME_CHARS = (
    {ord(c): None for c in '"/\\'}
    | {c: None for c in range(0x20)}
    | {0x7F: None}
)


def content_disposition(disposition: str, filename: str | None = None) -> str:
    """Build a ``Content-Disposition`` header value.

    ``filename`` is stripped of quotes / path separators / all control chars so
    it can't break out of the header or smuggle a path (header-injection
    hardening)."""
    if disposition == "inline" or not filename:
        return disposition
    safe = filename.translate(_UNSAFE_FILENAME_CHARS).strip() or "download"
    return f'{disposition}; filename="{safe}"'


class Storage(abc.ABC):
    @abc.abstractmethod
    def create_upload_session(
        self, key: str, part_count: int, content_type: str | None = None
    ) -> dict[str, Any]:
        """Return {upload_id, bucket, key, parts:[{part_number,url}], expires_in_sec}."""

    @abc.abstractmethod
    def complete_multipart_upload(
        self, key: str, upload_id: str, parts: list[dict[str, Any]]
    ) -> None:
        """Finalize a multipart upload in the raw bucket. ``parts`` is a list of
        ``{part_number, etag}`` collected by the browser."""

    @abc.abstractmethod
    def presigned_get(
        self,
        bucket: str,
        key: str,
        *,
        disposition: str | None = None,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str:
        """Return a presigned GET URL for an object.

        ``disposition`` ("inline" | "attachment") sets ``Content-Disposition`` on
        the signed response: "inline" streams into an in-page ``<video>``,
        "attachment" forces a save-to-disk download (with ``filename``). Default
        None preserves S3's stored metadata (legacy behaviour). ``content_type``
        overrides the served ``Content-Type`` (e.g. force ``video/mp4``)."""

    @abc.abstractmethod
    def presigned_put(self, bucket: str, key: str, content_type: str | None = None) -> str:
        """Return a presigned single-part PUT URL (for small direct uploads, e.g. chat.csv)."""

    @abc.abstractmethod
    def get_bytes(self, bucket: str, key: str) -> bytes:
        """Read raw bytes at ``bucket/key``. Raises ``KeyError`` if absent."""

    @abc.abstractmethod
    def download_to_file(self, bucket: str, key: str, dest_path: str) -> None:
        """Stream ``bucket/key`` to a local file (no full-object RAM load).

        Used by the FFmpeg render worker to pull a multi-GB source.mp4 without
        buffering it in memory. Raises ``KeyError`` if the object is absent."""

    @abc.abstractmethod
    def put_json(self, bucket: str, key: str, doc: dict[str, Any]) -> str:
        """Write ``doc`` as JSON to ``bucket/key``. Returns the key."""

    @abc.abstractmethod
    def get_json(self, bucket: str, key: str) -> dict[str, Any]:
        """Read+parse the JSON object at ``bucket/key``. Raises ``KeyError`` if absent."""

    @abc.abstractmethod
    def put_bytes(self, bucket: str, key: str, data: bytes, content_type: str) -> str:
        """Write raw bytes to ``bucket/key``. Returns the key."""

    @abc.abstractmethod
    def head_size(self, bucket: str, key: str) -> int:
        """Return the byte size of ``bucket/key`` without downloading it.

        Used by the transcribe split step to decide whether a source exceeds
        Amazon Transcribe's 2GB input limit. Raises ``KeyError`` if absent."""

    @abc.abstractmethod
    def upload_file(self, bucket: str, key: str, src_path: str, content_type: str | None = None) -> str:
        """Stream a local file to ``bucket/key`` (no full-object RAM load).

        Counterpart of ``download_to_file`` for writing multi-hundred-MB media
        segments back to S3 without buffering them in memory like ``put_bytes``.
        Returns the key."""


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

    def complete_multipart_upload(
        self, key: str, upload_id: str, parts: list[dict[str, Any]]
    ) -> None:
        # No real multipart offline; materialize a placeholder object so
        # object_exists-style checks / downstream reads don't 404.
        self._blobs[(self._settings.raw_bucket, key)] = b"stub-source"

    def presigned_get(
        self,
        bucket: str,
        key: str,
        *,
        disposition: str | None = None,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str:
        # Offline stub: disposition/content_type are irrelevant (no real S3 to
        # sign against), accept them for signature parity with S3Storage.
        return f"http://localhost:8080/stub-download/{bucket}/{key}"

    def presigned_put(self, bucket: str, key: str, content_type: str | None = None) -> str:
        return f"http://localhost:8080/stub-upload/{bucket}/{key}"

    def get_bytes(self, bucket: str, key: str) -> bytes:
        try:
            return self._blobs[(bucket, key)]
        except KeyError:
            raise KeyError(f"no object at {bucket}/{key}") from None

    def download_to_file(self, bucket: str, key: str, dest_path: str) -> None:
        try:
            data = self._blobs[(bucket, key)]
        except KeyError:
            raise KeyError(f"no object at {bucket}/{key}") from None
        with open(dest_path, "wb") as fh:
            fh.write(data)

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

    def head_size(self, bucket: str, key: str) -> int:
        try:
            return len(self._blobs[(bucket, key)])
        except KeyError:
            raise KeyError(f"no object at {bucket}/{key}") from None

    def upload_file(self, bucket: str, key: str, src_path: str, content_type: str | None = None) -> str:
        with open(src_path, "rb") as fh:
            self._blobs[(bucket, key)] = fh.read()
        return key


class S3Storage(Storage):
    def __init__(self, settings: Settings) -> None:
        import boto3  # lazy import
        from botocore.config import Config

        self._settings = settings
        # Force SigV4 presigning. A SigV2 presigned PUT bakes a Content-Type into
        # the signature (StringToSign), so a browser PUT that doesn't send that
        # exact Content-Type gets SignatureDoesNotMatch (403). SigV4 signs only the
        # host (UNSIGNED-PAYLOAD), so the browser's presigned video/chat upload works.
        # addressing_style: "path" makes presigned URLs use s3.amazonaws.com/<bucket>/…
        # instead of the per-bucket virtual-hosted subdomain, which some client
        # networks/resolvers fail to resolve (browser ERR_NAME_NOT_RESOLVED). "auto"
        # (default) preserves boto3's virtual-hosted behaviour.
        self._client = boto3.client(
            "s3",
            region_name=settings.aws_region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": settings.s3_addressing_style},
            ),
        )

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

    def complete_multipart_upload(
        self, key: str, upload_id: str, parts: list[dict[str, Any]]
    ) -> None:
        ordered = sorted(parts, key=lambda p: int(p["part_number"]))
        self._client.complete_multipart_upload(
            Bucket=self._settings.raw_bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={
                "Parts": [
                    {"ETag": p["etag"], "PartNumber": int(p["part_number"])}
                    for p in ordered
                ]
            },
        )

    def presigned_get(
        self,
        bucket: str,
        key: str,
        *,
        disposition: str | None = None,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str:
        params: dict[str, Any] = {"Bucket": bucket, "Key": key}
        if disposition:
            # Signed response-header override. Must be set at signing time — it is
            # part of the SigV4 canonical query string, so it can't be added later.
            params["ResponseContentDisposition"] = content_disposition(disposition, filename)
        if content_type:
            params["ResponseContentType"] = content_type
        return self._client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=self._settings.presign_expiry_sec,
        )

    def presigned_put(self, bucket: str, key: str, content_type: str | None = None) -> str:
        params: dict[str, Any] = {"Bucket": bucket, "Key": key}
        if content_type:
            params["ContentType"] = content_type
        return self._client.generate_presigned_url(
            "put_object",
            Params=params,
            ExpiresIn=self._settings.presign_expiry_sec,
        )

    def get_bytes(self, bucket: str, key: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            resp = self._client.get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                raise KeyError(f"no object at {bucket}/{key}") from exc
            raise
        return resp["Body"].read()

    def download_to_file(self, bucket: str, key: str, dest_path: str) -> None:
        # boto3 managed transfer: streams + multipart, flat memory (safe for
        # multi-GB source.mp4). Maps a missing object to KeyError like get_bytes.
        from botocore.exceptions import ClientError

        try:
            self._client.download_file(bucket, key, dest_path)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "NoSuchBucket"):
                raise KeyError(f"no object at {bucket}/{key}") from exc
            raise

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

    def head_size(self, bucket: str, key: str) -> int:
        from botocore.exceptions import ClientError

        try:
            resp = self._client.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") in ("NoSuchKey", "404", "NotFound"):
                raise KeyError(f"no object at {bucket}/{key}") from exc
            raise
        return int(resp["ContentLength"])

    def upload_file(self, bucket: str, key: str, src_path: str, content_type: str | None = None) -> str:
        # boto3 managed transfer: streams + multipart, flat memory (safe for
        # multi-hundred-MB segments), unlike the RAM-buffered put_bytes.
        extra = {"ContentType": content_type} if content_type else None
        self._client.upload_file(src_path, bucket, key, ExtraArgs=extra)
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
