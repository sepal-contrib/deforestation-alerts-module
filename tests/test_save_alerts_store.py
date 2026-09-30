"""The two call sites that persist ``alert_db.parquet`` write it to the user's
SEPAL workspace through ``Workspace``, and still skip when there is no recipe.

``recipe_folder_path`` holds a path built with ``Workspace.results_dir(...)``,
so both writers are handed the workspace alongside it.
"""

import types

import geopandas as gpd
from shapely.geometry import Polygon

from deforestation_alerts.scripts.recipe_helper import load_gdf_from_parquet
from gui.tile.analysis.export import _save_alerts_to_gdf


def _gdf():
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    gdf = gpd.GeoDataFrame({"status": ["Confirmed"]},
                           geometry=gpd.GeoSeries([poly], crs="EPSG:4326"))
    gdf["bounding_box"] = gdf.geometry.envelope
    gdf["point"] = gdf.geometry.centroid
    return gdf


def test_save_writes_parquet(ws):
    folder = ws.results_dir("recipe_test")
    model = types.SimpleNamespace(recipe_folder_path=folder)

    _save_alerts_to_gdf(_gdf(), model, ws)

    parquet = folder + "/alert_db.parquet"
    assert ws.exists(parquet)
    back = load_gdf_from_parquet(ws, parquet)
    assert back["status"].iloc[0] == "Confirmed"


def test_save_skips_without_recipe_folder(ws, data_files):
    model = types.SimpleNamespace(recipe_folder_path="")
    _save_alerts_to_gdf(_gdf(), model, ws)  # must not raise
    # nothing was written anywhere in the user's workspace
    assert data_files(ws) == []


def test_filter_persist_writes_parquet(ws):
    """The analyze store write runs inside the task (in a thread, after an
    explicit cancellation re-check) and reports back by return value: ``None``
    means "written"."""
    from gui.tile.alerts_filter_tile import _persist_alerts_gdf

    folder = ws.results_dir("recipe_test")
    assert _persist_alerts_gdf(_gdf(), folder, ws) is None

    back = load_gdf_from_parquet(ws, folder + "/alert_db.parquet")
    assert back["status"].iloc[0] == "Confirmed"


def test_filter_persist_skips_without_recipe_folder(ws, data_files):
    from gui.tile.alerts_filter_tile import _persist_alerts_gdf

    assert _persist_alerts_gdf(_gdf(), "", ws) is None  # must not raise
    assert data_files(ws) == []


def test_filter_persist_reports_a_failed_write_instead_of_raising(
    ws, monkeypatch, data_files
):
    """A store write that fails must neither kill the analyze (the alerts are
    already in memory) nor pass silently: it comes back as a message."""
    from gui.tile.alerts_filter_tile import _persist_alerts_gdf

    def _boom(*_a, **_kw):
        raise OSError("workspace is full")

    # The parquet write goes through `upload_from_scratch` (`write_bytes` is
    # never reached), so that is the seam to patch.
    monkeypatch.setattr(ws, "upload_from_scratch", _boom)

    error = _persist_alerts_gdf(_gdf(), ws.results_dir("recipe_test"), ws)

    assert error == "workspace is full"
    assert data_files(ws) == []


def test_a_failed_store_write_is_reported_as_an_error_not_a_success():
    """The completion effect routes this pair to notifications.error /
    .success; reporting a failed store write as "Analysis complete" was the bug.
    """
    import dataclasses

    from deforestation_alerts.message import cm
    from gui.tile.alerts_filter_tile import AnalyzeOutcome, _analyze_feedback

    outcome = AnalyzeOutcome(
        selections=None,
        filtered_alert_raster=None,
        alerts_total_bbs=None,
        alerts_bbs=None,
        alerts_gdf=None,
        alert_count=7,
        recipe_folder="/home/sepal-user/module_results/deforestation_alerts/r",
    )

    assert _analyze_feedback(outcome) == (
        "success",
        str(cm.filter_tile.analyze_done.format(n=7)),
    )

    failed = dataclasses.replace(outcome, persist_error="workspace is full")
    type_, message = _analyze_feedback(failed)
    assert type_ == "error"
    assert "workspace is full" in message and "7" in message
