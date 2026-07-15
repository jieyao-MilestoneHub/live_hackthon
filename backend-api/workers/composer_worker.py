"""Composer Worker：highlights.v1 → timeline.v1，append-only 落地並推進狀態。

pure-function I/O：讀 highlights、跑 composer.compose_timeline、寫入**新** timeline
版本（不覆蓋舊版）、更新 latest_timeline_version、把 Project 推進/維持 READY_TO_EDIT
（demand.md §九/§十）。初次組片入口為 COMPOSING;使用者重組（POST /compose）入口為
READY_TO_EDIT,兩者皆 → READY_TO_EDIT。
"""
from __future__ import annotations

from typing import Any, Iterable

from analysis.annotations import build_annotations
from composer import DEFAULT_ASPECT_RATIO, compose_timeline
from app.repository import ProjectRepository
from app.state import ProjectState, assert_project_transition


def run(
    repo: ProjectRepository,
    project_id: str,
    target: int | None = None,
    locked: Iterable[str] | None = None,
    excluded: Iterable[str] | None = None,
    aspect_ratio: str | None = None,
    created_by: str = "composer",
) -> dict[str, Any]:
    """Compose a new timeline version from the project's highlights.

    Returns the timeline.v1 document (with its assigned ``version``).
    Raises ``KeyError`` if the project is missing, ``ValueError`` if there are
    no highlights to compose from.
    """
    project = repo.get_project(project_id)
    if project is None:
        raise KeyError(f"project {project_id} not found")
    assert_project_transition(ProjectState(project["status"]), ProjectState.READY_TO_EDIT)

    highlights = repo.list_highlights(project_id)
    if not highlights:
        raise ValueError(f"project {project_id} has no highlights to compose")

    target_ms = int(target) if target is not None else int(project["target_duration_ms"])
    version = int(project.get("latest_timeline_version") or 0) + 1

    # 起承轉合節拍：由 highlights 就地產生 annotations（chat_window 訊號會對齊 punchline），
    # 供 NarrativeBeat 策略保埋梗+爆梗、不砍爆點。無 chatlog 時 chat_highlights 留言省略、
    # beats 仍成立（compose 只需 beats）。
    annotations = build_annotations(highlights, project_id=project_id)

    timeline = compose_timeline(
        project_id=project_id,
        highlights=highlights,
        target_duration_ms=target_ms,
        locked_ids=tuple(locked or ()),
        excluded_ids=tuple(excluded or ()),
        aspect_ratio=aspect_ratio or DEFAULT_ASPECT_RATIO,
        version=version,
        created_by=created_by,
        annotations=annotations,
    )

    repo.put_timeline(project_id, timeline)  # append-only; raises if version exists
    repo.update_project(
        project_id,
        {
            "latest_timeline_version": version,
            "status": ProjectState.READY_TO_EDIT.value,
        },
    )
    return timeline
