"""高光分析模組。

- 規則式偵測:transcript.v1 → highlights.v1（``detect_highlights``,LLM 可選)。
- 分析邊界 seam:「梗包」偵測(``BitDetector`` Protocol + ``StubBitDetector`` +
  ``get_bit_detector`` factory)+ adapter ``bits_to_highlights`` → highlights.v1。
  真 detector 由工程師之後 drop-in（只換 factory 綁定)。
"""
from .bits import (
    BitDetector,
    StubBitDetector,
    bits_to_highlights,
    get_bit_detector,
)
from .highlights import DEFAULT_PARAMS, detect_highlights

__all__ = [
    "detect_highlights",
    "DEFAULT_PARAMS",
    "BitDetector",
    "StubBitDetector",
    "get_bit_detector",
    "bits_to_highlights",
]
