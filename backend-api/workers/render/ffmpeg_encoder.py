"""Real one-pass FFmpeg encoder (demand.md §十三/§十四).

Consumes source.mp4 + timeline clips + subtitle VTT + render_spec and produces
final.mp4 / preview.mp4 / thumbnail.jpg in ONE main encode: per-clip trim →
scale/pad to the target aspect → concat → burn subtitles → loudnorm audio → x264.
Runs inside the AWS Batch container (RENDER_ENCODER=ffmpeg).

Effects (effects.v1) are frozen into the artifact bundle and passed in, but the
one-pass filtergraph currently applies cut/concat/aspect/subtitle/audio only;
zoom/flash effect application is a follow-up (kept out of the graph to keep the
encode robust). The effect_seed stays reproducible because the PLAN is frozen.

Note: for demo-length clips the source is held in memory then written to a temp
file so ffmpeg can seek/trim accurately. Large sources should stream to disk.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from workers.render_worker import EncodeInputs


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
        encode = spec.get("encode", {})
        crf = int(encode.get("crf", 20))
        fps = int(encode.get("fps", 30))
        normalize = bool(spec.get("audio", {}).get("normalize", True))

        clips = sorted(inputs.timeline.get("clips", []), key=lambda c: c["timeline_order"])
        if not clips:
            raise ValueError("timeline has no clips to render")

        ff = _ffmpeg_bin()
        with tempfile.TemporaryDirectory() as workdir:
            source_path = os.path.join(workdir, "source.mp4")
            with open(source_path, "wb") as fh:
                fh.write(inputs.source or b"")
            subs_path = os.path.join(workdir, "subs.vtt")
            with open(subs_path, "w", encoding="utf-8") as fh:
                fh.write(inputs.subtitle_vtt or "WEBVTT\n\n")

            final_path = os.path.join(workdir, "final.mp4")
            preview_path = os.path.join(workdir, "preview.mp4")
            thumb_path = os.path.join(workdir, "thumb.jpg")

            filtergraph = self._build_filtergraph(clips, width, height, fps, subs_path, normalize)
            audio_map = "[ao]" if normalize else "[ac]"
            _run([
                ff, "-y", "-i", source_path,
                "-filter_complex", filtergraph,
                "-map", "[vout]", "-map", audio_map,
                "-c:v", "libx264", "-crf", str(crf), "-preset", "veryfast",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                final_path,
            ])

            # Preview: quick low-res, first ~6s.
            pv_w = max(2, (width // 2) // 2 * 2)
            pv_h = max(2, (height // 2) // 2 * 2)
            _run([
                ff, "-y", "-i", final_path, "-t", "6",
                "-vf", f"scale={pv_w}:{pv_h}",
                "-c:v", "libx264", "-crf", "28", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
                preview_path,
            ])

            # Thumbnail: a frame ~1s in (fall back to the very first frame).
            try:
                _run([ff, "-y", "-ss", "1", "-i", final_path, "-vframes", "1", thumb_path])
            except RuntimeError:
                _run([ff, "-y", "-i", final_path, "-vframes", "1", thumb_path])

            return {
                "final": _read(final_path),
                "preview": _read(preview_path) if os.path.exists(preview_path) else _read(final_path),
                "thumbnail": _read(thumb_path) if os.path.exists(thumb_path) else b"",
            }

    @staticmethod
    def _build_filtergraph(
        clips: list[dict[str, Any]], width: int, height: int, fps: int,
        subs_path: str, normalize: bool,
    ) -> str:
        parts: list[str] = []
        for i, clip in enumerate(clips):
            start = _sec(clip["source_start_ms"])
            end = _sec(clip["source_end_ms"])
            parts.append(
                f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,"
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v{i}]"
            )
            parts.append(
                f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{i}]"
            )
        concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(len(clips)))
        parts.append(f"{concat_inputs}concat=n={len(clips)}:v=1:a=1[vc][ac]")

        # Burn subtitles. Escape the filter path (': ' and '\\' are special).
        subs_escaped = subs_path.replace("\\", "/").replace(":", "\\:")
        parts.append(f"[vc]subtitles='{subs_escaped}'[vout]")
        if normalize:
            parts.append("[ac]loudnorm[ao]")
        return ";".join(parts)
