import ast
from pathlib import Path

import reacton
import solara

from gui.scripts import legend_specs
from gui.scripts.map_layers import MapLayers
from gui.store.state_manager import AppState
from gui.store.workflow_steps import step_index

ROOT = Path(__file__).resolve().parents[1]


def _compiled(*names):
    """The named functions of gui/solara_app.py (which initialises Earth Engine at import)."""
    from pysepal.solara.components.legend import LegendComponent

    from gui.i18n import t
    from gui.store.workflow_steps import STEPS

    tree = ast.parse((ROOT / "gui" / "solara_app.py").read_text())
    ns = {"solara": solara, "t": t, "STEPS": STEPS, "LegendComponent": LegendComponent}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "gui/solara_app.py", "exec"), ns)
    return ns


def _state_with_two_legends():
    state = AppState()
    layers = MapLayers(_FakeMap(), legends=state.layer_legends, selected=state.selected_legend)
    import gui.scripts.map_layers as ml
    ml_spawn = ml.spawn_in_context
    ml.spawn_in_context = lambda target, args=(), **kw: target(*args)
    try:
        for k in ("src:GLAD-L", "src:RADD"):
            layers.show(k, lambda: None, legend=legend_specs.alert_legend(k, k, legend_specs.preview_title(k, "a", "b")))
    finally:
        ml.spawn_in_context = ml_spawn
    return state, layers


class _FakeMap:
    def add_layer(self, layer, key=""):
        pass

    def remove_layer(self, key, none_ok=False):
        pass


def test_legend_props_follow_the_registry_and_hide_on_analysis():
    ns = _compiled("legend_props")
    state, _ = _state_with_two_legends()
    props = ns["legend_props"](state, lambda key, **kw: f"<{key}>")
    assert props["selected"] == "src:RADD" and [o["value"] for o in props["selector_options"]] == ["src:GLAD-L",
                                                                                                    "src:RADD"]
    assert props["visible"]
    state.active_step.set(step_index("analysis"))
    assert not ns["legend_props"](state, lambda key, **kw: key)["visible"]
    state.panel_view.set("overview")
    assert ns["legend_props"](state, lambda key, **kw: key)["visible"]


def test_map_legend_renders_pysepal_legend_component():
    ns = _compiled("legend_props", "MapLegend")
    state, layers = _state_with_two_legends()
    box, rc = reacton.render(ns["MapLegend"](app_state=state, layers=layers))
    try:
        def find(w):
            if hasattr(w, "trait_names") and "legend_data" in w.trait_names():
                return w
            for c in list(getattr(w, "children", []) or []):
                found = find(c)
                if found is not None:
                    return found
        widget = find(box)
        assert widget is not None and widget.selected == "src:RADD" and widget.visible
    finally:
        rc.close()
