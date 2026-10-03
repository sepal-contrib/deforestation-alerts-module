"""Block A of the Analysis controls: the queue counter, the tally and the
progress bar. Pure helpers plus one raw-widget block driven by a real
``AppState``, as tests/test_analysis_alert_info.py drives the tile.
"""

from datetime import datetime
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pytest
from pysepal.i18n import set_locale
from shapely.geometry import box

from deforestation_alerts.project.models import AlertSet, AlertSetParams, Project
from gui.store.state_manager import AppState
from gui.tile.analysis import context


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    s = AppState()
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    p.alert_sets["x"] = AlertSet(
        id="x", period_id="per", sources=["GLAD-L"], digit_map={0: "GLAD-L"},
        params=AlertSetParams(), store_rel_path="periods/per/sets/glad-l/alert_db.parquet",
        combined_rel_path="periods/per/sets/glad-l/c.tif",
        footprint_rel_path="periods/per/sets/glad-l/footprint.tif",
        fingerprint="f", created_at=datetime.now())
    p.active_set = "x"
    s.new_project_state(p)
    return s


def _alerts(statuses, set_id="x"):
    n = len(statuses)
    return gpd.GeoDataFrame(
        {"status": list(statuses), "alert_id": list(range(n)), "set_id": [set_id] * n,
         "alert_sources": ["GLAD-L"] * n, "alert_date_min": [2026.010] * n,
         "alert_date_max": [2026.020] * n},
        geometry=[box(i, 0, i + 1, 1) for i in range(n)], crs="EPSG:4326")


def test_review_counts_counts_only_real_reviews():
    gdf = _alerts(["Not reviewed", "Confirmed", "False Positive", "Need revision", None, ""])
    assert context.review_counts(gdf) == (6, 3)


def test_review_counts_tolerates_a_nan_status_and_a_missing_column():
    gdf = _alerts(["Confirmed", np.nan])
    assert context.review_counts(gdf) == (2, 1)
    bare = gdf.drop(columns=["status"])
    assert context.review_counts(bare) == (2, 0)


def test_review_counts_of_nothing_is_zero_zero():
    assert context.review_counts(None) == (0, 0)
    assert context.review_counts(_alerts([])) == (0, 0)


def test_the_counter_is_one_based_and_padded_to_the_total():
    assert context.counter_text(11, 530) == "012 / 530"
    assert context.counter_text(0, 9) == "1 / 9"
    assert context.counter_text(0, 1200) == "0001 / 1,200"


def test_the_counter_and_the_tally_of_an_empty_queue_say_so():
    assert context.counter_text(-1, 0) == "No alert set is loaded"
    assert context.tally_text(0, 0) == ""


def test_the_tally_names_what_is_left():
    assert context.tally_text(530, 212) == "212 reviewed · 318 left"


def test_the_queue_line_follows_the_alerts_and_the_position(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    card = context.build_context(s, SimpleNamespace(v_model=None))
    counter, tally, bar = context.queue_widgets(card)
    assert counter.children == ["No alert set is loaded"]

    s.alerts_gdf.set(_alerts(["Confirmed"] + ["Not reviewed"] * 9))
    s.actual_alert_id.value = 0
    assert counter.children == ["01 / 10"]
    assert tally.children == ["1 reviewed · 9 left"]
    assert bar.value == 10.0

    s.go_to(4)
    assert counter.children == ["05 / 10"]


def test_the_tally_is_formatted_in_the_language_in_force_when_it_is_written(tmp_path, monkeypatch):
    """The block is built once per session and a language switch never rebuilds
    it, so the strings must be resolved at write time, not at build time."""
    s = _state(tmp_path, monkeypatch)
    card = context.build_context(s, SimpleNamespace(v_model=None))
    _, tally, _ = context.queue_widgets(card)
    set_locale("es-ES")
    s.alerts_gdf.set(_alerts(["Confirmed", "Not reviewed"]))
    s.actual_alert_id.value = 0
    assert tally.children == ["1 revisadas · 1 pendientes"]
