"""Runtime configuration for the Editor API.

Dependency-light: reads from ``os.environ`` so the walking skeleton runs offline
by default (in-memory stores) and switches to real AWS (DynamoDB / S3) when
``USE_INMEMORY=0`` and the bucket/table env vars are provided.

Region defaults to ``us-east-1`` to match the checked-in infra / openapi server
URL (override with ``AWS_REGION``). See plan flags re: Tokyo vs us-east-1.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    env: str
    aws_region: str
    dynamodb_table: str
    raw_bucket: str
    work_bucket: str
    output_bucket: str
    use_inmemory: bool
    presign_expiry_sec: int
    # Defaulted so tests/helpers that construct Settings() directly keep working.
    max_upload_bytes: int = 10 * 1024**3
    max_batch_files: int = 20
    # Content moderation feature flag. When False the publish gates are skipped
    # (pre-moderation projects / disabled deployments are never blocked).
    moderation_enabled: bool = True
    # S3 presigned-URL addressing style: "auto" (default, virtual-hosted
    # <bucket>.s3.amazonaws.com), "virtual", or "path" (s3.amazonaws.com/<bucket>).
    # Set "path" when a client network can't resolve the per-bucket virtual-hosted
    # subdomain (browser ERR_NAME_NOT_RESOLVED on multipart part PUT).
    s3_addressing_style: str = "auto"
    # Fail-safe for the chat pipeline: when we cannot extract the video's
    # creation_time (the only chat↔video timebase anchor), block auto-render
    # instead of shipping a mis-timed clip cut against a fabricated chat-relative
    # timebase. Enforced only on the real cloud path (USE_INMEMORY=0); offline
    # stubs have no real MP4 and stay on the chat-relative fallback.
    require_video_timebase: bool = True
    # Honor the dev-only X-Tenant-Id / X-User-Id / X-Roles headers (+ unverified
    # Bearer decode) for identity when there are NO gateway-verified JWT claims.
    # Default on for local/pytest; the deployed Lambda sets AUTH_DEV_HEADERS=0 so
    # header-based identity/role self-grant is impossible in production. See auth.py.
    auth_dev_headers: bool = True

    def source_key(
        self, tenant_id: str, project_id: str, filename: str = "source.mp4", *, batch_id: str | None = None
    ) -> str:
        """Raw-bucket object key (demand.md §十六). Batch uploads (WS6) nest the source
        under a shared ``batch={batch_id}`` prefix so a batch's files sit together in
        S3 under one timestamp; each file is still its own project + parallel run."""
        prefix = f"tenant={tenant_id}"
        if batch_id:
            prefix += f"/batch={batch_id}"
        return f"{prefix}/project={project_id}/source/{filename}"

    def chat_key(
        self, tenant_id: str, project_id: str, filename: str = "chat.csv", *, batch_id: str | None = None
    ) -> str:
        """Raw-bucket key for the uploaded chat-room log CSV (聊天優先分析輸入)."""
        return self.source_key(tenant_id, project_id, filename, batch_id=batch_id)

    def _project_prefix(self, tenant_id: str, project_id: str) -> str:
        return f"tenant={tenant_id}/project={project_id}"

    def transcript_key(self, tenant_id: str, project_id: str) -> str:
        """Work-bucket key for the normalized transcript.v1 (§十六)."""
        return f"{self._project_prefix(tenant_id, project_id)}/transcript/transcript.v1.json"
    def chatlog_key(self, tenant_id: str, project_id: str) -> str:
        """Work-bucket key for the normalized chatlog.v1 (分析中間產物)."""
        return f"{self._project_prefix(tenant_id, project_id)}/chatlog/chatlog.json"

    def annotations_key(self, tenant_id: str, project_id: str) -> str:
        """Work-bucket key for the structured annotations.v1 (5 維度標註 + 敘事節拍)."""
        return f"{self._project_prefix(tenant_id, project_id)}/annotations/annotations.json"

    def moderation_key(self, tenant_id: str, project_id: str) -> str:
        """Work-bucket key for the moderation.v1 result doc (findings detail)."""
        return f"{self._project_prefix(tenant_id, project_id)}/moderation/moderation.json"

    def timeline_key(self, tenant_id: str, project_id: str, version: int) -> str:
        """Work-bucket key for a timeline version (§十六)."""
        return f"{self._project_prefix(tenant_id, project_id)}/timelines/version={version}/timeline.json"

    def render_key(self, tenant_id: str, project_id: str, render_id: str, filename: str) -> str:
        """Work-bucket key for a render's plan file (§十六)."""
        return f"{self._project_prefix(tenant_id, project_id)}/renders/render={render_id}/{filename}"

    def artifact_output_key(
        self, tenant_id: str, project_id: str, artifact_id: str, filename: str
    ) -> str:
        """Output-bucket key for a published artifact file (§十六)."""
        return f"{self._project_prefix(tenant_id, project_id)}/artifacts/artifact={artifact_id}/{filename}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    env = os.environ.get("ENV", "dev")
    return Settings(
        env=env,
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        dynamodb_table=os.environ.get("DYNAMODB_TABLE", "VideoEditor"),
        raw_bucket=os.environ.get("RAW_BUCKET", f"video-editor-raw-{env}"),
        work_bucket=os.environ.get("WORK_BUCKET", f"video-editor-work-{env}"),
        output_bucket=os.environ.get("OUTPUT_BUCKET", f"video-editor-output-{env}"),
        # Default to in-memory so local uvicorn + pytest work with no AWS creds.
        # Set USE_INMEMORY=0 to hit real DynamoDB/S3 (or moto in tests).
        use_inmemory=_env_bool("USE_INMEMORY", default=True),
        # 6h default: a 10GB upload at ~15Mbps takes 90+ min, so the old 900s
        # (15 min) guaranteed mid-flight expiry. Bounded by the Lambda role's
        # temp-credential lifetime, so 6h is a practical ceiling. See batch-upload plan.
        presign_expiry_sec=int(os.environ.get("PRESIGN_EXPIRY_SEC", "21600")),
        # Per-file upload cap (default 10GB) and per-batch file-count cap (default 20).
        max_upload_bytes=int(os.environ.get("MAX_UPLOAD_BYTES", str(10 * 1024**3))),
        max_batch_files=int(os.environ.get("MAX_BATCH_FILES", "20")),
        moderation_enabled=_env_bool("MODERATION_ENABLED", default=True),
        s3_addressing_style=os.environ.get("S3_ADDRESSING_STYLE", "auto"),
        require_video_timebase=_env_bool("REQUIRE_VIDEO_TIMEBASE", default=True),
        auth_dev_headers=_env_bool("AUTH_DEV_HEADERS", default=True),
    )
