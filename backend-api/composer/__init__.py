"""Composer: highlights.v1 (+annotations.v1) -> timeline.v1 (duration-optimized cut decision list)."""
from composer.strategies import (
    MAX_DURATION_MS,
    MIN_CLIP_MS,
    ClipPlanner,
    NarrativeBeatPlanner,
    ScoreGreedyPlanner,
    SelectedClip,
    default_planner,
)
from composer.timeline import DEFAULT_ASPECT_RATIO, compose_timeline
from composer.transitions import (
    JOIN_FADE_MS,
    JoinStrategy,
    beat_boundaries,
    get_join_strategy,
    snap_cut_points,
)

__all__ = [
    "compose_timeline",
    "MAX_DURATION_MS",
    "MIN_CLIP_MS",
    "DEFAULT_ASPECT_RATIO",
    "ClipPlanner",
    "ScoreGreedyPlanner",
    "NarrativeBeatPlanner",
    "SelectedClip",
    "default_planner",
    "JoinStrategy",
    "get_join_strategy",
    "snap_cut_points",
    "beat_boundaries",
    "JOIN_FADE_MS",
]
