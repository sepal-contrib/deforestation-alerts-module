"""Pure navigation logic: current_page_view -> (workflow index | rail step id
| screen) and the action the _NavBridge applies to the shell's
reactives (the rail step, the right panel) and AppState.

Side-effect-free (no EE, no UI) so it runs offline."""

import pytest
import solara

from gui.store.navigation import (
    PAGE_VIEW_TO_RAIL,
    PAGE_VIEW_TO_SCREEN,
    PAGE_VIEW_TO_WORKFLOW,
    RAIL_STEP_TO_PAGE_VIEW,
    apply_navigation,
    dismiss_dialog,
    page_view_for_rail_step,
    resolve_navigation,
)
from gui.store.state_manager import AppState


def test_workflow_map_covers_the_eight_steps_in_order():
    assert PAGE_VIEW_TO_WORKFLOW == {
        "aoi_tile": 0,
        "period_tile": 1,
        "alerts_tile": 2,
        "harmonize_tile": 3,
        "integrate_tile": 4,
        "layers_tile": 5,
        "questionnaire_tile": 6,
        "evaluators_tile": 7,
    }


def test_the_analysis_view_opens_a_screen_not_a_step():
    assert PAGE_VIEW_TO_SCREEN == {"analysis_tile": "analysis"}
    assert not set(PAGE_VIEW_TO_SCREEN) & (set(PAGE_VIEW_TO_WORKFLOW) | set(PAGE_VIEW_TO_RAIL))


def test_rail_map_covers_the_three_dialogs():
    assert PAGE_VIEW_TO_RAIL == {
        "about_tile": 1,
        "project_tile": 2,
        "overview_tile": 3,
    }
    # The Analysis rail item mirrors the screen's page view.
    assert RAIL_STEP_TO_PAGE_VIEW == {**{v: k for k, v in PAGE_VIEW_TO_RAIL.items()}, 4: "analysis_tile"}
    assert page_view_for_rail_step(3) == "overview_tile"
    assert page_view_for_rail_step(99) is None


def test_resolve_unknown_view_returns_none():
    assert resolve_navigation("nope_tile") is None


def test_resolve_workflow_rail_and_screen_targets():
    assert resolve_navigation("integrate_tile") == ("workflow", 4)
    assert resolve_navigation("analysis_tile") == ("screen", "analysis")
    assert resolve_navigation("overview_tile") == ("rail", 3)
    assert resolve_navigation("project_tile") == ("rail", 2)


def test_apply_workflow_target_closes_the_dialog_opens_panel_and_sets_step():
    nav, panel, state = solara.reactive(3), solara.reactive(False), AppState()
    state.screen.set("analysis")
    apply_navigation(nav, panel, state, resolve_navigation("questionnaire_tile"))
    assert panel.value is True  # a panel the user closed opens again
    assert nav.value is None  # closes the Overview dialog and un-highlights its item
    assert state.active_step.value == 6
    assert state.screen.value == "workflow"


def test_apply_a_screen_target_closes_the_dialog_and_keeps_the_step():
    """The Overview's *Review alerts* opens Analysis from a dialog: the dialog
    closes, the panel is left alone (MapApp's right panel is hidden while the
    screen shows) and the workflow step is remembered for *← Workflow*."""
    nav, panel, state = solara.reactive(3), solara.reactive(False), AppState()
    state.active_step.set(5)
    apply_navigation(nav, panel, state, resolve_navigation("analysis_tile"))
    assert state.screen.value == "analysis" and state.active_step.value == 5
    assert nav.value is None
    assert panel.value is False


@pytest.mark.parametrize("view,step", [("about_tile", 1), ("project_tile", 2), ("overview_tile", 3)])
def test_apply_a_rail_target_only_activates_its_dialog_step(view, step):
    """Every rail item is a dialog -- About, Project and the Overview
    dashboard: MapApp opens it when its step changes, and the right panel is
    left as the user left it behind the dialog."""
    nav, panel, state = solara.reactive(2), solara.reactive(False), AppState()
    state.active_step.set(1)
    apply_navigation(nav, panel, state, resolve_navigation(view))
    assert nav.value == step
    assert panel.value is False
    assert state.active_step.value == 1  # the workflow position is remembered
    assert state.screen.value == "workflow"


def test_dismiss_dialog_closes_an_open_dialog_like_its_close_button():
    """The Project dialog closes itself after opening a project: MapApp
    closes the dialog when its step is cleared."""
    nav = solara.reactive(2)  # the Project dialog
    dismiss_dialog(nav)
    assert nav.value is None
    nav = solara.reactive(3)  # the Overview dashboard
    dismiss_dialog(nav)
    assert nav.value is None


def test_dismiss_dialog_with_nothing_open_is_a_no_op():
    """It is called on every screen navigation, most of which follow no
    dialog at all: a cleared step must not be written again."""
    nav = solara.reactive(None)
    writes = []
    nav.subscribe(writes.append)
    dismiss_dialog(nav)
    assert nav.value is None and writes == []
