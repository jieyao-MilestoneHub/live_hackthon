"""Analysis Worker：transcript(+log) → 梗包 → highlights.v1，落地並推進狀態。

pure-function I/O：讀入 transcript、經**梗包偵測器**(工程師之後 drop-in real detector,
本輪為 StubBitDetector)產出梗包、以 adapter 轉為 highlights.v1、寫進 repository、把
Project 自 ANALYZING 推進到 COMPOSING（demand.md §七/§八）。真部署時包成 ai-task
Lambda,輸入(transcript / log 原檔)改自 S3。
"""
from __future__ import annotations

from typing import Any

from analysis import BitDetector, bits_to_highlights, get_bit_detector
from app.repository import ProjectRepository
from app.state import ProjectState, assert_project_transition


def run(
    repo: ProjectRepository,
    project_id: str,
    transcript: dict[str, Any],
    log_info: Any = None,
    detector: BitDetector | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect bit-packages, adapt to highlights.v1, and persist them.

    Precondition: Project status == ANALYZING. Postcondition: highlights stored,
    ``source_duration_ms`` recorded, status advanced to COMPOSING.
    ``detector`` defaults to the factory-bound BitDetector (the engineer's real
    detector drops in there); ``log_info`` is passed through (stub ignores it).
    Returns the highlights.v1 document.
    """
    project = repo.get_project(project_id)
    if project is None:
        raise KeyError(f"project {project_id} not found")
    assert_project_transition(ProjectState(project["status"]), ProjectState.COMPOSING)

    # Tie the analysis output to this project (transcript may carry a different id).
    scoped = {**transcript, "project_id": project_id}
    detector = detector or get_bit_detector()
    bit_packages = detector.detect(scoped, log_info)
    result = bits_to_highlights(bit_packages, params)

    repo.put_highlights(project_id, result["highlights"])
    repo.update_project(
        project_id,
        {
            "source_duration_ms": result["source_duration_ms"],
            "status": ProjectState.COMPOSING.value,
        },
    )
    return result
