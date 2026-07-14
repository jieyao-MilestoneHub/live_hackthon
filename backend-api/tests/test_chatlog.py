"""聊天優先分析（階段一～三）單元測試。

用確定性合成資料驗證：清理/重排、洗版標記、每分鐘熱區（mean+1σ）、候選情緒排序、
輸出 highlights.v1。最下方的 golden 測試以 **合成** 固定樣本
（tests/fixtures/chatlog_golden.sample.csv）重現真實 lang-live 匯出格式（頂層
msg=="msg"、聊天內容巢狀於 `c`、頂層 time 為 ISO、含 join/duration/msg_join 雜訊、
檔序反時序），驗證巢狀 `c` / ISO 時間解析與整條 parse→detect 流程。樣本為合成資料、
不含任何真實使用者資料；要跑真實檔請設 CHATLOG_GOLDEN_CSV 指向本機（未追蹤的）完整 CSV。
"""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path

import pytest

from analysis.chatlog import candidates, clean, clean_chatlog, detect, spam, volume
from analysis.validate import validate_highlights

VIDEO_START = 1_700_000_000_000  # 影片 0:00 的 epoch 毫秒（合成用）

# (offset_seconds, type, nick, text) — 檔案順序刻意打亂（熱區在前、開場在後）以製造亂序。
_ROWS = [
    (185, "msg", "B", "太扯了吧哈哈哈！"),
    (1, "join", "sys", "B 進入直播間"),          # 非聊天，應丟棄
    (5, "msg", "A", "先卡個位子"),
    (15, "msg", "bot", "輸入浪🌊ID 追蹤主播抽好禮"),  # spam: promo_id
    (190, "msg", "C", "太神了 起雞皮疙瘩"),
    (40, "msg", "A2", "嗨大家安安"),
    (70, "msg", "D", "主播晚上好唷～～"),          # spam: canned_greeting
    (195, "msg", "E", "哇 這個超級厲害"),
    (80, "msg", "F", "📢 本場公告：抽獎規則"),      # spam: announcement
    (200, "msg", "G", "成功了！太爽了"),
    (205, "msg", "H", "誇張 XDDD 🤣🤣"),
    (210, "msg", "I", "沒想到會這樣 天啊"),
    (215, "msg", "J", "最精彩的一段！"),
    (300, "duration", "sys", "120"),             # 非聊天，應丟棄
]


def _csv_text() -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["seq", "message"])
    for i, (off, typ, nick, text) in enumerate(_ROWS):
        env = {"msg": typ, "time": VIDEO_START + off * 1000, "nickname": nick, "content": text}
        w.writerow([i, json.dumps(env, ensure_ascii=False)])
    return buf.getvalue()


@pytest.fixture(scope="module")
def chatlog() -> dict:
    return clean_chatlog(_csv_text(), "project-syn", source={"bucket": "b", "key": "k"})


# --- 階段一：清理 / 重排 --------------------------------------------------

def test_clean_drops_non_chat_and_keeps_msg(chatlog: dict) -> None:
    # 14 列中 2 列（join/duration）非聊天 → 12 則聊天訊息。
    assert len(chatlog["messages"]) == 12
    assert chatlog["schema_version"] == "chatlog.v1"
    assert chatlog["time_base"] == "epoch_ms"


def test_clean_resorts_by_internal_time(chatlog: dict) -> None:
    times = [m["time_ms"] for m in chatlog["messages"]]
    assert times == sorted(times)
    # message_id 依排序後序號穩定生成
    assert [m["message_id"] for m in chatlog["messages"]][:2] == ["m-00001", "m-00002"]


def test_file_order_has_disorder_that_resort_fixes() -> None:
    file_order = clean.extract_chat_messages(clean._iter_message_json(_csv_text()))
    # 檔案原序存在相鄰亂序（熱區被放到開場之前）。
    assert clean.out_of_order_ratio(file_order) > 0.0


def test_parser_handles_unquoted_comma_json() -> None:
    # 未加引號、JSON 內含逗號的 NDJSON 風格列也要能解析（回接 fallback）。
    line = json.dumps({"msg": "msg", "time": VIDEO_START, "nickname": "x", "content": "嗨,你好"}, ensure_ascii=False)
    parsed = clean._iter_message_json(line)
    assert len(parsed) == 1 and parsed[0]["content"] == "嗨,你好"


# --- 階段二：洗版 ---------------------------------------------------------

def test_spam_tagging_rules(chatlog: dict) -> None:
    by_rule = {m["spam_rule"] for m in chatlog["messages"] if m["is_spam"]}
    assert {"promo_id", "canned_greeting", "announcement"} <= by_rule
    assert sum(1 for m in chatlog["messages"] if m["is_spam"]) == 3


