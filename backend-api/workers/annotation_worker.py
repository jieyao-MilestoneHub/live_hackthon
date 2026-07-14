"""Annotation Worker：highlights.v1 (+chatlog.v1) → annotations.v1，落地 work bucket。

編輯迴圈內的衍生產物（階段 7–8 結構化標註），**不改 Project 狀態**。讀入專案已偵測的
highlights 與（若有）正規化聊天 log，跑規則式標註產生器，寫入 work bucket。真部署時包成
ai-task Lambda，輸入改自 S3/DynamoDB，演算法（analysis.annotations.build_annotations）不變。
"""
from __future__ import annotations

from typing import Any

from analysis.annotations import build_annotations
from app.repository import ProjectRepository
from app.settings import Settings
from app.storage import Storage


def run(
    repo: ProjectRepository,
    storage: Storage,
    settings: Settings,
    project_id: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate + persist annotations.v1 for a project. Returns the doc.

    Raises ``KeyError`` if the project is missing, ``ValueError`` if it has no highlights.
    """
    project = repo.get_project(project_id)
    if project is None:
        raise KeyError(f"project {project_id} not found")
    highlights = repo.list_highlights(project_id)
    if not highlights:
        raise ValueError(f"project {project_id} has no highlights to annotate")

    tenant = project.get("tenant_id", "demo")
    try:
        chatlog = storage.get_json(settings.work_bucket, settings.chatlog_key(tenant, project_id))
    except KeyError:
        chatlog = None  # chat_highlights 留言省略；其餘維度/beats 仍由事件窗產生

    doc = build_annotations(highlights, chatlog, project_id=project_id, params=params)
    storage.put_json(settings.work_bucket, settings.annotations_key(tenant, project_id), doc)
    return doc
