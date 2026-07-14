"""共用情緒/高光計分原語。

集中關鍵詞表、驚嘆/疊字/emoji regex 與權重，供兩條偵測路徑共用：
  - ``analysis.highlights``：逐字稿（speech）情緒偵測。
  - ``analysis.chatlog``：聊天量熱區之上的 Level-1 情緒 overlay。

抽出前這些常數散落在 ``highlights.py``；抽出後兩邊共享同一份詞彙與權重，
調參只需改一處。純函式、無副作用、時間無關。
"""
from __future__ import annotations

import re

# 情緒 / 高光關鍵詞（可擴充；LLM 模式可改用模型打分）。
# 注意：部分詞為彼此的子字串（如「太扯」⊃「扯」、「太神/神操作」⊃「神」、「太爽」⊃「爽」），
# count_keywords 以逐詞 count 疊加，維持與抽出前完全一致的計分行為。
EMOTION_KEYWORDS: tuple[str, ...] = (
    "太扯", "扯", "太神", "神操作", "厲害", "超級", "精彩", "誇張", "天啊", "哇",
    "起雞皮疙瘩", "衝", "太爽", "爽", "成功", "做到了", "感謝", "應援", "絕對",
    "沒想到", "快看", "來了", "最精彩", "神",
)

EXCLAIM_RE = re.compile(r"[！!]")
REPEAT_RE = re.compile(r"(.)\1{1,}")  # 疊字：啊啊啊、欸欸欸、來了來了

# emoji（涵蓋常見表情/符號區段；粗略但足夠當情緒強度代理）
EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # symbols & pictographs、emoticons、transport、supplemental
    "\U00002600-\U000027BF"  # misc symbols + dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicators
    "\U00002B00-\U00002BFF"  # misc symbols and arrows（★ 等）
    "\U0000FE0F"             # variation selector-16
    "]"
)

# 分數權重（逐字稿情緒偵測使用；抽出前的原值，勿隨意更動以免動到既有測試）
W_KEYWORD = 1.5
W_EXCLAIM = 2.0
W_REPEAT = 1.0
W_RATE = 0.15  # 語速（字/秒）代理興奮度


def count_keywords(text: str) -> int:
    """情緒關鍵詞命中次數（逐詞疊加，含子字串重複計數，與抽出前一致）。"""
    return sum(text.count(k) for k in EMOTION_KEYWORDS)


def count_exclaims(text: str) -> int:
    """驚嘆號（全形/半形）數量。"""
    return len(EXCLAIM_RE.findall(text))


def count_repeats(text: str) -> int:
    """疊字群組數（如「啊啊啊」計 1）。"""
    return len(REPEAT_RE.findall(text))


def count_emojis(text: str) -> int:
    """emoji 數量（情緒強度代理，供聊天 Level-1 overlay）。"""
    return len(EMOJI_RE.findall(text))


def matched_keywords(texts: list[str], limit: int = 4) -> list[str]:
    """回傳出現過的情緒關鍵詞（依 EMOTION_KEYWORDS 順序、去重、上限 limit），供產出 reason 文案。"""
    found: list[str] = []
    joined = "".join(texts)
    for k in EMOTION_KEYWORDS:
        if k in joined and k not in found:
            found.append(k)
        if len(found) >= limit:
            break
    return found
