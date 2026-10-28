"""The Analysis review save writes ``alert_db.parquet`` into the set folder,
and skips when there is no set folder. (Integrate writes a new set's store
itself: ``tests/test_engine_integrate.py``.)
"""

import types

import geopandas as gpd
from shapely.geometry import Polygon, box

from deforestation_alerts.scripts.alert_store import load_gdf_from_parquet
from gui.tile.analysis.review_save import write_store as _save_alerts_to_gdf
from tests._review_helpers import stub_admin_lookups


def _gdf():
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    gdf = gpd.GeoDataFrame({"status": ["Confirmed"]},
                           geometry=gpd.GeoSeries([poly], crs="EPSG:4326"))
    gdf["bounding_box"] = gdf.geometry.envelope
    gdf["point"] = gdf.geometry.centroid
    return gdf


def test_save_writes_parquet(data_root):
    folder = data_root / "recipe_test"
    model = types.SimpleNamespace(recipe_folder_path=str(folder))

    _save_alerts_to_gdf(_gdf(), model)

    parquet = folder / "alert_db.parquet"
    assert parquet.exists()
    assert not list(folder.glob("*.part"))
    back = load_gdf_from_parquet(parquet)
    assert back["status"].iloc[0] == "Confirmed"


def test_save_skips_without_recipe_folder(data_root, data_files):
    model = types.SimpleNamespace(recipe_folder_path="")
    _save_alerts_to_gdf(_gdf(), model)  # must not raise
    # nothing was written anywhere in the data directory
    assert data_files(data_root) == []


# ---------------------------------------------------------------------------
# the review Save writes the draft growth links
# ---------------------------------------------------------------------------
def _save_with_links(tmp_path, monkeypatch, s, alerts, links):
    """Press Save on alert 0 of ``alerts`` (set x of the state ``s``) with
    ``links`` drafted, for real; the Earth Engine admin lookups stand in.
    Returns the reloaded store."""
    from gui.scripts.set_context import SetContext
    from gui.tile.analysis import export
    from gui.tile.analysis.state import AnalysisState
    from tests.test_set_context import _store

    s.alerts_gdf.set(alerts)
    stub_admin_lookups(monkeypatch)
    analysis = AnalysisState()
    analysis.verdict.set("Confirmed")
    analysis.links.set(tuple(links))
    s.actual_alert_id.set(0)
    widgets = types.SimpleNamespace(actual_bb=types.SimpleNamespace(geometry=lambda: None))
    assert export._save_attributes(widgets, s, analysis, SetContext(s)) is True
    return load_gdf_from_parquet(_store(tmp_path, "d", "glad-l")), s


def _earlier_set(s, tmp_path):
    """An earlier set "old" with a Confirmed alert 3 stored on disk; returns its link to alert 3."""
    from datetime import date, datetime

    from deforestation_alerts.project.growth import run_id_of
    from deforestation_alerts.project.models import AlertSet, AlertSetParams, Period
    from deforestation_alerts.scripts.alert_store import save_gdf_to_parquet

    per = Period(id="old-per", start=date(2025, 1, 1), end=date(2025, 1, 31))
    made = datetime(2025, 2, 1, 8)

    def _add(p):
        p.periods["per"] = Period(id="per", start=date(2025, 2, 1), end=date(2025, 2, 28))
        p.periods[per.id] = per
        p.alert_sets["old"] = AlertSet(
            id="old", period_id=per.id, sources=["GLAD-L"], digit_map={0: "GLAD-L"}, params=AlertSetParams(),
            store_rel_path="periods/old-per/sets/old/alert_db.parquet", combined_rel_path="c.tif",
            footprint_rel_path="f.tif", fingerprint="o", created_at=made)

    s.update_project("d", _add)
    old = gpd.GeoDataFrame(
        {"alert_id": [3], "status": ["Confirmed"], "area_ha": [1.0], "alert_date_min": [2025.01],
         "alert_date_max": [2025.02], "grows_from": [[]]},
        geometry=[box(0, 0, 1, 1)], crs="EPSG:4326")
    target = tmp_path / "d" / "periods" / "old-per" / "sets" / "old" / "alert_db.parquet"
    target.parent.mkdir(parents=True)
    save_gdf_to_parquet(old, str(target))
    return f"old#3@{run_id_of(s.project.value.alert_sets['old'])}"


def _alerts_x():
    from tests.test_set_context import _review_alerts

    alerts = _review_alerts("x")
    alerts["alert_id"] = [0, 1]
    alerts["alert_date_min"] = [2025.05, 2025.05]
    alerts["alert_date_max"] = [2025.06, 2025.06]
    alerts["grows_from"] = [[], []]
    return alerts


def test_save_writes_grows_from_and_round_trips(tmp_path, monkeypatch):
    from deforestation_alerts.project.growth import links_of
    from tests.test_set_context import _state

    s = _state(tmp_path, monkeypatch)
    link = _earlier_set(s, tmp_path)
    alerts = _alerts_x()
    back, s = _save_with_links(tmp_path, monkeypatch, s, alerts, [link, "ghost#1@20250101000000000000"])
    assert links_of(back.loc[0, "grows_from"]) == [link]  # the ghost's set does not exist: dropped
    assert links_of(back.loc[1, "grows_from"]) == []
    assert links_of(s.alerts_gdf.value.loc[0, "grows_from"]) == [link]


def test_save_creates_grows_from_on_old_store(tmp_path, monkeypatch):
    from deforestation_alerts.project.growth import links_of
    from tests.test_set_context import _review_alerts, _state

    back, _ = _save_with_links(tmp_path, monkeypatch, _state(tmp_path, monkeypatch), _review_alerts("x"), [])
    assert "grows_from" in back.columns
    assert [links_of(c) for c in back["grows_from"]] == [[], []]


def test_save_into_an_all_empty_grows_from_column(tmp_path, monkeypatch):
    """Integrate writes ``list<null>`` when no alert got a link; it reads back as
    empty arrays and must take a list of strings."""
    from deforestation_alerts.project.growth import links_of
    from deforestation_alerts.scripts.alert_store import save_gdf_to_parquet
    from tests.test_set_context import _state

    s = _state(tmp_path, monkeypatch)
    link = _earlier_set(s, tmp_path)
    alerts = _alerts_x()
    path = tmp_path / "e.parquet"
    save_gdf_to_parquet(alerts, str(path))
    reread = load_gdf_from_parquet(path)
    reread.attrs.update(alerts.attrs)
    back, _ = _save_with_links(tmp_path, monkeypatch, s, reread, [link])
    assert links_of(back.loc[0, "grows_from"]) == [link]
