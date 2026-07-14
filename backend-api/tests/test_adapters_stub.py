"""AWS adapter 測試：純解析/正規化函式 + Stub 路徑產出契約合法資料。

真實 boto3 路徑不進 CI（Transcribe/Rekognition/Bedrock 無 moto 支援）；此處驗證
(1) 秒→毫秒與 words↔speaker 對齊的解析、(2) Rekognition 稀疏點合併、(3) Stub 契約合法、
(4) Nova toolConfig / 受約束輸出解析、(5) factory 依 USE_INMEMORY 綁定 Stub。
"""
from __future__ import annotations

from app.aws.bedrock_nova import build_tool_config, extract_person_id
from app.aws.config import get_attribution_config
from app.aws.rekognition import normalize_face_search, parse_s3_uri
from app.aws.transcribe import parse_transcribe_result
from app.settings import Settings
from analysis.validate import validate_transcript


def _settings() -> Settings:
    return Settings(
        env="test", aws_region="us-east-1", dynamodb_table="VideoEditor",
        raw_bucket="raw", work_bucket="work", output_bucket="out",
        use_inmemory=True, presign_expiry_sec=900,
    )


# ---- Transcribe 解析：秒(字串) → 毫秒(int) + 對齊說話者 ----
RAW_TRANSCRIBE = {
    "results": {
        "transcripts": [{"transcript": "大家好 你好"}],
        "items": [
            {"type": "pronunciation", "start_time": "0.0", "end_time": "0.5",
             "alternatives": [{"confidence": "0.98", "content": "大家好"}]},
            {"type": "pronunciation", "start_time": "3.0", "end_time": "3.4",
             "alternatives": [{"confidence": "0.90", "content": "你好"}]},
            {"type": "punctuation", "alternatives": [{"confidence": "0.0", "content": "。"}]},
        ],
        "speaker_labels": {
            "speakers": 2,
            "segments": [
                {"start_time": "0.0", "end_time": "1.0", "speaker_label": "spk_0", "items": []},
                {"start_time": "2.5", "end_time": "3.6", "speaker_label": "spk_1", "items": []},
            ],
        },
    }
}


def test_parse_transcribe_result_seconds_to_ms():
    doc = parse_transcribe_result(RAW_TRANSCRIBE, "project-123", "zh-TW")
    validate_transcript(doc)
    segs = doc["segments"]
    assert segs[0]["start_ms"] == 0 and segs[0]["end_ms"] == 1000  # 秒→毫秒
    assert segs[0]["speaker"] == "spk_0"
    assert segs[0]["text"] == "大家好"
    assert segs[1]["speaker"] == "spk_1"
    assert segs[1]["start_ms"] == 2500
    assert doc["duration_ms"] == 3600


def test_parse_s3_uri():
    assert parse_s3_uri("s3://my-bucket/a/b/c.mp4") == ("my-bucket", "a/b/c.mp4")


# ---- Rekognition 稀疏點 → 區間 ----
def test_normalize_face_search_merges_points():
    persons = [
        {"Timestamp": 0, "Person": {"Index": 3},
         "FaceMatches": [{"Similarity": 98.0, "Face": {"ExternalImageId": "person_001"}}]},
        {"Timestamp": 1000, "Person": {"Index": 3},
         "FaceMatches": [{"Similarity": 97.0, "Face": {"ExternalImageId": "person_001"}}]},
        {"Timestamp": 60000, "Person": {"Index": 5},
         "FaceMatches": [{"Similarity": 90.0, "Face": {"ExternalImageId": "person_002"}}]},
    ]
    out = normalize_face_search(persons, gap_ms=1500, pad_ms=500)
    # person_001 的兩個相鄰點合併成一段；person_002 另一段
    p1 = [a for a in out if a["person_id"] == "person_001"]
    assert len(p1) == 1
    assert p1[0]["start_ms"] == 0 and p1[0]["end_ms"] == 1500
    assert p1[0]["similarity"] == 0.98  # 0–100 → 0–1
    assert p1[0]["face_track_id"] == "track_3"
    assert any(a["person_id"] == "person_002" for a in out)


# ---- Stub 路徑產出契約合法 transcript ----
def test_stub_transcriber_contract_valid():
    from app.aws.transcribe import StubTranscriber

    t = StubTranscriber(_settings(), get_attribution_config())
    doc = t.transcribe("project-123", "s3://raw/x.mp4", language_code="zh-TW", max_speakers=5)
    validate_transcript(doc)
    assert {s["speaker"] for s in doc["segments"]} == {"spk_0", "spk_1"}


def test_stub_rekognition_and_nova():
    from app.aws.rekognition import StubRekognition
    from app.aws.bedrock_nova import StubNovaReviewer

    rek = StubRekognition(_settings(), get_attribution_config())
    assert rek.create_collection("project-123") == "lang-live-project-123"
    apps = rek.search_faces("lang-live-project-123", "s3://raw/x.mp4", threshold=0.85)
    assert all(0 <= a["similarity"] <= 1 for a in apps)

    nova = StubNovaReviewer(_settings(), get_attribution_config())
    assert nova.review_speaker(["person_001"], {"transcript": "x"}) == "unknown"


# ---- Nova 受約束輸出：toolConfig enum + 解析 ----
def test_nova_tool_config_and_extract():
    cfg = build_tool_config(["person_001", "person_002"])
    enum = cfg["tools"][0]["toolSpec"]["inputSchema"]["json"]["properties"]["person_id"]["enum"]
    assert enum == ["person_001", "person_002", "unknown"]
    assert cfg["toolChoice"]["tool"]["name"] == "resolve_speaker"

    good = {"output": {"message": {"content": [
        {"toolUse": {"name": "resolve_speaker", "input": {"person_id": "person_002"}}}
    ]}}}
    assert extract_person_id(good, ["person_001", "person_002"]) == "person_002"

    # 不在候選內 → unknown
    bad = {"output": {"message": {"content": [
        {"toolUse": {"name": "resolve_speaker", "input": {"person_id": "person_999"}}}
    ]}}}
    assert extract_person_id(bad, ["person_001", "person_002"]) == "unknown"


def test_factory_binds_stub_when_inmemory(monkeypatch):
    monkeypatch.setenv("USE_INMEMORY", "1")
    from app.aws import factory
    from app.aws.transcribe import StubTranscriber
    from app.settings import get_settings

    get_settings.cache_clear()
    get_attribution_config.cache_clear()
    factory.cache_clear()

    assert isinstance(factory.get_transcriber(), StubTranscriber)

    get_settings.cache_clear()
    factory.cache_clear()
