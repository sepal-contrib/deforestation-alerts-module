"""The Analysis "Alert info" card (source, dates, status) and what keeps it
current: ``view_actual_alert`` on every alert change, and a refresh when the
alerts are rebound with the same alert still selected, which is what a
review Save does (``export._apply_save``). Stand-in widgets, a real
``AppState`` and ``SetContext``, as tests/test_set_context.py drives them.
"""

from datetime import datetime
from types import SimpleNamespace

import geopandas as gpd
from shapely.geometry import box

from deforestation_alerts.project.models import AlertSet, AlertSetParams, Project
from gui.i18n import t
from gui.scripts.set_context import SetContext
from gui.store.state_manager import AppState
from gui.tile.analysis import analysis_tile
from gui.tile.analysis.state import AnalysisState


def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    s = AppState()
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    p.alert_sets["x"] = AlertSet(id="x", period_id="per", sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                                 params=AlertSetParams(), store_rel_path="periods/per/sets/glad-l/alert_db.parquet",
                                 combined_rel_path="periods/per/sets/glad-l/c.tif",
                                 footprint_rel_path="periods/per/sets/glad-l/footprint.tif",
                                 fingerprint="f", created_at=datetime.now())
    p.active_set = "x"
    s.new_project_state(p)
    return s


def _alerts(n):
    return gpd.GeoDataFrame(
        {"status": ["Not reviewed"] * n, "alert_id": list(range(n)), "set_id": ["x"] * n,
         "alert_sources": ["GLAD-L"] * n, "alert_date_min": [2026.010] * n, "alert_date_max": [2026.020] * n},
        geometry=[box(i, 0, i + 1, 1) for i in range(n)], crs="EPSG:4326")


def _wire(s):
    widgets = SimpleNamespace(selected_alert_info=SimpleNamespace(children=[]))
    analysis_tile.wire_alert_navigation(widgets, s, AnalysisState(), SetContext(s),
                                        SimpleNamespace(v_model=None))
    return widgets


def _status(widgets):
    return widgets.selected_alert_info.children[-1]


def test_the_alert_info_card_follows_a_review_save(tmp_path, monkeypatch):
    """Right after the first Save the card said "Status: Not reviewed" while
    the header said 1 reviewed; it read "Reviewed" only after a reopen. The
    Save writes the status into the row and rebinds ``alerts_gdf`` to a copy
    with the same alert selected, which no subscription refreshed the card on."""
    s = _state(tmp_path, monkeypatch)
    widgets = _wire(s)
    gdf = _alerts(3)
    s.alerts_gdf.set(gdf)  # the set arrives: alert 0 is selected and drawn
    assert s.actual_alert_id.value == 0
    assert _status(widgets) == t("analysis_tile.alert_info.status_not_reviewed")

    gdf.at[0, "status"] = "Confirmed"  # _save_attributes writes into the row...
    s.alerts_gdf.value = gdf.copy()  # ...and _apply_save rebinds a copy

    assert s.actual_alert_id.value == 0
    assert _status(widgets) == t("analysis_tile.alert_info.status_reviewed")
    assert widgets.selected_alert_info.children[1] == "GLAD-L"


def test_a_rebind_with_no_alert_selected_leaves_the_card_alone(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    widgets = _wire(s)
    s.alerts_gdf.set(_alerts(2))
    s.go_to(1)
    assert widgets.selected_alert_info.children[1] == "GLAD-L"
    s.actual_alert_id.value = -1  # a set switch resets the selection before it publishes
    before = list(widgets.selected_alert_info.children)
    assert analysis_tile.refresh_alert_info(widgets, s) is None
    assert widgets.selected_alert_info.children == before
    s.alerts_gdf.set(_alerts(1))  # the new set: its first alert is selected and drawn once
    assert s.actual_alert_id.value == 0 and _status(widgets) == t("analysis_tile.alert_info.status_not_reviewed")


def test_another_sets_alerts_leave_the_card_and_the_review_state_alone(tmp_path, monkeypatch):
    """Integrate records a new set before it publishes that set's alerts: until
    then ``alerts_gdf`` still holds the old set's rows, which Analysis must not
    show as the new set's (Overview already checks ``alerts_belong_to``)."""
    s = _state(tmp_path, monkeypatch)
    old = _alerts(2)
    old["set_id"] = "old"
    s.alerts_gdf.value = old
    s.actual_alert_id.value = 0
    widgets = SimpleNamespace(selected_alert_info=SimpleNamespace(children=["kept"]))
    assert analysis_tile.refresh_alert_info(widgets, s) is None
    assert widgets.selected_alert_info.children == ["kept"]

    analysis = AnalysisState()
    analysis.defo_layer.set("drawn")
    id_field = SimpleNamespace(v_model=None)
    analysis_tile.view_actual_alert(widgets, s, analysis, SetContext(s), id_field)
    assert widgets.selected_alert_info.children == ["kept"]
    assert id_field.v_model is None and analysis.defo_layer.value == "drawn"


def test_an_alert_without_a_status_reads_not_reviewed(tmp_path, monkeypatch):
    """A missing (NaN) status is not a review, as the set badge counts it."""
    s = _state(tmp_path, monkeypatch)
    widgets = _wire(s)
    gdf = _alerts(1)
    gdf["status"] = [None]
    s.alerts_gdf.set(gdf)
    assert _status(widgets) == t("analysis_tile.alert_info.status_not_reviewed")
