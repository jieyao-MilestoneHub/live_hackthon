"""Attribution 編排：adapters → fusion → Nova 複核 → attributed_transcript.v1。

屬既有 ``ANALYZING`` 階段的內部子步驟。所有外部依賴以 Port 注入（DIP）：未注入時由
``app/aws/factory`` 依 ``USE_INMEMORY`` 綁定 Real/Stub，故本函式可完全離線測試。

Nova 只對 ``needs_review`` 片段做語意複核、且僅**填補建議**，不覆蓋高信心臉部/ASD 結果
（依提案第六節）。持久化交給呼叫端（API/worker），保持本層純編排（SRP）。
"""
from __future__ import annotations

from typing import Any

from analysis.attribution.contracts import validate_attributed_transcript
from analysis.attribution.fusion import fuse


def _roster_map(people: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {p["person_id"]: p for p in people if p.get("person_id")}


def _apply_nova_review(
    doc: dict[str, Any],
    people: list[dict[str, Any]],
    reviewer: Any,
) -> dict[str, Any]:
    """對 needs_review 片段請 Nova 提供人物建議（不自動確認）。"""
    candidates = [p["person_id"] for p in people if p.get("person_id")]
    if not candidates or reviewer is None:
        return doc
    roster = _roster_map(people)
    utts = doc["utterances"]
    for i, utt in enumerate(utts):
        if utt["attribution"]["status"] != "needs_review":
            continue
        context = {
            "target_text": utt["text"],
            "candidates": [
                {"person_id": p["person_id"], "role": p.get("role")} for p in people
            ],
            "previous_text": utts[i - 1]["text"] if i > 0 else None,
            "next_text": utts[i + 1]["text"] if i + 1 < len(utts) else None,
        }
        pick = reviewer.review_speaker(candidates, context)
        if pick and pick != "unknown":
            person = roster.get(pick, {})
            utt["person_id"] = pick
            utt["display_name"] = person.get("display_name") or pick
            utt["role"] = person.get("role") or "unknown"
            utt["attribution"]["method"] = "nova_review"
            utt["attribution"].setdefault("evidence", {})["nova_pick"] = pick
    return doc


def run_attribution(
    project_id: str,
    media_uri: str,
    *,
    people: list[dict[str, Any]] | None = None,
    collection_id: str | None = None,
    params: dict[str, Any] | None = None,
    cluster_labels: dict[str, str] | None = None,
    # 注入點（DIP）——未給則走 factory（Real/Stub 依 USE_INMEMORY）
    transcriber: Any = None,
    face_searcher: Any = None,
    nova_reviewer: Any = None,
    asd_provider: Any = None,
    config: Any = None,
) -> dict[str, Any]:
    """回傳 attributed_transcript.v1 dict（已通過契約驗證）。"""
    people = people or []

    if config is None:
        from app.aws.config import get_attribution_config

        config = get_attribution_config()
    if transcriber is None:
        from app.aws import factory

        transcriber = factory.get_transcriber()
    if face_searcher is None:
        from app.aws import factory

        face_searcher = factory.get_face_search()
    if nova_reviewer is None:
        from app.aws import factory

        nova_reviewer = factory.get_nova_reviewer()

    # 1) 逐字稿（+ diarization）
    transcript = transcriber.transcribe(
        project_id, media_uri,
        language_code=config.language_code,
        max_speakers=config.max_speaker_labels,
    )

    # 2) 人物出現區間（僅在有登錄名冊時搜尋）
    face_appearances: list[dict[str, Any]] = []
    if people and collection_id:
        face_appearances = face_searcher.search_faces(
            collection_id, media_uri, threshold=config.face_match_threshold
        )

    # 3) Active Speaker Detection（選配，一等證據）
    asd_results: list[dict[str, Any]] = []
    if asd_provider is not None:
        asd_results = asd_provider.detect(
            project_id, media_uri, transcript=transcript, face_appearances=face_appearances
        )

    # 4) 融合
    attributed = fuse(transcript, face_appearances, asd_results, people, params, cluster_labels)

    # 5) Nova 語意複核（僅 needs_review、僅填建議）
    attributed = _apply_nova_review(attributed, people, nova_reviewer)

    validate_attributed_transcript(attributed)
    return attributed
