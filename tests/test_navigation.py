"""Pure navigation logic: current_page_view <-> step mapping + the per-transition
action resolver that the _NavBridge applies to the MapApp widget.

These are side-effect-free (no EE, no UI) so they run offline like the other
Phase-0 unit tests.
"""

import pytest

from gui.store.navigation import (
    PAGE_VIEW_TO_STEP,
    STEP_TO_PAGE_VIEW,
    apply_navigation,
    page_view_for_step,
    resolve_navigation,
)

# The real app layout (solara_app.py steps_data): About/Recipes/Aux are dialogs,
# AOI/Filter/Overview are right-panel tiles, Analysis is a full step.
STEP_DISPLAY = {1: "dialog", 2: "dialog", 3: "dialog", 4: "panel", 5: "panel", 6: "panel", 7: "step"}


def test_mapping_covers_all_seven_tiles():
    assert PAGE_VIEW_TO_STEP == {
        "about_tile": 1,
        "recipe_tile": 2,
        "aux_tile": 3,
        "aoi_tile": 4,
        "filter_alerts": 5,
        "overview_tile": 6,
        "analysis_tile": 7,
    }


def test_reverse_map_is_the_inverse():
    assert STEP_TO_PAGE_VIEW == {v: k for k, v in PAGE_VIEW_TO_STEP.items()}
    for view, step in PAGE_VIEW_TO_STEP.items():
        assert page_view_for_step(step) == view


def test_page_view_for_unknown_step_is_none():
    assert page_view_for_step(99) is None


def test_resolve_unknown_view_returns_none():
    assert resolve_navigation("nope_tile", STEP_DISPLAY) is None


def test_resolve_panel_target_opens_panel_and_clears_dialog():
    # AOI search lands on Filter (step 5, a right-panel tile).
    action = resolve_navigation("filter_alerts", STEP_DISPLAY)
    assert action.step_id == 5
    assert action.panel_action == "open"
    assert action.close_dialog is True  # ensure no dialog lingers over the panel


def test_resolve_step_target_closes_panel_and_dialog():
    # Filter analyze lands on Analysis (step 7, a full step).
    action = resolve_navigation("analysis_tile", STEP_DISPLAY)
    assert action.step_id == 7
    assert action.panel_action == "close"
    assert action.close_dialog is True


def test_resolve_dialog_target_closes_panel_keeps_dialog_open():
    # Recipe create lands on Aux (step 3, a dialog) -> dialog must stay/open.
    action = resolve_navigation("aux_tile", STEP_DISPLAY)
    assert action.step_id == 3
    assert action.panel_action == "close"
    assert action.close_dialog is False


def test_resolve_about_dialog_target():
    action = resolve_navigation("about_tile", STEP_DISPLAY)
    assert action.step_id == 1
    assert action.panel_action == "close"
    assert action.close_dialog is False


def test_resolve_missing_display_defaults_to_safe_close():
    # Defensive: a mapped step absent from the display map -> close panel + dialog.
    action = resolve_navigation("overview_tile", {})
    assert action.step_id == 6
    assert action.panel_action == "close"
    assert action.close_dialog is True


class _FakeMapApp:
    """Records the panel action + step_open writes the bridge applies."""

    def __init__(self):
        self.step_open = True  # pretend a dialog is currently open
        self.panel_calls = []

    def vue_handle_right_panel_action(self, action):
        self.panel_calls.append(action)


class _FakeNav:
    def __init__(self):
        self.current_step = None


def test_apply_panel_target_opens_panel_dismisses_dialog_sets_step():
    mapapp, nav = _FakeMapApp(), _FakeNav()
    apply_navigation(mapapp, nav, resolve_navigation("filter_alerts", STEP_DISPLAY))
    assert mapapp.panel_calls == ["open"]
    assert mapapp.step_open is False  # close_dialog True -> step_open cleared
    assert nav.current_step == 5


def test_apply_step_target_closes_panel_and_dialog():
    mapapp, nav = _FakeMapApp(), _FakeNav()
    apply_navigation(mapapp, nav, resolve_navigation("analysis_tile", STEP_DISPLAY))
    assert mapapp.panel_calls == ["close"]
    assert mapapp.step_open is False
    assert nav.current_step == 7


def test_apply_dialog_target_leaves_dialog_open():
    mapapp, nav = _FakeMapApp(), _FakeNav()
    apply_navigation(mapapp, nav, resolve_navigation("aux_tile", STEP_DISPLAY))
    assert mapapp.panel_calls == ["close"]
    assert mapapp.step_open is True  # close_dialog False -> dialog untouched
    assert nav.current_step == 3


def test_apply_without_mapapp_still_sets_step():
    # MapApp not mounted yet -> only the step is set; no crash.
    nav = _FakeNav()
    apply_navigation(None, nav, resolve_navigation("analysis_tile", STEP_DISPLAY))
    assert nav.current_step == 7
