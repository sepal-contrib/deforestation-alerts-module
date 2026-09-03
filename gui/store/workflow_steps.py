"""Single source of truth for the app's steps.

Two registries:

* ``STEPS`` — the three gated **workflow** steps shown in the right panel's
  Workflow view (AOI/Date → Filter → Analysis). Order, labels, gates, lock
  reasons and per-step output counts derive from it; ``PipelineHeader`` and
  the shell never hand-maintain step numbers.
* ``RAIL_ITEMS`` — the four entries on the left rail: About, Recipes and
  Optional files open as MapApp dialogs; Overview (``display="panel"``) opens
  its own view in the right panel — outside the workflow, keeping its summary
  table, exports and status clusters on the shared map.

Semantics are outputs, not completion: steps are revisitable workspaces. A
step is LOCKED (upstream requirement missing), EMPTY (unlocked, nothing
produced yet) or HAS_OUTPUTS. "Current" is view state owned by the shell
(``app_state.active_step`` / ``app_state.panel_view``), not a status.

Solara-free: gate/count callables take plain values — ``gdf`` (the loaded
alert store GeoDataFrame or ``None``) and ``aoi`` (the AOI name string when an
AOI is selected, else ``None``) — never reactives, so everything here is
unit-testable without a render harness.

The machinery (``StepStatus``/``StepSpec``/``step_status``/``step_states``/
``nav_targets``) is copied from the spatial_risk reference scaffold; only the
lists and the layout flags are per-app.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, List, Optional, Tuple, Union


class StepStatus(Enum):
    LOCKED = "locked"
    EMPTY = "empty"
    HAS_OUTPUTS = "has_outputs"


@dataclass(frozen=True)
class StepSpec:
    key: str
    page_view: str  # the app_state.current_page_view signal string tiles write
    label_key: str  # dotted i18n key (gui.i18n.t)
    icon: str
    unlocked: Callable[[Any, Any], bool] = field(default=lambda gdf, aoi: True)
    lock_reason_key: Optional[str] = None
    count: Callable[[Any, Any], Union[int, str, None]] = field(
        default=lambda gdf, aoi: None
    )
    count_key_one: Optional[str] = None
    count_key_other: Optional[str] = None
    owns_map: bool = False  # draws layers on the shared map
    full_stage: bool = False  # Analysis: stage overlay shown, shared map hidden


@dataclass(frozen=True)
class RailItem:
    key: str
    step_id: int  # MapApp drawer step id (1-based, rail order)
    page_view: str
    label_key: str
    icon: str
    display: str  # "dialog" (MapApp modal) | "panel" (a right-panel view)
    dialog_width: Optional[int] = None  # dialogs only
    owns_map: bool = False  # panel items that draw layers on the shared map


def _alert_count(gdf, aoi) -> int:
    return 0 if gdf is None else len(gdf)


def _reviewed_count(gdf, aoi) -> int:
    if gdf is None or len(gdf) == 0 or "status" not in gdf:
        return 0
    return int((gdf["status"] != "Not reviewed").sum())


# "filter_alerts" (not "filter_tile") is intentional: it is the exact string
# the AOI tile sets as its navigation signal.
STEPS: List[StepSpec] = [
    StepSpec(
        key="aoi",
        page_view="aoi_tile",
        label_key="app.drawer_item.aoi",
        icon="mdi-map-marker",
        count=lambda gdf, aoi: aoi,
        owns_map=True,
    ),
    StepSpec(
        key="filter",
        page_view="filter_alerts",
        label_key="app.drawer_item.filter",
        icon="mdi-filter",
        unlocked=lambda gdf, aoi: bool(aoi),
        lock_reason_key="workflow.lock_filter",
        count=_alert_count,
        count_key_one="workflow.count_alerts_one",
        count_key_other="workflow.count_alerts_other",
        owns_map=True,
    ),
    StepSpec(
        key="analysis",
        page_view="analysis_tile",
        label_key="app.drawer_item.analysis",
        icon="mdi-image-search",
        unlocked=lambda gdf, aoi: gdf is not None,
        lock_reason_key="workflow.lock_analysis",
        count=_reviewed_count,
        count_key_one="workflow.count_reviewed_one",
        count_key_other="workflow.count_reviewed_other",
        full_stage=True,
    ),
]

# Rail order mirrors spatial_risk (Project, Project Summary, Tools): About,
# Recipes, Overview, Optional files. mdi-chart-bar is a core MDI glyph
# (mdi-chart-box-outline is absent from the bundled font).
RAIL_ITEMS: List[RailItem] = [
    RailItem("about", 1, "about_tile", "app.drawer_item.about", "mdi-help-circle", "dialog", 1300),
    RailItem("recipes", 2, "recipe_tile", "app.drawer_item.recipes", "mdi-folder-open", "dialog", 900),
    RailItem("overview", 3, "overview_tile", "app.drawer_item.overview", "mdi-chart-bar", "panel", owns_map=True),
    RailItem("aux", 4, "aux_tile", "app.drawer_item.aux", "mdi-layers-plus", "dialog", 900),
]


def rail_item_for_step(step_id: int) -> Optional[RailItem]:
    return next((r for r in RAIL_ITEMS if r.step_id == step_id), None)


def step_index(key: str) -> int:
    return next(i for i, s in enumerate(STEPS) if s.key == key)


def step_status(spec: StepSpec, gdf, aoi) -> StepStatus:
    if not spec.unlocked(gdf, aoi):
        return StepStatus.LOCKED
    c = spec.count(gdf, aoi)
    has = bool(c) if isinstance(c, str) else bool(c or 0)
    return StepStatus.HAS_OUTPUTS if has else StepStatus.EMPTY


def step_states(gdf, aoi) -> List[StepStatus]:
    return [step_status(s, gdf, aoi) for s in STEPS]


def nav_targets(
    active: int, states: List[StepStatus]
) -> Tuple[Optional[int], Optional[int]]:
    """Nearest non-locked step on each side of ``active`` (Back/Next)."""
    prev_t = next(
        (i for i in range(active - 1, -1, -1) if states[i] is not StepStatus.LOCKED),
        None,
    )
    next_t = next(
        (
            i
            for i in range(active + 1, len(states))
            if states[i] is not StepStatus.LOCKED
        ),
        None,
    )
    return prev_t, next_t
