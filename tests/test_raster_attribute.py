"""Tier-1 tests for per-alert raster attribution (no GEE, no UI)."""

import numpy as np
import rasterio
from rasterio.transform import from_origin

from deforestation_alerts.scripts import raster_attribute as ra


def _write_raster(path, bands, *, crs="EPSG:4326", res=1.0, lon0=0.0, lat0=10.0,
                  nodata=None, descriptions=None, dtype="float32"):
    """Write a (multiband) GeoTIFF; top-left origin (lon0, lat0), square pixels."""
    bands = [np.asarray(b, dtype=dtype) for b in bands]
    h, w = bands[0].shape
    transform = from_origin(lon0, lat0, res, res)
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=len(bands),
        dtype=dtype, crs=crs, transform=transform, nodata=nodata,
    ) as dst:
        for i, b in enumerate(bands, start=1):
            dst.write(b, i)
        if descriptions:
            dst.descriptions = tuple(descriptions)
    return str(path)


def test_safe_column():
    assert ra.safe_column("Forest/No Forest") == "forest_no_forest"
    assert ra.safe_column("band 1!!") == "band_1"
    assert ra.safe_column("") == "band"
    assert ra.safe_column(None) == "band"


def test_resolve_column_names_dedups_vs_reserved_and_each_other():
    specs = [
        {"band": "a", "stat": "min", "column": "count"},   # collides with reserved
        {"band": "b", "stat": "max", "column": "forest"},
        {"band": "c", "stat": "mean", "column": "forest"},  # collides with previous
    ]
    out = ra.resolve_column_names(specs, reserved={"count"})
    assert [s["column"] for s in out] == ["count_2", "forest", "forest_2"]


def test_band_names_reads_descriptions_with_fallback(tmp_path):
    path = _write_raster(
        tmp_path / "r.tif",
        [np.zeros((2, 2)), np.zeros((2, 2))],
        descriptions=["forest", None],
    )
    assert ra.band_names(path) == ["forest", "band_2"]
