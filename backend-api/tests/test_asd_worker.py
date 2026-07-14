"""ASD worker 測試：幾何/打分純函式 + provider 產出 asd_result.v1 + fusion 消費。"""
from __future__ import annotations

from analysis.attribution import fuse
from workers.asd.heuristic import iou, lip_sync_score, mouth_open_ratio
from workers.asd.worker import HeuristicASD, run_asd

PEOPLE = [{"person_id": "person_001", "display_name": "主播 A", "role": "protagonist", "identity_source": "rekognition_collection"}]


def test_mouth_open_ratio():
    landmarks = {
        "mouthUp": (0.5, 0.55), "mouthDown": (0.5, 0.60),
        "eyeLeft": (0.4, 0.4), "eyeRight": (0.6, 0.4),
    }
    assert abs(mouth_open_ratio(landmarks) - 0.25) < 1e-6
    assert mouth_open_ratio({}) == 0.0


def test_iou():
    box = {"Left": 0.1, "Top": 0.1, "Width": 0.2, "Height": 0.2}
    assert abs(iou(box, box) - 1.0) < 1e-9
    disjoint = {"Left": 0.8, "Top": 0.8, "Width": 0.1, "Height": 0.1}
    assert iou(box, disjoint) == 0.0


def test_lip_sync_score_correlation():
    assert lip_sync_score([0, 1, 0, 1], [0, 1, 0, 1]) > 0.99   # 同步
    assert lip_sync_score([0, 1, 0, 1], [1, 0, 1, 0]) == 0.0    # 反相 → 0


def _transcript():
    return {
        "schema_version": "transcript.v1", "project_id": "project-123",
        "language_code": "zh-TW", "duration_ms": 10000,
        "segments": [{"segment_id": "seg_1", "start_ms": 0, "end_ms": 10000, "speaker": "spk_0", "text": "哈囉"}],
    }


def test_run_asd_produces_valid_contract():
    faces = [{"start_ms": 0, "end_ms": 10000, "person_id": "person_001",
              "face_track_id": "track_1", "similarity": 0.9, "visible_ratio": 0.9}]
    doc = run_asd("project-123", "s3://raw/x.mp4", transcript=_transcript(), face_appearances=faces)
    # validate_asd_result 已在 build_asd_result 內呼叫
    assert doc["schema_version"] == "asd_result.v1"
    seg = doc["segments"][0]
    assert seg["person_id"] == "person_001"
    assert seg["lip_sync_confidence"] == 0.9   # ratio 1.0 * similarity 0.9


def test_fusion_consumes_heuristic_asd():
    faces = [{"start_ms": 0, "end_ms": 10000, "person_id": "person_001",
              "face_track_id": "track_1", "similarity": 0.9, "visible_ratio": 0.9}]
    asd = HeuristicASD().detect("project-123", "s3://raw/x.mp4", transcript=_transcript(), face_appearances=faces)
    out = fuse(_transcript(), faces, asd, PEOPLE)
    u = out["utterances"][0]
    assert u["person_id"] == "person_001"
    assert u["attribution"]["method"] == "face_search_and_lip_sync"
    assert u["attribution"]["lip_sync_confidence"] is not None
