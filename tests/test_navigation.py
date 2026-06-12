"""Pure navigation logic: current_page_view <-> step mapping + the per-transition
action resolver that the _NavBridge applies to the MapApp widget.

These are side-effect-free (no EE, no UI) so they run offline like the other
Phase-0 unit tests.
"""

import pytest

from gui.store.navigation import (
    PAGE_VIEW_TO_STEP,
    STEP_TO_PAGE_VIEW,
    active_map_view_for_step,
    apply_navigation,
    page_view_for_step,
    resolve_navigation,
)

# The real app layout (solara_app.py steps_data): About/Recipes/Aux AND Analysis
# are dialogs, AOI/Filter/Overview are right-panel tiles. (Analysis is the wide
# before/after dual-map review popup; it used to be a full "step" page.)
STEP_DISPLAY = {1: "dialog", 2: "dialog", 3: "dialog", 4: "panel", 5: "panel", 6: "panel", 7: "dialog"}

# The right-panel ("panel") tiles' step ids — the only tiles that own layers on
# the single shared map (AOI 4, Filter 5, Overview 6). Matches solara_app.py's
# panel_wrappers keys.
PANEL_STEP_IDS = {4, 5, 6}


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


def test_resolve_analysis_dialog_target_keeps_dialog_open():
    # Filter "analyze" lands on Analysis (step 7, now a wide modal dialog): the
    # dialog must open/stay (close_dialog False) and the right panel must close.
    action = resolve_navigation("analysis_tile", STEP_DISPLAY)
    assert action.step_id == 7
    assert action.panel_action == "close"
    assert action.close_dialog is False


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


def test_active_map_view_updates_for_each_panel_tile():
    # Activating a right-panel tile makes it the active *map* tile (it owns layers
    # on the shared map), so the resolver yields that tile's view name.
    assert active_map_view_for_step(4, PANEL_STEP_IDS) == "aoi_tile"
    assert active_map_view_for_step(5, PANEL_STEP_IDS) == "filter_alerts"
    assert active_map_view_for_step(6, PANEL_STEP_IDS) == "overview_tile"


def test_active_map_view_unchanged_for_popup_tiles():
    # Popup/dialog tiles (About 1, Recipes 2, Aux 3, Analysis 7) overlay the map
    # without owning its content. Activating one must NOT change the active map
    # tile -> the resolver returns None ("leave it unchanged"), so the underlying
    # panel tile's layers persist behind the popup and survive its close.
    for popup_step in (1, 2, 3, 7):
        assert active_map_view_for_step(popup_step, PANEL_STEP_IDS) is None


def test_active_map_view_unchanged_for_unknown_step():
    assert active_map_view_for_step(99, PANEL_STEP_IDS) is None


def test_popup_over_panel_keeps_active_map_tile():
    """End-to-end of the bug: opening a popup over a right-panel tile must not
    refresh the map content. The active map tile only changes when another panel
    tile is activated."""
    active = ""  # initial step is About (a dialog) -> no panel tile active yet
    # Land on Filter (panel) -> it becomes the active map tile.
    active = active_map_view_for_step(5, PANEL_STEP_IDS) or active
    assert active == "filter_alerts"
    # Open the Analysis popup (dialog) over it -> active map tile UNCHANGED
    # (before the fix this flipped to analysis and cleared Filter's layers).
    active = active_map_view_for_step(7, PANEL_STEP_IDS) or active
    assert active == "filter_alerts"
    # Open the About popup too -> still unchanged.
    active = active_map_view_for_step(1, PANEL_STEP_IDS) or active
    assert active == "filter_alerts"
    # Navigate to Overview (panel) -> now the active map tile switches.
    active = active_map_view_for_step(6, PANEL_STEP_IDS) or active
    assert active == "overview_tile"


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


def test_apply_analysis_dialog_target_leaves_dialog_open():
    mapapp, nav = _FakeMapApp(), _FakeNav()
    apply_navigation(mapapp, nav, resolve_navigation("analysis_tile", STEP_DISPLAY))
    assert mapapp.panel_calls == ["close"]
    assert mapapp.step_open is True  # close_dialog False -> dialog left to open
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
