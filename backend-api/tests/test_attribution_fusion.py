"""Fusion 引擎純函式測試（離線，餵 canned 證據）。

覆蓋五條路徑：confirmed / needs_review / unknown / overlapping_speech /
off_screen(propagation)，加上使用者手動 cluster 標記；並確認輸出通過
attributed_transcript.v1 契約驗證。
"""
from __future__ import annotations

from analysis.attribution import fuse
from analysis.attribution.contracts import validate_attributed_transcript

PEOPLE = [
    {"person_id": "person_001", "display_name": "主播 A", "role": "protagonist", "identity_source": "rekognition_collection"},
    {"person_id": "person_002", "display_name": "來賓 B", "role": "guest", "identity_source": "user_label"},
]


def _seg(seg_id, start, end, speaker, text="話"):
    return {"segment_id": seg_id, "start_ms": start, "end_ms": end, "speaker": speaker, "text": text}


def _transcript(segments):
    return {
        "schema_version": "transcript.v1",
        "project_id": "project-123",
        "language_code": "zh-TW",
        "duration_ms": 300000,
        "segments": segments,
    }


def _by_id(doc, utt_id):
    return next(u for u in doc["utterances"] if u["utterance_id"] == utt_id)


def test_confirmed_face_and_lip_sync():
    t = _transcript([_seg("seg_1", 0, 10000, "spk_0")])
    faces = [{"start_ms": 0, "end_ms": 10000, "person_id": "person_001",
              "face_track_id": "track_04", "similarity": 0.98, "visible_ratio": 0.96}]
    asd = [{"start_ms": 0, "end_ms": 10000, "speaker_cluster_id": "spk_0",
            "active_face_track_id": "track_04", "person_id": "person_001",
            "lip_sync_confidence": 0.91, "visible_ratio": 0.96}]
    out = fuse(t, faces, asd, PEOPLE)
    validate_attributed_transcript(out)
    u = _by_id(out, "utt_0001")
    assert u["person_id"] == "person_001"
    assert u["display_name"] == "主播 A"
    assert u["attribution"]["status"] == "confirmed"
    assert u["attribution"]["method"] == "face_search_and_lip_sync"
    assert u["attribution"]["confidence"] >= 0.85


def test_confirmed_face_only():
    t = _transcript([_seg("seg_1", 0, 10000, "spk_0")])
    faces = [{"start_ms": 0, "end_ms": 10000, "person_id": "person_001",
              "face_track_id": "track_04", "similarity": 0.99, "visible_ratio": 0.98}]
    out = fuse(t, faces, [], PEOPLE)
    validate_attributed_transcript(out)
    u = _by_id(out, "utt_0001")
    assert u["person_id"] == "person_001"
    assert u["attribution"]["method"] == "face_search"
    assert u["attribution"]["status"] == "confirmed"
    assert u["attribution"]["lip_sync_confidence"] is None


def test_needs_review_midconfidence():
    t = _transcript([_seg("seg_1", 0, 10000, "spk_0")])
    faces = [{"start_ms": 0, "end_ms": 10000, "person_id": "person_001",
              "face_track_id": "track_04", "similarity": 0.65, "visible_ratio": None}]
    out = fuse(t, faces, [], PEOPLE)
    validate_attributed_transcript(out)
    u = _by_id(out, "utt_0001")
    assert u["attribution"]["status"] == "needs_review"
    assert 0.60 <= u["attribution"]["confidence"] < 0.85
    assert u["person_id"] == "person_001"


def test_unknown_no_evidence():
    t = _transcript([_seg("seg_1", 0, 10000, "spk_2")])
    out = fuse(t, [], [], PEOPLE)
    validate_attributed_transcript(out)
    u = _by_id(out, "utt_0001")
    assert u["person_id"] is None
    assert u["display_name"] == "未知說話者"
    assert u["role"] is None
    assert u["attribution"]["status"] == "unknown"
    assert u["attribution"]["method"] == "insufficient_evidence"


def test_overlapping_speech():
    t = _transcript([_seg("seg_1", 0, 10000, "spk_0")])
    faces = [
        {"start_ms": 0, "end_ms": 10000, "person_id": "person_001",
         "face_track_id": "t1", "similarity": 0.95, "visible_ratio": 0.9},
        {"start_ms": 0, "end_ms": 10000, "person_id": "person_002",
         "face_track_id": "t2", "similarity": 0.92, "visible_ratio": 0.8},
    ]
    out = fuse(t, faces, [], PEOPLE)
    validate_attributed_transcript(out)
    u = _by_id(out, "utt_0001")
    assert u["attribution"]["status"] == "overlapping_speech"
    assert u["person_id"] is None


def test_off_screen_propagation():
    # spk_0 段1有臉確認 person_001 → 成為該群組主人物；段2無臉 → 畫外音延續
    t = _transcript([
        _seg("seg_1", 0, 10000, "spk_0"),
        _seg("seg_2", 30000, 40000, "spk_0"),
    ])
    faces = [{"start_ms": 0, "end_ms": 10000, "person_id": "person_001",
              "face_track_id": "track_04", "similarity": 0.98, "visible_ratio": 0.96}]
    out = fuse(t, faces, [], PEOPLE)
    validate_attributed_transcript(out)
    u2 = _by_id(out, "utt_0002")
    assert u2["person_id"] == "person_001"
    assert u2["attribution"]["status"] == "off_screen"
    assert u2["attribution"]["method"] == "speaker_cluster_propagation"
    assert 0 < u2["attribution"]["confidence"] < 0.98


def test_manual_cluster_label_overrides():
    t = _transcript([_seg("seg_1", 0, 10000, "spk_1")])
    out = fuse(t, [], [], PEOPLE, cluster_labels={"spk_1": "person_002"})
    validate_attributed_transcript(out)
    u = _by_id(out, "utt_0001")
    assert u["person_id"] == "person_002"
    assert u["display_name"] == "來賓 B"
    assert u["attribution"]["status"] == "confirmed"
    assert u["attribution"]["method"] == "user_label"
    assert u["attribution"]["confidence"] == 1.0


def test_asd_only_without_face():
    # 只有 ASD（person 來自 asd）、無 face_appearance
    t = _transcript([_seg("seg_1", 0, 10000, "spk_0")])
    asd = [{"start_ms": 0, "end_ms": 10000, "speaker_cluster_id": "spk_0",
            "active_face_track_id": "track_04", "person_id": "person_001",
            "lip_sync_confidence": 0.95, "visible_ratio": 0.9}]
    out = fuse(t, [], asd, PEOPLE)
    validate_attributed_transcript(out)
    u = _by_id(out, "utt_0001")
    assert u["person_id"] == "person_001"
    assert u["attribution"]["method"] == "face_search_and_lip_sync"  # ASD 帶嘴型同步
    assert u["attribution"]["lip_sync_confidence"] == 0.95
