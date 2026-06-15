"""Per-alert zonal attribution. Reads a (multiband) raster and adds one column
per selected band to the alerts gdf, using a chosen statistic over the pixels each
alert polygon covers. Pure rasterio/geopandas/numpy — no GEE, no UI (mirrors
vectorize.py). Generalizes vectorize._date_stats (min/max of one band) to a
configurable reducer over many bands, read via per-polygon windows so it scales
to national alert counts (cost ~ sum of polygon bbox areas, not n_polygons * grid)."""

import re

import numpy as np
import rasterio
from rasterio import features

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


def _majority(values):
    """Most frequent value; ties broken toward the smallest value."""
    uniq, counts = np.unique(values, return_counts=True)
    top = counts.max()
    # uniq is sorted ascending, so the first max-count entry is the smallest value.
    return uniq[counts == top][0]


_REDUCERS = {
    "majority": _majority,
    "mean": np.mean,
    "median": np.median,
    "min": np.min,
    "max": np.max,
}


def _resolve_band_index(src, band_name):
    """Map a band name to its 1-based index: match a description, else band_N."""
    descs = [d if d else f"band_{i}" for i, d in enumerate(src.descriptions, start=1)]
    if band_name in descs:
        return descs.index(band_name) + 1
    raise ValueError(
        f"Attribution band {band_name!r} not found in raster (have {descs})"
    )


def attribute_alerts(gdf, raster_path, band_stats):
    """Return a copy of `gdf` with one new column per `band_stats` entry.

    `band_stats`: list of ``{"band": <name>, "stat": <reducer>, "column": <out>}``.
    For each alert polygon: read only the raster window under its bounds, mask to
    the polygon, drop nodata / non-finite pixels, apply the reducer. Empty
    coverage -> ``None``. Polygons are reprojected to the raster CRS for masking;
    the returned gdf keeps the original CRS/geometry with the new columns appended.
    Raises ValueError if the raster has no CRS or a requested band is missing.
    """
    out = gdf.copy()
    if not band_stats:
        return out

    with rasterio.open(raster_path) as src:
        if src.crs is None:
            raise ValueError(f"Attribution raster has no CRS: {raster_path}")
        nodata = src.nodata

        # Resolve band->index + reducer up front (fail loud on bad config).
        resolved = [
            (spec["column"], _resolve_band_index(src, spec["band"]),
             _REDUCERS[spec["stat"]])
            for spec in band_stats
        ]

        if out.empty:
            for col, _, _ in resolved:
                out[col] = []
            return out

        # Reproject polygons to the raster CRS once for masking.
        geoms = gdf.geometry
        if gdf.crs is not None and gdf.crs != src.crs:
            geoms = geoms.to_crs(src.crs)

        columns = {col: [] for col, _, _ in resolved}
        for geom in geoms:
            shape = geom.__geo_interface__
            try:
                window = features.geometry_window(src, [shape])
            except Exception:
                window = None
            if window is None or window.width == 0 or window.height == 0:
                for col, _, _ in resolved:
                    columns[col].append(None)
                continue
            win_transform = src.window_transform(window)
            mask = features.geometry_mask(
                [shape], out_shape=(int(window.height), int(window.width)),
                transform=win_transform, invert=True,  # True INSIDE the polygon
            )
            for col, idx, reducer in resolved:
                band = src.read(idx, window=window)
                vals = band[mask]
                vals = vals[np.isfinite(vals)]
                if nodata is not None:
                    vals = vals[vals != nodata]
                columns[col].append(reducer(vals) if vals.size else None)

    for col, _, _ in resolved:
        out[col] = columns[col]
    return out
