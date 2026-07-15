"""字幕樣式模型：可擴充的字型/字體/顏色/邊框/位置設定（Creative Planning 共用）。

依 SOLID 的 OCP/擴展性設計：樣式是**資料**（`SubtitleStyle` dataclass）而非散在
encoder 的魔術字串。兩層字幕各有一組 preset —— `CAPTION_STYLE`（逐字稿基底、下三分之一、
白字黑邊、易讀）與 `KEYWORD_STYLE`（爆點關鍵字、置中偏上、大字強調色）。使用者透過
timeline.v1 的開放欄位 `subtitle_settings.style` 覆寫（deep-merge），或給 flat 覆寫套用
兩層。輸出序列化進 subtitle.v1 的開放 `style`，由 render 層（`workers/render/subtitle_render.py`）
轉成 ASS Style。

`alignment` 沿用 ASS/SSA 的數字鍵盤語意（1–9）：1=左下 2=中下 3=右下 / 4=左中 5=正中
6=右中 / 7=左上 8=中上 9=右上。顏色以 `#RRGGBB` 表示（render 層再轉 ASS `&HBBGGRR`）。
純資料 + 純函式、無副作用、時間無關。
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

# --- 出現動畫（多用於 keyword；具體視覺由 render 層的 override tag 實作）----------
KEYWORD_ANIMATION_TYPES: tuple[str, ...] = ("pop", "fade", "flash", "shake", "none")
DEFAULT_KEYWORD_ANIMATION: dict[str, Any] = {"type": "pop", "duration_ms": 260}


@dataclass(frozen=True)
class SubtitleStyle:
    """一組字幕樣式（可被 subtitle_settings.style 覆寫；新增樣式軸只需加欄位）。"""

    font_family: str = "Noto Sans TC"
    font_size: int = 48
    bold: bool = True
    primary_color: str = "#FFFFFF"   # 主色（#RRGGBB）
    outline_color: str = "#000000"   # 邊框色
    outline_width: int = 3           # 邊框寬（px）
    shadow: int = 1                  # 陰影深度
    alignment: int = 2               # ASS numpad 1–9（2 = 中下）
    margin_v: int = 96               # 垂直邊距（px）
    margin_l: int = 48
    margin_r: int = 48


# 兩層字幕的預設 preset。
CAPTION_STYLE = SubtitleStyle()  # 逐字稿基底字幕（下三分之一、白字黑邊）
KEYWORD_STYLE = SubtitleStyle(   # 爆點關鍵字（置中偏上、大字、強調黃）
    font_size=96,
    primary_color="#FFE23D",
    outline_width=5,
    shadow=2,
    alignment=8,
    margin_v=220,
)

_FIELDS = {f.name for f in dataclasses.fields(SubtitleStyle)}


def style_to_dict(style: SubtitleStyle) -> dict[str, Any]:
    """dataclass → 可序列化 dict（進 subtitle.v1 的開放 style）。"""
    return dataclasses.asdict(style)


def style_from_dict(data: dict[str, Any] | None) -> SubtitleStyle:
    """dict → SubtitleStyle（忽略未知欄位，缺欄位用預設）。供 render 層還原。"""
    known = {k: v for k, v in (data or {}).items() if k in _FIELDS and v is not None}
    return SubtitleStyle(**known)


def merge_style(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    """淺層覆寫（override 的非 None 值覆蓋 base）；樣式是扁平表故淺層即足夠。"""
    out = dict(base)
    for k, v in (override or {}).items():
        if v is not None:
            out[k] = v
    return out


def resolve_styles(subtitle_settings: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """把 preset 與使用者覆寫合併成 ``{"caption": {...}, "keyword": {...}}``。

    覆寫來源 ``subtitle_settings.style`` 支援三種形狀（可混用）：
      * ``{"caption": {...}}`` / ``{"keyword": {...}}``：只覆寫該層；
      * flat 欄位（如 ``{"font_family": "..."}``）：同時套用兩層（全域字型/顏色）。
    合併順序：preset ← 該層覆寫 ← flat 覆寫。
    """
    style_cfg = (subtitle_settings or {}).get("style") or {}
    per_kind_keys = {"caption", "keyword"}
    flat = {k: v for k, v in style_cfg.items() if k not in per_kind_keys}

    caption = merge_style(style_to_dict(CAPTION_STYLE), style_cfg.get("caption"))
    keyword = merge_style(style_to_dict(KEYWORD_STYLE), style_cfg.get("keyword"))
    caption = merge_style(caption, flat)
    keyword = merge_style(keyword, flat)
    return {"caption": caption, "keyword": keyword}


def resolve_animation(override: dict[str, Any] | None) -> dict[str, Any]:
    """把 keyword 動畫覆寫併到預設；未知 type 退回預設 pop（保穩）。"""
    anim = dict(DEFAULT_KEYWORD_ANIMATION)
    for k, v in (override or {}).items():
        if v is not None:
            anim[k] = v
    if anim.get("type") not in KEYWORD_ANIMATION_TYPES:
        anim["type"] = DEFAULT_KEYWORD_ANIMATION["type"]
    return anim
