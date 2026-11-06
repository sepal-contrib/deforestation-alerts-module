import numpy as np
import pytest

from deforestation_alerts.engine import raster_io
from deforestation_alerts.engine.vectorize import COLUMNS, vectorize_alerts, yyyyddd_to_decimal
from deforestation_alerts.project.models import GridSpec

GRID = GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=8900000, width=40, height=40)


def _raster(tmp_path, alert, date=None, name="a.tif", grid=GRID):
    out = tmp_path / name
    date = np.zeros_like(alert) if date is None else date
    with raster_io.write_grid_raster(out, grid, count=2) as dst:
        dst.write(alert.astype("int32"), 1)
        dst.write(date.astype("int32"), 2)
    return out


def _blank():
    return np.zeros((GRID.height, GRID.width), dtype="int64")


def test_decimal_dates():
    assert yyyyddd_to_decimal(2026045) == pytest.approx(2026.045)
    assert yyyyddd_to_decimal(2024366) == pytest.approx(2024.366)


def test_min_cluster_size_filters_small_components(tmp_path):
    a = _blank()
    a[2:4, 2:4] = 1      # 4 px, dropped at min 5
    a[10:12, 10:13] = 1  # 6 px, kept
    gdf = vectorize_alerts(_raster(tmp_path, a), min_cluster_size=5)
    assert len(gdf) == 1 and gdf.iloc[0]["count"] == 6


def test_cluster_split_by_block_edge_is_kept(tmp_path):
    a = _blank()
    a[17:23, 5] = 1  # 6 px vertical line across the row-20 block edge: 3 + 3
    gdf = vectorize_alerts(_raster(tmp_path, a), min_cluster_size=5, block_rows=20)
    assert len(gdf) == 1 and gdf.iloc[0]["count"] == 6


def test_diagonal_pixels_are_one_component_with_8_connectivity(tmp_path):
    a = _blank()
    for i in range(5):
        a[5 + i, 5 + i] = 1  # 5 diagonal pixels
    gdf = vectorize_alerts(_raster(tmp_path, a), min_cluster_size=5)
    assert len(gdf) == 1 and gdf.iloc[0]["count"] == 5
    assert len(vectorize_alerts(_raster(tmp_path, a, name="b.tif"), min_cluster_size=5, connectivity=4)) == 0


def test_mixed_codes_in_one_cluster_are_one_alert(tmp_path):
    a = _blank()
    a[5:8, 5:8] = 1     # 9 px GLAD-L
    a[5:8, 8:11] = 10   # 9 px RADD, touching: one 18 px cluster
    gdf = vectorize_alerts(_raster(tmp_path, a), min_cluster_size=5)
    assert len(gdf) == 1
    row = gdf.iloc[0]
    assert row["count"] == 18 and row["alert_type_unique"] == [1, 10]
    assert row["area_ha"] == pytest.approx(18 * 100 / 10_000)


def test_nine_digit_code_survives(tmp_path):
    a = _blank()
    a[5:8, 5:8] = 222222222
    gdf = vectorize_alerts(_raster(tmp_path, a), min_cluster_size=1)
    assert gdf.iloc[0]["alert_type_unique"] == [222222222]


def test_dates_per_region_and_crs_and_area(tmp_path):
    a, d = _blank(), _blank()
    a[5:7, 5:8] = 2
    d[5:7, 5:8] = 2026010
    d[5, 5] = 2026003
    gdf = vectorize_alerts(_raster(tmp_path, a, d), min_cluster_size=1)
    row = gdf.iloc[0]
    assert row["alert_date_min"] == pytest.approx(2026.003)
    assert row["alert_date_max"] == pytest.approx(2026.010)
    assert gdf.crs.to_epsg() == 32721
    assert row["area_ha"] == pytest.approx(6 * 100 / 10_000)


def test_empty_raster_gives_empty_frame(tmp_path):
    gdf = vectorize_alerts(_raster(tmp_path, _blank()), min_cluster_size=1)
    assert len(gdf) == 0 and "alert_type_unique" in gdf.columns


def _rows(gdf):
    cols = ["count", "alert_date_min", "alert_date_max", "area_ha"]
    return sorted(tuple(round(float(v), 6) for v in r) for r in gdf[cols].itertuples(index=False))


def test_region_cut_at_a_block_edge_matches_the_unblocked_result(tmp_path):
    # A 6 px diagonal whose halves (3 + 3) meet only at a pixel corner on the
    # row-20 block edge; each pixel has its own date.
    a, d = _blank(), _blank()
    for i in range(6):
        a[17 + i, 5 + i] = 1
        d[17 + i, 5 + i] = 2026001 + i
    path = _raster(tmp_path, a, d)

    blocked = vectorize_alerts(path, min_cluster_size=5, block_rows=20)
    assert len(blocked) == 1
    row = blocked.iloc[0]
    assert row["count"] == 6
    assert row["alert_date_min"] == pytest.approx(2026.001)
    assert row["alert_date_max"] == pytest.approx(2026.006)
    assert _rows(blocked) == _rows(vectorize_alerts(path, min_cluster_size=5))

    # 4-connectivity does not follow a shared corner: six 1 px regions either way.
    blocked4 = vectorize_alerts(path, min_cluster_size=1, connectivity=4, block_rows=20)
    assert list(blocked4["count"]) == [1] * 6
    assert _rows(blocked4) == _rows(vectorize_alerts(path, min_cluster_size=1, connectivity=4))


