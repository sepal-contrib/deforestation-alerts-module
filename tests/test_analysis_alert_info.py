"""The guard ``view_actual_alert`` keeps over the per-alert view: alerts of
another set than the active one must change nothing on screen. The facts the
alert-info card used to show now live in Block A, with their own guard and
their own tests (tests/test_analysis_context.py); what is left here is the
tile's side of it, plus the ``_state`` / ``_alerts`` / ``_wire`` helpers
tests/test_loss_drivers.py drives the review panel with. Stand-in widgets, a
real ``AppState`` and ``SetContext``, as tests/test_set_context.py drives them.
"""

from datetime import datetime
from types import SimpleNamespace

import geopandas as gpd
from shapely.geometry import box

from deforestation_alerts.project.models import AlertSet, AlertSetParams, Project
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


def _wire(s, analysis=None):
    """A tile wired to one state, as ``build_analysis_layout`` wires it.
    tests/test_analysis_questions_panel.py drives the Questions section through
    this, sharing ``analysis`` with the section as the layout does.
    """
    widgets = SimpleNamespace()
    analysis_tile.wire_alert_navigation(widgets, s, analysis if analysis is not None else AnalysisState(),
                                        SetContext(s), SimpleNamespace(v_model=None))
    return widgets


def test_another_sets_alerts_leave_the_panels_numbers_alone(tmp_path, monkeypatch):
    """Integrate records a new set before it publishes that set's alerts: until
    then ``alerts_gdf`` still holds the old set's rows, which Analysis must not
    show as the new set's (Overview already checks ``alerts_belong_to``).

    The panel's numbers are what stays: the jump field is not touched, so the
    panel does not blank and re-fill on every set switch. The review STATE
    does not stay -- the alert it belongs to is not one of the active set's
    any more, and keeping it kept the token of an alert a CNN run or an export
    could still be running on (see
    ``test_analysis_state.test_alerts_of_another_set_invalidate_the_analysis_state_too``).
    """
    s = _state(tmp_path, monkeypatch)
    old = _alerts(2)
    old["set_id"] = "old"
    s.alerts_gdf.value = old
    s.actual_alert_id.value = 0

    analysis = AnalysisState()
    analysis.defo_layer.set("drawn")
    token = analysis.alert_token.value
    id_field = SimpleNamespace(v_model=None)
    analysis_tile.view_actual_alert(SimpleNamespace(), s, analysis, SetContext(s), id_field)
    assert id_field.v_model is None
    assert analysis.defo_layer.value is None and not analysis.is_current(token)
