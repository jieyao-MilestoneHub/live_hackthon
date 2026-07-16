"""Real one-pass FFmpeg encoder (demand.md §十三/§十四).

Consumes source.mp4 + timeline clips + subtitle VTT + render_spec and produces
final.mp4 / preview.mp4 / thumbnail.jpg in ONE main encode: per-clip trim →
scale/pad to the target aspect → concat → burn subtitles → loudnorm audio → x264.
Runs inside the AWS Batch container (RENDER_ENCODER=ffmpeg).

Effects (effects.v1) are applied in Phase 1 per-clip: each effect's OUTPUT-timeline
range is mapped to the clip it overlaps and rendered as a window-gated crop/scale
(zoom/pan/shake) or brightness pop (flash), so effects can target sub-segments
WITHIN a highlight — not just clip openings. Emphasis words (subtitle.v1) animate
via a burned ASS track (Phase 2) when one is supplied; plain VTT is the fallback.
The effect_seed stays reproducible because the PLAN is frozen and the only encoder-
derived parameter (pan direction) is a pure function of (effect_seed, clip order).

The source is provided as a local file path (streamed to disk by the render
worker, not buffered in RAM — safe for multi-GB inputs). Each clip is extracted
with FFmpeg INPUT seeking (`-ss <start> -i source -t <dur>`) so a clip late in a
long source does not decode from 0; the normalized segments are then concatenated
and finished (subtitles + loudnorm) in one pass.
"""
from __future__ import annotations

import os
import random
import shutil
import subprocess
import tempfile
from typing import TYPE_CHECKING, Any

from composer.transitions import get_join_strategy
from creative.effects_registry import EffectContext
from workers.render.effects_apply import clip_effect_fragments
from workers.render.subtitle_render import to_ass

if TYPE_CHECKING:
    from workers.render_worker import EncodeInputs


def _env_on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _ffmpeg_bin() -> str:
    override = os.environ.get("FFMPEG_BINARY")
    if override:
        return override
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:  # local dev fallback: pip imageio-ffmpeg bundles a static binary
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return "ffmpeg"


def _sec(ms: Any) -> float:
    return max(0.0, float(ms) / 1000.0)


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "ignore")[-2000:]
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {tail}")


def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


# --- effects.v1 → filtergraph ---------------------------------------------
# effects.v1 times are OUTPUT-timeline ms (0 = start of the rendered short). We
# apply effects in Phase 1 (per clip), so we map each effect's output range onto
# the clip it overlaps and express it in CLIP-LOCAL seconds. Every effect is
# gated to its window and is identity outside it, so multiple effects on one clip
# chain safely (an effect can begin mid-clip → sub-segment targeting).

_RANGED_EFFECTS = {"zoom_in", "zoom_out", "pan", "shake"}
_FLASH_DEFAULT_MS = 240


def _clip_window(
    effect_start_ms: Any, effect_end_ms: Any, clip_start_ms: int, clip_end_ms: int
) -> tuple[float, float, float] | None:
    """Overlap of an output-timeline effect range with a clip → clip-local
    ``(ls, le, td)`` seconds, or ``None`` when they don't overlap."""
    os_ = max(clip_start_ms, int(effect_start_ms))
    oe = min(clip_end_ms, int(effect_end_ms))
    if oe <= os_:
        return None
    ls = (os_ - clip_start_ms) / 1000.0
    le = (oe - clip_start_ms) / 1000.0
    return ls, le, max(0.05, le - ls)


def _clamp_strength(value: Any) -> float:
    try:
        s = float(value)
    except (TypeError, ValueError):
        s = 0.08
    return max(0.02, min(0.30, s))


def _pan_direction(seed: int, order: int) -> str:
    """Deterministic pan direction (pure function of frozen inputs)."""
    return random.Random(f"{int(seed)}:{int(order)}").choice(("lr", "rl", "tb", "bt"))


