"""融合打分權重與門檻（Speaker Attribution）。

純常數 + 純函式、無 IO、無隨機。權重以「可得訊號」重新正規化——face-only 在高
相似度時仍可達 confirmed；缺 ASD/視覺訊號時不因補 0 而被系統性低估。調權重／門檻
只改此檔，不動 ``fusion`` 演算法（OCP）。
"""
from __future__ import annotations

# 各證據訊號權重（皆作用於 [0,1] 的訊號值）
_W_FACE = 0.45          # 臉部相似度（Rekognition FaceSearch Similarity）
_W_LIPSYNC = 0.30       # 嘴型—音訊同步（Active Speaker Detection）
_W_VISIBLE = 0.10       # 臉部可見比例
_W_CONSISTENCY = 0.15   # 聲音群組 ↔ 人物歷史一致性

CONFIRM_THRESHOLD = 0.85
REVIEW_THRESHOLD = 0.60

# 畫外音延續（speaker_cluster_propagation）信心衰減係數
PROPAGATION_DECAY = 0.85

# 「重疊說話」判定：同一時間窗內、重疊比例與相似度雙雙達標的相異人物臉部 >= 2
OVERLAP_SIMILARITY = 0.80
OVERLAP_MIN_OVERLAP_RATIO = 0.5


def weighted_score(
    face: float | None,
    lip_sync: float | None,
    visible: float | None,
    consistency: float | None,
) -> float:
    """對『可得』訊號加權平均並正規化到 [0,1]；全缺回 0.0。

    以「有值才計權重」的方式正規化，避免缺席訊號被當 0 拖低分數。
    """
    pairs = (
        (_W_FACE, face),
        (_W_LIPSYNC, lip_sync),
        (_W_VISIBLE, visible),
        (_W_CONSISTENCY, consistency),
    )
    num = sum(w * s for w, s in pairs if s is not None)
    den = sum(w for w, s in pairs if s is not None)
    return (num / den) if den > 0 else 0.0


def classify_status(
    score: float,
    confirm_threshold: float = CONFIRM_THRESHOLD,
    review_threshold: float = REVIEW_THRESHOLD,
) -> str:
    """由分數落點回傳 confirmed / needs_review / unknown。"""
    if score >= confirm_threshold:
        return "confirmed"
    if score >= review_threshold:
        return "needs_review"
    return "unknown"
