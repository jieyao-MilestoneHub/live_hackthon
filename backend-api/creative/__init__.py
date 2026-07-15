"""Creative Planning: timeline.v1 -> subtitle.v1 + effects.v1 + render_spec.v1."""
from creative.effects import RANGED_EFFECT_TYPES, plan_effects
from creative.effects_registry import (
    EFFECT_REGISTRY,
    EffectContext,
    EffectStrategy,
    get_effect,
    point_types,
    ranged_types,
    register,
)
from creative.planners import (
    DUAL_TRACK_ROUTES,
    AgentPlanner,
    CreativePlanner,
    PipelinePlanner,
    get_creative_planner,
    register_planner,
)
from creative.render_spec import RESOLUTION_BY_ASPECT, build_render_spec
from creative.style import (
    CAPTION_STYLE,
    DEFAULT_KEYWORD_ANIMATION,
    KEYWORD_STYLE,
    SubtitleStyle,
    resolve_animation,
    resolve_styles,
    style_from_dict,
    style_to_dict,
)
from creative.subtitle import plan_subtitles

__all__ = [
    "plan_subtitles",
    "plan_effects",
    "RANGED_EFFECT_TYPES",
    "build_render_spec",
    "RESOLUTION_BY_ASPECT",
    # dual-track creative planners
    "CreativePlanner",
    "PipelinePlanner",
    "AgentPlanner",
    "get_creative_planner",
    "register_planner",
    "DUAL_TRACK_ROUTES",
    # effects registry
    "EFFECT_REGISTRY",
    "EffectStrategy",
    "EffectContext",
    "register",
    "get_effect",
    "ranged_types",
    "point_types",
    # subtitle style
    "SubtitleStyle",
    "CAPTION_STYLE",
    "KEYWORD_STYLE",
    "DEFAULT_KEYWORD_ANIMATION",
    "resolve_styles",
    "resolve_animation",
    "style_to_dict",
    "style_from_dict",
]
