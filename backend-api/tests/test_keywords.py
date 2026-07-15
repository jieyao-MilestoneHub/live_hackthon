"""爆點關鍵字抽取（EmotionKeywordExtractor）：精簡、代表性、決定性。"""
from __future__ import annotations

from analysis.keywords import (
    MAX_KEYWORD_CHARS,
    EmotionKeywordExtractor,
    KeywordHit,
    get_keyword_extractor,
)

_PUNCH = "成功了！我們做到了！太爽了吧這個，感謝大家的應援！"


def test_hits_are_concise_and_representative() -> None:
    ex = EmotionKeywordExtractor()
    hits = ex.extract(_PUNCH, reason="情緒詞與驚呼密集（成功了、太爽了）", limit=3)
    assert hits and all(isinstance(h, KeywordHit) for h in hits)
    assert all(len(h.text) <= MAX_KEYWORD_CHARS for h in hits)  # 字數精簡
    # 代表性：命中情緒關鍵詞（成功/做到了/太爽/感謝/應援 之一）。
    assert any(any(k in h.text for k in ("成功", "做到了", "太爽", "感謝", "應援")) for h in hits)


def test_deterministic() -> None:
    ex = EmotionKeywordExtractor()
    assert ex.extract(_PUNCH, limit=3) == ex.extract(_PUNCH, limit=3)


def test_emphasis_words_take_priority() -> None:
    ex = EmotionKeywordExtractor()
    hits = ex.extract(_PUNCH, emphasis_words=("成功了",), limit=1)
    assert hits[0].text == "成功了"


def test_prefers_longer_over_substring() -> None:
    # 同時命中「太扯」與「扯」時只留較長者。
    ex = EmotionKeywordExtractor()
    hits = ex.extract("哇太扯了吧！", limit=5)
    texts = [h.text for h in hits]
    assert "太扯" in texts
    assert "扯" not in texts


def test_empty_text_yields_no_hits() -> None:
    assert EmotionKeywordExtractor().extract("") == []


def test_factory_defaults_to_rule_based(monkeypatch) -> None:
    monkeypatch.delenv("SUBTITLE_LLM_KEYWORDS", raising=False)
    get_keyword_extractor.cache_clear()
    assert isinstance(get_keyword_extractor(), EmotionKeywordExtractor)
    get_keyword_extractor.cache_clear()
