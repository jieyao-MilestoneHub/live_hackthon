"""Analysis Worker：transcript.v1 → highlights.v1，落地並推進狀態。

pure-function I/O：讀入 transcript、產出 highlights、寫進 repository、把 Project
自 ANALYZING 推進到 COMPOSING（demand.md §七/§八）。真部署時包成 ai-task Lambda,
輸入改自 S3/DynamoDB;演算法（analysis.highlights.detect_highlights）不變。
"""
from __future__ import annotations

from typing import Any

from analysis import detect_highlights
from app.repository import ProjectRepository
from app.state import ProjectState, assert_project_transition


def run(
    repo: ProjectRepository,
    project_id: str,
    transcript: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect highlights for a project and persist them.

    Precondition: Project status == ANALYZING. Postcondition: highlights stored,
    ``source_duration_ms`` recorded, status advanced to COMPOSING.
    Returns the highlights.v1 document.
    """
    project = repo.get_project(project_id)
    if project is None:
        raise KeyError(f"project {project_id} not found")
    assert_project_transition(ProjectState(project["status"]), ProjectState.COMPOSING)

    # Tie the analysis output to this project (transcript may carry a different id).
    scoped = {**transcript, "project_id": project_id}
    result = detect_highlights(scoped, params)

    repo.put_highlights(project_id, result["highlights"])
    repo.update_project(
        project_id,
        {
            "source_duration_ms": result["source_duration_ms"],
            "status": ProjectState.COMPOSING.value,
        },
    )
    return result
