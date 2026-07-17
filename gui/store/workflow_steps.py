"""Single source of truth for the app's drawer steps (workflow registry).

Order, ids, page-view names, drawer labels, icons, display types, right-panel
actions, gates and per-step output counts all derive from ``STEPS`` — the
shell (``gui.solara_app`` ``steps_data``/``panel_wrappers``), the navigation
store (``gui.store.navigation.PAGE_VIEW_TO_STEP``) and tests must never
hand-maintain step numbers again.

Semantics are outputs, not completion: steps are revisitable workspaces. A
step is LOCKED (upstream requirement missing), EMPTY (unlocked, nothing
created yet) or HAS_OUTPUTS (holds at least one artifact). "Current" is view
state owned by the shell, not a status.

Solara-free: gate/count callables take plain values (``alerts_gdf`` — the
loaded alert store GeoDataFrame or ``None`` — and ``aoi_result`` — truthy when
an AOI/date selection exists), never reactives, so everything here is
unit-testable without a render harness.

The machinery (``StepStatus``/``StepSpec``/``step_status``/``step_states``/
``nav_targets``) is copied from the spatial_risk reference scaffold; only the
``STEPS`` list and the shell-layout fields on ``StepSpec`` are per-app.
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
    step_id: int  # MapApp drawer step id (drawer order; 1-based)
    page_view: str  # the app_state.current_page_view signal string
    label_key: str  # dotted i18n key for the drawer name (gui.i18n.t)
    icon: str
    display: str  # "dialog" | "panel" | "step" (MapApp steps_data display)
    panel_action: str  # right_panel_action fed to MapApp ("open"/"close")
    unlocked: Callable[[Any, Any], bool] = field(default=lambda gdf, aoi: True)
    lock_reason_key: Optional[str] = None
    count: Callable[[Any, Any], Union[int, str, None]] = field(
        default=lambda gdf, aoi: None
    )
    count_key_one: Optional[str] = None
    count_key_other: Optional[str] = None
    dialog_width: Optional[int] = None  # only for display == "dialog"
    owns_map: bool = False  # right-panel tile owning layers on the shared map
    hides_shared_map: bool = False  # full-stage step that hides the shared map


def _alert_count(gdf, aoi) -> int:
    return 0 if gdf is None else len(gdf)


# Drawer order matches the ORIGINAL Voila app (ui.ipynb): About, Recipes,
# Optional files (Aux), AOI/Date, Filter, Overview, Analysis. ids are
# sequential so they read top-to-bottom.
#
# display types under the single-map model:
#   "dialog" → modal popup over the (dimmed) main map. Config/IO forms only.
#   "panel"  → right-panel card over the visible+interactive map.
#   "step"   → opaque full panel (Analysis two-map workspace).
#
# "filter_alerts" (not "filter_tile") is intentional: it is the exact string
# the AOI tile sets as its navigation signal.
STEPS: List[StepSpec] = [
    StepSpec(
        key="about",
        step_id=1,
        page_view="about_tile",
        label_key="app.drawer_item.about",
        icon="mdi-help-circle",
        display="dialog",
        panel_action="close",
        dialog_width=1300,
    ),
    StepSpec(
        key="recipes",
        step_id=2,
        page_view="recipe_tile",
        label_key="app.drawer_item.recipes",
        icon="mdi-folder-open",
        display="dialog",
        panel_action="close",
        dialog_width=900,
    ),
    StepSpec(
        key="aux",
        step_id=3,
        page_view="aux_tile",
        label_key="app.drawer_item.aux",
        icon="mdi-layers-plus",
        display="dialog",
        panel_action="close",
        dialog_width=900,
    ),
    StepSpec(
        key="aoi",
        step_id=4,
        page_view="aoi_tile",
        label_key="app.drawer_item.aoi",
        icon="mdi-map-marker",
        display="panel",
        panel_action="open",
        owns_map=True,
    ),
    StepSpec(
        key="filter",
        step_id=5,
        page_view="filter_alerts",
        label_key="app.drawer_item.filter",
        icon="mdi-filter",
        display="panel",
        panel_action="open",
        unlocked=lambda gdf, aoi: bool(aoi),
        lock_reason_key="workflow.lock_filter",
        owns_map=True,
    ),
    StepSpec(
        key="overview",
        step_id=6,
        page_view="overview_tile",
        # mdi-chart-box-outline is absent from the bundled MDI font version
        # (it rendered as a blank gap next to "Overview"); mdi-chart-bar is a
        # core glyph present in every MDI release, so the symbol shows.
        icon="mdi-chart-bar",
        label_key="app.drawer_item.overview",
        display="panel",
        panel_action="open",
        unlocked=lambda gdf, aoi: gdf is not None,
        lock_reason_key="workflow.lock_overview",
        count=_alert_count,
        count_key_one="workflow.count_alerts_one",
        count_key_other="workflow.count_alerts_other",
        owns_map=True,
    ),
    StepSpec(
        key="analysis",
        step_id=7,
        page_view="analysis_tile",
        label_key="app.drawer_item.analysis",
        icon="mdi-image-search",
        display="step",
        panel_action="open",
        unlocked=lambda gdf, aoi: gdf is not None,
        lock_reason_key="workflow.lock_analysis",
        count=_alert_count,
        count_key_one="workflow.count_alerts_one",
        count_key_other="workflow.count_alerts_other",
        hides_shared_map=True,
    ),
]


def step_status(spec: StepSpec, alerts_gdf, aoi_result) -> StepStatus:
    if not spec.unlocked(alerts_gdf, aoi_result):
        return StepStatus.LOCKED
    c = spec.count(alerts_gdf, aoi_result)
    has = bool(c) if isinstance(c, str) else bool(c or 0)
    return StepStatus.HAS_OUTPUTS if has else StepStatus.EMPTY


def step_states(alerts_gdf, aoi_result) -> List[StepStatus]:
    return [step_status(s, alerts_gdf, aoi_result) for s in STEPS]


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
