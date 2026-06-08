"""alert_repository: pure gdf merge (extracted from analysis_tile.create_gdf_full).

The real merge dedups fresh rows by the `bounding_box` column, so the fixtures
carry that column. Invariants pinned: reviewed/active rows survive, fresh rows
with a new bounding_box are appended, duplicates are dropped, inputs are never
mutated, and a new object is returned (no aliasing -> no cross-thread race)."""

import geopandas as gpd
from shapely.geometry import Polygon

from deforestation_alerts.scripts.alert_repository import merge_reviewed_into_full


def _gdf(statuses, bboxes):
    poly = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    return gpd.GeoDataFrame(
        {"status": statuses, "bounding_box": bboxes, "geometry": [poly] * len(statuses)},
        geometry="geometry", crs="EPSG:4326",
    )


def test_reviewed_rows_are_preserved_over_fresh_full():
    current = _gdf(["Reviewed", "Not reviewed"], ["a", "b"])   # row 0 reviewed
    full = _gdf(["Not reviewed", "Not reviewed", "Not reviewed"], ["a", "b", "c"])
    merged = merge_reviewed_into_full(current, full, actual_alert_id=0)
    # the reviewed row survives exactly once; input untouched
    assert (merged["status"] == "Reviewed").sum() == 1
    assert (current["status"] == "Reviewed").sum() == 1  # input untouched


def test_reviewed_bbox_is_deduped_from_fresh_full():
    current = _gdf(["Reviewed"], ["a"])
    full = _gdf(["Not reviewed", "Not reviewed"], ["a", "b"])
    merged = merge_reviewed_into_full(current, full, actual_alert_id=0)
    # bbox "a" comes only from the reviewed row (not duplicated from full);
    # bbox "b" (new) is appended
    assert list(merged["bounding_box"]) == ["a", "b"]
    assert (merged["bounding_box"] == "a").sum() == 1


def test_actual_alert_id_row_is_kept_even_if_not_reviewed():
    current = _gdf(["Not reviewed", "Not reviewed"], ["a", "b"])
    full = _gdf(["Not reviewed", "Not reviewed", "Not reviewed"], ["a", "b", "c"])
    merged = merge_reviewed_into_full(current, full, actual_alert_id=0)
    # row 0 (actual_alert_id) is kept from current; its bbox "a" not duplicated
    assert (merged["bounding_box"] == "a").sum() == 1
    assert "c" in list(merged["bounding_box"])


def test_returns_new_object_and_does_not_mutate_inputs():
    current = _gdf(["Not reviewed"], ["a"])
    full = _gdf(["Not reviewed", "Not reviewed"], ["a", "b"])
    full_len_before = len(full)
    merged = merge_reviewed_into_full(current, full, actual_alert_id=0)
    assert merged is not current and merged is not full
    assert len(full) == full_len_before  # input untouched


def test_none_current_returns_fresh_full_copy():
    full = _gdf(["Not reviewed", "Not reviewed"], ["a", "b"])
    merged = merge_reviewed_into_full(None, full, actual_alert_id=0)
    assert merged is not full
    assert list(merged["bounding_box"]) == ["a", "b"]
