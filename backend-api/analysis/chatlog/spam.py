"""洗版偵測（分析流程階段二）：標記導流廣告 / 罐頭問候 / 制式公告。

全場約六成聊天是洗版；規則式標記（保留不刪除，利於下游確定性重算與稽核）後，
每分鐘熱區只計「真人自發留言」（非 spam 且 kind==human）。

規則是**資料集相關**、易 overfit 單一主播，因此：
  - 以有序規則清單表示，`ruleset_version` 供溯源；
  - 呼叫端可傳自訂規則（highlights.v1.parameters 為 additionalProperties:true，
    可把調過的規則版本記進去）。

⚠️ 預設規則詞是依使用者描述（導流「輸入浪🌊ID…」、罐頭「主播晚上好唷～～」、
公告「📢」）建的初版；真實 CSV 到位後校準命中率（目標 ~58.9% 洗版比例）。
"""
from __future__ import annotations

import re
from typing import Any, Iterable

RULESET_VERSION = "spam-1.0.0"

# (rule_id, compiled pattern)；由上而下，命中即停。
_DEFAULT_RULES: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    # 導流廣告：引導觀眾去輸入浪 ID / 追蹤 / 抽獎
    ("promo_id", re.compile(r"(輸入|打上|填).{0,4}浪.{0,6}id", re.IGNORECASE)),
    ("promo_follow", re.compile(r"(追蹤|訂閱|加入).{0,6}(抽|好禮|禮物|粉絲團|官方)")),
    # 罐頭問候：主播晚上好 / 晚安 / 早安（含拉長語尾～）
    ("canned_greeting", re.compile(r"主播.{0,3}(晚上好|晚安|早安|午安|你好|好唷|好呀)")),
    # 制式公告：📢 開頭或含公告字樣
    ("announcement", re.compile(r"[📢🔔]|^【?公告】?")),
)


def build_ruleset(
    extra_rules: Iterable[tuple[str, str]] | None = None,
) -> list[tuple[str, "re.Pattern[str]"]]:
    """回傳規則清單；extra_rules 為 (rule_id, regex 字串) 可附加自訂規則。"""
    rules = list(_DEFAULT_RULES)
    if extra_rules:
        rules += [(rid, re.compile(pat)) for rid, pat in extra_rules]
    return rules


def classify(text: str, rules: list[tuple[str, "re.Pattern[str]"]] | None = None) -> tuple[bool, str | None]:
    """判斷單則訊息是否為洗版，回傳 (is_spam, 命中規則 id 或 None)。"""
    if not text:
        return False, None
    for rule_id, pattern in (rules or _DEFAULT_RULES):
        if pattern.search(text):
            return True, rule_id
    return False, None


def apply(messages: list[dict[str, Any]], rules: list[tuple[str, "re.Pattern[str]"]] | None = None) -> list[dict[str, Any]]:
    """就地標記每則訊息的 is_spam / spam_rule（回傳同一個 list 便於串接）。

    已被上游標為非 human（gift/sticker/system/bot）者仍會標記，方便稽核；熱區計算
    另以 is_human_message() 過濾。
    """
    rs = rules or list(_DEFAULT_RULES)
    for m in messages:
        is_spam, rule = classify(m.get("text") or "", rs)
        m["is_spam"] = is_spam
        m["spam_rule"] = rule
    return messages


def is_human_message(m: dict[str, Any]) -> bool:
    """熱區計算採計的『真人自發留言』：kind==human 且非洗版。"""
    return (m.get("kind", "human") == "human") and not m.get("is_spam", False)


def spam_ratio(messages: list[dict[str, Any]]) -> float:
    """洗版比例（供黃金測試斷言，如 ~0.589）。空 list 回 0.0。"""
    if not messages:
        return 0.0
    spam = sum(1 for m in messages if m.get("is_spam"))
    return spam / len(messages)
