"""A version 1 recipe folder (``recipe_parameters.json`` + ``alert_db.csv``)
as the old app wrote it, for the v1 migration tests."""

import json
from pathlib import Path

import pandas as pd
from shapely.geometry import box

from deforestation_alerts.project.review_status import CONFIRMED, FALSE_POSITIVE, NEED_REVISION, NOT_REVIEWED

STATUSES = (NOT_REVIEWED, CONFIRMED, FALSE_POSITIVE, NEED_REVISION)
#: The drawn polygon of the default row 1.
DRAWN = "POLYGON ((0.11 0.01, 0.14 0.01, 0.14 0.04, 0.11 0.04, 0.11 0.01))"

#: ``recipe_2026-09-29-223039``'s parameters (spec "Facts the builder needs").
PARAMS = {
    "ccdc_layer": None, "mask_layer": None, "aux_layer": None, "aux_layer_vis": None, "custom_report_template": "",
    "aoi_admin": "665", "aoi_asset_name": None, "aoi_name": "BRA_Acre", "aoi_method": "ADMIN1",
    "aoi_asset_json": None, "aoi_vector_json": None, "aoi_geo_json": {"type": "FeatureCollection", "features": []},
    "start_date": "2026-08-01", "end_date": "2026-08-31", "selected_alert_sources": ["GLAD-L"],
    "alert_selection_polygons": {"type": "FeatureCollection", "features": []}, "min_area": 0.5,
    "max_number_alerts": 0, "alert_selection_area": 2, "alert_sorting_method": 1, "actual_alert_id": 0,
    "max_alert_id": 5000, "last_save_time": 0.0, "current_page_view": "filter_alerts",
    "temporary_recipe_name": "recipe_2026-09-29-223039", "recipe_name": "recipe_2026-09-29-223039",
    "recipe_folder_path": "",
}


def v1_row(i: int) -> dict:
    """Row ``i`` of a v1 ``alert_db.csv``, geometries as WKT."""
    bbox = box(i * 0.1, 0.0, i * 0.1 + 0.05, 0.05)
    return {"alert_date_max": 2026.62, "alert_date_min": 2026.6, "alert_type_unique": "[0]", "count": 100,
            "gee_id": f"-{i}-1", "bounding_box": bbox.wkt, "point": bbox.centroid.wkt,
            "status": STATUSES[i % 4], "alert_sources": "GLAD-L", "before_img": None, "before_img_info": None,
            "after_img": None, "after_img_info": None, "alert_polygon": DRAWN if i == 1 else None,
            "area_ha": 1.5 if i == 1 else None, "description": None, "admin1": None, "admin2": None,
            "admin3": None}


def write_recipe(folder: Path, *, rows: list[dict] | None = None, **params) -> Path:
    """Write both v1 files into ``folder``; returns it. ``rows`` (one dict per
    alert) override the default row of the same position; 4 rows by default."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rows = [{}] * 4 if rows is None else rows
    pd.DataFrame([{**v1_row(i), **over} for i, over in enumerate(rows)]).to_csv(folder / "alert_db.csv", index=False)
    recipe = {**PARAMS, "recipe_name": folder.name, "recipe_folder_path": str(folder), **params}
    (folder / "recipe_parameters.json").write_text(json.dumps(recipe), encoding="utf-8")
    return folder
