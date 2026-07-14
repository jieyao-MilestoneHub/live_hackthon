"""渲染規格書：timeline.v1 (+ 計畫 keys) → render_spec.v1（Creative Planning 第四步）。

純函式:彙整 source + timeline_version + 三份計畫的 S3 key + 輸出 key + 比例/解析度/
音訊/編碼參數,讓 FFmpeg Heavy Worker(M4)一次完成(demand.md §十三)。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from analysis.validate import validate_render_spec

# aspect ratio -> (width, height)
RESOLUTION_BY_ASPECT = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
}
_DEFAULT_ASPECT = "9:16"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_render_spec(
    project: dict[str, Any],
    timeline: dict[str, Any],
    render_id: str,
    effect_seed: int,
    inputs: dict[str, str],
    outputs: dict[str, str],
    created_at: str | None = None,
) -> dict[str, Any]:
    """回傳符合 render_spec.v1 的 dict。

    ``inputs`` 需含 timeline_key/subtitle_key/effect_plan_key;
    ``outputs`` 需含 video_key/preview_key/thumbnail_key。
    """
    aspect = timeline.get("aspect_ratio") or _DEFAULT_ASPECT
    width, height = RESOLUTION_BY_ASPECT.get(aspect, RESOLUTION_BY_ASPECT[_DEFAULT_ASPECT])

    source: dict[str, Any] = {"bucket": project["source_bucket"], "key": project["source_key"]}
    if project.get("source_version_id"):
        source["version_id"] = project["source_version_id"]

    spec = {
        "schema_version": "render_spec.v1",
        "project_id": project["project_id"],
        "render_id": render_id,
        "timeline_version": int(timeline["version"]),
        "effect_seed": int(effect_seed),
        "aspect_ratio": aspect,
        "resolution": {"width": width, "height": height},
        "source": source,
        "inputs": {
            "timeline_key": inputs["timeline_key"],
            "subtitle_key": inputs["subtitle_key"],
            "effect_plan_key": inputs["effect_plan_key"],
        },
        "outputs": {
            "video_key": outputs["video_key"],
            "preview_key": outputs["preview_key"],
            "thumbnail_key": outputs["thumbnail_key"],
        },
        "audio": {"normalize": True, "target_lufs": -14},
        "encode": {"video_codec": "h264", "audio_codec": "aac", "crf": 20, "fps": 30},
        "created_at": created_at or _now_iso(),
    }
    validate_render_spec(spec)
    return spec
