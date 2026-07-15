"""Human/plain renderers for the core entities (shared by several commands).

JSON mode never calls these — it dumps the raw contract payload. These only shape
the *human* (stderr-friendly, brief) and *plain* (tab-separated) surfaces.
"""
from __future__ import annotations

from typing import Any

from .output import Printer


def fmt_ms(ms: Any) -> str:
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return "--:--"
    m, s = divmod(ms // 1000, 60)
    return f"{m:d}:{s:02d}"


# -- project ---------------------------------------------------------------
def project_human(p: Printer, proj: dict[str, Any]) -> None:
    print(f"{proj.get('project_id')}  [{proj.get('status')}]")
    bits = []
    if proj.get("title"):
        bits.append(f"title={proj['title']}")
    if proj.get("analysis_source"):
        bits.append(f"source={proj['analysis_source']}")
    if proj.get("target_duration_ms") is not None:
        bits.append(f"target={fmt_ms(proj['target_duration_ms'])}")
    if proj.get("latest_timeline_version"):
        bits.append(f"timeline_v{proj['latest_timeline_version']}")
    if proj.get("latest_artifact_id"):
        bits.append(f"artifact={proj['latest_artifact_id']}")
    if bits:
        print("  " + "  ".join(bits))


# -- highlights ------------------------------------------------------------
def highlights_human(p: Printer, hl: dict[str, Any]) -> None:
    items = hl.get("highlights", [])
    print(f"{len(items)} highlight(s) for {hl.get('project_id')}")
    for i, h in enumerate(items):
        title = h.get("suggested_title") or h.get("reason") or h.get("transcript") or ""
        title = (title[:56] + "…") if len(title) > 57 else title
        flags = "".join(
            [
                "L" if h.get("locked") else "",
                "x" if h.get("status") == "excluded" else "",
            ]
        )
        score = h.get("score")
        score_s = f"{score:.2f}" if isinstance(score, (int, float)) else str(score)
        print(
            f"  [{i}] {p.paint(h.get('highlight_id',''), 'dim')}  "
            f"score={score_s}  {fmt_ms(h.get('start_ms'))}–{fmt_ms(h.get('end_ms'))}"
            f"  {flags:2}  {title}"
        )


def highlights_plain(hl: dict[str, Any]) -> str:
    rows = []
    for h in hl.get("highlights", []):
        rows.append(
            "\t".join(
                str(x)
                for x in (
                    h.get("highlight_id", ""),
                    h.get("score", ""),
                    h.get("start_ms", ""),
                    h.get("end_ms", ""),
                    (h.get("suggested_title") or h.get("reason") or "").replace("\t", " "),
                )
            )
        )
    return "\n".join(rows)


# -- timeline --------------------------------------------------------------
def timeline_human(p: Printer, t: dict[str, Any]) -> None:
    clips = t.get("clips", [])
    print(
        f"timeline v{t.get('version')}  {len(clips)} clip(s)  "
        f"actual={fmt_ms(t.get('actual_duration_ms'))} / target={fmt_ms(t.get('target_duration_ms'))}"
        f"  {t.get('aspect_ratio') or ''}"
    )
    for c in clips:
        print(
            f"  #{c.get('timeline_order')}  {c.get('highlight_id')}  "
            f"src {fmt_ms(c.get('source_start_ms'))}–{fmt_ms(c.get('source_end_ms'))}"
            f"  →  tl {fmt_ms(c.get('timeline_start_ms'))}–{fmt_ms(c.get('timeline_end_ms'))}"
        )


# -- render ----------------------------------------------------------------
def render_human(p: Printer, r: dict[str, Any]) -> None:
    print(
        f"render {r.get('render_id')}  [{r.get('status')}]  "
        f"stage={r.get('current_stage') or '-'}  timeline_v{r.get('timeline_version')}"
    )
    if r.get("artifact_id"):
        print(f"  artifact={r['artifact_id']}")
