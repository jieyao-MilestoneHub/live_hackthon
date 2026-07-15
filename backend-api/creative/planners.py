"""雙軌分流的「分流點」：CreativePlanner Port（產 subtitle.v1 / effects.v1 計畫）。

拼接（compose）後兩條創意路線各建一個 render，**共用同一個 FFmpeg encoder**；唯一差異在
「怎麼產 subtitle/effects 計畫」——抽成可替換、可被其他 worktree 覆寫的 ``CreativePlanner``：

  * ``PipelinePlanner``（route="pipeline"）：規則式管線（Part 1，已成）。
  * ``AgentPlanner``（route="agent"）：**佔位、fail-open**，目前委派 pipeline 規劃器。真正的
    Bedrock agent 由另一 worktree 實作，於其模組 ``register_planner("agent", RealAgentPlanner())``
    覆寫本佔位——不需改本檔/fork/契約/下載（OCP/DIP）。因兩路 render_id 不同 → effect_seed 不同，
    即使佔位委派 pipeline，兩份成品仍自然相異。

render_spec 與三份計畫落地、render 狀態機推進由 ``workers/creative_worker.py`` 統一處理
（route-agnostic）；本檔只負責「創意內容」。純函式風格、可離線測。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from creative.effects import plan_effects as _plan_effects
from creative.subtitle import plan_subtitles as _plan_subtitles

DUAL_TRACK_ROUTES: tuple[str, ...] = ("pipeline", "agent")
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


class AgentPlanner:
    """AI agent 路線（佔位）。fail-open：目前委派 ``fallback``（pipeline）規劃器。

    TODO(agent worktree): 以 Bedrock agent 產 subtitle/effects 計畫取代本體，並於該 worktree
    的模組 import 時 ``register_planner("agent", RealAgentPlanner())`` 覆寫此佔位。

    注意：實際啟用「agent 路線自動產出」需**兩件事同時成立**——(1) 上述 register_planner 注入真
    agent、(2) 部署設 ``DUAL_TRACK=on``（見 ``workers.lambda_handlers._dual_track_routes``，預設
    off）。在此之前 main 只跑 pipeline，本佔位不會被自動觸發產出誤導性成品。
    """

    route = "agent"

    def __init__(self, fallback: CreativePlanner | None = None) -> None:
        self._fallback = fallback or PipelinePlanner()

    def plan_subtitle(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._fallback.plan_subtitle(*args, **kwargs)

    def plan_effects(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._fallback.plan_effects(*args, **kwargs)


_PLANNERS: dict[str, CreativePlanner] = {}


def register_planner(route: str, planner: CreativePlanner) -> None:
    """登記/覆寫某 route 的規劃器（agent worktree 用此注入真正的 agent）。"""
    _PLANNERS[route] = planner


def get_creative_planner(route: str | None) -> CreativePlanner:
    """依 route 取規劃器（未知/None → pipeline 預設）。"""
    return _PLANNERS.get(route or DEFAULT_ROUTE) or _PLANNERS[DEFAULT_ROUTE]


register_planner("pipeline", PipelinePlanner())
register_planner("agent", AgentPlanner())
