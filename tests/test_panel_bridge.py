"""The shell's ``_PanelBridge``, rendered for real: the active step's wrapper
shows and the ``current_page_view``
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
        component(app_state=state, panel_wrappers=wrappers),
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
    write of the step's view there (a step change) would make ``_NavBridge``
    send the reviewer back to the workflow."""
    bridge.state.screen.set("analysis")
    bridge.state.current_page_view.set("analysis_tile")
    bridge.state.active_step.set(step_index("integrate"))
    assert bridge.state.current_page_view.value == "analysis_tile"
    # Back on the workflow (``_ScreenBridge`` mirrors the step back itself),
    # a step change is mirrored again.
    bridge.state.screen.set("workflow")
    bridge.state.active_step.set(step_index("questionnaire"))
    assert bridge.state.current_page_view.value == "questionnaire_tile"


def test_the_panel_title_is_a_prop_not_a_widget_write():
    """The native MapApp renders ``right_panel_config`` on every shell render,
    so the re-translated title reaches the panel without the bridge (the
    legacy MapApp needed it written back onto its RightPanel widget)."""
    src = (ROOT / "gui" / "solara_app.py").read_text()
    assert "right_panel[0].config" not in src
    assert "right_panel_config=WORKFLOW_PANEL_CONFIG," in src


def test_a_language_switch_leaves_the_mirror_alone(bridge):
    """A rail dialog leaves its view in ``current_page_view`` after it closes.
    A language switch must not rewrite it to the step's view: that write
    reaches ``_NavBridge`` as a navigation."""
    from pysepal.i18n import set_locale

    bridge.state.current_page_view.set("overview_tile")  # the Overview dialog, opened and closed
    set_locale("en")
    try:
        set_locale("fr")
        assert bridge.state.current_page_view.value == "overview_tile"
    finally:
        set_locale("en")
