"""分析邊界:「梗包」偵測 seam（Port/Stub/factory）+ adapter → highlights.v1。

真正的高光/「梗」偵測(埋梗 setup + 報梗 payoff 的喜劇結構)由另一位工程師之後提供
function;本檔先定義**介面(Protocol)**與一個 **Stub 實作**,並用 factory 綁定(DIP),
讓真 detector 可直接 drop-in(只換 ``get_bit_detector`` 的綁定)。

工程師 function 介面:
    detect(transcript: dict, log_info: Any = None) -> 梗包(bit-packages)

梗包 shape(內部形狀,尚未立契約;時間一律整數 ms):
    {
      "project_id": str,
      "source_duration_ms": int,
      "bits": [
        {
          "bit_id": "bit-001",
          "setup":   {"start_ms": int, "end_ms": int},   # 埋梗
          "payoffs": [{"start_ms": int, "end_ms": int}],  # 報梗/callback（可空）
          "score": float,                                  # 0..1
          "metadata": {"theme": str, "transcript": str, "suggested_title": str}
        }
      ],
      "metadata": {"generated_by": str}
    }

下游仍走 highlights.v1:``bits_to_highlights`` 把梗包轉為 highlights.v1,讓既有
composer/timeline/render(M2–M4)與前端契約完全不動。
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Protocol, runtime_checkable

from analysis.highlights import DEFAULT_PARAMS, EMOTION_KEYWORDS, detect_highlights
from analysis.validate import validate_highlights

BIT_STUB_VERSION = "bit-stub-1.0.0"
_MAX_PAYOFFS = 2


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@runtime_checkable
class BitDetector(Protocol):
    """工程師要實作的偵測介面。input:transcript(+log_info);output:梗包。"""

    def detect(self, transcript: dict[str, Any], log_info: Any = None) -> dict[str, Any]:
        ...


class StubBitDetector:
    """MVP 佔位偵測器:重用規則式 detect_highlights,把每個高光包成一個「梗」。

    TODO(engineer): replace with the real 梗 detector（setup/payoff 喜劇結構,
    可用 transcript + log_info)。本 stub 忽略 log_info。
    """

    def detect(self, transcript: dict[str, Any], log_info: Any = None) -> dict[str, Any]:  # noqa: ARG002
        result = detect_highlights(transcript)
        highlights = result["highlights"]

        bits: list[dict[str, Any]] = []
        for i, h in enumerate(highlights, start=1):
            text_i = h.get("transcript") or ""
            payoffs = _find_payoffs(text_i, highlights, exclude=h["highlight_id"])
            bits.append({
                "bit_id": f"bit-{i:03d}",
                "setup": {"start_ms": int(h["start_ms"]), "end_ms": int(h["end_ms"])},
                "payoffs": payoffs,
                "score": float(h.get("score", 0.0)),
                "metadata": {
                    "theme": h.get("reason", ""),
                    "transcript": text_i,
                    "suggested_title": h.get("suggested_title", ""),
                },
            })

        return {
            "project_id": result["project_id"],
            "source_duration_ms": int(result["source_duration_ms"]),
            "bits": bits,
            "metadata": {"generated_by": BIT_STUB_VERSION},
        }


def _find_payoffs(
    setup_text: str, highlights: list[dict[str, Any]], exclude: str
) -> list[dict[str, Any]]:
    """佔位啟發式:與 setup 共享情緒關鍵詞的其他高光 → 視為 callback/報梗。"""
    keys = [k for k in EMOTION_KEYWORDS if k in setup_text]
    payoffs: list[dict[str, Any]] = []
    for h in highlights:
        if h["highlight_id"] == exclude:
            continue
        text = h.get("transcript") or ""
        if any(k in text for k in keys):
            payoffs.append({"start_ms": int(h["start_ms"]), "end_ms": int(h["end_ms"])})
        if len(payoffs) >= _MAX_PAYOFFS:
            break
    return payoffs


@lru_cache(maxsize=1)
def get_bit_detector() -> BitDetector:
    """factory:回目前綁定的偵測器。工程師 drop-in real detector 時只改這裡。

    Tests set env / inject then call ``get_bit_detector.cache_clear()``.
    """
    return StubBitDetector()


def bits_to_highlights(
    bit_packages: dict[str, Any], params: dict[str, Any] | None = None
) -> dict[str, Any]:
    """adapter:梗包 → highlights.v1（下游 composer 消費的契約)。

    每個 bit → 一個 highlight(取 setup 區間)。payoffs 目前保留在梗包,未攤平進
    highlights(真 detector 定案後可加富組片)。輸出以 validate_highlights 自驗。
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    highlights: list[dict[str, Any]] = []
    for bit in bit_packages.get("bits", []):
        setup = bit["setup"]
        text = bit.get("metadata", {}).get("transcript", "")
        highlights.append({
            "highlight_id": bit["bit_id"],
            "start_ms": int(setup["start_ms"]),
            "end_ms": int(setup["end_ms"]),
            "score": float(bit.get("score", 0.0)),
            "reason": bit.get("metadata", {}).get("theme", ""),
            "transcript": text,
            "suggested_title": bit.get("metadata", {}).get("suggested_title") or (text[:12] or "梗片段"),
            "source_segment_ids": [],
            "selected": True,
            "locked": False,
        })

    doc = {
        "schema_version": "highlights.v1",
        "project_id": bit_packages["project_id"],
        "source_duration_ms": int(bit_packages["source_duration_ms"]),
        "analysis_version": BIT_STUB_VERSION,
        "parameters": {
            "max_clips": p["max_clips"],
            "min_duration_ms": p["min_duration_ms"],
            "max_duration_ms": p["max_duration_ms"],
            "padding_before_ms": p["padding_before_ms"],
            "padding_after_ms": p["padding_after_ms"],
        },
        "highlights": highlights,
        "created_at": _now_iso(),
    }
    validate_highlights(doc)
    return doc
