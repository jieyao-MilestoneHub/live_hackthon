"""AI 進度旁白（ProgressNarrator）— gated, additive, fail-open.

給定 pipeline 步驟（``step``）與該步的「數據/分析」``facts``，產生一句「資訊量多但精簡」
的繁體中文進度訊息：只講**用了哪些數據、正在做什麼分析**，讓使用者不再面對黑盒子。
嚴禁洩漏參數值、欄位名、函式名或任何程式細節。

Design（比照 ``analysis/highlights_llm.py`` 的骨架）:
  * 旗標 ``PROGRESS_NARRATOR_LLM``（default OFF）：開才呼叫 Amazon Bedrock（Converse + 強制
    tool use, ``temperature=0``）；否則 / 離線 / pytest 一律走 ``StubNarrator`` 的確定性模板。
  * Fail-open：任何錯誤（無憑證、限流、模型權限）都退回 ``StubNarrator`` 模板 —— 旁白壞掉
    **絕不**中斷 pipeline。
  * SOLID：``NarratorPort``（ISP 窄介面 Protocol）；``StubNarrator`` / ``RealNarrator`` 於 Port 後
    可互換（LSP）；boto3 只出現在 ``RealNarrator``（DIP）。步驟字串由 ``app.progress.StepKey``
    提供，narrator 只認 ``str`` 以保持零耦合。
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Callable, Protocol, runtime_checkable

from app.aws.config import get_attribution_config


@runtime_checkable
class NarratorPort(Protocol):
    """把一個 pipeline 步驟 + facts 轉成一句人話進度訊息。"""

    def narrate(self, *, step: str, facts: dict[str, Any], status: str = "RUNNING") -> str: ...


def llm_enabled() -> bool:
    return os.environ.get("PROGRESS_NARRATOR_LLM", "").strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------- #
# Deterministic templates — 離線 / CI / fail-open 的預設回覆。
# 同樣「資訊量多但精簡」風格：數據來源 + 當前分析 + 即時數字，塞進一句、無贅字。
# key 對齊 ``app.progress.StepKey`` 的字串值。
# --------------------------------------------------------------------------- #
def _minutes(ms: Any) -> str:
    try:
        return f"{int(ms) / 60000:.0f}"
    except (TypeError, ValueError):
        return "—"


def _seconds(ms: Any) -> str:
    try:
        return f"{int(ms) / 1000:.0f}"
    except (TypeError, ValueError):
        return "—"


def _n(value: Any, unit: str = "") -> str:
    return f"{value}{unit}" if value is not None else f"數{unit}" if unit else "數"


_TEMPLATES: dict[str, Callable[[dict[str, Any]], str]] = {
    "UPLOAD_RECEIVED": lambda f: "已接收影片與聊天室 LOG，準備進場分析。",
    "VALIDATING": lambda f: "正在驗證來源影片編碼與時間基準。",
    "TRANSCRIBING": lambda f: "正把直播音訊轉為逐字稿並分離說話者。",
    "ANALYZING_CHATLOG": lambda f: f"正從聊天室 LOG 解析情緒起伏與洗版熱區（{_n(f.get('messages'), ' 則')}）。",
    "DETECTING_HIGHLIGHTS": lambda f: f"交叉逐字稿與聊天室反應鎖定情緒高峰——已抓出 {_n(f.get('found'), ' 段')}。",
    "MODERATION_SCAN": lambda f: "正並行掃描畫面與字幕內容的合規風險。",
    "MODERATION_DECISION": lambda f: "彙整視覺與文字風險，判定發布分級。",
    "COMPOSING": lambda f: f"依起承轉合把 {_n(f.get('clips'), ' 段')}高光編排成初剪時間軸。",
    "READY": lambda f: f"初剪完成，{_n(f.get('clips'), ' 段')}精華已可預覽微調。",
    "PLANNING_SUBTITLES": lambda f: "正逐字生成雙層字幕與爆點關鍵字動畫。",
    "PLANNING_EFFECTS": lambda f: "為爆點段落配置轉場與強調特效。",
    "QUEUED": lambda f: "剪輯藍圖就緒，排入影片編碼佇列。",
    "RENDERING": lambda f: "FFmpeg 正合成畫面、字幕與特效輸出短片。",
    "VALIDATING_ARTIFACT": lambda f: "正驗證輸出短片的時長與完整性。",
    "PUBLISHING": lambda f: "封裝成品與縮圖，發佈可下載連結。",
    "DONE": lambda f: "完成，精華短片已可下載。",
    "SUMMARY": lambda f: (
        f"已從 {_minutes(f.get('source_duration_ms'))} 分鐘直播、依情緒高峰與聊天室熱度"
        f"選出 {_n(f.get('clips'), ' 段')}，產出 {_seconds(f.get('output_duration_ms'))} 秒精華短片。"
    ),
}

_FALLBACK = "處理中，正在分析本段資料。"


class StubNarrator:
    """Deterministic，無外部呼叫。離線/CI/fail-open 使用。"""

    def narrate(self, *, step: str, facts: dict[str, Any], status: str = "RUNNING") -> str:
        fn = _TEMPLATES.get(step)
        if fn is None:
            return _FALLBACK
        try:
            return fn(facts or {})
        except Exception:  # noqa: BLE001 — 模板永不炸
            return _FALLBACK


# --------------------------------------------------------------------------- #
# Real（Bedrock Converse + forced tool use）
# --------------------------------------------------------------------------- #
_TOOL_NAME = "write_progress_message"
_SYSTEM_PROMPT = (
    "你是直播剪輯流程的即時旁白。根據提供的 step 與 facts，用「一句」繁體中文說明目前"
    "正在用哪些數據、做什麼分析，讓使用者清楚且安心。風格＝資訊量多但精簡：把數據來源、"
    "當前分析與關鍵數字塞進一句，高訊噪比、無贅字客套與形容詞堆疊，不超過 40 字。"
    "嚴禁提到參數值、欄位名、函式名或任何程式/技術細節；只描述數據與分析行為。"
)


def _tool_config() -> dict[str, Any]:
    return {
        "toolChoice": {"tool": {"name": _TOOL_NAME}},
        "tools": [{
            "toolSpec": {
                "name": _TOOL_NAME,
                "description": "輸出一句『資訊量多但精簡』的繁中進度旁白。",
                "inputSchema": {"json": {
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                }},
            }
        }],
    }


class RealNarrator:
    """boto3 Bedrock Converse（強制 tool use）。任何失敗退回 Stub 模板。"""

    def __init__(self, region: str, model_id: str) -> None:
        import boto3  # lazy

        self._client = boto3.client("bedrock-runtime", region_name=region)
        self._model_id = model_id
        self._stub = StubNarrator()

    def narrate(self, *, step: str, facts: dict[str, Any], status: str = "RUNNING") -> str:
        try:
            payload = {"step": step, "status": status, "facts": facts or {}}
            resp = self._client.converse(
                modelId=self._model_id,
                system=[{"text": _SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": [{"text": json.dumps(payload, ensure_ascii=False)}]}],
                inferenceConfig={"temperature": 0, "topP": 1, "maxTokens": 128},
                toolConfig=_tool_config(),
            )
            content = (resp.get("output", {}).get("message", {}) or {}).get("content", []) or []
            for block in content:
                tool = block.get("toolUse")
                if tool and tool.get("name") == _TOOL_NAME:
                    msg = ((tool.get("input") or {}).get("message") or "").strip()
                    if msg:
                        return msg
            return self._stub.narrate(step=step, facts=facts, status=status)
        except Exception:  # noqa: BLE001 — fail-open：旁白絕不反噬 pipeline
            return self._stub.narrate(step=step, facts=facts, status=status)


def _model_id() -> str:
    # 短進度旁白用便宜快速的 Haiku（Converse 支援）；可用 env 覆寫。
    return os.environ.get("PROGRESS_NARRATOR_MODEL_ID", "us.anthropic.claude-haiku-4-5")


@lru_cache(maxsize=1)
def get_narrator() -> NarratorPort:
    """旗標 PROGRESS_NARRATOR_LLM 開才用 Bedrock；預設/離線/建構失敗走 Stub。

    測試設 env 後呼叫 ``get_narrator.cache_clear()`` 重綁。
    """
    if not llm_enabled():
        return StubNarrator()
    try:
        return RealNarrator(get_attribution_config().bedrock_region, _model_id())
    except Exception:  # noqa: BLE001 — 建構失敗（無 boto3/憑證）也 fail-open
        return StubNarrator()
