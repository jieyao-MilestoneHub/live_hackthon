"""爆點關鍵字抽取：從 analysis metadata 取「字數精簡、代表性高」的關鍵字。

供第二層字幕（kind=keyword）用——在爆點（punchline）位置疊一個精簡關鍵字（含出現動畫）。
依 SOLID 的 DIP/OCP：抽取器是可注入的 ``KeywordExtractor`` Port，預設規則式
（``EmotionKeywordExtractor``，離線、決定性），另備 gated 的 Bedrock LLM 實作
（``LLMKeywordExtractor``，env ``SUBTITLE_LLM_KEYWORDS`` 開，fail-open）。新增抽取策略
只要實作 Protocol + 改 factory，不動 subtitle.py。

素材：emphasis_words（已篩的強調詞）、逐字稿/爆點台詞、reason，複用 ``analysis.emotion``
的關鍵詞表與計數原語。純函式、時間無關。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol, runtime_checkable

from analysis import emotion

# 關鍵字要「精簡」：超過此字數的候選捨棄（爆點字卡不放整句）。
MAX_KEYWORD_CHARS = 6


@dataclass(frozen=True)
class KeywordHit:
    """一個候選關鍵字：``text`` 短語 + ``weight`` 代表性（大者優先）。"""

    text: str
    weight: float


@runtime_checkable
class KeywordExtractor(Protocol):
    """關鍵字抽取 Port（domain 層抽象，subtitle.py 依此注入）。"""

    def extract(
        self,
        text: str,
        *,
        emphasis_words: tuple[str, ...] = (),
        reason: str | None = None,
        limit: int = 3,
    ) -> list[KeywordHit]:
        """回傳依代表性排序的候選（可空）；呼叫端通常取前 1 個當爆點字卡。"""
        ...


def _prefer_longer(cands: list[str]) -> list[str]:
    """去掉「被其他更長候選包含」的子字串（如同時有『太扯』與『扯』時只留『太扯』）。"""
    kept: list[str] = []
    for c in cands:
        if any(c != k and c in k for k in kept):
            continue  # c 是某個已留較長詞的子字串
        kept = [k for k in kept if not (k != c and k in c)]  # 移除是 c 子字串的舊詞
        if c not in kept:
            kept.append(c)
    return kept


class EmotionKeywordExtractor:
    """規則式、決定性：以 emphasis_words + emotion 關鍵詞挑精簡代表詞。

    優先序：emphasis_words（已由字幕層篩出的強調詞）→ 逐字稿命中的 emotion 關鍵詞
    → reason 命中的 emotion 關鍵詞。過濾過長者、去子字串重複，保序取前 ``limit``。
    """

    def extract(
        self,
        text: str,
        *,
        emphasis_words: tuple[str, ...] = (),
        reason: str | None = None,
        limit: int = 3,
    ) -> list[KeywordHit]:
        pool: list[str] = list(emphasis_words)
        pool += emotion.matched_keywords([text or ""], limit=8)
        if reason:
            pool += emotion.matched_keywords([reason], limit=4)

        concise: list[str] = []
        for kw in pool:
            kw = (kw or "").strip()
            if not kw or len(kw) > MAX_KEYWORD_CHARS:
                continue
            if kw not in concise:
                concise.append(kw)

        ordered = _prefer_longer(concise)
        n = len(ordered)
        return [KeywordHit(text=kw, weight=float(n - i)) for i, kw in enumerate(ordered)][:limit]


# --- gated LLM seam（預設關；離線/pytest 不觸 AWS）--------------------------------

def llm_enabled() -> bool:
    return os.environ.get("SUBTITLE_LLM_KEYWORDS", "").strip().lower() in {"1", "true", "yes", "on"}


class LLMKeywordExtractor:
    """Bedrock 版（gated + fail-open）：抽不到/出錯就退回規則式，永不 raise。

    比照 ``analysis/highlights_llm.py`` 的 Converse + forced tool use 慣例；真開啟需 AWS
    憑證。此處保留 seam 與 fallback，具體 prompt 於啟用時再調校。
    """

    def __init__(self, fallback: KeywordExtractor | None = None) -> None:
        self._fallback = fallback or EmotionKeywordExtractor()

    def extract(
        self,
        text: str,
        *,
        emphasis_words: tuple[str, ...] = (),
        reason: str | None = None,
        limit: int = 3,
    ) -> list[KeywordHit]:
        try:
            hit = self._call_bedrock(text)
        except Exception:  # noqa: BLE001 — fail-open
            hit = None
        if hit:
            return [KeywordHit(text=hit, weight=10.0)][:limit]
        return self._fallback.extract(text, emphasis_words=emphasis_words, reason=reason, limit=limit)

    def _call_bedrock(self, text: str) -> str | None:  # pragma: no cover - needs AWS
        if not text.strip():
            return None
        import boto3  # lazy

        from app.aws.config import get_attribution_config

        cfg = get_attribution_config()
        client = boto3.client("bedrock-runtime", region_name=cfg.bedrock_region)
        tool = {
            "toolChoice": {"tool": {"name": "pick_keyword"}},
            "tools": [{"toolSpec": {
                "name": "pick_keyword",
                "description": "為短影音爆點挑一個最有代表性的精簡繁中關鍵字（≤6 字）。",
                "inputSchema": {"json": {
                    "type": "object",
                    "properties": {"keyword": {"type": "string"}},
                    "required": ["keyword"],
                }},
            }}],
        }
        resp = client.converse(
            modelId=cfg.nova_review_model_id,
            system=[{"text": "你是短影音字卡文案助手，只輸出畫面上要放大的爆點關鍵字，精簡有力、不超過 6 字。"}],
            messages=[{"role": "user", "content": [{"text": text}]}],
            inferenceConfig={"temperature": 0, "topP": 1, "maxTokens": 64},
            toolConfig=tool,
        )
        for block in (resp.get("output", {}).get("message", {}) or {}).get("content", []) or []:
            t = block.get("toolUse")
            if t and t.get("name") == "pick_keyword":
                kw = (t.get("input") or {}).get("keyword")
                return kw.strip()[:MAX_KEYWORD_CHARS] if kw else None
        return None


@lru_cache(maxsize=1)
def get_keyword_extractor() -> KeywordExtractor:
    """factory（依 env 選擇；預設規則式）。測試可 ``get_keyword_extractor.cache_clear()``。"""
    if llm_enabled():
        return LLMKeywordExtractor()
    return EmotionKeywordExtractor()
