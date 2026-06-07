"""Tier-1 tests for the local raster→vector pipeline (no GEE, no UI)."""

import inspect

from deforestation_alerts.scripts import raster_export
from deforestation_alerts.scripts.geo_utils import (
    get_utm_proj_str_from_lat_lon,
    optimal_block_shape,
)


def test_download_ee_image_exposes_expected_params():
    sig = inspect.signature(raster_export.download_ee_image)
    for p in ("image", "filename", "region", "scale", "crs",
              "unmask_value", "nodata_value"):
        assert p in sig.parameters, f"download_ee_image missing param {p}"


def test_utm_proj_northern_and_southern():
    # London ~ (51.5N, 0W) -> UTM 31N -> 32631
    assert get_utm_proj_str_from_lat_lon(0.0, 51.5) == "EPSG:32631"
    # Rio ~ (-22.9S, -43.2W) -> UTM 23S -> 32723
    assert get_utm_proj_str_from_lat_lon(-43.2, -22.9) == "EPSG:32723"


def test_utm_proj_polar_caps():
    assert get_utm_proj_str_from_lat_lon(10.0, 85.0) == "EPSG:5041"   # UPS North
    assert get_utm_proj_str_from_lat_lon(10.0, -82.0) == "EPSG:5042"  # UPS South


def test_optimal_block_shape_bounds_rows_by_ram():
    # 10000x10000, 1 band, 1 GB at 70% -> rows bounded, cols = full width
    rows, cols = optimal_block_shape(10000, 10000, 1, ram_gb=1.0)
    assert cols == 10000
    assert 1 <= rows <= 10000


import numpy as np
import rasterio
from rasterio.transform import from_origin

from deforestation_alerts.scripts.vectorize import vectorize_alert_raster


def _write_two_band(tmp_path, alert, date, *, res=0.01, lon0=10.0, lat0=0.0):
    """Write a 2-band GeoTIFF (band1=alert code, band2=date YYYY.jjj) in EPSG:4326."""
    h, w = alert.shape
    transform = from_origin(lon0, lat0, res, res)  # top-left origin
    path = tmp_path / "alerts.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=2,
        dtype="float32", crs="EPSG:4326", transform=transform, nodata=255,
    ) as dst:
        dst.write(alert.astype("float32"), 1)
        dst.write(date.astype("float32"), 2)
    return str(path)


def test_sieve_drops_subthreshold_clusters(tmp_path):
    # A 1-pixel cluster (code 1) and a 4-pixel cluster (code 10), well separated.
    alert = np.zeros((6, 6), dtype="float32")
    alert[0, 0] = 1                      # 1-pixel speckle
    alert[3:5, 3:5] = 10                 # 4-pixel cluster
    date = np.where(alert > 0, 2023.100, 255).astype("float32")
    path = _write_two_band(tmp_path, alert, date)

    gdf = vectorize_alert_raster(path, min_cluster_size=4, connectivity=8)

    # Only the 4-pixel cluster survives the sieve.
    assert len(gdf) == 1
    assert int(gdf.iloc[0]["alert_type_unique"][0]) == 10
    assert gdf.iloc[0]["count"] == 4


def test_each_cluster_becomes_one_polygon_with_codes_and_dates(tmp_path):
    alert = np.zeros((8, 8), dtype="float32")
    alert[1:3, 1:3] = 1     # cluster A, code 1 (GLAD-L)
    alert[5:7, 5:7] = 100   # cluster B, code 100 (GLAD-S2)
    date = np.full((8, 8), 255, dtype="float32")
    date[1:3, 1:3] = 2022.050
    date[5:7, 5:7] = 2023.200
    path = _write_two_band(tmp_path, alert, date)

    gdf = vectorize_alert_raster(path, min_cluster_size=1, connectivity=8)

    assert len(gdf) == 2
    codes = sorted(int(r[0]) for r in gdf["alert_type_unique"])
    assert codes == [1, 100]
    a = gdf[gdf["alert_type_unique"].apply(lambda x: x[0] == 1)].iloc[0]
    assert a["alert_date_min"] == a["alert_date_max"]
    assert a["geometry"].geom_type == "Polygon"


def test_area_filter_drops_small_polygons(tmp_path):
    # At the equator a 0.01-deg pixel is ~123 ha; a single pixel is ~123 ha and the
    # 4x4 cluster ~1968 ha, so a 200-ha threshold drops the speckle, keeps the block.
    alert = np.zeros((10, 10), dtype="float32")
    alert[0, 0] = 1            # one ~123-ha pixel (res 0.01 deg at the equator)
    alert[4:8, 4:8] = 1        # 16 pixels (~1968 ha)
    date = np.where(alert > 0, 2023.0, 255).astype("float32")
    path = _write_two_band(tmp_path, alert, date)

    gdf = vectorize_alert_raster(path, min_cluster_size=1, min_area_ha=200.0)
    assert len(gdf) == 1
    assert gdf.iloc[0]["count"] == 16


def test_empty_raster_returns_empty_gdf(tmp_path):
    alert = np.zeros((5, 5), dtype="float32")
    date = np.full((5, 5), 255, dtype="float32")
    path = _write_two_band(tmp_path, alert, date)
    gdf = vectorize_alert_raster(path, min_cluster_size=1)
    assert len(gdf) == 0
    assert "area_ha" in gdf.columns


def test_nodata_fill_is_not_read_as_an_alert(tmp_path):
    # The download unmasks masked pixels to 255 (== nodata) in BOTH bands. A block
    # of 255s in the alert band must be treated as background, not as code-255 alerts.
    alert = np.zeros((6, 6), dtype="float32")
    alert[0:2, 0:2] = 255                 # masked-out fill (nodata) -> not an alert
    alert[3:5, 3:5] = 10                  # a real 4-pixel cluster
    date = np.full((6, 6), 255, dtype="float32")
    date[3:5, 3:5] = 2023.100
    path = _write_two_band(tmp_path, alert, date)

    gdf = vectorize_alert_raster(path, min_cluster_size=1, connectivity=8)

    assert len(gdf) == 1
    assert int(gdf.iloc[0]["alert_type_unique"][0]) == 10


def test_blockwise_stitches_cluster_across_block_boundary(tmp_path):
    # one 4x2 cluster (code 1) spanning rows 3-6; force a block split at row 4
    alert = np.zeros((8, 4), dtype="float32")
    alert[3:7, 1:3] = 1
    date = np.where(alert > 0, 2023.0, 255).astype("float32")
    path = _write_two_band(tmp_path, alert, date)

    gdf = vectorize_alert_raster(
        path, min_cluster_size=1, connectivity=8, block_rows=4
    )
    assert len(gdf) == 1               # stitched, not two block fragments
    assert gdf.iloc[0]["count"] == 8
    assert int(gdf.iloc[0]["alert_type_unique"][0]) == 1