def _geometry_vf(
    etype: str, s: float, ls: float, le: float, td: float,
    width: int, height: int, seed: int, order: int,
) -> str:
    """crop+scale filter applying ``etype`` over clip-local ``[ls, le]`` seconds,
    identity outside that window. Values are single-quoted so inner commas are
    literal to the filtergraph parser (no ``\\,`` escaping needed)."""
    ls_s, le_s, td_s, s_s = f"{ls:.3f}", f"{le:.3f}", f"{td:.3f}", f"{s:.4f}"
    w, h = int(width), int(height)
    gate = f"between(t,{ls_s},{le_s})"
    ramp = f"min((t-{ls_s})/{td_s},1)"
    if etype in ("zoom_in", "zoom_out"):
        prog = ramp if etype == "zoom_in" else f"(1-{ramp})"
        z = f"if({gate},1+{s_s}*{prog},1)"
        return f"crop=w='iw/({z})':h='ih/({z})':x='(iw-ow)/2':y='(ih-oh)/2',scale={w}:{h}"
    if etype == "pan":
        z = f"if({gate},1+{s_s},1)"
        direction = _pan_direction(seed, order)
        if direction == "lr":
            x, y = f"(iw-ow)*{ramp}", "(ih-oh)/2"
        elif direction == "rl":
            x, y = f"(iw-ow)*(1-{ramp})", "(ih-oh)/2"
        elif direction == "tb":
            x, y = "(iw-ow)/2", f"(ih-oh)*{ramp}"
        else:  # bt
            x, y = "(iw-ow)/2", f"(ih-oh)*(1-{ramp})"
        return f"crop=w='iw/({z})':h='ih/({z})':x='{x}':y='{y}',scale={w}:{h}"
    # shake: decaying oscillation about center; identity outside the window.
    z = f"if({gate},1+{s_s},1)"
    dx = f"if({gate},(iw-ow)/2*max(0,1-(t-{ls_s})/{td_s})*sin(2*PI*12*(t-{ls_s})),0)"
    dy = f"if({gate},(ih-oh)/2*max(0,1-(t-{ls_s})/{td_s})*sin(2*PI*13*(t-{ls_s})+1.7),0)"
    return f"crop=w='iw/({z})':h='ih/({z})':x='(iw-ow)/2+{dx}':y='(ih-oh)/2+{dy}',scale={w}:{h}"


def _flash_vf(la: float, d: float) -> str:
    """Short brightness pop at clip-local ``[la, la+d]`` seconds."""
    la_s, d_s = f"{la:.3f}", f"{max(0.04, d):.3f}"
    return (
        f"eq=brightness='if(between(t,{la_s},{la_s}+{d_s}),"
        f"0.6*max(0,1-(t-{la_s})/{d_s}),0)':eval=frame"
    )


def _clip_filters(
    effects_doc: dict[str, Any], clip: dict[str, Any], width: int, height: int, seed: int
) -> list[str]:
    """Extra per-clip filter strings from an effects.v1 doc (ranged geometry +
    point flashes), mapped from output-timeline into this clip's local time."""
    cs, ce = int(clip["timeline_start_ms"]), int(clip["timeline_end_ms"])
    order = int(clip.get("timeline_order", 0))
    out: list[str] = []
    for e in (effects_doc or {}).get("effects", []):
        etype = e.get("type")
        if etype in _RANGED_EFFECTS and "start_ms" in e and "end_ms" in e:
            win = _clip_window(e["start_ms"], e["end_ms"], cs, ce)
            if win is None:
                continue
            ls, le, td = win
            out.append(_geometry_vf(
                etype, _clamp_strength(e.get("strength")), ls, le, td, width, height, seed, order
            ))
        elif etype == "flash_transition" and "at_ms" in e:
            at = int(e["at_ms"])
            if cs <= at < ce:
                la = (at - cs) / 1000.0
                d = int(e.get("duration_ms", _FLASH_DEFAULT_MS)) / 1000.0
                out.append(_flash_vf(la, d))
    return out


