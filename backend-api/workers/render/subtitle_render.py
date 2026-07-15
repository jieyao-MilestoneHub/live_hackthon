"""字幕燒錄 renderer：subtitle.v1 + style → ASS（libass 全控樣式與動畫）。

把兩層字幕轉成 ASS：`Caption`/`Keyword` 兩個 Style 帶入樣式模型（字型/字體/顏色/邊框/位置/
粗體），Tier 2 keyword 逐句加出現動畫 override tag（\\fad 淡入、\\t(\\fscx\\fscy) 彈入）。
設計成可替換的 renderer seam——由 ``SUBTITLE_STYLE_ENGINE`` 選（預設 ass，退回 vtt）。

ASS 色彩為 ``&HAABBGGRR``（此處不透明 AA=00）；時間為 ``H:MM:SS.cs``（百分秒）；Alignment
沿用 numpad 1–9。純函式、可離線斷言（不需跑 FFmpeg）。
"""
from __future__ import annotations

from typing import Any

from creative.style import CAPTION_STYLE, KEYWORD_STYLE, style_from_dict, style_to_dict

_STYLE_FORMAT = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
    "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
    "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
)
_EVENT_FORMAT = "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"


def _hex_to_ass(color: str) -> str:
    c = (color or "").lstrip("#")
    if len(c) != 6:
        return "&H00FFFFFF"
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H00{b}{g}{r}".upper()


def _ass_time(ms: int) -> str:
    ms = max(0, int(ms))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h}:{m:02d}:{s:02d}.{ms // 10:02d}"


def _style_line(name: str, style: dict[str, Any]) -> str:
    # 以 preset 補齊缺欄位，確保欄位齊全、值合法。
    s = style_to_dict(style_from_dict(style))
    bold = -1 if s.get("bold") else 0
    return (
        f"Style: {name},{s['font_family']},{int(s['font_size'])},"
        f"{_hex_to_ass(s['primary_color'])},&H000000FF,{_hex_to_ass(s['outline_color'])},&H00000000,"
        f"{bold},0,0,0,100,100,0,0,1,"
        f"{int(s['outline_width'])},{int(s.get('shadow', 0))},{int(s['alignment'])},"
        f"{int(s['margin_l'])},{int(s['margin_r'])},{int(s['margin_v'])},1"
    )


def _anim_tag(animation: dict[str, Any] | None) -> str:
    if not animation:
        return ""
    t = (animation.get("type") or "none").lower()
    d = max(1, int(animation.get("duration_ms", 240)))
    if t == "pop":  # 縮小→過衝→回正 的彈入
        return f"{{\\fad({max(60, d // 2)},{max(40, d // 3)})\\fscx70\\fscy70\\t(0,{d},\\fscx112\\fscy112)\\t({d},{d + 120},\\fscx100\\fscy100)}}"
    if t == "fade":
        return f"{{\\fad({d},{d})}}"
    if t == "flash":
        q = max(30, d // 4)
        return f"{{\\fad({q},{q})}}"
    if t == "shake":
        return "{\\fad(60,60)}"
    return ""


def _escape_text(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace("\n", "\\N").replace("{", "(").replace("}", ")")


def to_ass(subtitle: dict[str, Any], width: int, height: int) -> str:
    """subtitle.v1 (+style) → ASS 全文字串。"""
    style = subtitle.get("style") or {}
    caption = style.get("caption") if isinstance(style.get("caption"), dict) else style_to_dict(CAPTION_STYLE)
    keyword = style.get("keyword") if isinstance(style.get("keyword"), dict) else style_to_dict(KEYWORD_STYLE)

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {int(width)}",
        f"PlayResY: {int(height)}",
        "",
        "[V4+ Styles]",
        _STYLE_FORMAT,
        _style_line("Caption", caption),
        _style_line("Keyword", keyword),
        "",
        "[Events]",
        _EVENT_FORMAT,
    ]
    for cue in subtitle.get("cues", []):
        is_kw = cue.get("kind") == "keyword"
        style_name = "Keyword" if is_kw else "Caption"
        tag = _anim_tag(cue.get("animation")) if is_kw else ""
        text = tag + _escape_text(cue.get("text", ""))
        lines.append(
            f"Dialogue: 0,{_ass_time(cue['start_ms'])},{_ass_time(cue['end_ms'])},{style_name},,0,0,0,,{text}"
        )
    return "\n".join(lines) + "\n"
