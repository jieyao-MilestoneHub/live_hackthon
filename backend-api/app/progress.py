"""ProgressReporter — 把 pipeline 每個語意步驟即時「統整成人話」並落庫。

SRP：本模組只做「編排」——委派 ``NarratorPort`` 生成訊息、委派 ``ProjectRepository``
落庫；不碰 Bedrock、不碰 DynamoDB 細節。worker/handler 只呼叫 ``reporter.step(...)``。

進度是**附加訊號**：``step()`` 全程吞例外，narration/落庫失敗**絕不**反噬主流程。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.progress_narrator import NarratorPort, get_narrator
from app.repository import ProjectRepository, get_repository


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class StepKey(str, Enum):
    """機器穩定的步驟代碼；``value`` 對齊 ``progress_narrator._TEMPLATES`` 的 key。"""

    UPLOAD_RECEIVED = "UPLOAD_RECEIVED"
    VALIDATING = "VALIDATING"
    TRANSCRIBING = "TRANSCRIBING"
    ANALYZING_CHATLOG = "ANALYZING_CHATLOG"
    DETECTING_HIGHLIGHTS = "DETECTING_HIGHLIGHTS"
    MODERATION_SCAN = "MODERATION_SCAN"
    MODERATION_DECISION = "MODERATION_DECISION"
    COMPOSING = "COMPOSING"
    READY = "READY"
    PLANNING_SUBTITLES = "PLANNING_SUBTITLES"
    PLANNING_EFFECTS = "PLANNING_EFFECTS"
    QUEUED = "QUEUED"
    RENDERING = "RENDERING"
    VALIDATING_ARTIFACT = "VALIDATING_ARTIFACT"
    PUBLISHING = "PUBLISHING"
    DONE = "DONE"
    SUMMARY = "SUMMARY"


class ProgressReporter:
    """組合 narrator + repo；一次呼叫＝生成一句進度旁白並 append 成一列 progress event。"""

    def __init__(self, narrator: NarratorPort, repo: ProjectRepository) -> None:
        self._narrator = narrator
        self._repo = repo

    def step(
        self,
        project_id: str,
        step: StepKey,
        *,
        facts: dict[str, Any] | None = None,
        status: str = "RUNNING",
        phase: str | None = None,
    ) -> None:
        """Narrate + persist one progress event. Never raises."""
        try:
            facts = facts or {}
            message = self._narrator.narrate(step=step.value, facts=facts, status=status)
            event = {
                "schema_version": "progress.v1",
                "progress_id": f"prog-{uuid.uuid4().hex[:12]}",
                "project_id": project_id,
                "step": step.value,
                "phase": phase,
                "status": status,
                "message": message,
                "created_at": _now_iso(),
            }
            self._repo.put_progress_event(project_id, event)
        except Exception:  # noqa: BLE001 — 進度是附加訊號，絕不中斷主流程
            return


def get_progress_reporter() -> ProgressReporter:
    """Compose the reporter from the current repo + narrator singletons.

    Deliberately NOT cached: ``get_repository()`` / ``get_narrator()`` are each
    ``lru_cache`` singletons, so this is cheap — and rebuilding每次 guarantees we
    always bind to the live repo (tests clear ``get_repository`` cache between cases;
    a cached reporter would keep a stale InMemory store)."""
    return ProgressReporter(get_narrator(), get_repository())


# RenderState.value → StepKey，供 render workers 的 _advance 一處埋點覆蓋所有渲染子步。
# 刻意不含 SUCCEEDED —— 收尾改由 render_worker 顯式 emit StepKey.SUMMARY（含全流程統整）。
_RENDER_STATE_STEP = {
    "PLANNING_SUBTITLES": StepKey.PLANNING_SUBTITLES,
    "PLANNING_EFFECTS": StepKey.PLANNING_EFFECTS,
    "QUEUED": StepKey.QUEUED,
    "RENDERING": StepKey.RENDERING,
    "VALIDATING": StepKey.VALIDATING_ARTIFACT,
    "PUBLISHING": StepKey.PUBLISHING,
}


def report_render_stage(project_id: str, render_state: str, *, facts: dict[str, Any] | None = None) -> None:
    """Narrate a render sub-step keyed by RenderState. No-op for unmapped states.
    Never raises (delegates to ``ProgressReporter.step``)."""
    step = _RENDER_STATE_STEP.get(render_state)
    if step is not None:
        get_progress_reporter().step(project_id, step, facts=facts or {}, phase="RENDERING")
