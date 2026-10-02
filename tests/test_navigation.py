"""Pure navigation logic: current_page_view -> (workflow index | rail step id)
and the action the _NavBridge applies to the MapApp widget + AppState.

Side-effect-free (no EE, no UI) so it runs offline."""

from gui.store.navigation import (
    PAGE_VIEW_TO_RAIL,
    PAGE_VIEW_TO_WORKFLOW,
    RAIL_STEP_TO_PAGE_VIEW,
    apply_navigation,
    dismiss_dialog,
    page_view_for_rail_step,
    resolve_navigation,
)
from gui.store.state_manager import AppState


def test_workflow_map_covers_the_seven_steps_in_order():
    assert PAGE_VIEW_TO_WORKFLOW == {
        "aoi_tile": 0,
        "alerts_tile": 1,
        "layers_tile": 2,
        "period_tile": 3,
        "prepare_tile": 4,
        "integrate_tile": 5,
        "analysis_tile": 6,
    }


def test_rail_map_covers_the_three_items():
    assert PAGE_VIEW_TO_RAIL == {
        "about_tile": 1,
        "project_tile": 2,
        "overview_tile": 3,
    }
    assert RAIL_STEP_TO_PAGE_VIEW == {v: k for k, v in PAGE_VIEW_TO_RAIL.items()}
    assert page_view_for_rail_step(3) == "overview_tile"
    assert page_view_for_rail_step(99) is None


def test_resolve_unknown_view_returns_none():
    assert resolve_navigation("nope_tile") is None


def test_resolve_workflow_and_rail_targets():
    assert resolve_navigation("integrate_tile") == ("workflow", 5)
    assert resolve_navigation("analysis_tile") == ("workflow", 6)
    assert resolve_navigation("overview_tile") == ("rail", 3)
    assert resolve_navigation("project_tile") == ("rail", 2)


class _FakeMapApp:
    """Records the panel action + step_open writes the bridge applies."""

    def __init__(self):
        self.step_open = True  # pretend a dialog is currently open
        self.panel_calls = []

    def vue_handle_right_panel_action(self, action):
        self.panel_calls.append(action)


class _FakeNav:
    def __init__(self, current_step=2):
        self.current_step = current_step


def test_apply_workflow_target_dismisses_dialog_opens_panel_resets_view_sets_step():
    mapapp, nav, state = _FakeMapApp(), _FakeNav(current_step=3), AppState()
    state.panel_view.set("overview")  # coming from the Overview view
    apply_navigation(mapapp, nav, state, resolve_navigation("analysis_tile"))
    assert mapapp.step_open is False
    assert mapapp.panel_calls == ["open"]
    assert nav.current_step is None  # the Overview rail item un-highlights
    assert state.panel_view.value == "workflow"
    assert state.active_step.value == 6


def test_apply_panel_rail_target_opens_panel_and_switches_view():
    mapapp, nav, state = _FakeMapApp(), _FakeNav(), AppState()
    state.active_step.set(1)
    apply_navigation(mapapp, nav, state, resolve_navigation("overview_tile"))
    assert mapapp.step_open is False
    assert mapapp.panel_calls == ["open"]
    assert nav.current_step == 3
    assert state.panel_view.value == "overview"
    assert state.active_step.value == 1  # workflow position remembered


def test_apply_dialog_rail_target_only_activates_the_dialog_step():
    mapapp, nav, state = _FakeMapApp(), _FakeNav(), AppState()
    state.panel_view.set("overview")
    apply_navigation(mapapp, nav, state, resolve_navigation("project_tile"))
    assert nav.current_step == 2
    assert mapapp.panel_calls == []
    assert mapapp.step_open is True  # MapApp's own watcher opens the dialog
    assert state.panel_view.value == "overview"  # dialogs never change the view


def test_apply_without_mapapp_still_moves_state():
    nav, state = _FakeNav(), AppState()
    apply_navigation(None, nav, state, resolve_navigation("integrate_tile"))
    assert state.active_step.value == 5 and nav.current_step is None
    apply_navigation(None, nav, state, resolve_navigation("overview_tile"))
    assert state.panel_view.value == "overview" and nav.current_step == 3


def test_dismiss_dialog_closes_an_open_dialog_like_its_close_button():
    """The Project dialog closes itself after opening a project: MapApp.vue's
    closeDialog clears the step and closes the dialog, and so does this."""
    mapapp, nav = _FakeMapApp(), _FakeNav(current_step=2)  # the Project dialog
    dismiss_dialog(mapapp, nav)
    assert mapapp.step_open is False and nav.current_step is None
    assert mapapp.panel_calls == []  # the right panel is left as it was


def test_dismiss_dialog_leaves_a_panel_item_current_and_runs_before_the_mount():
    mapapp, nav = _FakeMapApp(), _FakeNav(current_step=3)  # Overview highlighted
    dismiss_dialog(mapapp, nav)
    assert nav.current_step == 3 and mapapp.step_open is False
    nav = _FakeNav(current_step=2)
    dismiss_dialog(None, nav)  # MapApp not mounted yet: the step still clears
    assert nav.current_step is None