def test_block_edge_stitch_holds_for_a_fractional_pixel_size(tmp_path):
    # A 4.77 m grid snapped like grid.build_grid. In world coordinates the
    # row-10 edge of block 0 and the top of block 1 differ in the last bit, so
    # the two halves would not touch; stitching must not depend on that.
    grid = GridSpec(
        epsg="EPSG:32721", resolution=4.77, xmin=500000.94, ymax=8900004.33, width=40, height=40
    )
    a = _blank()
    a[3:37, 5] = 1  # 34 px vertical line crossing the edges at rows 10, 20 and 30
    path = _raster(tmp_path, a, grid=grid)
    gdf = vectorize_alerts(path, min_cluster_size=1, block_rows=10)
    assert len(gdf) == 1 and gdf.iloc[0]["count"] == 34
    assert gdf.iloc[0]["area_ha"] == pytest.approx(34 * 4.77**2 / 10_000)


def test_the_minimum_gates_the_whole_cluster_not_its_parts(tmp_path):
    a = _blank()
    a[5:10, 5:10] = 1  # 25 px GLAD-L
    a[5, 10] = 10      # 1 px RADD touching it: one 26 px cluster, kept whole
    a[20:22, 20] = 1   # 2 px GLAD-L
    a[20:22, 21] = 10  # 2 px RADD touching it: one 4 px cluster, dropped whole
    gdf = vectorize_alerts(_raster(tmp_path, a), min_cluster_size=5)
    assert len(gdf) == 1
    assert gdf.iloc[0]["count"] == 26 and gdf.iloc[0]["alert_type_unique"] == [1, 10]


def test_a_mixed_cluster_cut_by_a_block_edge_is_one_alert(tmp_path):
    # GLAD-L above the row-20 block edge, RADD below it, touching across it;
    # the two codes and the dates of both halves land on one row.
    a, d = _blank(), _blank()
    a[17:20, 5:8], d[17:20, 5:8] = 1, 2026010
    a[20:23, 5:8], d[20:23, 5:8] = 10, 2026020
    d[17, 5] = 2026002
    path = _raster(tmp_path, a, d)
    blocked = vectorize_alerts(path, min_cluster_size=5, block_rows=20)
    assert len(blocked) == 1
    row = blocked.iloc[0]
    assert row["count"] == 18 and row["alert_type_unique"] == [1, 10]
    assert row["alert_date_min"] == pytest.approx(2026.002)
    assert row["alert_date_max"] == pytest.approx(2026.020)
    assert row.geometry.geom_type == "Polygon"
    assert _rows(blocked) == _rows(vectorize_alerts(path, min_cluster_size=5))


def test_a_cluster_that_meets_itself_only_in_a_later_block_is_one_alert(tmp_path):
    # A U shape: its two arms are separate in the top block and join only in
    # the bottom one, so the top block sees two pieces of one cluster.
    a = _blank()
    a[10:25, 5] = 1
    a[10:25, 9] = 10
    a[24, 5:10] = 100
    gdf = vectorize_alerts(_raster(tmp_path, a), min_cluster_size=5, block_rows=20)
    assert len(gdf) == 1
    assert gdf.iloc[0]["count"] == 33 and gdf.iloc[0]["alert_type_unique"] == [1, 10, 100]


def _mask(tmp_path, arr, name="mask.tif", grid=GRID):
    out = tmp_path / name
    with raster_io.write_grid_raster(out, grid, count=1) as dst:
        dst.write(arr.astype("int32"), 1)
    return out


def test_small_cluster_touching_mask_is_kept(tmp_path):
    a, m = _blank(), _blank()
    a[5, 5:7] = 1  # 2 px, under the minimum
    m[5, 6] = 1
    gdf = vectorize_alerts(_raster(tmp_path, a), min_cluster_size=5, touch_mask=_mask(tmp_path, m))
    assert len(gdf) == 1
    assert gdf.iloc[0]["count"] == 2 and bool(gdf.iloc[0]["below_min"]) is True


def test_small_cluster_off_mask_is_dropped(tmp_path):
    a, m = _blank(), _blank()
    a[5, 5:7] = 1
    m[5, 8] = 1  # two pixels away
    gdf = vectorize_alerts(_raster(tmp_path, a), min_cluster_size=5, touch_mask=_mask(tmp_path, m))
    assert len(gdf) == 0 and "below_min" in gdf.columns


def test_big_cluster_on_mask_is_not_below_min(tmp_path):
    a, m = _blank(), _blank()
    a[10:12, 10:13] = 1  # 6 px
    m[10, 10] = 1
    gdf = vectorize_alerts(_raster(tmp_path, a), min_cluster_size=5, touch_mask=_mask(tmp_path, m))
    assert len(gdf) == 1 and bool(gdf.iloc[0]["below_min"]) is False


def test_touch_found_across_blocks(tmp_path):
    a, m = _blank(), _blank()
    a[7:10, 5] = 1  # 3 px vertical line across the row-8 block edge: 1 + 2
    m[9, 5] = 1     # the mask meets it only in the second block
    gdf = vectorize_alerts(_raster(tmp_path, a), min_cluster_size=5, block_rows=8,
                           touch_mask=_mask(tmp_path, m))
    assert len(gdf) == 1
    assert gdf.iloc[0]["count"] == 3 and bool(gdf.iloc[0]["below_min"]) is True


def test_no_mask_keeps_old_columns(tmp_path):
    a = _blank()
    a[10:12, 10:13] = 1
    gdf = vectorize_alerts(_raster(tmp_path, a), min_cluster_size=5)
    assert list(gdf.columns) == COLUMNS


def test_mask_off_the_alert_grid_is_refused(tmp_path):
    other = GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=8900000, width=30, height=40)
    a = _blank()
    a[5, 5:7] = 1
    mask = _mask(tmp_path, np.zeros((other.height, other.width)), grid=other)
    with pytest.raises(raster_io.GridMismatchError):
        vectorize_alerts(_raster(tmp_path, a), min_cluster_size=5, touch_mask=mask)
