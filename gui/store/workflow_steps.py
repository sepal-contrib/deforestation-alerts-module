"""Single source of truth for the app's steps.

Two registries:

* ``STEPS`` — the gated workflow steps (AOI → Alerts → Optional layers →
  Period → Download & harmonize → Integrate & vectorize → Questionnaire →
  Analysis). Gates and counts read only the Project. Order, labels, gates, lock reasons and
  per-step output counts derive from it; ``PipelineHeader`` and the shell
  never hand-maintain step numbers.
* ``RAIL_ITEMS`` — the three entries on the left rail: About and Project open
  as MapApp dialogs; Overview (``display="panel"``) opens its own view in the
  right panel — outside the workflow, keeping its summary table, exports and
  status clusters on the shared map.

Semantics are outputs, not completion: steps are revisitable workspaces. A
step is LOCKED (upstream requirement missing), EMPTY (unlocked, nothing
produced yet) or HAS_OUTPUTS. "Current" is view state owned by the shell
(``app_state.active_step``), not a status.

Solara-free: gate/count callables take the Project (or ``None`` when no
project is open) — never reactives, so everything here is unit-testable
without a render harness.

The machinery (``StepStatus``/``StepSpec``/``step_status``/``step_states``/
``nav_targets``) is copied from the spatial_risk reference scaffold; only the
lists and the layout flags are per-app.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, List, Optional, Tuple, Union

from deforestation_alerts.engine.plan import ready_sources
from deforestation_alerts.project import paths


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
    unlocked: Callable[[Any], bool] = field(default=lambda p: True)  # (project)
    lock_reason_key: Optional[str] = None
    count: Callable[[Any], Union[int, str, None]] = field(default=lambda p: None)  # (project)
    count_key: Optional[str] = None  # a plural node: t(count_key, count=n)
    full_stage: bool = False  # Analysis: stage overlay shown, shared map hidden


@dataclass(frozen=True)
class RailItem:
    key: str
    step_id: int  # MapApp drawer step id (1-based, rail order)
    page_view: str
    label_key: str
    icon: str
    display: str  # MapApp's own vocabulary; every rail item is a "dialog"
    dialog_width: Optional[int] = None


def active_period(p):
    return p.periods.get(p.active_period) if p is not None and p.active_period else None


def active_set(p):
    return p.alert_sets.get(p.active_set) if p is not None and p.active_set else None


def _ready(p) -> list:
    period = active_period(p)
    if period is None or p.grid is None:
        return []
    return ready_sources(p, paths.project_dir(p.name), period)


STEPS: List[StepSpec] = [
    StepSpec(key="aoi", page_view="aoi_tile", label_key="app.drawer_item.aoi", icon="mdi-map-marker",
             count=lambda p: p.aoi.name if p is not None and p.aoi else None),
    StepSpec(key="alerts", page_view="alerts_tile", label_key="app.drawer_item.alerts", icon="mdi-alert-decagram",
             unlocked=lambda p: p is not None and p.aoi is not None, lock_reason_key="workflow.lock_alerts",
             count=lambda p: len(p.sources) if p is not None else 0,
             count_key="workflow.count_sources"),
    StepSpec(key="layers", page_view="layers_tile", label_key="app.drawer_item.layers", icon="mdi-layers-outline",
             unlocked=lambda p: p is not None and p.aoi is not None, lock_reason_key="workflow.lock_layers",
             count=lambda p: len(p.vis_layers) if p is not None else 0,
             count_key="workflow.count_layers"),
    StepSpec(key="period", page_view="period_tile", label_key="app.drawer_item.period", icon="mdi-calendar-range",
             unlocked=lambda p: p is not None and bool(p.sources), lock_reason_key="workflow.lock_period",
             count=lambda p: len(p.periods) if p is not None else 0,
             count_key="workflow.count_periods"),
    StepSpec(key="prepare", page_view="prepare_tile", label_key="app.drawer_item.prepare", icon="mdi-cloud-download",
             unlocked=lambda p: active_period(p) is not None, lock_reason_key="workflow.lock_prepare",
             count=lambda p: len(_ready(p)),
             count_key="workflow.count_ready"),
    StepSpec(key="integrate", page_view="integrate_tile", label_key="app.drawer_item.integrate",
             icon="mdi-vector-union",
             unlocked=lambda p: bool(_ready(p)), lock_reason_key="workflow.lock_integrate",
             count=lambda p: active_set(p).n_alerts if active_set(p) else 0,
             count_key="workflow.count_alerts"),
    StepSpec(key="questionnaire", page_view="questionnaire_tile", label_key="app.drawer_item.questionnaire",
             icon="mdi-clipboard-list-outline",
             unlocked=lambda p: p is not None, lock_reason_key="workflow.lock_alerts",
             count=lambda p: len(p.questions) if p is not None else 0,
             count_key="workflow.count_questions"),
    StepSpec(key="analysis", page_view="analysis_tile", label_key="app.drawer_item.analysis", icon="mdi-image-search",
             unlocked=lambda p: active_set(p) is not None, lock_reason_key="workflow.lock_analysis",
             count=lambda p: active_set(p).n_reviewed if active_set(p) else 0,
             count_key="workflow.count_reviewed",
             full_stage=True),
]

RAIL_ITEMS: List[RailItem] = [
    RailItem("about", 1, "about_tile", "app.drawer_item.about", "mdi-help-circle", "dialog", 1300),
    # spatial_risk's ProjectPanel width: the status block only. Manage
    # projects (480 px) and the name form (420 px) open over it.
    RailItem("project", 2, "project_tile", "app.drawer_item.project", "mdi-folder-open", "dialog", 560),
    # The Overview dashboard is the widest dialog: two columns of charts
    # (spec §9.5), the same 1300 px About uses.
    RailItem("overview", 3, "overview_tile", "app.drawer_item.overview", "mdi-chart-bar", "dialog", 1300),
]


def rail_item_for_step(step_id: int) -> Optional[RailItem]:
    return next((r for r in RAIL_ITEMS if r.step_id == step_id), None)


def step_index(key: str) -> int:
    return next(i for i, s in enumerate(STEPS) if s.key == key)


def step_status(spec: StepSpec, p) -> StepStatus:
    if not spec.unlocked(p):
        return StepStatus.LOCKED
    c = spec.count(p)
    has = bool(c) if isinstance(c, str) else bool(c or 0)
    return StepStatus.HAS_OUTPUTS if has else StepStatus.EMPTY


def step_states(p) -> List[StepStatus]:
    return [step_status(s, p) for s in STEPS]


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
