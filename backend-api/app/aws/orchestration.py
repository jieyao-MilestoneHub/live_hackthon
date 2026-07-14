"""AWS orchestration adapters — Step Functions StartExecution + SQS SendMessage.

Factory-bound Real/Stub by ``settings.use_inmemory`` (mirrors ``app/aws/factory``).
Lets the control plane (``POST /renders``, optional async ``/compose``) and the
analysis Starter Lambda hand work to the async data plane WITHOUT the control
plane running the heavy work itself (demand.md §十九: control plane submits jobs,
workers do CPU/GPU work).

ARNs / queue URLs come from env (set by Terraform on each Lambda):
  ANALYSIS_STATE_MACHINE_ARN, RENDER_STATE_MACHINE_ARN, AI_TASK_QUEUE_URL

Idempotency: ``start_execution`` swallows ``ExecutionAlreadyExists`` so a
duplicate S3 event (at-least-once, demand.md §六) with the same deterministic
execution name is a no-op rather than a second pipeline run.
"""
from __future__ import annotations

import abc
import json
import os
import re
from functools import lru_cache
from typing import Any

from app.settings import Settings, get_settings

# SFN execution name: 1–80 chars, [a-zA-Z0-9-_]. Sanitize project/render ids.
_EXEC_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")


def sanitize_execution_name(raw: str) -> str:
    return _EXEC_NAME_RE.sub("-", raw)[:80] or "exec"


class Orchestrator(abc.ABC):
    @abc.abstractmethod
    def start_execution(self, state_machine_arn: str, name: str, payload: dict[str, Any]) -> str | None:
        """Start a Step Functions execution. Returns the execution ARN, or
        ``None`` if an execution with that (deterministic) name already exists."""

    @abc.abstractmethod
    def send_message(self, queue_url: str, payload: dict[str, Any]) -> str:
        """Send one SQS message. Returns the message id."""


class StubOrchestrator(Orchestrator):
    """No-AWS stub: records calls in-process so offline/pytest never touch AWS."""

    def __init__(self) -> None:
        self.executions: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []

    def start_execution(self, state_machine_arn: str, name: str, payload: dict[str, Any]) -> str | None:
        exec_name = sanitize_execution_name(name)
        if any(e["name"] == exec_name and e["arn"] == state_machine_arn for e in self.executions):
            return None  # simulate ExecutionAlreadyExists
        self.executions.append({"arn": state_machine_arn, "name": exec_name, "input": payload})
        return f"stub-exec:{state_machine_arn}:{exec_name}"

    def send_message(self, queue_url: str, payload: dict[str, Any]) -> str:
        self.messages.append({"queue_url": queue_url, "payload": payload})
        return f"stub-msg:{len(self.messages)}"


class AwsOrchestrator(Orchestrator):
    def __init__(self, settings: Settings) -> None:
        import boto3  # lazy

        self._sfn = boto3.client("stepfunctions", region_name=settings.aws_region)
        self._sqs = boto3.client("sqs", region_name=settings.aws_region)

    def start_execution(self, state_machine_arn: str, name: str, payload: dict[str, Any]) -> str | None:
        from botocore.exceptions import ClientError

        try:
            resp = self._sfn.start_execution(
                stateMachineArn=state_machine_arn,
                name=sanitize_execution_name(name),
                input=json.dumps(payload, ensure_ascii=False),
            )
            return resp["executionArn"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ExecutionAlreadyExists":
                return None  # idempotent: duplicate event / retry
            raise

    def send_message(self, queue_url: str, payload: dict[str, Any]) -> str:
        return self._sqs.send_message(
            QueueUrl=queue_url, MessageBody=json.dumps(payload, ensure_ascii=False)
        )["MessageId"]


@lru_cache(maxsize=1)
def get_orchestrator() -> Orchestrator:
    settings = get_settings()
    return StubOrchestrator() if settings.use_inmemory else AwsOrchestrator(settings)


def cache_clear() -> None:
    get_orchestrator.cache_clear()


# --- Convenience wrappers (env-configured targets) -------------------------

def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"{name} is not set (orchestration target missing)")
    return val


def start_analysis(
    project_id: str,
    *,
    tenant_id: str | None,
    bucket: str,
    key: str,
    version_id: str | None = None,
) -> str | None:
    """Kick the analysis+composition workflow. Execution name is deterministic
    on project_id+version_id so duplicate S3 events collapse to one run."""
    payload = {
        "project_id": project_id,
        "tenant_id": tenant_id,
        "bucket": bucket,
        "key": key,
        "version_id": version_id,
    }
    # Idempotency key = project_id + version_id — equivalent to demand.md §六's
    # "bucket + key + version_id" (bucket is fixed; key <-> project_id is 1:1 via
    # tenant=/project=/source/). Raw bucket versioning is Enabled, so a re-upload
    # carries a new version_id -> new name -> fresh run. The 'v0' fallback only
    # applies when an event lacks version-id; then a re-upload to the same project
    # reuses '{project_id}-v0' and is swallowed as a duplicate (no re-analysis).
    name = f"{project_id}-{version_id or 'v0'}"
    return get_orchestrator().start_execution(
        _require_env("ANALYSIS_STATE_MACHINE_ARN"), name, payload
    )


def start_render(render_id: str, project_id: str, timeline_version: int) -> str | None:
    """Kick the artifact render workflow. render_id is unique per submission, so
    it doubles as the (idempotent) execution name."""
    payload = {
        "render_id": render_id,
        "project_id": project_id,
        "timeline_version": timeline_version,
    }
    return get_orchestrator().start_execution(
        _require_env("RENDER_STATE_MACHINE_ARN"), render_id, payload
    )


def enqueue_ai_task(payload: dict[str, Any]) -> str:
    """Put a light AI task (e.g. re-compose) on the ai-task queue."""
    return get_orchestrator().send_message(_require_env("AI_TASK_QUEUE_URL"), payload)
