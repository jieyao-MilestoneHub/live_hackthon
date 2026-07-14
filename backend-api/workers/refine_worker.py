"""Refine Worker：AI 精修（階段 5–6）——transcribe → 提議笑點 offset + 敘事填台詞。

編輯迴圈內的衍生產物，**預設不改 Project 狀態、不自動套用 offset**（只提議，交編輯器
PATCH /highlights 確認，貼合「人工確認」精神；`apply_offsets=True` 才自動套用）。

轉錄用 `app.aws.factory.get_transcriber()`（離線 Stub / 真 Amazon Transcribe），敘事用
`get_narrative_reviewer()`（離線 Stub / 真 Bedrock Nova）——皆依 USE_INMEMORY 綁定。
持久化（enriched annotations、transcript.v1）走主 API 的 Storage（work bucket）。
"""
from __future__ import annotations

from typing import Any

from analysis.annotations import build_annotations
from analysis.chatlog.correction import apply_correction
from analysis.refine import run_refine
from app.aws import factory
from app.aws.config import get_attribution_config
from app.repository import ProjectRepository
from app.settings import Settings
from app.storage import Storage


def run(
    repo: ProjectRepository,
    storage: Storage,
    settings: Settings,
    project_id: str,
    *,
    transcriber: Any = None,
    narrative_reviewer: Any = None,
    apply_offsets: bool = False,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Transcribe → propose punchline offsets + enrich annotations. Persists both.

    Raises ``KeyError`` if the project is missing, ``ValueError`` if it has no highlights.
    Returns ``{proposed_offsets, annotations, transcript_segment_count, applied}``.
    """
    project = repo.get_project(project_id)
    if project is None:
        raise KeyError(f"project {project_id} not found")
    highlights = repo.list_highlights(project_id)
    if not highlights:
        raise ValueError(f"project {project_id} has no highlights to refine")

    tenant = project.get("tenant_id", "demo")
    media_uri = f"s3://{project.get('source_bucket', settings.raw_bucket)}/{project['source_key']}"
    config = get_attribution_config()

    transcriber = transcriber or factory.get_transcriber()
    transcript = transcriber.transcribe(
        project_id, media_uri, language_code=config.language_code, max_speakers=config.max_speaker_labels
    )
    storage.put_json(settings.work_bucket, settings.transcript_key(tenant, project_id), transcript)

    # 既有 annotations（若尚未產生則先用規則式建）。
    try:
        annotations = storage.get_json(settings.work_bucket, settings.annotations_key(tenant, project_id))
    except KeyError:
        try:
            chatlog = storage.get_json(settings.work_bucket, settings.chatlog_key(tenant, project_id))
        except KeyError:
            chatlog = None
        annotations = build_annotations(highlights, chatlog, project_id=project_id)

    result = run_refine(highlights, annotations, transcript, narrative_reviewer=narrative_reviewer)
    storage.put_json(settings.work_bucket, settings.annotations_key(tenant, project_id), result["annotations"])

    applied = 0
    if apply_offsets:
        for off in result["proposed_offsets"]:
            if not off.get("offset_ms"):
                continue
            hl = repo.get_highlight(project_id, off["highlight_id"])
            if hl is None:
                continue
            updated = apply_correction(
                hl,
                offset_ms=off["offset_ms"],
                corrected_by="ai-refine",
                note="AI 逐字稿定位笑點自動校正",
                source_duration_ms=project.get("source_duration_ms"),
            )
            repo.update_highlight(project_id, off["highlight_id"], updated)
            applied += 1

    return {
        "proposed_offsets": result["proposed_offsets"],
        "annotations": result["annotations"],
        "transcript_segment_count": len(transcript.get("segments") or []),
        "applied": applied,
    }
