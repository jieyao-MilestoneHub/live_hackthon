"""Real one-pass FFmpeg encoder (demand.md §十三/§十四).

Consumes source.mp4 + timeline clips + subtitle VTT + render_spec and produces
final.mp4 / preview.mp4 / thumbnail.jpg in ONE main encode: per-clip trim →
scale/pad to the target aspect → concat → burn subtitles → loudnorm audio → x264.
Runs inside the AWS Batch container (RENDER_ENCODER=ffmpeg).

Effects (effects.v1) are frozen into the artifact bundle and passed in, but the
one-pass filtergraph currently applies cut/concat/aspect/subtitle/audio only;
zoom/flash effect application is a follow-up (kept out of the graph to keep the
encode robust). The effect_seed stays reproducible because the PLAN is frozen.

The source is provided as a local file path (streamed to disk by the render
worker, not buffered in RAM — safe for multi-GB inputs). Each clip is extracted
with FFmpeg INPUT seeking (`-ss <start> -i source -t <dur>`) so a clip late in a
long source does not decode from 0; the normalized segments are then concatenated
and finished (subtitles + loudnorm) in one pass.
"""
from __future__ import annotations

import os
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
        fx_ctx = EffectContext(width=width, height=height, fps=fps)

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

            subs_path = os.path.join(workdir, "subs.vtt")
            with open(subs_path, "w", encoding="utf-8") as fh:
                fh.write(inputs.subtitle_vtt or "WEBVTT\n\n")

            # Phase 1: extract + normalize each clip with INPUT seeking (`-ss` before
            # `-i`) so a clip late in a long source seeks to a nearby keyframe instead
            # of decoding from 0. Uniform output params let the concat stream-copy.
            seg_paths: list[str] = []
            n = len(clips)
            for i, clip in enumerate(clips):
                start = _sec(clip["source_start_ms"])
                dur = max(0.05, _sec(clip["source_end_ms"]) - start)
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
            # 燒字幕：預設 styled ASS（字型/顏色/邊框/位置 + keyword 出現動畫），退回 VTT。
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
