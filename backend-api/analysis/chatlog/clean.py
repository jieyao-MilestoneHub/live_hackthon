"""聊天室 log CSV → chatlog.v1（分析流程階段一：資料清理與探索）。

步驟：
  1. 讀 CSV，逐列解析出 JSON 格式的 message 內容（自動偵測哪個 cell 是 JSON dict，
     不硬綁欄位名，對真實檔案較穩健）。
  2. 篩出真正「聊天室輸入內容」（type == 'msg'），取出時間 / 使用者 / 內容三欄；
     其他如 join / duration 等非聊天內容一律丟棄。
  3. **一律以訊息內部 time 欄位重新排序**（檔案原始列序不可信，約 37% 相鄰列違反順序）。
  4. 套用洗版標記（spam.apply），輸出符合 chatlog.v1 的 dict。

⚠️ 欄位映射（FIELD_SPEC）是依使用者口述格式建的初版：JSON 內以某個鍵（預設 'msg'）
   之值 == 'msg' 判定為聊天輸入、time/user/content 的實際鍵名待真實 CSV 到位定稿。
   解析採「候選鍵依序命中」策略，多半能直接吃到真實資料；若欄位名不同，只需調整
   FIELD_SPEC，不必改邏輯。
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analysis.chatlog import spam

# 候選鍵依序嘗試。真實檔（Kibana 匯出的 lang-live chat log）已對齊：
#   - 頂層 msg == "msg" 才是聊天輸入（其餘 join / duration / msg_join … 丟棄）
#   - 聊天內容巢狀於頂層鍵 `c`：c.msg=文字、c.name=暱稱、c.pfid=使用者 id、c.at=epoch ms
#   - 頂層 `time` 為 ISO-8601 字串（c.at 缺席時的退路）
FIELD_SPEC: dict[str, Any] = {
    "type_keys": ["msg", "type", "cmd", "event"],   # 類型鑑別鍵（讀頂層）
    "chat_type_value": "msg",                        # 類型值 == 此者才是聊天輸入
    "payload_keys": ["c"],                           # 聊天內容巢狀 dict（真實檔為 c）
    "time_keys": ["at", "time", "timestamp", "ts", "send_time", "sendTime", "t"],
    "user_id_keys": ["pfid", "uid", "user_id", "userId", "userid", "id"],
    "username_keys": ["name", "nickname", "nick", "username", "user", "displayName"],
    # "msg" 放最後：巢狀 c 內文字鍵為 c.msg，但頂層 msg 是類型鑑別鍵（值=="msg"），
    # 故先吃 content/text… 再退回 msg，避免把平坦格式的類型值誤當文字。
    "text_keys": ["content", "text", "msg_content", "message", "body", "comment", "msg"],
}


def _first_present(d: dict[str, Any], keys: list[str]) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _first_present_dict(d: dict[str, Any], keys: list[str]) -> dict[str, Any] | None:
    """回傳第一個命中且值為 dict 的鍵（用於下潛巢狀 payload，如 `c`）。"""
    for k in keys:
        v = d.get(k)
        if isinstance(v, dict):
            return v
    return None


def _iso_to_epoch_ms(value: str) -> int | None:
    """把 ISO-8601 字串（可含毫秒 / `Z` / 時區）轉為 epoch 毫秒。"""
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _to_epoch_ms(value: Any) -> int | None:
    """把時間值正規化為 epoch 毫秒。

    數值：10 位（秒）→ ×1000、13 位（毫秒）→ 原值；ISO-8601 字串（如頂層 `time`）
    則解析為 epoch 毫秒。
    """
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        # 非數值 → 試 ISO-8601 字串（真實檔頂層 time="2026-06-17T…Z"）
        if isinstance(value, str):
            return _iso_to_epoch_ms(value)
        return None
    if n <= 0:
        return None
    if n < 100_000_000_000:  # < 1e11 → 視為 epoch 秒
        return n * 1000
    return n


def _try_json_dict(s: str) -> dict[str, Any] | None:
    """從字串中擷取第一個 { … 最後一個 } 之間的 JSON dict；失敗回 None。"""
    s = (s or "").strip()
    i, j = s.find("{"), s.rfind("}")
    if i == -1 or j == -1 or j < i:
        return None
    try:
        obj = json.loads(s[i : j + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _iter_message_json(csv_text: str) -> "list[dict[str, Any]]":
    """逐列取出 JSON dict，保留檔案原始順序。表頭列自然被略過。

    穩健處理兩種常見格式：
      (a) 標準 CSV：JSON 在某個以雙引號括住的欄位（csv.reader 正確還原成單一 cell）。
      (b) 未加引號、JSON 內含逗號的列（會被 csv.reader 依逗號切碎）→ 回接後再解析。
    """
    out: list[dict[str, Any]] = []
    for row in csv.reader(io.StringIO(csv_text)):
        parsed: dict[str, Any] | None = None
        for cell in row:  # (a) 逐 cell 找 JSON dict
            parsed = _try_json_dict(cell)
            if parsed is not None:
                break
        if parsed is None and row:  # (b) 回接被逗號切碎的整列
            parsed = _try_json_dict(",".join(row))
        if parsed is not None:
            out.append(parsed)
    return out


def _is_chat(msg: dict[str, Any], spec: dict[str, Any]) -> bool:
    for tk in spec["type_keys"]:
        if tk in msg:
            return str(msg[tk]) == str(spec["chat_type_value"])
    return False


def extract_chat_messages(
    raw_msgs: list[dict[str, Any]],
    spec: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """從解析後的 JSON 訊息（檔案順序）過濾出聊天輸入並映射欄位（仍保留檔案順序）。"""
    s = {**FIELD_SPEC, **(spec or {})}
    chat: list[dict[str, Any]] = []
    for msg in raw_msgs:
        if not _is_chat(msg, s):
            continue
        # 聊天內容巢狀於 payload（真實檔為 `c`）；找不到就退回頂層 dict。
        payload = _first_present_dict(msg, s.get("payload_keys", [])) or msg
        # 時間：優先 payload 內數值（c.at），其次頂層 ISO 字串（time）。
        time_ms = _to_epoch_ms(_first_present(payload, s["time_keys"]))
        if time_ms is None:
            time_ms = _to_epoch_ms(_first_present(msg, s["time_keys"]))
        if time_ms is None:
            continue
        user_id = _first_present(payload, s["user_id_keys"]) or _first_present(msg, s["user_id_keys"])
        username = _first_present(payload, s["username_keys"]) or _first_present(msg, s["username_keys"])
        text = _first_present(payload, s["text_keys"])
        chat.append(
            {
                "time_ms": time_ms,
                "user_id": str(user_id) if user_id is not None else None,
                "username": username,
                "text": str(text or ""),
                "kind": "human",
            }
        )
    return chat


def out_of_order_ratio(messages_file_order: list[dict[str, Any]]) -> float:
    """檔案原始順序中『相鄰兩則 time 逆序』的比例（供黃金測試斷言 ~0.37）。"""
    n = len(messages_file_order)
    if n < 2:
        return 0.0
    violations = sum(
        1
        for a, b in zip(messages_file_order, messages_file_order[1:])
        if int(b["time_ms"]) < int(a["time_ms"])
    )
    return violations / (n - 1)


def _read_text(csv_source: str | Path) -> str:
    p = Path(csv_source)
    if len(str(csv_source)) < 260 and p.exists():
        return p.read_text(encoding="utf-8-sig")
    return str(csv_source)


def clean_chatlog(
    csv_source: str | Path,
    project_id: str,
    *,
    field_spec: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None,
    spam_rules: list[Any] | None = None,
) -> dict[str, Any]:
    """CSV（檔案路徑或原始文字）→ chatlog.v1 dict。

    產出的 messages 已依 time_ms 升冪排序（不信檔案原序）並標記洗版；message_id 依
    排序後序號生成（m-00001…），穩定可重現。
    """
    csv_text = _read_text(csv_source)
    raw = _iter_message_json(csv_text)
    chat_file_order = extract_chat_messages(raw, field_spec)

    # 階段一核心：以訊息內部 time 重排（穩定排序保留同 time 的相對序）。
    ordered = sorted(chat_file_order, key=lambda m: int(m["time_ms"]))
    for i, m in enumerate(ordered, start=1):
        m["message_id"] = f"m-{i:05d}"

    spam.apply(ordered, spam_rules)

    times = [int(m["time_ms"]) for m in ordered]
    doc: dict[str, Any] = {
        "schema_version": "chatlog.v1",
        "project_id": project_id,
        "time_base": "epoch_ms",
        "messages": ordered,
        "message_count": len(ordered),
        "filter": {"spam_ruleset_version": spam.RULESET_VERSION, "kept_kinds": ["msg"]},
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if times:
        doc["started_at_epoch_ms"] = min(times)
        doc["ended_at_epoch_ms"] = max(times)
    if source:
        doc["source"] = source
    return doc