def test_human_message_excludes_spam(chatlog: dict) -> None:
    humans = [m for m in chatlog["messages"] if spam.is_human_message(m)]
    assert len(humans) == 9  # 12 - 3 spam


# --- 階段三：每分鐘熱區 + 候選 --------------------------------------------

def test_volume_detects_single_hot_window(chatlog: dict) -> None:
    vol = volume.hot_windows(chatlog, sigma=1.0)
    assert vol["threshold"] > vol["mean"] > 0
    assert len(vol["windows"]) == 1
    assert vol["windows"][0]["minute_indices"] == [3]  # 熱區在第 3 分鐘


def test_candidates_ranked_with_emotion_breakdown(chatlog: dict) -> None:
    vol = volume.hot_windows(chatlog, sigma=1.0)
    cands = candidates.build_candidates(chatlog, vol, {"max_clips": 5})
    assert len(cands) == 1
    c = cands[0]
    assert c["score"] == 1.0  # 唯一候選 → 正規化為 1.0
    assert set(c["emotion"]["breakdown"]) == {"keyword", "emoji", "punctuation", "volume"}
    assert c["emotion"]["counts"]["emoji"] >= 2  # 🤣🤣
    assert c["detection"]["threshold"] > 0


def test_detect_emits_valid_highlights(chatlog: dict) -> None:
    hl = detect.detect_highlights_from_chat(chatlog, VIDEO_START, 300000)
    validate_highlights(hl)
    assert len(hl["highlights"]) == 1
    h = hl["highlights"][0]
    assert h["signal"] == "chat_volume"
    assert h["status"] == "candidate"
    assert 0 <= h["start_ms"] < h["end_ms"] <= 300000
    assert h["chat_window"]["start_ms"] < h["chat_window"]["end_ms"]
    assert h["highlight_id"] == "hl-001"


# --- Golden：合成樣本重現真實 lang-live 匯出格式（可用完整檔覆寫） ----------------

_GOLDEN_ENV = os.environ.get("CHATLOG_GOLDEN_CSV")          # 指向本機完整真實 CSV 可覆寫
_GOLDEN_DEFAULT = Path(__file__).parent / "fixtures" / "chatlog_golden.sample.csv"


def _golden_path() -> Path | None:
    if _GOLDEN_ENV and Path(_GOLDEN_ENV).exists():
        return Path(_GOLDEN_ENV)
    if _GOLDEN_DEFAULT.exists():
        return _GOLDEN_DEFAULT
    return None


@pytest.mark.skipif(
    _golden_path() is None,
    reason="golden fixture missing; expected tests/fixtures/chatlog_golden.sample.csv or CHATLOG_GOLDEN_CSV",
)
def test_golden_real_format_parses_reorders_and_detects() -> None:
    """真實巢狀 `c` / ISO 時間格式：解析出足量聊天、檔序高度亂序、clean 重排成功、
    洗版比例落在合理區間，且整條 parse→detect 產出合法 highlights.v1。"""
    path = _golden_path()
    text = path.read_text(encoding="utf-8-sig")

    # 1) 巢狀 `c` + 頂層 msg=="msg" 過濾正確：抽出足量聊天（雜訊 join/duration/msg_join 應丟棄）。
    file_order = clean.extract_chat_messages(clean._iter_message_json(text))
    assert len(file_order) >= 100, "parser extracted too few chat messages — check FIELD_SPEC nested-`c` mapping"

    # 2) 真實檔案原序高度亂序（Kibana 匯出非時間序）→ 必須靠 clean 依內部 time 重排。
    assert clean.out_of_order_ratio(file_order) > 0.5

    doc = clean_chatlog(path, "project-golden")
    times = [m["time_ms"] for m in doc["messages"]]
    assert times == sorted(times), "clean 未依內部 time 重排"
    # 每則都成功解析出 epoch 毫秒（13 位）與非空文字/時間。
    assert all(t > 1_000_000_000_000 for t in times)

    # 3) 洗版比例非退化（規則初版；區間寬鬆，避免綁死單一資料集）。
    ratio = spam.spam_ratio(doc["messages"])
    assert 0.05 <= ratio <= 0.9

    # 4) 整條 parse→detect 產出合法 highlights.v1（chat-relative 時基即可）。
    span = doc["ended_at_epoch_ms"] - doc["started_at_epoch_ms"]
    hl = detect.detect_highlights_from_chat(doc, doc["started_at_epoch_ms"], span + 1000)
    validate_highlights(hl)
    assert len(hl["highlights"]) >= 1
    for h in hl["highlights"]:
        assert h["signal"] == "chat_volume"
        assert 0 <= h["start_ms"] < h["end_ms"] <= span + 1000
