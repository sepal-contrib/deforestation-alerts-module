"""The shell's ``_PanelBridge``, rendered for real: the active step's wrapper
shows, the panel keeps its Workflow title, and the ``current_page_view``
mirror follows the step on the workflow screen only.

The panel itself has one view -- the PipelineHeader over the active step's
controls -- because the three rail items (About, Project, the Overview
dashboard) are all MapApp dialogs. The analysis screen is not the panel's:
``_ScreenBridge`` shows it (tests/test_analysis_screen.py).

``gui/solara_app.py`` cannot be imported here (it initialises Earth Engine and
the tile transport at import), so the component's own source is compiled on its
own, against the names it uses.
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

    tree = ast.parse((ROOT / "gui" / "solara_app.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = {"solara": solara, "STEPS": STEPS, "t": t, "current_locale": current_locale}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "gui/solara_app.py", "exec"), ns)
    return ns[name]


@pytest.fixture
def bridge():
    state = AppState()
    wrappers = {s.key: SimpleNamespace(style_="") for s in STEPS}
    component = _shell_component("_PanelBridge")
    _, rc = reacton.render(
        component(app_state=state, panel_wrappers=wrappers, mapapp_ref={}, workflow_config={}),
        handle_error=False,
    )
    yield SimpleNamespace(state=state, wrappers=wrappers)
    rc.close()


def test_the_active_steps_wrapper_shows_and_the_mirror_follows(bridge):
    bridge.state.active_step.set(step_index("integrate"))
    assert [k for k, w in bridge.wrappers.items() if w.style_ == ""] == ["integrate"]
    assert bridge.state.current_page_view.value == "integrate_tile"


def test_the_mirror_leaves_the_analysis_screen_alone(bridge):
    """On the analysis screen ``current_page_view`` is "analysis_tile". A
    write of the step's view there (a step change, or a language switch,
    which re-runs the bridge) would make ``_NavBridge`` send the reviewer
    back to the workflow."""
    bridge.state.screen.set("analysis")
    bridge.state.current_page_view.set("analysis_tile")
    bridge.state.active_step.set(step_index("integrate"))
    assert bridge.state.current_page_view.value == "analysis_tile"
    # Back on the workflow (``_ScreenBridge`` mirrors the step back itself),
    # a step change is mirrored again.
    bridge.state.screen.set("workflow")
    bridge.state.active_step.set(step_index("questionnaire"))
    assert bridge.state.current_page_view.value == "questionnaire_tile"


def test_a_language_switch_puts_the_workflow_title_back():
    """MapApp copies the (re-translated) Workflow config onto the right panel
    when the app re-renders for a new language; the bridge must re-apply it
    afterwards, and the panel's title no longer follows navigation -- the rail
    items are dialogs, which leave the panel as the user left it."""
    from pysepal.i18n import set_locale

    panel = SimpleNamespace(config={})
    state = AppState()
    workflow_config = {"title": "Workflow", "icon": "mdi-tune", "width": 400}
    component = _shell_component("_PanelBridge")
    set_locale("en")
    _, rc = reacton.render(
        component(app_state=state, panel_wrappers={s.key: SimpleNamespace(style_="") for s in STEPS},
                  mapapp_ref={"widget": SimpleNamespace(right_panel=[panel])},
                  workflow_config=workflow_config),
        handle_error=False,
    )
    try:
        assert panel.config == {**workflow_config}
        state.active_step.set(step_index("questionnaire"))  # navigation leaves the title alone
        assert panel.config == {**workflow_config}
        panel.config = {"title": "stale"}  # MapApp's prop sync on the language re-render
        set_locale("fr")
        assert panel.config == {**workflow_config}
    finally:
        set_locale("en")
        rc.close()
