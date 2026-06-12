"""AppState: ephemeral reactive UI/nav state (D8)."""

from gui.store.state_manager import AppState


def test_defaults():
    s = AppState()
    assert s.current_page_view.value == "about_tile"
    assert s.actual_alert_id.value == -1
    assert s.alerts_gdf.value is None
    assert s.loading.value is False
    # The active *map* tile is distinct from current_page_view: it tracks only the
    # right-panel tile that owns the shared map's layers, ignoring popups. No panel
    # tile is active at startup (the initial step is the About dialog).
    assert s.active_map_view.value == ""


def test_reactives_are_independent_per_instance():
    a, b = AppState(), AppState()
    a.current_page_view.value = "aoi_tile"
    assert b.current_page_view.value == "about_tile"


def test_navigation_helpers():
    s = AppState()
    s.alerts_gdf.value = list(range(5))  # stand-in: 5 alerts
    s.actual_alert_id.value = 0
    s.go_next()
    assert s.actual_alert_id.value == 1
    s.go_to(4)
    s.go_next()  # clamp at last
    assert s.actual_alert_id.value == 4
