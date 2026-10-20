"""Draft growth links (spec 2026-10-20 §6): the Analysis state, the seeding
from a row and what ``capture_alert`` takes."""

from datetime import datetime
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
from shapely.geometry import box

from deforestation_alerts.project.growth import kind_of, links_of
from deforestation_alerts.project.models import Project
from gui.scripts.set_context import SetContext
from gui.store.state_manager import AppState
from gui.tile.analysis import analysis_tile
from gui.tile.analysis.state import AnalysisState, capture_alert, set_links

L1 = "aug#4@20260902080000000000"
L2 = "sep#12@20261002080000000000"


def test_set_links_marks_dirty_once():
    a = AnalysisState()
    set_links(a, [L1, L2, L1])
    assert a.links.value == (L1, L2) and a.dirty.value is True and a.edit_rev.value == 1
    set_links(a, [L1, L2])
    assert a.edit_rev.value == 1


def test_reset_alert_forgets_the_links():
    a = AnalysisState()
    a.links.set((L1,))
    a.auto_links.set((L1,))
    a.parent_windows.set(((2026.1, 2026.2),))
    a.reset_alert()
    assert a.links.value == () and a.auto_links.value == () and a.parent_windows.value == ()
    assert a.history.value is None


def _viewed(tmp_path, monkeypatch, extra):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    app_state = AppState()
    app_state.new_project_state(Project(name="d", created_at=datetime.now(), updated_at=datetime.now()))
    columns = {"status": ["Not reviewed"], "alert_id": [0], "set_id": ["x"], "alert_sources": ["GLAD-L"],
               "alert_date_min": [2026.010], "alert_date_max": [2026.020], "alert_polygon": [None],
               "bounding_box": [box(0, 0, 1, 1)], **extra}
    app_state.alerts_gdf.value = gpd.GeoDataFrame(columns, geometry=[box(0, 0, 1, 1)], crs="EPSG:4326")
    app_state.actual_alert_id.value = 0
    analysis = AnalysisState()
    analysis.links.set(("the previous alert's",))
    analysis_tile.view_actual_alert(SimpleNamespace(), app_state, analysis, SetContext(app_state),
                                    SimpleNamespace(v_model=None))
    return analysis, app_state


def test_view_seeds_links_from_row(tmp_path, monkeypatch):
    row = {"grows_from": [np.array([L1, L2], dtype=object)], "grows_from_auto": [np.array([L1], dtype=object)],
           "below_min": [False]}
    analysis, _ = _viewed(tmp_path, monkeypatch, row)
    assert analysis.links.value == (L1, L2)
    assert analysis.auto_links.value == (L1,)
    assert analysis.dirty.value is False  # viewing is not a change


def test_old_set_seeds_no_links(tmp_path, monkeypatch):
    analysis, app_state = _viewed(tmp_path, monkeypatch, {})
    assert analysis.links.value == () and analysis.auto_links.value == ()
    assert analysis.dirty.value is False
    row = app_state.alerts_gdf.value.iloc[0]
    assert kind_of(links_of(row.get("grows_from")), row.get("below_min")) == "new"


def test_capture_alert_carries_the_draft_links(tmp_path, monkeypatch):
    analysis, app_state = _viewed(tmp_path, monkeypatch, {})
    set_links(analysis, [L1])
    alert = capture_alert(analysis, SimpleNamespace(), app_state)
    assert alert.links == (L1,)
