"""A version 1 ``alert_db.csv`` read as a version 2 store frame (v1 migration, task 2)."""

import math

import pandas as pd
import pytest
from shapely.geometry import box

from deforestation_alerts.project.review_groups import polygon_area_ha
from deforestation_alerts.project.review_status import CONFIRMED, FALSE_POSITIVE, NEED_REVISION, NOT_REVIEWED
from deforestation_alerts.project.store_schema import STORE_COLUMNS
from deforestation_alerts.project.v1_migration import TABLE_FILE, fill_areas, pixel_ha, read_table, v2_pick
from deforestation_alerts.scripts import image_sources, scene_query
from deforestation_alerts.scripts.alert_store import load_gdf_from_parquet, save_gdf_to_parquet
from tests._v1_helpers import write_recipe

AUG_15 = 1786752000000  # 2026-08-15 00:00 UTC, in milliseconds


def _read(tmp_path, rows=None):
    gdf, notes = read_table(write_recipe(tmp_path / "recipe", rows=rows) / TABLE_FILE)
    fill_areas(gdf, ["GLAD-L"])
    return gdf, notes


def test_ids_are_row_positions(tmp_path):
    gdf, _ = _read(tmp_path)
    assert list(gdf["alert_id"]) == [0, 1, 2, 3]


def test_statuses_round_trip(tmp_path):
    gdf, _ = _read(tmp_path)
    assert list(gdf["status"]) == [NOT_REVIEWED, CONFIRMED, FALSE_POSITIVE, NEED_REVISION]


def test_blank_status_reads_not_reviewed(tmp_path):
    gdf, _ = _read(tmp_path, rows=[{"status": None}, {"status": ""}, {}])
    assert list(gdf["status"]) == [NOT_REVIEWED, NOT_REVIEWED, FALSE_POSITIVE]


def test_blank_description_is_missing_not_nan_text(tmp_path):
    gdf, _ = _read(tmp_path, rows=[{}, {"description": "Agriculture"}])
    v = gdf["description"].iloc[0]
    assert pd.isna(v) and not (isinstance(v, str) and v == "nan")
    assert gdf["description"].iloc[1] == "Agriculture"


def test_geometry_is_the_bounding_box(tmp_path):
    gdf, _ = _read(tmp_path)
    assert gdf.geometry.name == "geometry"
    assert gdf.geometry.iloc[0].equals(gdf["bounding_box"].iloc[0])
    assert gdf["point"].iloc[0].geom_type == "Point"
    assert gdf.crs.to_epsg() == 4326


def test_drawn_polygon_kept_and_measured_again(tmp_path):
    gdf, _ = _read(tmp_path)
    assert gdf["alert_polygon"].iloc[1].geom_type == "Polygon"
    assert gdf["alert_polygon"].iloc[0] is None
    # not v1's 1.5 (Web Mercator): the polygon's area, as a Save in v2 measures it
    assert gdf["area_ha"].iloc[1] == pytest.approx(polygon_area_ha(gdf["alert_polygon"].iloc[1]))
    assert gdf["area_ha"].iloc[1] > 1000


def test_v1_area_kept_when_its_polygon_is_unreadable(tmp_path):
    gdf, _ = _read(tmp_path, rows=[{"alert_polygon": "POLYGON((", "area_ha": 3.0}])
    assert gdf["area_ha"].iloc[0] == 3.0


def test_pixel_narrows_with_latitude(tmp_path):
    south = box(-54.64, -24.45, -54.63, -24.44)
    gdf, _ = _read(tmp_path, rows=[{"bounding_box": south.wkt, "point": south.centroid.wkt}])
    assert gdf["detected_area_ha"].iloc[0] == pytest.approx(7.84 * math.cos(math.radians(-24.445)), rel=1e-4)


def test_admin_names_lose_v1_trailing_comma(tmp_path):
    gdf, _ = _read(tmp_path, rows=[{"admin1": "Paraguay,", "admin2": "Canindeyu, "}, {}])
    assert gdf["admin1"].iloc[0] == "Paraguay" and gdf["admin2"].iloc[0] == "Canindeyu"
    assert pd.isna(gdf["admin1"].iloc[1])


def test_area_estimated_from_count(tmp_path):
    gdf, _ = _read(tmp_path)
    assert gdf["detected_area_ha"].iloc[0] == pytest.approx(7.84)
    assert gdf["area_ha"].iloc[0] == pytest.approx(7.84)
    assert gdf["detected_area_ha"].iloc[1] == pytest.approx(7.84)  # every row, the drawn one too


