"""The shell's ``_PanelBridge``, rendered for real: the Analysis stage and the
shared map trade places in an order the frontend can act on.

``gui/solara_app.py`` cannot be imported here (it initialises Earth Engine and
the tile transport at import), so the component's own source is compiled on its
own, against the names it uses. The stage, the shared map and the panel
wrappers are stand-ins that record what the bridge does to them.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
import reacton
import solara

from gui.store.state_manager import AppState
from gui.store.workflow_steps import STEPS, step_index

ROOT = Path(__file__).resolve().parents[1]


def _shell_component(name):
    """Compile ``name`` from gui/solara_app.py with the names it reads."""
    from pysepal.i18n import current_locale

    from gui.i18n import t
    from gui.store.workflow_steps import RAIL_ITEMS, rail_item_for_step

    tree = ast.parse((ROOT / "gui" / "solara_app.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = {"solara": solara, "STEPS": STEPS, "RAIL_ITEMS": RAIL_ITEMS,
          "rail_item_for_step": rail_item_for_step, "t": t, "current_locale": current_locale}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "gui/solara_app.py", "exec"), ns)
    return ns[name]


class _Stage:
    """StageHost stand-in: records every ``visible`` write, in order."""

    def __init__(self, ops):
        self._ops, self._visible = ops, False

    @property
    def visible(self):
        return self._visible

    @visible.setter
    def visible(self, value):
        self._visible = bool(value)
        self._ops.append(("stage", "show" if value else "hide"))


class _Map:
    """Shared-map stand-in: records the hiding class going on and off."""

    def __init__(self, ops):
        self._ops, self.classes = ops, set()

    def add_class(self, name):
        self.classes.add(name)
        self._ops.append(("map", f"+{name}"))

    def remove_class(self, name):
        self.classes.discard(name)
        self._ops.append(("map", f"-{name}"))


@pytest.fixture
def bridge():
    ops = []
    state, stage, map_ = AppState(), _Stage(ops), _Map(ops)
    wrappers = {s.key: SimpleNamespace(style_="") for s in STEPS}
    wrappers["overview"] = SimpleNamespace(style_="display: none;")
    component = _shell_component("_PanelBridge")
    _, rc = reacton.render(
        component(app_state=state, panel_wrappers=wrappers, header_wrapper=SimpleNamespace(style_=""),
                  stage_host=stage, map_=map_, mapapp_ref={}, workflow_config={}),
        handle_error=False,
    )
    yield SimpleNamespace(state=state, stage=stage, map=map_, ops=ops)
    rc.close()


ANALYSIS = step_index("analysis")


def _enter_analysis(b):
    b.state.panel_view.set("workflow")
    b.state.active_step.set(ANALYSIS)
    assert b.stage.visible and "defo-map-hidden" in b.map.classes
    del b.ops[:]


def test_entering_analysis_shows_the_stage_before_hiding_the_shared_map(bridge):
    del bridge.ops[:]
    bridge.state.active_step.set(ANALYSIS)
    assert bridge.ops == [("stage", "show"), ("map", "+defo-map-hidden")]


@pytest.mark.parametrize(
    "leave",
    [
        pytest.param(lambda s: s.active_step.set(step_index("aoi")), id="to-AOI"),
        pytest.param(lambda s: s.panel_view.set("overview"), id="to-Overview"),
    ],
)
def test_leaving_analysis_shows_the_shared_map_before_hiding_the_stage(bridge, leave):
    """The stage's hide nudge (a window resize) re-measures the shared map, so
    the map must be displayed by the time the stage hides: the frontend applies
    the two messages in this order. The other way round, leaflet kept the 0x0
    size it measured while the map was hidden and filled only its top-left part."""
    for _ in range(2):  # the round trip repeats the same way
        _enter_analysis(bridge)
        leave(bridge.state)
        assert bridge.ops == [("map", "-defo-map-hidden"), ("stage", "hide")]
        assert not bridge.stage.visible and "defo-map-hidden" not in bridge.map.classes


def test_a_language_switch_on_overview_keeps_the_overview_title():
    """MapApp copies the (re-translated) Workflow config onto the right panel
    when the app re-renders for a new language; the bridge must put the
    showing view's title and icon back afterwards."""
    from pysepal.i18n import set_locale

    from gui.i18n import t
    from gui.store.workflow_steps import RAIL_ITEMS

    overview = next(r for r in RAIL_ITEMS if r.key == "overview")
    panel = SimpleNamespace(config={})
    state = AppState()
    workflow_config = {"title": "Workflow", "icon": "mdi-tune", "width": 400}
    component = _shell_component("_PanelBridge")
    set_locale("en")
    _, rc = reacton.render(
        component(app_state=state, panel_wrappers={s.key: SimpleNamespace(style_="") for s in STEPS},
                  header_wrapper=SimpleNamespace(style_=""), stage_host=_Stage([]), map_=_Map([]),
                  mapapp_ref={"widget": SimpleNamespace(right_panel=[panel])},
                  workflow_config=workflow_config),
        handle_error=False,
    )
    try:
        state.panel_view.set("overview")
        assert panel.config["title"] == t(overview.label_key)
        panel.config = {**workflow_config}  # MapApp's prop sync on the language re-render
        set_locale("fr")
        assert panel.config == {**workflow_config, "title": t(overview.label_key), "icon": overview.icon}
    finally:
        set_locale("en")
        rc.close()