class FFmpegEncoder:
    needs_source = True

    def encode(self, inputs: "EncodeInputs") -> dict[str, bytes]:
        spec = inputs.render_spec
        res = spec.get("resolution", {})
        width, height = int(res.get("width", 1080)), int(res.get("height", 1920))
        enc = spec.get("encode", {})
        crf = int(enc.get("crf", 20))
        fps = int(enc.get("fps", 30))
        normalize = bool(spec.get("audio", {}).get("normalize", True))

        clips = sorted(inputs.timeline.get("clips", []), key=lambda c: c["timeline_order"])
        if not clips:
            raise ValueError("timeline has no clips to render")

        # 降卡點：接點微淡（dip），總長不變、與 concat 相容（RENDER_JOIN 選策略，預設 micro_fade）。
        join = get_join_strategy(os.environ.get("RENDER_JOIN"))
        fade_s = join.fade_ms / 1000.0
        # 特效套用（gated；預設關 → 離線/stub/既有測試不受影響）。
        apply_effects = _env_on("RENDER_APPLY_EFFECTS")
        effects_list: list[dict[str, Any]] = (inputs.effects or {}).get("effects", []) if apply_effects else []
        fx_ctx = EffectContext(width=width, height=height, fps=fps, seed=int(spec.get("effect_seed", 0)))

        ff = _ffmpeg_bin()
        vf_norm = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}"
        )
        with tempfile.TemporaryDirectory() as workdir:
            # Source: prefer the streamed path (flat memory); else materialize the
            # bytes (stub/legacy callers that still pass source=<bytes>).
            source_path = inputs.source_path
            if not (source_path and os.path.exists(source_path)):
                source_path = os.path.join(workdir, "source.mp4")
                with open(source_path, "wb") as fh:
                    fh.write(inputs.source or b"")

            # Burn from ASS when the worker authored one (animated emphasis words);
            # otherwise fall back to the plain-caption VTT.
            if inputs.subtitle_ass:
                subs_path = os.path.join(workdir, "subs.ass")
                subs_body = inputs.subtitle_ass
            else:
                subs_path = os.path.join(workdir, "subs.vtt")
                subs_body = inputs.subtitle_vtt or "WEBVTT\n\n"
            with open(subs_path, "w", encoding="utf-8") as fh:
                fh.write(subs_body)

            seed = int(spec.get("effect_seed", 0))

            # Phase 1: extract + normalize each clip with INPUT seeking (`-ss` before
            # `-i`) so a clip late in a long source seeks to a nearby keyframe instead
            # of decoding from 0. Effects (effects.v1) are injected here per clip, in
            # clip-local time, so they can target sub-segments within a highlight.
            seg_paths: list[str] = []
            n = len(clips)
            for i, clip in enumerate(clips):
                start = _sec(clip["source_start_ms"])
                dur = max(0.05, _sec(clip["source_end_ms"]) - start)
                # NOTE(edit-lane): edit 的 _clip_filters 保留於本檔，但活躍 encoder 統一走 main 的
                # clip_effect_fragments（見下 vf_parts）；兩者皆渲染 effects.v1，避免雙重套用只用一套。
                # 若 edit 特效語意與 main 有差，由 edit owner reconcile。
                seg = os.path.join(workdir, f"seg{i:03d}.mp4")

                vf_parts = [vf_norm]
                af_parts: list[str] = []
                # 接點微淡（第一刀不淡入、最後一刀不淡出）→ 柔化硬切、總長不變。
                if fade_s > 0 and n > 1:
                    out_st = max(0.0, dur - fade_s)
                    if i > 0:
                        vf_parts.append(f"fade=t=in:st=0:d={fade_s:.3f}")
                        af_parts.append(f"afade=t=in:st=0:d={fade_s:.3f}")
                    if i < n - 1:
                        vf_parts.append(f"fade=t=out:st={out_st:.3f}:d={fade_s:.3f}")
                        af_parts.append(f"afade=t=out:st={out_st:.3f}:d={fade_s:.3f}")
                # 特效片段（gated；歸屬此 clip 的 zoom/flash 等）。
                if effects_list:
                    vf_parts.extend(clip_effect_fragments(clip, effects_list, fx_ctx))

                cmd = [
                    ff, "-y", "-ss", f"{start:.3f}", "-i", source_path, "-t", f"{dur:.3f}",
                    "-map", "0:v:0", "-map", "0:a:0?",
                    "-vf", ",".join(vf_parts),
                    "-c:v", "libx264", "-crf", str(crf), "-preset", "veryfast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
                    "-video_track_timescale", "90000",
                ]
                if af_parts:
                    cmd += ["-af", ",".join(af_parts)]
                cmd.append(seg)
                _run(cmd)
                seg_paths.append(seg)

            # Phase 2: concat the segments + burn subtitles + loudnorm + faststart → final.
            concat_list = os.path.join(workdir, "concat.txt")
            with open(concat_list, "w", encoding="utf-8") as fh:
                for seg in seg_paths:
                    fh.write(f"file '{seg.replace(os.sep, '/')}'\n")
            final_path = os.path.join(workdir, "final.mp4")
            subs_escaped = subs_path.replace("\\", "/").replace(":", "\\:")
            # 燒字幕：活躍 pipeline 走 main 的 subtitle.v1 → styled ASS（ass filter，與 main 行為相同，
            # 不改活躍路徑）；缺 subtitle 時退回 edit 旁路的現成 ASS（subtitle_ass 已寫入 subs_path，
            # +fontsdir 供 CJK 字型）；再退回 VTT。ASS 失敗永不中斷編碼。
            sub_filter = f"subtitles='{subs_escaped}'"
            if os.environ.get("SUBTITLE_STYLE_ENGINE", "ass").strip().lower() == "ass" and inputs.subtitle:
                try:
                    ass_path = os.path.join(workdir, "subs.ass")
                    with open(ass_path, "w", encoding="utf-8") as fh:
                        fh.write(to_ass(inputs.subtitle, width, height))
                    ass_escaped = ass_path.replace("\\", "/").replace(":", "\\:")
                    sub_filter = f"ass='{ass_escaped}'"
                except Exception:  # noqa: BLE001 — ASS 失敗退回 VTT，永不中斷編碼
                    sub_filter = f"subtitles='{subs_escaped}'"
            elif inputs.subtitle_ass:
                # edit 旁路（無 subtitle.v1 時）：subs_path 已是現成 ASS；補 fontsdir（否則 zh-TW tofu）。
                fonts_dir = os.environ.get("SUBTITLE_FONTS_DIR")
                if fonts_dir:
                    fonts_escaped = fonts_dir.replace("\\", "/").replace(":", "\\:")
                    sub_filter += f":fontsdir='{fonts_escaped}'"
            final_cmd = [
                ff, "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
                "-vf", sub_filter,
            ]
            if normalize:
                final_cmd += ["-af", "loudnorm"]
            final_cmd += [
                "-c:v", "libx264", "-crf", str(crf), "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                final_path,
            ]
            _run(final_cmd)

            # Preview: quick low-res, first ~6s.
            pv_w = max(2, (width // 2) // 2 * 2)
            pv_h = max(2, (height // 2) // 2 * 2)
            preview_path = os.path.join(workdir, "preview.mp4")
            _run([
                ff, "-y", "-i", final_path, "-t", "6",
                "-vf", f"scale={pv_w}:{pv_h}",
                "-c:v", "libx264", "-crf", "28", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
                preview_path,
            ])

            # Thumbnail: a frame ~1s in (fall back to the very first frame).
            thumb_path = os.path.join(workdir, "thumb.jpg")
            try:
                _run([ff, "-y", "-ss", "1", "-i", final_path, "-vframes", "1", thumb_path])
            except RuntimeError:
                _run([ff, "-y", "-i", final_path, "-vframes", "1", thumb_path])

            return {
                "final": _read(final_path),
                "preview": _read(preview_path) if os.path.exists(preview_path) else _read(final_path),
                "thumbnail": _read(thumb_path) if os.path.exists(thumb_path) else b"",
            }
