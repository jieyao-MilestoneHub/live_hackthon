"""熱區 → 候選片段 + Level-1 情緒計分排序（分析流程階段三 b）。

在方法一（每分鐘量門檻）圈出的熱區窗上，疊加 Level-1 情緒計分
（關鍵字 / emoji / 標點強度 / 留言量），排序取前 N 名候選。全程 epoch 毫秒空間；
換算成影片相對毫秒與組出 highlights.v1 由 detect.py 負責。

情緒計分是可解釋的加權和，正規化到 0..1 當排序鍵；各面向對分數的貢獻保留在
breakdown，原始計數保留在 counts，符合「每一層都留下為什麼」的精神。
"""
from __future__ import annotations

from typing import Any

from analysis import emotion
from analysis.chatlog import spam

# 聊天 Level-1 情緒權重（與逐字稿情緒權重分開；可經 params 覆寫）
W_KEYWORD = 1.5
W_EMOJI = 1.0
W_PUNCT = 0.5
W_VOLUME = 0.1  # 每則真人留言的量能貢獻


def _window_messages(chatlog: dict[str, Any], start_epoch_ms: int, end_epoch_ms: int) -> list[dict[str, Any]]:
    return [
        m
        for m in (chatlog.get("messages") or [])
        if spam.is_human_message(m) and start_epoch_ms <= int(m["time_ms"]) < end_epoch_ms
    ]


def _raw_emotion(msgs: list[dict[str, Any]], weights: dict[str, float]) -> tuple[float, dict[str, int]]:
    kw = sum(emotion.count_keywords(m.get("text") or "") for m in msgs)
    emj = sum(emotion.count_emojis(m.get("text") or "") for m in msgs)
    exc = sum(emotion.count_exclaims(m.get("text") or "") for m in msgs)
    vol = len(msgs)
    raw = (
        weights["keyword"] * kw
        + weights["emoji"] * emj
        + weights["punctuation"] * exc
        + weights["volume"] * vol
    )
    counts = {"keyword": kw, "emoji": emj, "exclaim": exc, "human_msgs": vol}
    return raw, counts


def build_candidates(
    chatlog: dict[str, Any],
    volume_result: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """回傳 epoch 空間的候選 list，已依情緒分數排序、取前 max_clips 名。

    每個候選：chat_start_epoch_ms / chat_end_epoch_ms / score(0..1) / emotion{score,breakdown,counts}
    / detection{minute_volume,baseline_mean,baseline_sigma,threshold} / reason / message_ids / texts。
    """
    p = params or {}
    max_clips = int(p.get("max_clips", 5))
    weights = {
        "keyword": float(p.get("w_keyword", W_KEYWORD)),
        "emoji": float(p.get("w_emoji", W_EMOJI)),
        "punctuation": float(p.get("w_punctuation", W_PUNCT)),
        "volume": float(p.get("w_volume", W_VOLUME)),
    }

    windows = volume_result.get("windows") or []
    mean = volume_result.get("mean", 0.0)
    sigma_value = volume_result.get("sigma_value", 0.0)
    threshold = volume_result.get("threshold", 0.0)

    raws: list[tuple[float, dict[str, int], dict[str, Any], list[dict[str, Any]]]] = []
    for w in windows:
        msgs = _window_messages(chatlog, w["start_epoch_ms"], w["end_epoch_ms"])
        raw, counts = _raw_emotion(msgs, weights)
        raws.append((raw, counts, w, msgs))

    top = max((r[0] for r in raws), default=0.0)

    candidates: list[dict[str, Any]] = []
    for raw, counts, w, msgs in raws:
        norm = (raw / top) if top > 0 else 0.0
        # 各面向對正規化分數的貢獻（同除 top，維持可加性）
        breakdown = {
            "keyword": round((weights["keyword"] * counts["keyword"]) / top, 4) if top > 0 else 0.0,
            "emoji": round((weights["emoji"] * counts["emoji"]) / top, 4) if top > 0 else 0.0,
            "punctuation": round((weights["punctuation"] * counts["exclaim"]) / top, 4) if top > 0 else 0.0,
            "volume": round((weights["volume"] * counts["human_msgs"]) / top, 4) if top > 0 else 0.0,
        }
        kws = emotion.matched_keywords([m.get("text") or "" for m in msgs])
        reason = (
            "每分鐘真人留言 ≥ mean+1σ 熱區"
            + ("；情緒詞密集：" + "、".join(kws) if kws else "")
        )
        candidates.append(
            {
                "chat_start_epoch_ms": w["start_epoch_ms"],
                "chat_end_epoch_ms": w["end_epoch_ms"],
                "score": round(norm, 3),
                "emotion": {"score": round(norm, 3), "breakdown": breakdown, "counts": counts},
                "detection": {
                    "minute_volume": w["peak_minute_volume"],
                    "baseline_mean": round(float(mean), 3),
                    "baseline_sigma": round(float(sigma_value), 3),
                    "threshold": round(float(threshold), 3),
                },
                "reason": reason,
                "message_ids": [m["message_id"] for m in msgs],
                "texts": [m.get("text") or "" for m in msgs],
            }
        )

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:max_clips]
