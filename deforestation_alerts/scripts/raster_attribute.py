"""Per-alert zonal attribution. Reads a (multiband) raster and adds one column
per selected band to the alerts gdf, using a chosen statistic over the pixels each
alert polygon covers. Pure rasterio/geopandas/numpy — no GEE, no UI (mirrors
vectorize.py). Generalizes vectorize._date_stats (min/max of one band) to a
configurable reducer over many bands, read via per-polygon windows so it scales
to national alert counts (cost ~ sum of polygon bbox areas, not n_polygons * grid)."""

import re

import rasterio

# Columns the assembled alerts gdf already carries (convert_to_geopandas +
# vectorize output). Attribution columns must not silently overwrite these.
ALERT_RESERVED_COLUMNS = {
    "geometry", "bounding_box", "point", "count", "area_ha",
    "alert_date_min", "alert_date_max", "alert_type_unique", "alert_sources",
    "status", "before_img", "before_img_info", "after_img", "after_img_info",
    "alert_polygon", "description", "admin1", "admin2", "admin3",
}


def safe_column(name):
    """Lowercase, collapse runs of non-alphanumerics to one underscore.

    Returns ``"band"`` for empty / None / blank input."""
    col = re.sub(r"[^0-9a-zA-Z]+", "_", str(name or "")).strip("_").lower()
    return col or "band"


def resolve_column_names(specs, reserved=()):
    """Return specs with each `column` made unique vs `reserved` + earlier specs
    (suffix `_2`, `_3`, …). Falls back to the band name when `column` is blank."""
    used = set(reserved)
    out = []
    for spec in specs:
        base = spec.get("column") or spec["band"]
        name = base
        k = 2
        while name in used:
            name = f"{base}_{k}"
            k += 1
        used.add(name)
        out.append({**spec, "column": name})
    return out


def band_names(raster_path):
    """List band names from a raster's descriptions, falling back to band_N."""
    with rasterio.open(raster_path) as src:
        return [
            desc if desc else f"band_{i}"
            for i, desc in enumerate(src.descriptions, start=1)
        ]
