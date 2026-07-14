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

    def source_key(self, tenant_id: str, project_id: str, filename: str = "source.mp4") -> str:
        """Raw-bucket object key per demand.md §十六."""
        return f"tenant={tenant_id}/project={project_id}/source/{filename}"

    def chat_key(self, tenant_id: str, project_id: str, filename: str = "chat.csv") -> str:
        """Raw-bucket key for the uploaded chat-room log CSV (聊天優先分析輸入)."""
        return self.source_key(tenant_id, project_id, filename)

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

    def transcript_key(self, tenant_id: str, project_id: str) -> str:
        """Work-bucket key for the normalized transcript.v1 (AI 精修產生，供稽核/重用)."""
        return f"{self._project_prefix(tenant_id, project_id)}/transcript/transcript.v1.json"

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
        presign_expiry_sec=int(os.environ.get("PRESIGN_EXPIRY_SEC", "900")),
    )
