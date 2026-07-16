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

    # 起承轉合節拍：只有當高光帶「真訊號」(chat_window＝觀眾反應尖峰) 時，才產 annotations
    # 驅動 NarrativeBeat 拆段（punchline 依訊號對齊）。逐字稿路徑的高光沒有 chat_window，
    # build_annotations 會退回「固定比例切分（punchline=最後 20%）」——而高光尾端有 3 秒
    # padding_after，最後 20% 常落在內容結束後的死空氣，NarrativeBeat 又會「保尾、捨中段」而
    # 砍掉真正的爆點。故無訊號時傳 annotations=None → default_planner 退回 ScoreGreedyPlanner
    # （保完整高光、前段裁切保尾段 payoff），避免假結構驅動裁切。keyword 字幕另在 creative_worker
    # 就地建 annotations，不受此影響。
    has_punch_signal = any(h.get("chat_window") for h in highlights)
    annotations = build_annotations(highlights, project_id=project_id) if has_punch_signal else None

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
