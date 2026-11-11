"""A converted version 1 recipe reviews, saves, shows in Overview and exports,
without raster files or a review copy (v1 migration, task 8)."""

import hashlib
import math
import types
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest

from deforestation_alerts.project import io
from deforestation_alerts.scripts.alert_export import export_alerts_gdf
from deforestation_alerts.scripts.alert_store import load_gdf_from_parquet
from gui.i18n import t
from gui.scripts import alert_layers, overview_data, set_store
from gui.scripts.set_context import SetContext
from gui.scripts.setup_guard import update_setup
from gui.scripts.v1_open import ensure_converted
from gui.store.autosave import install_autosave
from gui.store.state_manager import AppState
from gui.tile.analysis import export
from gui.tile.analysis.state import AnalysisState
from tests._review_helpers import stub_admin_lookups
from tests._v1_helpers import write_recipe
from tests.test_v1_open import AOI_FC, NAME


V1_FILES = ("recipe_parameters.json", "alert_db.csv")


def _sha1(path):
    return hashlib.sha1(path.read_bytes()).hexdigest()


class _Notes:
    def __init__(self):
        self.warnings = []

    def warning(self, text, *a, **k):
        self.warnings.append(text)


class _Map:
    def __init__(self):
        self.added = []

    def add_layer(self, layer, *a, **k):
        self.added.append(layer)


def test_converted_recipe_reviews_saves_and_exports(data_root, monkeypatch):
    write_recipe(data_root / NAME)
    v1_before = {f: _sha1(data_root / NAME / f) for f in V1_FILES}
    assert ensure_converted(NAME, resolve=lambda params: AOI_FC) is True
    p = io.load_project(NAME)
    state = AppState()
    state.load_project_state(p)
    unsubscribe = install_autosave(state)
    s = p.alert_sets[p.active_set]

    # the alerts load as the set's
    gdf = set_store.load_active_alerts(p)
    assert len(gdf) == 4 and set_store.alerts_belong_to(gdf, s)

    # a review Save of alert 0 as Confirmed
    state.alerts_gdf.set(gdf)
    state.reviewer = "ana"
    stub_admin_lookups(monkeypatch)
    analysis = AnalysisState()
    analysis.verdict.set("Confirmed")
    # the timeline's picks ([0] source, [1] milliseconds, [3] the pick's text)
    analysis.before_info.set(["Landsat", 1786752000000, None, "Landsat 2026-08-15 ", "_b"])
    analysis.after_info.set(["Sentinel-2", 1789430400000, None, "Sentinel-2 2026-09-15 ", "_a"])
    state.actual_alert_id.set(0)
    widgets = types.SimpleNamespace(actual_bb=types.SimpleNamespace(geometry=lambda: None))
    assert export._save_attributes(widgets, state, analysis, SetContext(state)) is True
    stored = load_gdf_from_parquet(set_store.store_path(p, s))
    assert stored.loc[0, "status"] == "Confirmed" and stored.loc[0, "reviewed_by"] == "ana"
    assert stored.loc[0, "before_img"] == "Landsat 2026-08-15 " and stored.loc[0, "after_img_info"] == 1789430400000
    assert {f: _sha1(data_root / NAME / f) for f in V1_FILES} == v1_before  # the Save leaves v1's files alone
    p = io.load_project(NAME)
    s = p.alert_sets[p.active_set]
    assert s.n_reviewed == 4

    # Overview
    summary = overview_data.summary_of(p, s)
    total = overview_data.total_of(summary, "area_ha")
    assert not math.isnan(total) and total == pytest.approx(stored["area_ha"].sum())

    # the set export
    folder = set_store.set_folder(p, s)
    period = p.periods[s.period_id]
    for fmt in ("gpkg", "csv"):
        out = export_alerts_gdf(stored, folder, fmt, s.sources, str(period.start), str(period.end))
        rows = pd.read_csv(out) if fmt == "csv" else gpd.read_file(out, layer=Path(out).stem)
        assert len(rows) == 4

    # no combined raster: no pixel states drawn
    m = _Map()
    assert alert_layers.has_states(p, s) is False
    assert alert_layers.states_build(m, project=p, alert_set=s) is False and m.added == []

    # the setup stays locked
    notes = _Notes()
    assert update_setup(state, p.name, lambda live: None, notifier=notes) is False
    assert notes.warnings == [t("migrated.read_only")]
    unsubscribe()
