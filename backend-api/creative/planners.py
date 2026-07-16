"""雙軌分流的「分流點」：CreativePlanner Port（產 subtitle.v1 / effects.v1 計畫）。

兩條創意路線各建一個 render、**共用同一個 render SFN → FFmpeg Batch encoder**，差異只在
「怎麼產 subtitle/effects 計畫」：

  * ``PipelinePlanner``（route="pipeline"）：規則式管線；由 render 工作流的 PlanCreative
    步驟（``creative_worker.run``）呼叫本 registry 產計畫。
  * ``edit`` 路線（route="edit"）：AI 自然語言剪接。它**不走本 registry**——由
    ``app.edit_planning.plan_edit_render`` 事先用 NL 剪接 planner（EDIT_PLANNER_LLM=1 時走
    Claude on Bedrock，否則確定性 Stub）把計畫寫好，render 工作流對它略過 PlanCreative。

因此本 registry 現在只登記 pipeline；``get_creative_planner`` 對未知 route（含 "edit"）退回
pipeline，但 edit 路線因已預先規劃，實際不會用到它。純函式風格、可離線測。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from creative.effects import plan_effects as _plan_effects
from creative.subtitle import plan_subtitles as _plan_subtitles

# The two creative routes that each produce an artifact (auto dual-track). Both
# render through the render SFN → Batch; see app.edit_planning.kickoff_dual_track.
DUAL_TRACK_ROUTES: tuple[str, ...] = ("pipeline", "edit")
DEFAULT_ROUTE = "pipeline"


@runtime_checkable
class CreativePlanner(Protocol):
    route: str

    def plan_subtitle(
        self,
        timeline: dict[str, Any],
        highlights: list[dict[str, Any]],
        project_id: str,
        render_id: str,
        *,
        annotations: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """回傳 subtitle.v1。"""
        ...

    def plan_effects(
        self,
        timeline: dict[str, Any],
        effect_seed: int,
        project_id: str,
        render_id: str,
        *,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """回傳 effects.v1。"""
        ...


class PipelinePlanner:
    """規則式管線路線（Part 1）。"""

    route = "pipeline"

    def plan_subtitle(self, timeline, highlights, project_id, render_id, *, annotations=None, settings=None):
        return _plan_subtitles(
            timeline, highlights, project_id, render_id, annotations=annotations, settings=settings
        )

    def plan_effects(self, timeline, effect_seed, project_id, render_id, *, settings=None):
        return _plan_effects(timeline, effect_seed, project_id, render_id, settings=settings)


_PLANNERS: dict[str, CreativePlanner] = {}


def register_planner(route: str, planner: CreativePlanner) -> None:
    """登記/覆寫某 route 的 CreativePlanner。"""
    _PLANNERS[route] = planner


def get_creative_planner(route: str | None) -> CreativePlanner:
    """依 route 取規劃器（未知/None/"edit" → pipeline 預設；edit 路線已預先規劃故不會用到）。"""
    return _PLANNERS.get(route or DEFAULT_ROUTE) or _PLANNERS[DEFAULT_ROUTE]


register_planner("pipeline", PipelinePlanner())
