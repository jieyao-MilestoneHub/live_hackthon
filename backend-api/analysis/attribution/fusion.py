"""Speaker Identity Fusion — transcript.v1 (+ 證據) → attributed_transcript.v1。

把 Transcribe diarization 的匿名 ``spk_N`` 融合三路證據——Rekognition face-search 的
人物出現區間、Active Speaker Detection 的嘴型同步、（選配）人物名冊與使用者對群組的
手動標記——推導每段 utterance 的具名人物、辨識方法與可信度。

純 ``dict → dict``、無 AWS、無隨機、無時鐘（可完全離線測試；鏡像
``analysis/highlights.py`` 的風格）。門檻與權重集中在 ``scoring.py``。

判定規則（依提案第五節）：
  * >=0.85 → confirmed；0.60–0.85 → needs_review；<0.60 → unknown（person_id=null，不硬猜）
  * 同窗多相異人物臉部 → overlapping_speech
  * cluster 已有主人物、但本段無臉（畫外）→ off_screen（speaker_cluster_propagation，信心降階）
  * 使用者對整個 cluster 的手動標記優先級最高 → confirmed / user_label
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from analysis.attribution import scoring

ATTRIBUTION_VERSION = "attribution-fusion-1.0.0"

DEFAULT_ATTRIBUTION_PARAMS: dict[str, Any] = {
    "confirm_threshold": scoring.CONFIRM_THRESHOLD,
    "review_threshold": scoring.REVIEW_THRESHOLD,
    "propagation_decay": scoring.PROPAGATION_DECAY,
}

_UNKNOWN_NAME = "未知說話者"


def _overlap_ms(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def _best_face(seg: dict[str, Any], face_appearances: list[dict[str, Any]]) -> dict[str, Any] | None:
    """挑與該段重疊、相似度×重疊比最高的一筆 face_appearance。"""
    s0, s1 = int(seg["start_ms"]), int(seg["end_ms"])
    dur = max(1, s1 - s0)
    best: dict[str, Any] | None = None
    best_key = -1.0
    for fa in face_appearances:
        ov = _overlap_ms(s0, s1, int(fa["start_ms"]), int(fa["end_ms"]))
        if ov <= 0:
            continue
        ratio = ov / dur
        sim = float(fa.get("similarity") or 0.0)
        key = sim * ratio
        if key > best_key:
            best_key = key
            best = {
                "person_id": fa.get("person_id"),
                "face_track_id": fa.get("face_track_id"),
                "similarity": sim,
                "visible_ratio": fa.get("visible_ratio"),
                "overlap_ratio": ratio,
            }
    return best


def _best_asd(seg: dict[str, Any], asd_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """挑與該段重疊時長最長的一筆 ASD segment。"""
    s0, s1 = int(seg["start_ms"]), int(seg["end_ms"])
    best: dict[str, Any] | None = None
    best_ov = 0
    for a in asd_results:
        ov = _overlap_ms(s0, s1, int(a["start_ms"]), int(a["end_ms"]))
        if ov > best_ov:
            best_ov = ov
            best = a
    return best


def _distinct_overlap_persons(seg: dict[str, Any], face_appearances: list[dict[str, Any]]) -> set[str]:
    """回傳在該段內同時達到重疊/相似度門檻的相異人物集合（用於重疊說話判定）。"""
    s0, s1 = int(seg["start_ms"]), int(seg["end_ms"])
    dur = max(1, s1 - s0)
    persons: set[str] = set()
    for fa in face_appearances:
        pid = fa.get("person_id")
        if not pid:
            continue
        ov = _overlap_ms(s0, s1, int(fa["start_ms"]), int(fa["end_ms"]))
        if ov <= 0:
            continue
        if (ov / dur) >= scoring.OVERLAP_MIN_OVERLAP_RATIO and float(fa.get("similarity") or 0.0) >= scoring.OVERLAP_SIMILARITY:
            persons.add(pid)
    return persons


def _roster_index(people: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {p["person_id"]: p for p in people if p.get("person_id")}


def _display(roster: dict[str, dict[str, Any]], person_id: str | None) -> tuple[str | None, str | None]:
    """(display_name, role) — 未知回 (未知說話者, None)。"""
    if person_id is None:
        return _UNKNOWN_NAME, None
    p = roster.get(person_id)
    if p is None:
        return person_id, "unknown"
    return p.get("display_name") or person_id, p.get("role") or "unknown"


def fuse(
    transcript: dict[str, Any],
    face_appearances: list[dict[str, Any]] | None = None,
    asd_results: list[dict[str, Any]] | None = None,
    people: list[dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
    cluster_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """回傳符合 attributed_transcript.v1 的 dict。

    Args:
        transcript: transcript.v1（segments[] 帶 ``speaker=spk_N``）。
        face_appearances: Rekognition 正規化區間
            ``{start_ms,end_ms,person_id,face_track_id,similarity,visible_ratio}``。
        asd_results: asd_result.v1 的 ``segments``（可空）。
        people: people.v1 的 ``participants``（可空）。
        params: 覆寫門檻／衰減。
        cluster_labels: 使用者對整個 diarization 群組的手動標記 ``{spk_N: person_id}``。
    """
    face_appearances = face_appearances or []
    asd_results = asd_results or []
    people = people or []
    cluster_labels = cluster_labels or {}
    p = {**DEFAULT_ATTRIBUTION_PARAMS, **(params or {})}
    confirm_t = p["confirm_threshold"]
    review_t = p["review_threshold"]
    decay = p["propagation_decay"]

    roster = _roster_index(people)
    segments = sorted(transcript.get("segments", []), key=lambda s: int(s["start_ms"]))

    # ---- Pass 1：逐段收集證據 + 暫定人物 ----
    raw: list[dict[str, Any]] = []
    for seg in segments:
        face = _best_face(seg, face_appearances)
        asd = _best_asd(seg, asd_results)
        person = None
        if face and face.get("person_id"):
            person = face["person_id"]
        elif asd and asd.get("person_id"):
            person = asd["person_id"]
        raw.append({"seg": seg, "face": face, "asd": asd, "person": person})

    # ---- 聚合每個 cluster 的主人物與一致性 ----
    cluster_votes: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in raw:
        cluster = r["seg"].get("speaker") or "unknown"
        person = r["person"]
        if not person:
            continue
        dur = max(1, int(r["seg"]["end_ms"]) - int(r["seg"]["start_ms"]))
        if r["face"]:
            cluster_votes[cluster][person] += dur * float(r["face"]["similarity"])
        elif r["asd"]:
            cluster_votes[cluster][person] += dur * float(r["asd"].get("lip_sync_confidence") or 0.0)

    cluster_dominant: dict[str, str] = {}
    cluster_consistency: dict[str, float] = {}
    for cluster, votes in cluster_votes.items():
        total = sum(votes.values())
        if total <= 0:
            continue
        dom = max(votes, key=lambda k: votes[k])
        cluster_dominant[cluster] = dom
        cluster_consistency[cluster] = votes[dom] / total

    # ---- Pass 2：逐段判定 status / person / 信心 ----
    utterances: list[dict[str, Any]] = []
    used_person_ids: set[str] = set()
    for idx, r in enumerate(raw, start=1):
        seg = r["seg"]
        cluster = seg.get("speaker") or "unknown"
        face = r["face"]
        asd = r["asd"]
        person = r["person"]
        consistency = cluster_consistency.get(cluster)
        agrees = person is not None and cluster_dominant.get(cluster) == person
        cons_sig = consistency if agrees else None

        face_sig = float(face["similarity"]) if face else None
        lip_sig = float(asd.get("lip_sync_confidence")) if asd and asd.get("lip_sync_confidence") is not None else None
        vis_sig = None
        if face and face.get("visible_ratio") is not None:
            vis_sig = float(face["visible_ratio"])
        elif asd and asd.get("visible_ratio") is not None:
            vis_sig = float(asd["visible_ratio"])

        person_id: str | None = None
        status: str
        method: str
        confidence: float

        if cluster in cluster_labels:
            # (1) 使用者手動標記整個群組 → 最高優先
            person_id = cluster_labels[cluster]
            status, method, confidence = "confirmed", "user_label", 1.0
        elif len(_distinct_overlap_persons(seg, face_appearances)) >= 2:
            # (2) 多人同時入鏡且都達標 → 重疊說話，不歸屬
            status, method = "overlapping_speech", "insufficient_evidence"
            confidence = scoring.weighted_score(face_sig, lip_sig, vis_sig, None)
        elif person is not None and (face or asd):
            # (3) 有臉/嘴型證據 → 加權打分
            score = scoring.weighted_score(face_sig, lip_sig, vis_sig, cons_sig)
            status = scoring.classify_status(score, confirm_t, review_t)
            confidence = score
            if status == "unknown":
                person_id = None
                method = "insufficient_evidence"
            else:
                person_id = person
                # ASD 帶來嘴型同步證據（其 person 本身也源於臉部 track）→ 複合法
                method = "face_search_and_lip_sync" if asd else "face_search"
        elif cluster_dominant.get(cluster):
            # (4) 本段無臉但該群組已有主人物 → 畫外音延續
            person_id = cluster_dominant[cluster]
            status, method = "off_screen", "speaker_cluster_propagation"
            confidence = cluster_consistency[cluster] * decay
            vis_sig = 0.0
        else:
            # (5) 無任何證據 → 不硬猜
            status, method, confidence = "unknown", "insufficient_evidence", 0.0

        display_name, role = _display(roster, person_id)
        if person_id:
            used_person_ids.add(person_id)

        attribution: dict[str, Any] = {
            "status": status,
            "method": method,
            "confidence": round(float(confidence), 3),
            "face_track_id": (face or {}).get("face_track_id") if face else None,
            "face_similarity": round(face_sig, 3) if face_sig is not None else None,
            "lip_sync_confidence": round(lip_sig, 3) if lip_sig is not None else None,
            "visible_ratio": round(vis_sig, 3) if vis_sig is not None else None,
            "speaker_cluster_consistency": round(consistency, 3) if consistency is not None else None,
            "evidence": {
                "face_person": (face or {}).get("person_id") if face else None,
                "asd_person": (asd or {}).get("person_id") if asd else None,
                "cluster_dominant": cluster_dominant.get(cluster),
            },
        }

        utterances.append({
            "utterance_id": f"utt_{idx:04d}",
            "start_ms": int(seg["start_ms"]),
            "end_ms": int(seg["end_ms"]),
            "text": seg.get("text") or "",
            "speaker_cluster_id": cluster,
            "person_id": person_id,
            "display_name": display_name,
            "role": role,
            "source_segment_ids": [seg["segment_id"]],
            "attribution": attribution,
            "corrected_by": None,
            "corrected_at": None,
        })

    # ---- participants：名冊 + 任何被指派但未在名冊者（推得）----
    participants: list[dict[str, Any]] = [
        {
            "person_id": p_["person_id"],
            "display_name": p_.get("display_name") or p_["person_id"],
            "role": p_.get("role") or "unknown",
            "identity_source": p_.get("identity_source") or "inferred",
        }
        for p_ in people
        if p_.get("person_id")
    ]
    known = {p_["person_id"] for p_ in participants}
    for pid in sorted(used_person_ids - known):
        participants.append({
            "person_id": pid,
            "display_name": pid,
            "role": "unknown",
            "identity_source": "inferred",
        })

    return {
        "schema_version": "attributed_transcript.v1",
        "project_id": transcript.get("project_id", ""),
        "language_code": transcript.get("language_code", "zh-TW"),
        "source_transcript_version": (transcript.get("source") or {}).get("version_id"),
        "attribution_version": ATTRIBUTION_VERSION,
        "participants": participants,
        "utterances": utterances,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
