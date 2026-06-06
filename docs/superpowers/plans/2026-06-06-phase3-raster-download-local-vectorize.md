# Phase 3: Raster Download + Local Vectorization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the GEE server-side vector-alert pipeline (`reduceToVectors` +
`getInfo` + grid-fallback) with **raster download → local vectorization**, producing
an `alerts_gdf` with true pixel polygons (D2), a back-compat bounding-box column, and
a first-class cluster-size filter (D11) — while preserving the exact integer
alert-code encoding and keeping the recipe round-trip green.

**Architecture:** Keep Stages A/B (build + merge the alert `ee.Image`). After them,
download the filtered alert raster to a local GeoTIFF (`raster_export.py`, copied
near-verbatim from spatial-risk-module's geedim-2.0 exporter), vectorize it locally
(`vectorize.py`: `features.sieve` cluster filter → `features.shapes` → shapely
polygons → optional area filter), then assemble the GeoDataFrame by re-pointing the
existing `convert_to_geopandas`. The fragile GEE-vector machinery in
`alert_filter_helper.py` is deleted. This is done on the **stable traitlets base**
(still Voila) so the data change is testable in isolation before the Solara rewrite
(Phase 4).

**Tech Stack:** Python, Earth Engine, **geedim ≥2.0** (the `.gd` accessor),
rasterio (`features.sieve`/`features.shapes`), geopandas/shapely, pytest. No Solara
(Phase 4).

**Prerequisite:** Phase 2 complete — package is `deforestation_alerts/`, views are
`gui/`, `tests/` is green. All paths below use the post-Phase-2 layout.

**Scope note:** Third of the per-phase plans derived from
`docs/superpowers/specs/2026-06-06-pysepal-solara-migration-design.md` (spec §6 + §7
Tier-2 + §8 Phase 3; D2, D4, D11). Phase 4 (Solara) is separate.

---

## The column contract (decisions locked here)

The single biggest correctness risk is the shape of `alerts_gdf`. We lock it now so
every task is checked against the same target. **Today** `convert_to_geopandas`
returns a gdf whose active geometry is the centroid `point`, with the bounding box in
a `bounding_box` column. **After this phase:**

| Column | Type | Source | Notes |
|---|---|---|---|
| `geometry` | Polygon (EPSG:4326) | local vectorize | **NEW active geometry** = true pixel-cluster polygon (D2). |
| `bounding_box` | Polygon | `geometry.envelope` | Back-compat: reproduces the old bbox shape current consumers/recipes read (D2/D10). **Keep this exact name.** |
| `point` | Point | `geometry.centroid` | Unchanged role (centroid). |
| `alert_type_unique` | list[int] | vectorize | The packed alert-code value(s) in the cluster; decoded by `get_unique_alerts`. |
| `alert_sources` | str | `format_list(get_unique_alerts(...))` | Unchanged derivation. |
| `count` | int | vectorize | Pixel count of the cluster. |
| `alert_date_min`, `alert_date_max` | str (`YYYY-MM-DD`) or float | vectorize | From the raster `date` band over the cluster. |
| `area_ha` | float | vectorize | Polygon area in a metric CRS. (Was empty before; now populated.) |
| `status`, `before_img`, `before_img_info`, `after_img`, `after_img_info`, `alert_polygon`, `description`, `admin1`, `admin2`, `admin3` | per old defaults | `convert_to_geopandas` | Unchanged empty/user-filled columns. |

**Decision D2-naming:** D2 calls it a "`bbox` column"; we realize it as the existing
`bounding_box` column name so current consumers and saved recipes keep working
unchanged. (If a true `bbox` alias is wanted later, add it as a copy — not in scope.)

**Decision (recipe round-trip):** because the active geometry is now a Polygon, the
recipe CSV save/load must serialize/decode the `geometry` column too. Old recipes
(no polygon column) must still load — `geometry` is backfilled from `bounding_box`
when absent (D10 back-compat). Covered in Task 8.

---

## File structure (created/modified in this plan)

| File | Action | Responsibility |
|---|---|---|
| `requirements.txt`, `sepal_environment.yml` | Modify | `geedim==1.8.1` → `geedim>=2.0.0`. |
| `deforestation_alerts/scripts/geo_utils.py` | Create | `get_utm_proj_str_from_lat_lon`, `optimal_block_shape` (copied from srm). |
| `deforestation_alerts/scripts/raster_export.py` | Create | `download_ee_image(...)` (geedim-2.0 `.gd` exporter, copied from srm). |
| `deforestation_alerts/scripts/vectorize.py` | Create | `vectorize_alert_raster(...)`: sieve cluster filter → shapes → polygons → area filter; block-wise path for national scale. |
| `deforestation_alerts/scripts/alert_filter_helper.py` | Modify | **Delete** the GEE-vector machinery; **re-point** `convert_to_geopandas` to the local vectorize gdf (+ `bounding_box`/`point`). |
| `deforestation_alerts/scripts/mosaics_helper.py` | Modify | Re-point `download_both_images` from `geemap.download_ee_image` to `raster_export.download_ee_image` (geedim-2.0 safe). |
| `gui/tile/alerts_filter_tile.py` | Modify | Replace `start_thread_full`/`start_thread_partial` + `obtener_datos_gee_*` with download → vectorize → assemble. |
| `deforestation_alerts/scripts/recipe_helper.py` | Modify | Serialize/decode the new `geometry` polygon column; backfill from `bounding_box` for old recipes. |
| `tests/test_vectorize.py` | Create | Tier-1: sieve cluster filter, shapes→polygons, area filter, alert-code/date preservation, block-wise stitch. |
| `tests/test_convert_to_geopandas.py` | Create | Tier-1: column contract + alert-code decode on a fixture gdf. |
| `tests/test_recipe_geometry_roundtrip.py` | Create | Tier-1: polygon CSV round-trip + old-recipe backfill. |
| `tests/test_raster_pipeline_integration.py` | Create | Tier-2: skip-if-no-creds; tiny AOI download → vectorize → assert columns/codes; alert-code dtype survives export. |
| `tests/conftest.py` | Modify | Add the `ee`-init/skip fixtures for Tier-2. |
| `.github/workflows/ci.yaml` | Modify | Ensure Tier-1 runs offline; Tier-2 skips without creds. |

---

# TASKS

### Task 1: Bump geedim and confirm the `.gd` accessor

**Files:**
- Modify: `requirements.txt`, `sepal_environment.yml`

- [ ] **Step 1: Bump the pin in `requirements.txt`**

Change `geedim==1.8.1` to:
```
geedim>=2.0.0
```

- [ ] **Step 2: Bump the matching pin in `sepal_environment.yml`**

Change the `geedim==1.8.1` line to `geedim>=2.0.0` (keep the same indentation as the surrounding `pip:` entries).

- [ ] **Step 3: Install and confirm the geedim-2.0 accessor exists**

```bash
pip install "geedim>=2.0.0"
python -c "import geedim, ee; print('geedim', geedim.__version__); \
assert hasattr(ee.Image(1), 'gd'), 'no .gd accessor — geedim<2.0'; print('.gd accessor ok')"
```
Expected: prints a `2.x` version and `.gd accessor ok`. If the import registers the accessor lazily, `import geedim` first (done above).

- [ ] **Step 4: Confirm the earthengine fork is still intact**

Run: `python -c "import ee; print(ee.__version__)"`
Expected: `1.1.5rc0` (the pinned fork from Phase 1). If geedim's deps changed it, reinstall the fork line from `requirements.txt` and re-check.

- [ ] **Step 5: Run the existing suite under geedim 2.0**

Run: `pytest tests/ -v`
Expected: PASS (no code uses geedim yet; this just proves the bump didn't break the import graph).

- [ ] **Step 6: Commit**

```bash
git add requirements.txt sepal_environment.yml
git commit -m "build: bump geedim 1.8.1 -> >=2.0.0 for the .gd export accessor"
```

> **Risk note (geemap):** `mosaics_helper.download_both_images` currently calls
> `geemap.download_ee_image`, which wraps geedim and may break under geedim 2.0.
> Task 5 re-points it to our own `download_ee_image`. Until then, the CNN image
> download path may be broken — that is expected and resolved in Task 5.

---

### Task 2: Add `geo_utils.py` (metric CRS + block sizing)

Pure helpers copied from spatial-risk-module. No GEE, fully unit-testable.

**Files:**
- Create: `deforestation_alerts/scripts/geo_utils.py`
- Create: `tests/test_vectorize.py` (start the file with these helper tests)

- [ ] **Step 1: Write failing tests for the helpers**

Create `tests/test_vectorize.py`:
```python
"""Tier-1 tests for the local raster→vector pipeline (no GEE, no UI)."""

from deforestation_alerts.scripts.geo_utils import (
    get_utm_proj_str_from_lat_lon,
    optimal_block_shape,
)


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
```

- [ ] **Step 2: Run — expect FAIL (module missing)**

Run: `pytest tests/test_vectorize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deforestation_alerts.scripts.geo_utils'`.

- [ ] **Step 3: Create `deforestation_alerts/scripts/geo_utils.py`**

```python
"""Geo helpers copied from spatial-risk-module (srm/spatialrisk/geo_utils.py,
utilities/resources.py). Pure functions: no GEE, no UI."""


def get_utm_proj_str_from_lat_lon(lon, lat):
    """Return the EPSG string of the UTM/UPS zone containing (lon, lat) in WGS84."""
    if lat >= 84:
        return "EPSG:5041"  # UPS North
    if lat <= -80:
        return "EPSG:5042"  # UPS South

    # Norway / Svalbard exceptions
    if 55 < lat < 64 and 2 < lon < 6:
        zone_number = 32
    elif lat > 71 and 6 <= lon < 9:
        zone_number = 31
    elif lat > 71 and ((9 <= lon < 12) or (18 <= lon < 21)):
        zone_number = 33
    elif lat > 71 and ((21 <= lon < 24) or (30 <= lon < 33)):
        zone_number = 35
    else:
        zone_number = int((lon + 180) / 6) + 1

    epsg_code = (32600 if lat >= 0 else 32700) + zone_number
    return f"EPSG:{epsg_code}"


def optimal_block_shape(ny, nx, nband, ram_gb, ram_fraction=0.7):
    """Return (blk_rows, blk_cols) for memory-bounded sequential reading (float32)."""
    target_gb = ram_gb * ram_fraction
    bytes_per_row = nx * nband * 4  # float32
    max_rows = int((target_gb * 1024**3) / bytes_per_row)
    max_rows = max(1, min(ny, max_rows))
    return max_rows, nx
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_vectorize.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add deforestation_alerts/scripts/geo_utils.py tests/test_vectorize.py
git commit -m "feat: add geo_utils (UTM zone + optimal block shape) from srm"
```

---

### Task 3: Add `raster_export.py` (geedim-2.0 download)

Copied near-verbatim from `srm/spatialrisk/gee/ee_raster_export.py`. The real
download is exercised by the Tier-2 integration test (Task 9); here we only add the
module and a signature guard (no GEE call).

**Files:**
- Create: `deforestation_alerts/scripts/raster_export.py`
- Modify: `tests/test_vectorize.py` (add a signature guard)

- [ ] **Step 1: Create `deforestation_alerts/scripts/raster_export.py`**

```python
"""Download an Earth Engine image to a local GeoTIFF using the geedim 2.0 `.gd`
accessor. Copied near-verbatim from spatial-risk-module
(srm/spatialrisk/gee/ee_raster_export.py)."""

from typing import Any, Optional, Union

import ee


def download_ee_image(
    image: ee.Image,
    filename: str,
    region: Optional[Union[ee.Geometry, ee.FeatureCollection]] = None,
    crs: Optional[str] = None,
    crs_transform: Optional[list] = None,
    scale: Optional[float] = None,
    resampling: str = "near",
    dtype: Optional[str] = None,
    overwrite: bool = True,
    num_threads: Optional[int] = None,
    max_tile_size: Optional[int] = None,
    max_tile_dim: Optional[int] = None,
    shape: Optional[tuple] = None,
    scale_offset: bool = False,
    unmask_value: Optional[int] = None,
    nodata_value: Optional[int] = None,
    **kwargs: Any,
) -> None:
    """Download `image` as a GeoTIFF at `filename`. Tiled automatically by geedim
    (`max_tile_size`/`max_tile_dim`), so national-scale exports stream safely (D4).

    Set `unmask_value` to treat zeros as data (the alert raster uses 0 = no-alert,
    so pass e.g. 255 to distinguish masked-out from genuine zero). `nodata_value`
    sets the GeoTIFF NoData tag.
    """
    try:
        import geedim as gd  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise ImportError("Install geedim>=2.0 ('pip install geedim').") from exc

    if not isinstance(image, ee.Image):  # pragma: no cover
        raise ValueError("image must be an ee.Image.")

    if unmask_value is not None:
        if isinstance(region, (ee.Geometry, ee.FeatureCollection)):
            image = image.clip(region)
        image = image.unmask(unmask_value, sameFootprint=False)

    img = image.gd.prepareForExport(
        crs=crs, region=region, scale=scale, resampling=resampling, dtype=dtype
    )

    if nodata_value is None:
        img.gd.toGeoTIFF(file=filename, overwrite=overwrite, nodata=True, **kwargs)
    else:
        img.gd.toGeoTIFF(file=filename, overwrite=overwrite, nodata=nodata_value, **kwargs)

    print(f"File {filename}, downloaded")
```

(If the srm original takes additional params you want verbatim, diff against
`/tmp/srm/spatialrisk/gee/ee_raster_export.py` and copy any deltas.)

- [ ] **Step 2: Add a signature guard to `tests/test_vectorize.py`**

```python
import inspect

from deforestation_alerts.scripts import raster_export


def test_download_ee_image_exposes_expected_params():
    sig = inspect.signature(raster_export.download_ee_image)
    for p in ("image", "filename", "region", "scale", "crs",
              "unmask_value", "nodata_value"):
        assert p in sig.parameters, f"download_ee_image missing param {p}"
```

- [ ] **Step 3: Run**

Run: `pytest tests/test_vectorize.py -v`
Expected: PASS (helpers + signature guard). The function body is not invoked (no GEE).

- [ ] **Step 4: Commit**

```bash
git add deforestation_alerts/scripts/raster_export.py tests/test_vectorize.py
git commit -m "feat: add raster_export.download_ee_image (geedim 2.0) from srm"
```

---

### Task 4: Build `vectorize.py` — cluster filter + polygonize

The core of the phase. TDD against tiny synthetic GeoTIFFs written with rasterio, so
it is fully offline and deterministic. D11 makes the cluster-size `sieve` a
**required** step, not an optimization. The block-wise path (D4) is built in the same
module and tested with a cluster straddling a block boundary.

**Files:**
- Create: `deforestation_alerts/scripts/vectorize.py`
- Modify: `tests/test_vectorize.py`

- [ ] **Step 1: Add a synthetic-raster fixture + the cluster-filter test**

Append to `tests/test_vectorize.py`:
```python
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
```

- [ ] **Step 2: Run — expect FAIL (module missing)**

Run: `pytest tests/test_vectorize.py::test_sieve_drops_subthreshold_clusters -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deforestation_alerts.scripts.vectorize'`.

- [ ] **Step 3: Implement `vectorize.py`**

Create `deforestation_alerts/scripts/vectorize.py`:
```python
"""Local raster → vector for alerts. Generalizes mosaics_helper.raster_to_gdf:
required cluster-size `sieve` (D11) before polygonizing, then `shapes`, then an
optional area filter. Block-wise reads + edge stitch handle national scale (D4)."""

import geopandas as gpd
import numpy as np
import rasterio
import rasterio.windows
from rasterio import features
from shapely.geometry import shape

from deforestation_alerts.scripts.geo_utils import (
    get_utm_proj_str_from_lat_lon,
    optimal_block_shape,
)


def _date_stats(date_arr, transform, geom, nodata):
    """min/max of the date band within a single polygon (ignoring nodata)."""
    mask = features.geometry_mask(
        [geom], out_shape=date_arr.shape, transform=transform, invert=True
    )
    vals = date_arr[mask]
    vals = vals[vals != nodata]
    if vals.size == 0:
        return None, None
    return float(vals.min()), float(vals.max())


def _records_from_arrays(alert, date, transform, *, min_cluster_size,
                         connectivity, nodata):
    """Sieve the alert mask, polygonize surviving clusters, return raw records."""
    mask = (alert > 0).astype(np.uint8)
    cleaned = features.sieve(mask, size=int(min_cluster_size),
                             connectivity=connectivity)
    alert_clean = np.where(cleaned > 0, alert, 0).astype(alert.dtype)

    records = []
    px_area = abs(transform.a * transform.e)
    for geom_json, value in features.shapes(
        alert_clean, mask=(alert_clean > 0), transform=transform,
        connectivity=connectivity,
    ):
        geom = shape(geom_json)
        code = int(value)
        count = int(round(geom.area / px_area))  # polygon area / pixel area
        dmin, dmax = _date_stats(date, transform, geom, nodata)
        records.append(
            {"geometry": geom, "alert_type_unique": [code], "count": count,
             "alert_date_min": dmin, "alert_date_max": dmax}
        )
    return records


def _stitch(records):
    """Union same-code fragments that touch; aggregate count/date. One record per
    connected cluster (handles block-edge splits, D4)."""
    if not records:
        return records
    g = gpd.GeoDataFrame(records, geometry="geometry")
    g["code"] = g["alert_type_unique"].apply(lambda x: int(x[0]))

    out = []
    for code, grp in g.groupby("code"):
        grp = grp.reset_index(drop=True)
        merged = grp.geometry.union_all()
        parts = list(getattr(merged, "geoms", [merged]))
        for part in parts:
            members = grp[grp.geometry.intersects(part)]
            dmins = [d for d in members["alert_date_min"] if d is not None]
            dmaxs = [d for d in members["alert_date_max"] if d is not None]
            out.append({
                "geometry": part,
                "alert_type_unique": [code],
                "count": int(members["count"].sum()),
                "alert_date_min": min(dmins) if dmins else None,
                "alert_date_max": max(dmaxs) if dmaxs else None,
            })
    return out


def _finalize(records, src_crs, *, min_area_ha, output_epsg):
    """Build the gdf, compute area_ha in a metric CRS, apply the area filter."""
    cols = ["geometry", "alert_type_unique", "count",
            "alert_date_min", "alert_date_max", "area_ha"]
    if not records:
        return gpd.GeoDataFrame(columns=cols, geometry="geometry",
                                crs=f"EPSG:{output_epsg}")

    gdf = gpd.GeoDataFrame(records, geometry="geometry", crs=src_crs)
    gdf = gdf.to_crs(epsg=output_epsg)

    c = gdf.geometry.union_all().centroid
    utm = get_utm_proj_str_from_lat_lon(c.x, c.y)
    gdf["area_ha"] = gdf.geometry.to_crs(utm).area / 10_000.0

    if min_area_ha is not None:
        gdf = gdf[gdf["area_ha"] >= float(min_area_ha)].reset_index(drop=True)
    return gdf


def vectorize_alert_raster(
    raster_path, *, min_cluster_size, connectivity=8, min_area_ha=None,
    alert_band=1, date_band=2, output_epsg=4326, block_rows=None,
):
    """Vectorize a 2-band alert GeoTIFF (band1=alert code, band2=date) into a gdf
    of true pixel-cluster polygons. Cluster-size `sieve` is mandatory (D11).

    block_rows: if set (or auto via optimal_block_shape for huge rasters), read in
    row blocks and stitch edge-split clusters (D4 national scale).
    """
    with rasterio.open(raster_path) as src:
        height, width = src.height, src.width
        transform = src.transform
        nodata = src.nodata if src.nodata is not None else 255
        src_crs = src.crs

        if block_rows is None:
            block_rows, _ = optimal_block_shape(height, width, 2, ram_gb=4.0)

        if block_rows >= height:
            alert = src.read(alert_band)
            date = src.read(date_band)
            records = _records_from_arrays(
                alert, date, transform, min_cluster_size=min_cluster_size,
                connectivity=connectivity, nodata=nodata)
        else:
            records = []
            for row0 in range(0, height, block_rows):
                nrows = min(block_rows, height - row0)
                window = rasterio.windows.Window(0, row0, width, nrows)
                win_transform = src.window_transform(window)
                alert = src.read(alert_band, window=window)
                date = src.read(date_band, window=window)
                records += _records_from_arrays(
                    alert, date, win_transform,
                    min_cluster_size=min_cluster_size,
                    connectivity=connectivity, nodata=nodata)
            records = _stitch(records)

    return _finalize(records, src_crs, min_area_ha=min_area_ha,
                     output_epsg=output_epsg)
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_vectorize.py::test_sieve_drops_subthreshold_clusters -v`
Expected: PASS.

- [ ] **Step 5: Add tests for polygon fidelity, dates, area filter, and the block stitch**

Append to `tests/test_vectorize.py`:
```python
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
    alert = np.zeros((10, 10), dtype="float32")
    alert[0, 0] = 1            # one ~1km pixel (res 0.01 deg)
    alert[4:8, 4:8] = 1        # 16 pixels
    date = np.where(alert > 0, 2023.0, 255).astype("float32")
    path = _write_two_band(tmp_path, alert, date)

    gdf = vectorize_alert_raster(path, min_cluster_size=1, min_area_ha=100.0)
    assert len(gdf) == 1
    assert gdf.iloc[0]["count"] == 16


def test_empty_raster_returns_empty_gdf(tmp_path):
    alert = np.zeros((5, 5), dtype="float32")
    date = np.full((5, 5), 255, dtype="float32")
    path = _write_two_band(tmp_path, alert, date)
    gdf = vectorize_alert_raster(path, min_cluster_size=1)
    assert len(gdf) == 0
    assert "area_ha" in gdf.columns


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
```

- [ ] **Step 6: Run the full vectorize file**

Run: `pytest tests/test_vectorize.py -v`
Expected: PASS (all cases). The date assertion compares `min == max` so trailing-zero float display is irrelevant.

> **Block-path caveat to document in the commit:** per-block sieve removes speckle
> *within* a block; a sub-threshold cluster split across a block edge could survive as
> two even-smaller fragments that re-stitch. Negligible for alert speckle, fixable
> with a 1-row block overlap. Note this trade-off rather than hiding it; do not
> silently cap coverage.

- [ ] **Step 7: Commit**

```bash
git add deforestation_alerts/scripts/vectorize.py tests/test_vectorize.py
git commit -m "feat: vectorize_alert_raster — required sieve filter (D11) + block-wise stitch (D4)"
```

---

### Task 5: Re-point the CNN image download to `raster_export`

`mosaics_helper.download_both_images` uses `geemap.download_ee_image`, which may
break under geedim 2.0. Re-point it to our own exporter.

**Files:**
- Modify: `deforestation_alerts/scripts/mosaics_helper.py`

- [ ] **Step 1: Replace the geemap import and call**

In `mosaics_helper.py`, remove `from geemap import download_ee_image` and add
`from deforestation_alerts.scripts.raster_export import download_ee_image`. The call
signature is compatible (`scale`, `crs`, `region`, `overwrite`), so the existing
`download_ee_image(rimage1.addBands(rimage2), image_name, scale=4.77, crs="EPSG:3857", region=region, overwrite=True)` call stays as-is.

- [ ] **Step 2: Verify no other `geemap.download_ee_image` usages remain**

Run: `grep -rn "geemap.download_ee_image\|from geemap import download_ee_image" deforestation_alerts/ gui/`
Expected: no output. If geemap is otherwise unused, leave its pin (other geemap APIs may still be used elsewhere — check with `grep -rn "geemap" deforestation_alerts/ gui/`).

- [ ] **Step 3: Confirm the module imports cleanly**

Run: `python -c "from deforestation_alerts.scripts import mosaics_helper; print('mosaics import ok')"`
Expected: `mosaics import ok`.

- [ ] **Step 4: Commit**

```bash
git add deforestation_alerts/scripts/mosaics_helper.py
git commit -m "refactor: download CNN imagery via raster_export (geedim 2.0), drop geemap wrapper"
```

---

### Task 6: Re-point `convert_to_geopandas` and delete the GEE-vector machinery

**Files:**
- Modify: `deforestation_alerts/scripts/alert_filter_helper.py`
- Create: `tests/test_convert_to_geopandas.py`

- [ ] **Step 1: Write the new `convert_to_geopandas` contract test**

Create `tests/test_convert_to_geopandas.py`:
```python
"""Tier-1: the local-vectorize gdf -> alerts_gdf assembler (no GEE, no UI)."""

import geopandas as gpd
from shapely.geometry import Polygon

from deforestation_alerts.scripts.alert_filter_helper import convert_to_geopandas


def _vectorized_gdf():
    poly = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    return gpd.GeoDataFrame(
        {
            "alert_type_unique": [[1], [110]],   # GLAD-L ; RADD+GLAD-S2
            "count": [4, 9],
            "alert_date_min": [2022.1, 2023.2],
            "alert_date_max": [2022.3, 2023.4],
            "area_ha": [0.5, 1.2],
            "geometry": [poly, poly],
        },
        geometry="geometry", crs="EPSG:4326",
    )


def test_convert_sets_polygon_geometry_and_backcompat_columns():
    gdf = convert_to_geopandas(_vectorized_gdf())
    assert gdf.geometry.iloc[0].geom_type == "Polygon"          # D2 active geom
    assert gdf["bounding_box"].iloc[0].geom_type in ("Polygon", "LineString")
    assert gdf["point"].iloc[0].geom_type == "Point"
    assert gdf["area_ha"].iloc[0] == 0.5


def test_convert_decodes_alert_sources():
    gdf = convert_to_geopandas(_vectorized_gdf())
    assert gdf["alert_sources"].iloc[0] == "GLAD-L"
    # 110 -> RADD (tens) + GLAD-S2 (hundreds); membership check (order-independent)
    parts = gdf["alert_sources"].iloc[1].replace(" and ", ", ").split(", ")
    assert set(parts) == {"RADD", "GLAD-S2"}


def test_convert_adds_empty_review_columns():
    gdf = convert_to_geopandas(_vectorized_gdf())
    for col in ("status", "before_img", "after_img", "alert_polygon",
                "description", "admin1", "admin2", "admin3"):
        assert col in gdf.columns
    assert (gdf["status"] == "Not reviewed").all()
```

- [ ] **Step 2: Run — expect FAIL (old signature takes GEE features)**

Run: `pytest tests/test_convert_to_geopandas.py -v`
Expected: FAIL — the current `convert_to_geopandas(polygon_features)` iterates GeoJSON feature dicts and will error on a GeoDataFrame input.

- [ ] **Step 3: Rewrite `convert_to_geopandas` to consume the vectorize gdf**

Replace the body of `convert_to_geopandas` (currently ~lines 486–526) with:
```python
def convert_to_geopandas(vectorized_gdf):
    """Assemble alerts_gdf from the local-vectorize output.

    Input: gpd.GeoDataFrame with columns geometry(Polygon), alert_type_unique(list),
    count, alert_date_min, alert_date_max, area_ha (from vectorize_alert_raster).
    Output: the alerts_gdf the app consumes — active geometry = polygon (D2),
    plus the back-compat `bounding_box`, `point`, decoded `alert_sources`, and the
    empty user-review columns. Preserves the integer alert-code decoding.
    """
    gdf = vectorized_gdf.copy()

    # geometry stays the true polygon; derive back-compat shapes
    gdf["bounding_box"] = gdf.geometry.envelope          # D2 back-compat bbox
    gdf["point"] = gdf.geometry.centroid

    gdf["status"] = "Not reviewed"
    gdf["alert_sources"] = gdf["alert_type_unique"].apply(
        lambda x: format_list(get_unique_alerts(x))
    )
    gdf["before_img"] = pd.Series([], dtype="str")
    gdf["before_img_info"] = pd.Series([], dtype="object")
    gdf["after_img"] = pd.Series([], dtype="str")
    gdf["after_img_info"] = pd.Series([], dtype="object")
    gdf["alert_polygon"] = pd.Series([], dtype="object")
    if "area_ha" not in gdf.columns:
        gdf["area_ha"] = pd.Series([], dtype="float")
    gdf["description"] = pd.Series([], dtype="str")
    gdf["admin1"] = pd.Series([], dtype="str")
    gdf["admin2"] = pd.Series([], dtype="str")
    gdf["admin3"] = pd.Series([], dtype="str")

    return gdf
```
Keep the existing `from …report_builder import get_unique_alerts, format_list` import at the top of the file; ensure `import pandas as pd` and `import geopandas as gpd` are present.

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_convert_to_geopandas.py -v`
Expected: PASS. (`gdf.geometry.envelope` of a square is a Polygon; the test allows a degenerate LineString just in case.)

- [ ] **Step 5: Delete the GEE-vector machinery**

In `alert_filter_helper.py`, delete these now-dead functions (call sites are
removed/replaced in Task 7):
- `evaluate_with_retry` (≈ lines 59–90)
- `obtener_datos_gee_parcial_map` (≈ lines 119–224)
- `obtener_datos_gee_total_v2` (≈ lines 227–290)
- `obtener_datos_gee_parcial_map_2` (≈ lines 293–378)
- `obtener_datos_gee_total_v3` (≈ lines 381–482)

Keep `check_integer`, `check_alert_filter_inputs`, `custom_reduce_image_collection`,
and the new `convert_to_geopandas`. Remove any now-unused imports (`time`, the
`reduceToVectors`/`coveringGrid` paths).

- [ ] **Step 6: Verify nothing in the package still references the deleted functions**

Run:
```bash
grep -rn "obtener_datos_gee\|evaluate_with_retry\|reduceToVectors\|coveringGrid" deforestation_alerts/ gui/
```
Expected: only matches inside `gui/tile/alerts_filter_tile.py` (fixed in Task 7) — note them and proceed; do Task 7 before importing that file. If anything in `deforestation_alerts/` prints, fix by hand.

- [ ] **Step 7: Run the suite**

Run: `pytest tests/ -v`
Expected: PASS (Phase 0 + the new convert/vectorize tests). The Phase 0 `test_report_builder.py` still guards `get_unique_alerts`, so the decode contract is double-covered.

- [ ] **Step 8: Commit**

```bash
git add deforestation_alerts/scripts/alert_filter_helper.py tests/test_convert_to_geopandas.py
git commit -m "feat: re-point convert_to_geopandas to local vectorize; delete GEE-vector machinery"
```

---

### Task 7: Wire the new pipeline into `alerts_filter_tile`

Replace the two GEE-vector threads with a single worker that downloads the filtered
alert raster, vectorizes it, assembles `alerts_gdf`, and publishes it via the
existing model trait so the existing `analysis_tile` observer path still builds the
review state. Threading stays (traitlets phase); Phase 4 converts it to `use_task`.

**Files:**
- Modify: `gui/tile/alerts_filter_tile.py`, `gui/tile/analysis_tile.py`

- [ ] **Step 1: Read the current `analyze_alerts_function` flow**

Open `gui/tile/alerts_filter_tile.py` and locate (≈ lines 532–654)
`analyze_alerts_function`, plus `start_thread_full`/`start_thread_partial`
(≈ 361–411) and `create_vector_download_params` (≈ 458–497). Confirm
`create_vector_download_params` returns `(poly, alerta_reducir, pixel_size, min_size_pixels, grid_size)` and computes `min_size_pixels` from the user's `min_area` (ha → pixels).

- [ ] **Step 2: Replace the thread spawners with a download+vectorize worker**

Delete `start_thread_full` and `start_thread_partial`. Add:
```python
def run_local_vectorize(self, image, region, scale, min_size_pixels, max_number_alerts):
    """Download the filtered alert raster, vectorize locally, publish alerts.

    Runs on a worker thread (kept for the traitlets phase; Phase 4 -> use_task).
    """
    import threading

    def _work():
        from deforestation_alerts.scripts.raster_export import download_ee_image
        from deforestation_alerts.scripts.vectorize import vectorize_alert_raster
        from deforestation_alerts.parameter import module_dir

        out_tif = str(module_dir / "filtered_alerts.tif")
        # alert raster: band order [alert, date]; 0 = no-alert so unmask to 255
        download_ee_image(
            image.select(["alert", "date"]), out_tif, region=region,
            scale=scale, crs="EPSG:4326", unmask_value=255, nodata_value=255,
        )
        gdf = vectorize_alert_raster(
            out_tif, min_cluster_size=min_size_pixels, connectivity=8,
        )
        # publish: full set, plus the first N for the "partial" quick view
        self.selected_alerts_model.alerts_total_bbs = gdf
        self.selected_alerts_model.alerts_bbs = gdf.head(int(max_number_alerts))
        self.selected_alerts_model.received_alerts = True

    threading.Thread(target=_work, daemon=True).start()
```

- [ ] **Step 3: Call it from `analyze_alerts_function`**

In `analyze_alerts_function`, after building
`self.selected_alerts_model.filtered_alert_raster` and computing the download params,
replace the two `self.start_thread_partial(...)` / `self.start_thread_full(...)`
calls with:
```python
        self.run_local_vectorize(
            image=self.selected_alerts_model.filtered_alert_raster,
            region=poly,
            scale=pixel_size,
            min_size_pixels=min_size_pixels,
            max_number_alerts=max_number_alerts,
        )
```
Remove the now-unused `custom_reducer`/`sorting` GEE-reducer construction. Keep
`create_vector_download_params` — it still computes `pixel_size` and `min_size_pixels`.

- [ ] **Step 4: Update the `analysis_tile` consumers to accept a gdf**

The `analysis_tile` observers `update_gdf_partial`/`update_gdf_full` (→
`create_gdf_partial`/`create_gdf_full`, ≈ lines 803–910) previously received *GeoJSON
feature lists* and called `convert_to_geopandas(features)`. They now receive a
*vectorize gdf*. Edit those two methods so they call `convert_to_geopandas(<the gdf>)`
once on the incoming gdf and assign to `analyzed_alerts_model.alerts_gdf`. Drop the
re-conversion of `complete` (it is already a gdf). Keep the merge logic; only the
input type changes. (The `create_gdf_full` cross-thread race is intentionally left to
Phase 4's `use_task` conversion — spec §8 tech-debt.)

- [ ] **Step 5: Verify the tiles import and the old machinery is gone**

```bash
python -c "import warnings; warnings.simplefilter('ignore'); from gui.tile.alerts_filter_tile import AlertsFilterTile; from gui.tile.analysis_tile import AnalysisTile; print('tiles ok')"
grep -rn "obtener_datos_gee\|start_thread_full\|start_thread_partial" deforestation_alerts/ gui/
```
Expected: `tiles ok`; the grep prints nothing.

- [ ] **Step 6: Run the suite**

Run: `pytest tests/ -v`
Expected: PASS (unit suite unaffected; the tile change is exercised by the Tier-2 integration test in Task 9 and the manual app run in Task 10).

- [ ] **Step 7: Commit**

```bash
git add gui/tile/alerts_filter_tile.py gui/tile/analysis_tile.py
git commit -m "feat: wire download+local-vectorize into alerts_filter_tile (replaces GEE threads)"
```

---

### Task 8: Keep the recipe round-trip green with polygon geometry

The active geometry is now a Polygon, so the recipe CSV must serialize/decode it; old
recipes (no polygon column) must still load (D10 back-compat).

**Files:**
- Modify: `deforestation_alerts/scripts/recipe_helper.py`
- Create: `tests/test_recipe_geometry_roundtrip.py`

- [ ] **Step 1: Write the round-trip + backfill tests**

Create `tests/test_recipe_geometry_roundtrip.py`:
```python
"""Tier-1: alerts_gdf CSV round-trip with polygon geometry + old-recipe backfill."""

import geopandas as gpd
from shapely.geometry import Polygon

from deforestation_alerts.scripts.recipe_helper import (
    save_gdf_to_csv,
    load_gdf_from_csv,
    backfill_geometry,
)

GEOM_COLS = ["geometry", "bounding_box", "point"]


def _alerts_gdf():
    poly = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    gdf = gpd.GeoDataFrame(
        {"id": [0], "status": ["Not reviewed"], "geometry": [poly]},
        geometry="geometry", crs="EPSG:4326",
    )
    gdf["bounding_box"] = gdf.geometry.envelope
    gdf["point"] = gdf.geometry.centroid
    return gdf


def test_polygon_roundtrips_through_csv(tmp_path):
    gdf = _alerts_gdf()
    csv = tmp_path / "alert_db.csv"
    save_gdf_to_csv(gdf, str(csv), GEOM_COLS)

    loaded = load_gdf_from_csv(str(csv), GEOM_COLS)
    assert loaded["geometry"].iloc[0].geom_type == "Polygon"
    assert loaded["geometry"].iloc[0].equals(gdf["geometry"].iloc[0])
    assert loaded["point"].iloc[0].geom_type == "Point"


def test_old_recipe_without_polygon_backfills_from_bbox(tmp_path):
    # Simulate a pre-Phase-3 recipe: only point + bounding_box stored.
    csv = tmp_path / "old_alert_db.csv"
    csv.write_text(
        "id,point,bounding_box\n"
        "0,POINT (0.5 0.5),\"POLYGON ((0 0, 0 1, 1 1, 1 0, 0 0))\"\n"
    )
    loaded = load_gdf_from_csv(str(csv), ["point", "bounding_box"])
    loaded = backfill_geometry(loaded)   # fills geometry from bounding_box
    assert loaded["geometry"].iloc[0].geom_type == "Polygon"
```

- [ ] **Step 2: Run — expect FAIL (helpers missing)**

Run: `pytest tests/test_recipe_geometry_roundtrip.py -v`
Expected: FAIL (`ImportError` for `save_gdf_to_csv` / `backfill_geometry`, or the Phase-0 `load_gdf_from_csv` not handling polygon WKT).

- [ ] **Step 3: Add/confirm the recipe geometry helpers**

In `recipe_helper.py`:
- Confirm `load_gdf_from_csv(path, geometry_cols)` decodes each named column from WKT
  via `shapely.wkt.loads` (Phase 0 pinned it for `point`; WKT decoding is
  geometry-type agnostic, so polygons already work — if it special-cased Points, generalize it).
- Add `save_gdf_to_csv(gdf, path, geometry_cols)` that writes the gdf to CSV with the
  named geometry columns encoded as WKT:
```python
def save_gdf_to_csv(gdf, path, geometry_cols):
    """Write a gdf to CSV, encoding each named geometry column as WKT."""
    import pandas as pd
    out = pd.DataFrame(gdf).copy()
    for col in geometry_cols:
        if col in out.columns:
            out[col] = out[col].apply(lambda g: g.wkt if g is not None else "")
    out.to_csv(path, index=False)
    return path
```
- Add the back-compat helper:
```python
def backfill_geometry(gdf):
    """Old recipes have no polygon `geometry`; fill it from `bounding_box`."""
    import geopandas as gpd
    if "geometry" not in gdf.columns and "bounding_box" in gdf.columns:
        gdf = gdf.copy()
        gdf["geometry"] = gdf["bounding_box"]
        gdf = gpd.GeoDataFrame(gdf, geometry="geometry")
    return gdf
```
Wire `backfill_geometry` into the existing recipe-load path (where `load_gdf_from_csv`
is called for `alert_db.csv`) so loaded recipes always have a polygon `geometry`.

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_recipe_geometry_roundtrip.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite (Phase 0 recipe tests must stay green)**

Run: `pytest tests/ -v`
Expected: PASS — including the Phase 0 `test_recipe_io.py` (`load_gdf_from_csv` with `["point"]` still behaves identically).

- [ ] **Step 6: Commit**

```bash
git add deforestation_alerts/scripts/recipe_helper.py tests/test_recipe_geometry_roundtrip.py
git commit -m "feat: serialize polygon geometry in recipes; backfill old recipes (D10)"
```

---

### Task 9: Tier-2 integration test (skip-if-no-creds)

Validates the whole chain against real Earth Engine on a tiny AOI, and asserts the
fidelity items from spec §6.5: alert-code integers survive GeoTIFF export, columns
match, cluster filter behaves.

**Files:**
- Modify: `tests/conftest.py`
- Create: `tests/test_raster_pipeline_integration.py`

- [ ] **Step 1: Add the ee-init/skip fixtures to `conftest.py`**

Append to `tests/conftest.py`:
```python
@pytest.fixture(scope="session")
def gee():
    """Initialize Earth Engine once; skip the module if unauthenticated."""
    try:
        from pysepal.scripts.utils import init_ee
        init_ee()
        import ee
        ee.Number(1).getInfo()  # force a round-trip to confirm creds
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Earth Engine unavailable: {exc}")
    return True


@pytest.fixture
def tiny_aoi(gee):
    import ee
    return ee.Geometry.Rectangle([-55.10, -10.10, -55.00, -10.00])  # ~Amazon
```

- [ ] **Step 2: Write the integration test**

Create `tests/test_raster_pipeline_integration.py`:
```python
"""Tier-2: real EE download -> local vectorize. Skips without credentials."""

import numpy as np
import rasterio

from deforestation_alerts.scripts.raster_export import download_ee_image
from deforestation_alerts.scripts.vectorize import vectorize_alert_raster
from deforestation_alerts.scripts.alert_filter_helper import convert_to_geopandas


def test_alert_code_integers_survive_geotiff_export(tiny_aoi, tmp_path):
    """A constant integer alert band must export + read back bit-exact (§6.5)."""
    import ee
    alert = ee.Image.constant(110).toInt().rename("alert")   # RADD+GLAD-S2 code
    date = ee.Image.constant(2023.0).rename("date")
    img = alert.addBands(date)

    out = str(tmp_path / "codes.tif")
    download_ee_image(img, out, region=tiny_aoi, scale=30,
                      crs="EPSG:4326", unmask_value=255, nodata_value=255)

    with rasterio.open(out) as src:
        band = src.read(1)
    present = set(np.unique(band).tolist()) - {255}
    assert present == {110}, f"alert code corrupted on export: {present}"


def test_download_then_vectorize_produces_expected_columns(tiny_aoi, tmp_path):
    import ee
    alert = ee.Image.constant(1).toInt().rename("alert")
    date = ee.Image.constant(2023.123).rename("date")
    out = str(tmp_path / "alerts.tif")
    download_ee_image(alert.addBands(date), out, region=tiny_aoi, scale=30,
                      crs="EPSG:4326", unmask_value=255, nodata_value=255)

    vgdf = vectorize_alert_raster(out, min_cluster_size=1, connectivity=8)
    assert len(vgdf) >= 1
    gdf = convert_to_geopandas(vgdf)
    for col in ("geometry", "bounding_box", "point", "alert_sources",
                "area_ha", "status"):
        assert col in gdf.columns
    assert gdf.geometry.iloc[0].geom_type == "Polygon"
    assert "GLAD-L" in gdf["alert_sources"].iloc[0]
```

- [ ] **Step 3: Run (passes with creds, skips without)**

Run: `pytest tests/test_raster_pipeline_integration.py -v`
Expected: PASS if EE creds are present; otherwise `SKIPPED (Earth Engine unavailable…)`. Both outcomes are acceptable locally; CI runs it with the `EARTHENGINE_TOKEN` secret.

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/test_raster_pipeline_integration.py
git commit -m "test: Tier-2 integration — EE download survives + vectorizes (skip-if-no-creds)"
```

---

### Task 10: CI + manual app validation + finalize

**Files:**
- Modify: `.github/workflows/ci.yaml`

- [ ] **Step 1: Confirm the unit step runs Tier-1 offline and Tier-2 skips cleanly**

The Phase 0 CI step runs `pytest tests/`. With `EARTHENGINE_TOKEN` set (already in
`ci.yaml`'s `env:`), the Tier-2 module runs; without it, it skips. No change is
strictly required, but make the intent explicit by ensuring the unit step is:
```yaml
      - name: Run unit tests
        shell: micromamba-shell {0}
        run: |
          pip install pytest
          pytest tests/ -v
```

- [ ] **Step 2: Validate the YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yaml')); print('yaml ok')"`
Expected: `yaml ok`.

- [ ] **Step 3: Manually validate the app on a small AOI (the load-bearing check)**

Launch Voila (`nox -s app` or `voila ui.ipynb`), select a small AOI, run the alert
analysis, and confirm: alerts appear as polygons on the map, the cluster-size filter
drops speckle, and the review/report flow works end to end. This is the spec §9
"validate on a large AOI early" risk check — start small, then try a larger AOI to
exercise the block path (Task 4). Record findings.

> **D4 escape hatch:** if a national-scale AOI exhausts RAM/time even with block-wise
> reads, the spec keeps dask as a later option (out of scope here). Note the limit
> observed; do not silently truncate the AOI.

- [ ] **Step 4: Full suite one more time**

Run: `pytest tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yaml
git commit -m "ci: run Tier-1 offline + Tier-2 EE integration in the test suite"
```

---

## Definition of done (Phase 3)

- [ ] `geedim>=2.0.0` installed; `ee.Image(...).gd` accessor works; ee fork `1.1.5rc0` intact.
- [ ] `raster_export.download_ee_image` and `vectorize_alert_raster` exist; vectorize enforces the sieve cluster filter (D11) and supports the block-wise path (D4).
- [ ] `convert_to_geopandas` produces the locked column contract: polygon `geometry` (D2), `bounding_box`, `point`, decoded `alert_sources`, `area_ha`, empty review columns.
- [ ] The GEE-vector machinery (`obtener_datos_gee_*`, `evaluate_with_retry`, `reduceToVectors`/`coveringGrid` paths) is deleted; nothing references it.
- [ ] `alerts_filter_tile` runs download→vectorize→assemble; `analysis_tile` observers consume the gdf.
- [ ] Recipe round-trip is green, including old-recipe polygon backfill (D10).
- [ ] Tier-1 tests pass offline; Tier-2 integration passes with creds (or skips cleanly) and proves alert-code integers survive GeoTIFF export.
- [ ] Manual app run on a real AOI produces polygon alerts; the app is still Voila-runnable.

---

## Self-review notes (author)

- **Spec coverage:** Implements spec §6 (the full new pipeline, §6.2/§6.3 reuse
  targets, §6.4 deps, §6.5 fidelity tests) and §7 Tier-2 testing; honors D2 (polygon
  + bbox column), D4 (block-wise national scale), D11 (mandatory sieve cluster
  filter). Keeps Stages A/B (`get_alerts`/`custom_reduce_image_collection`).
- **Column-contract decision recorded:** D2's "bbox" is realized as the existing
  `bounding_box` column name for true back-compat; the active geometry becomes the
  polygon. This is the highest-risk change and is pinned by Task 6's contract test +
  Task 8's recipe round-trip.
- **Deliberately deferred:** se.plan-style JSON-schema recipe *validation* (the spec's
  Phase 3 step list in §8 omits it; only "recipe round-trip green" is required — Task 8
  delivers that). `recipe_schema.json` and full schema validation can land alongside
  Phase 4's recipe view, or as a follow-up. dask (spec §10 YAGNI) stays out.
- **Cross-file edit flagged (Task 7):** the `analysis_tile` observer bodies must switch
  from feature-list input to gdf input. It is a small mechanical change but spans a
  file otherwise owned by Phase 4; called out explicitly so it isn't missed. The
  `create_gdf_full` race is intentionally left to Phase 4's `use_task` conversion.
- **Block-path caveat documented:** per-block sieve can, in principle, under-filter a
  cluster split across a block edge; negligible for alert speckle, fixable with a
  1-row overlap. Noted in Task 4 rather than hidden.
- **Type consistency check:** `vectorize_alert_raster` returns the gdf with columns
  `geometry, alert_type_unique, count, alert_date_min, alert_date_max, area_ha`;
  `convert_to_geopandas` consumes exactly those and adds the rest; the integration
  test asserts the union. `min_cluster_size` (vectorize) is fed `min_size_pixels`
  (from `create_vector_download_params`) — same quantity, consistent across tasks.
