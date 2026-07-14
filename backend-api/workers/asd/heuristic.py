"""Active Speaker Detection — 啟發式純函式（無 AWS/IO、可離線單測）。

提供兩層：
  1. 真模型接縫用的幾何/打分工具（``mouth_open_ratio`` / ``iou`` / ``lip_sync_score``），
     未來以 Rekognition ``StartFaceDetection`` landmarks + 音訊 VAD 計算真實嘴型同步。
  2. MVP 代理 ``estimate_asd_segments``：在缺少逐幀 landmarks 時，用臉部出現區間與說話段
     的重疊×相似度當作「當下發言」的近似訊號。清楚標示為 placeholder。
"""
from __future__ import annotations

import math
from typing import Any


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def mouth_open_ratio(landmarks: dict[str, tuple[float, float]]) -> float:
    """嘴部開合比 = |mouthUp−mouthDown| / 兩眼間距（臉部尺度正規化）。

    landmarks：Rekognition ``Landmarks`` 的 ``{Type: (X, Y)}``（X/Y 為 0–1 影像比例）。
    缺點時回 0.0。
    """
    need = ("mouthUp", "mouthDown", "eyeLeft", "eyeRight")
    if not all(k in landmarks for k in need):
        return 0.0
    mouth = _dist(landmarks["mouthUp"], landmarks["mouthDown"])
    scale = _dist(landmarks["eyeLeft"], landmarks["eyeRight"])
    if scale <= 0:
        return 0.0
    return max(0.0, min(1.0, mouth / scale))


def iou(a: dict[str, float], b: dict[str, float]) -> float:
    """兩個 Rekognition BoundingBox（Left/Top/Width/Height，0–1）的 IoU。"""
    ax0, ay0, ax1, ay1 = a["Left"], a["Top"], a["Left"] + a["Width"], a["Top"] + a["Height"]
    bx0, by0, bx1, by1 = b["Left"], b["Top"], b["Left"] + b["Width"], b["Top"] + b["Height"]
    ix0, iy0, ix1, iy1 = max(ax0, bx0), max(ay0, by0), min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    union = a["Width"] * a["Height"] + b["Width"] * b["Height"] - inter
    return inter / union if union > 0 else 0.0


def lip_sync_score(mouth_series: list[float], audio_series: list[float]) -> float:
    """嘴部開合序列與音訊能量序列的正相關程度（0–1）。

    真模型的核心訊號：發聲時嘴應在動。長度不齊時取較短者對齊。
    """
    n = min(len(mouth_series), len(audio_series))
    if n < 2:
        return 0.0
    m, a = mouth_series[:n], audio_series[:n]
    mm, ma = sum(m) / n, sum(a) / n
    cov = sum((m[i] - mm) * (a[i] - ma) for i in range(n))
    vm = math.sqrt(sum((m[i] - mm) ** 2 for i in range(n)))
    va = math.sqrt(sum((a[i] - ma) ** 2 for i in range(n)))
    if vm == 0 or va == 0:
        return 0.0
    corr = cov / (vm * va)
    return max(0.0, min(1.0, corr))  # 只取正相關


def _overlap_ms(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def estimate_asd_segments(
    transcript: dict[str, Any],
    face_appearances: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """MVP 代理：以臉部出現區間對說話段的重疊×相似度近似「當下發言」。

    ⚠️ Placeholder：缺逐幀 landmarks/音訊時的近似；真模型應改用 ``lip_sync_score``。
    輸出 asd_result.v1 的 ``segments``。
    """
    segments: list[dict[str, Any]] = []
    for seg in transcript.get("segments", []):
        s0, s1 = int(seg["start_ms"]), int(seg["end_ms"])
        dur = max(1, s1 - s0)
        best = None
        best_key = -1.0
        for fa in face_appearances:
            ov = _overlap_ms(s0, s1, int(fa["start_ms"]), int(fa["end_ms"]))
            if ov <= 0:
                continue
            ratio = ov / dur
            sim = float(fa.get("similarity") or 0.0)
            key = ratio * sim
            if key > best_key:
                best_key = key
                best = {"fa": fa, "ratio": ratio, "sim": sim}
        if best:
            fa = best["fa"]
            segments.append({
                "start_ms": s0, "end_ms": s1,
                "speaker_cluster_id": seg.get("speaker") or "unknown",
                "active_face_track_id": fa.get("face_track_id"),
                "person_id": fa.get("person_id"),
                "lip_sync_confidence": round(best["ratio"] * best["sim"], 3),
                "visible_ratio": round(best["ratio"], 3),
                "evidence": {"proxy": "face_overlap_similarity"},
            })
        else:
            segments.append({
                "start_ms": s0, "end_ms": s1,
                "speaker_cluster_id": seg.get("speaker") or "unknown",
                "active_face_track_id": None, "person_id": None,
                "lip_sync_confidence": 0.1, "visible_ratio": 0.0,
                "evidence": {"proxy": "no_face"},
            })
    return segments