def test_pixel_ha():
    assert pixel_ha(["GLAD-L"]) == 0.0784 and pixel_ha(["GLAD-L", "RADD"]) == 0.01
    assert pixel_ha(["GLAD-S2"]) == 0.01


def test_alert_codes_are_a_list(tmp_path):
    gdf, _ = _read(tmp_path, rows=[{"alert_type_unique": "[1, 2]"}])
    assert list(gdf["alert_type_unique"].iloc[0]) == [1, 2]


def test_bad_codes_keep_the_row(tmp_path):
    gdf, notes = _read(tmp_path, rows=[{}, {"alert_type_unique": "[1,"}])
    assert len(gdf) == 2 and gdf["alert_type_unique"].iloc[1] is None
    assert any("alert 1" in n and "alert_type_unique" in n and "[1," in n for n in notes)


def test_v1_index_column_is_dropped(tmp_path):
    folder = write_recipe(tmp_path / "recipe")
    assert (folder / TABLE_FILE).read_text(encoding="utf-8").startswith(",")  # v1 wrote its index
    gdf, _ = read_table(folder / TABLE_FILE)
    assert not [c for c in gdf.columns if str(c).startswith("Unnamed")]


def test_bad_polygon_keeps_the_row(tmp_path):
    gdf, notes = _read(tmp_path, rows=[{}, {}, {"alert_polygon": "POLYGON(("}, {}])
    assert len(gdf) == 4
    assert gdf["alert_polygon"].iloc[2] is None
    assert gdf["status"].iloc[2] == FALSE_POSITIVE
    assert any("alert 2" in n and "alert_polygon" in n for n in notes)


def test_store_columns_present(tmp_path):
    gdf, _ = _read(tmp_path)
    assert set(STORE_COLUMNS) - {"set_id"} <= set(gdf.columns)
    assert "gee_id" not in gdf.columns
    assert gdf["reviewed_by"].isna().all() and gdf["before_radar_img"].isna().all()


def test_v2_pick_sentinel_and_landsat():
    s2 = v2_pick("COPERNICUS/S2_SR_HARMONIZED/20260815T143731_20260815T144140_T19LDK", AUG_15)
    assert s2.strip() == f"{image_sources.S2} 2026-08-15"
    assert scene_query.saved_pick_id(s2) is not None
    ls = v2_pick("LANDSAT/LC08/C02/T1_L2/LC08_002067_20260815", AUG_15)
    assert ls.strip() == f"{image_sources.LANDSAT} 2026-08-15"
    assert scene_query.saved_pick_id(ls) is not None


def test_v2_pick_unknown_is_none():
    assert v2_pick("planet_medres_normalized_analytic_2026-08_mosaic", AUG_15) is None
    assert v2_pick(float("nan"), float("nan")) is None
    assert v2_pick("Not available", "Not available") is None
    assert v2_pick("COPERNICUS/S2_SR_HARMONIZED/x", float("nan")) is None


def test_picks_rewritten_unknown_kept(tmp_path):
    planet = "planet_medres_normalized_analytic_2026-08_mosaic"
    gdf, _ = _read(tmp_path, rows=[{"before_img": "COPERNICUS/S2_SR_HARMONIZED/20260815T1", "before_img_info": AUG_15,
                                    "after_img": planet, "after_img_info": AUG_15}])
    assert gdf["before_img"].iloc[0].strip() == f"{image_sources.S2} 2026-08-15"
    assert gdf["after_img"].iloc[0] == planet


def test_blank_picks_take_text(tmp_path):
    # read_csv makes an all-blank column float: a review Save then writes a pick's text into it
    gdf, _ = _read(tmp_path)
    for col in ("before_img", "after_img"):
        assert gdf[col].dtype == object and gdf[col].isna().all()
        gdf.loc[0, col] = "Landsat 2026-10-07 "


def test_frame_survives_the_store(tmp_path):
    gdf, _ = _read(tmp_path)
    gdf["set_id"] = "s"
    save_gdf_to_parquet(gdf, tmp_path / "alert_db.parquet")
    back = load_gdf_from_parquet(tmp_path / "alert_db.parquet")
    assert list(back["status"]) == list(gdf["status"])
    assert list(back["alert_id"]) == [0, 1, 2, 3]
    assert back["alert_polygon"].iloc[1] is not None
