"""Pipeline 編排測試（全注入 stub/fake deps，離線、確定性）。

覆蓋：端到端產出契約合法的 attributed_transcript；Nova 對 needs_review 填建議；
ASD 證據進入融合（method 變 face_search_and_lip_sync）。
"""
from __future__ import annotations

from analysis.attribution.pipeline import run_attribution
from app.aws.config import get_attribution_config
from app.aws.transcribe import StubTranscriber
from app.aws.rekognition import StubRekognition
from app.aws.bedrock_nova import StubNovaReviewer
from app.settings import Settings

PEOPLE = [
    {"person_id": "person_001", "display_name": "主播 A", "role": "protagonist", "identity_source": "rekognition_collection"},
    {"person_id": "person_002", "display_name": "來賓 B", "role": "guest", "identity_source": "user_label"},
]


def _settings():
    return Settings(
        env="test", aws_region="us-east-1", dynamodb_table="VideoEditor",
        raw_bucket="raw", work_bucket="work", output_bucket="out",
        use_inmemory=True, presign_expiry_sec=900,
    )


def _cfg():
    return get_attribution_config()


class _FakeTranscriber:
    def __init__(self, segments):
        self._segs = segments

    def transcribe(self, project_id, media_uri, *, language_code, max_speakers):
        return {
            "schema_version": "transcript.v1",
            "project_id": project_id,
            "language_code": language_code,
            "duration_ms": max((s["end_ms"] for s in self._segs), default=0),
            "segments": self._segs,
        }


class _FakeFaceSearcher:
    def __init__(self, apps):
        self._apps = apps

    def search_faces(self, collection_id, media_uri, *, threshold):
        return self._apps


class _FakeReviewer:
    def __init__(self, pick):
        self._pick = pick

    def review_speaker(self, candidate_person_ids, context, *, complex_case=False):
        return self._pick


class _FakeASD:
    def __init__(self, segs):
        self._segs = segs

    def detect(self, project_id, media_uri, *, transcript, face_appearances):
        return self._segs


def test_pipeline_end_to_end_with_stubs():
    out = run_attribution(
        "project-123", "s3://raw/x.mp4",
        people=PEOPLE, collection_id="lang-live-project-123",
        config=_cfg(),
        transcriber=StubTranscriber(_settings(), _cfg()),
        face_searcher=StubRekognition(_settings(), _cfg()),
        nova_reviewer=StubNovaReviewer(_settings(), _cfg()),
    )
    # validate_attributed_transcript 已在 run_attribution 內呼叫
    assert out["schema_version"] == "attributed_transcript.v1"
    u1 = next(u for u in out["utterances"] if u["utterance_id"] == "utt_0001")
    assert u1["person_id"] == "person_001"
    assert u1["attribution"]["status"] == "confirmed"
    # spk_0 第二段無臉 → 畫外音延續
    off = [u for u in out["utterances"] if u["attribution"]["status"] == "off_screen"]
    assert off and off[0]["person_id"] == "person_001"


def test_pipeline_nova_fills_needs_review():
    seg = [{"segment_id": "seg_1", "start_ms": 0, "end_ms": 10000, "speaker": "spk_0", "text": "那個功能是誰做的？"}]
    faces = [{"start_ms": 0, "end_ms": 10000, "person_id": "person_001",
              "face_track_id": "t1", "similarity": 0.65, "visible_ratio": None}]
    out = run_attribution(
        "project-123", "s3://raw/x.mp4",
        people=PEOPLE, collection_id="c",
        config=_cfg(),
        transcriber=_FakeTranscriber(seg),
        face_searcher=_FakeFaceSearcher(faces),
        nova_reviewer=_FakeReviewer("person_002"),
    )
    u = out["utterances"][0]
    assert u["attribution"]["status"] == "needs_review"
    assert u["person_id"] == "person_002"           # Nova 建議取代暫定
    assert u["attribution"]["method"] == "nova_review"
    assert u["attribution"]["evidence"]["nova_pick"] == "person_002"


def test_pipeline_uses_asd_evidence():
    seg = [{"segment_id": "seg_1", "start_ms": 0, "end_ms": 10000, "speaker": "spk_0", "text": "哈囉"}]
    faces = [{"start_ms": 0, "end_ms": 10000, "person_id": "person_001",
              "face_track_id": "t1", "similarity": 0.9, "visible_ratio": 0.9}]
    asd = [{"start_ms": 0, "end_ms": 10000, "speaker_cluster_id": "spk_0",
            "active_face_track_id": "t1", "person_id": "person_001",
            "lip_sync_confidence": 0.93, "visible_ratio": 0.95}]
    out = run_attribution(
        "project-123", "s3://raw/x.mp4",
        people=PEOPLE, collection_id="c",
        config=_cfg(),
        transcriber=_FakeTranscriber(seg),
        face_searcher=_FakeFaceSearcher(faces),
        nova_reviewer=StubNovaReviewer(_settings(), _cfg()),
        asd_provider=_FakeASD(asd),
    )
    u = out["utterances"][0]
    assert u["attribution"]["lip_sync_confidence"] == 0.93
    assert u["attribution"]["method"] == "face_search_and_lip_sync"
    assert u["attribution"]["status"] == "confirmed"
