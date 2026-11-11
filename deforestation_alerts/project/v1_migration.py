"""Convert a version 1 recipe (``recipe_parameters.json`` + ``alert_db.csv``,
written by the old Voila app) into a version 2 project, in place
(spec 2026-11-11 v1 recipe migration). No GUI imports."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd
from shapely import wkt
from shapely.errors import ShapelyError

from deforestation_alerts.project.review_status import NOT_REVIEWED
from deforestation_alerts.project.store_schema import DETECTED_AREA, GEOM_COLUMNS, STORE_COLUMNS
from deforestation_alerts.scripts.image_sources import LANDSAT, S2

RECIPE_FILE = "recipe_parameters.json"
TABLE_FILE = "alert_db.csv"

WGS84 = "EPSG:4326"
#: v1 columns holding text; read_csv makes an all-blank one float, the store keeps them as objects.
_TEXT_COLUMNS = ("status", "alert_sources", "before_img", "after_img", "description", "admin1", "admin2", "admin3")
#: v1 image id prefix -> the v2 timeline source it belongs to.
_PICK_SOURCES = (("COPERNICUS/S2", S2), ("LANDSAT/", LANDSAT))


def pixel_ha(sources: list[str]) -> float:
    """One alert pixel in hectares: 10 m when a Sentinel-2 based source
    (RADD, GLAD-S2) is in the set, else Landsat's 28 m."""
    return 0.01 if {"RADD", "GLAD-S2"} & set(sources) else 0.0784


def fill_areas(gdf: gpd.GeoDataFrame, sources: list[str]) -> None:
    """``detected_area_ha`` from the pixel count; ``area_ha`` keeps a v1
    value above 0 (a reviewer's drawn polygon), else takes the detected one."""
    gdf[DETECTED_AREA] = pd.to_numeric(gdf["count"], errors="coerce") * pixel_ha(sources)
    area = pd.to_numeric(gdf["area_ha"], errors="coerce")
    gdf["area_ha"] = area.where(area > 0, gdf[DETECTED_AREA])


def v2_pick(value, millis) -> Optional[str]:
    """v1's saved image (an Earth Engine id) and its time in milliseconds as
    the v2 pick ``"<source> <YYYY-MM-DD> "`` (the text the timeline writes),
    or ``None`` when the collection is not one the v2 timeline indexes."""
    if not isinstance(value, str):
        return None
    source = next((src for prefix, src in _PICK_SOURCES if value.startswith(prefix)), None)
    try:
        ms = float(millis)
    except (TypeError, ValueError):
        return None
    if source is None or pd.isna(ms):
        return None
    return f"{source} {datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()} "


def _parse_wkt(text, col: str, i: int, notes: list[str]):
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return wkt.loads(text)
    except ShapelyError:
        notes.append(f"alert {i}: unreadable {col}, left empty")
        return None


def _codes(value):
    """``alert_type_unique`` as v1 wrote it (``"[1, 2]"``) back to the list v2 stores."""
    return json.loads(value) if isinstance(value, str) else value


def read_table(csv_path: Path) -> tuple[gpd.GeoDataFrame, list[str]]:
    """v1's ``alert_db.csv`` as the frame a v2 store holds (``alert_id`` from
    0, ``geometry`` = the bounding box, every store column but ``set_id``),
    and notes on what could not be read. Call ``fill_areas`` before saving it."""
    notes: list[str] = []
    df = pd.read_csv(csv_path)
    df = df.drop(columns=[c for c in df.columns if c.startswith("Unnamed:")] + ["gee_id"], errors="ignore")
    df["alert_id"] = range(len(df))
    # columns v1 lacks (radar picks, review stamps, ...) are added empty, as an older v2 store reads
    for col in STORE_COLUMNS:
        if col not in df.columns and col not in ("geometry", "set_id"):
            df[col] = pd.Series(None, index=df.index, dtype=object)
    for col in _TEXT_COLUMNS:
        df[col] = df[col].astype(object)
    status = df["status"]
    df["status"] = status.where(status.notna() & (status.astype(str).str.strip() != ""), NOT_REVIEWED)
    df["alert_type_unique"] = df["alert_type_unique"].map(_codes)
    for side in ("before", "after"):
        df[f"{side}_img"] = [v2_pick(v, ms) or v for v, ms in zip(df[f"{side}_img"], df[f"{side}_img_info"])]
    geoms = {col: gpd.GeoSeries([_parse_wkt(v, col, i, notes) for i, v in enumerate(df[col])], index=df.index, crs=WGS84)
             for col in GEOM_COLUMNS[1:]}
    gdf = gpd.GeoDataFrame(df.drop(columns=list(geoms)), geometry=geoms["bounding_box"].copy())
    for col, series in geoms.items():
        gdf[col] = series
    return gdf, notes
