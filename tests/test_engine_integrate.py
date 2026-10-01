import hashlib
import math
from datetime import date, datetime
from fractions import Fraction

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from shapely.geometry import box

from deforestation_alerts.engine import fingerprint as fp
from deforestation_alerts.engine import integrate, raster_io
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import (
    AlertSetParams, AoiRecord, GridSpec, Period, Project, add_predefined_source,
)
from deforestation_alerts.scripts.workspace import Workspace

GRID = GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=8900000, width=40, height=40)
JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
DEC = Period(id="2025-12-01_2025-12-31", start=date(2025, 12, 1), end=date(2025, 12, 31))

# Spec §7.4 step 8 runs convert_to_geopandas on EPSG:4326, whose `point`
# centroid geopandas flags for any geographic CRS (a pre-existing warning of
# convert_to_geopandas). For alert-sized polygons the planar centroid in degrees
# is off by far less than a pixel, so the warning is noise here.
_LONLAT_CENTROID = pytest.mark.filterwarnings("ignore:Geometry is in a geographic CRS:UserWarning")


def _write(path, alert, dates):
    with raster_io.write_grid_raster(path, GRID, count=2) as dst:
        dst.write(alert.astype("int32"), 1)
        dst.write(dates.astype("int32"), 2)
    return path


def _blank():
    return np.zeros((GRID.height, GRID.width), dtype="int64")


def test_min_cluster_pixels():
    assert integrate.min_cluster_pixels(0.5, 10) == 50
    assert integrate.min_cluster_pixels(0.5, 30) == 6
    assert integrate.min_cluster_pixels(0, 10) == 1


@pytest.mark.parametrize("ha, res, pixels", [
    (0.07, 10, 7),   # 0.07 * 1e4 / 100 is 7.000000000000001 in floats
    (0.81, 30, 9),   # 9.000000000000002
    (0.29, 10, 29),
    (0.82, 20, 21),  # 20.5: a real fraction still rounds up
    (0.10, 30, 2),   # 1.11...
    (0.01, 30, 1),   # 0.11..., never below one pixel
])
def test_min_cluster_pixels_ignores_float_noise(ha, res, pixels):
    assert integrate.min_cluster_pixels(ha, res) == pixels


@pytest.mark.parametrize("res", [10, 20, 30])
def test_min_cluster_pixels_matches_exact_arithmetic_for_two_decimal_inputs(res):
    """Every two-decimal size from 0.01 to 5.00 ha, against exact fractions."""
    wrong = []
    for i in range(1, 501):
        exact = max(1, math.ceil(Fraction(i, 100) * 10_000 / res ** 2))
        if integrate.min_cluster_pixels(i / 100, res) != exact:
            wrong.append(i / 100)
    assert wrong == []


def test_combine_digits_dates_display(tmp_path):
    g, r = _blank(), _blank()
    g[5:8, 5:8] = 1; r[6:9, 6:9] = 2
    gd = np.where(g > 0, 2026020, 0); rd = np.where(r > 0, 2026010, 0)
    n = integrate.combine([(_write(tmp_path / "g.tif", g, gd), 0), (_write(tmp_path / "r.tif", r, rd), 1)],
                          GRID, tmp_path / "c.tif")
    with rasterio.open(tmp_path / "c.tif") as src:
        code, dates, disp = src.read(1), src.read(2), src.read(3)
    assert code[6, 6] == 21 and dates[6, 6] == 2026010 and disp[6, 6] == 1   # both, earliest date
    assert code[8, 8] == 20 and disp[8, 8] == 2                              # RADD probable only
    assert code[5, 5] == 1 and n == int((code > 0).sum())


def _drawn(cols, rows):
    """The grid cells ``[c0, c1) x [r0, r1)`` as a polygon in WGS84, like the map draws it."""
    t = Transformer.from_crs(GRID.epsg, "EPSG:4326", always_xy=True)
    (c0, c1), (r0, r1) = cols, rows
    x0, x1 = GRID.xmin + c0 * GRID.resolution, GRID.xmin + c1 * GRID.resolution
    y0, y1 = GRID.ymax - r1 * GRID.resolution, GRID.ymax - r0 * GRID.resolution
    corners = [t.transform(x, y) for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]]
    return {"type": "Polygon", "coordinates": [corners + [corners[0]]]}


def test_footprint_and_area_clip(tmp_path):
    a = _blank(); a[0:40, 0:40] = 1
    src = _write(tmp_path / "a.tif", a, np.where(a > 0, 2026001, 0))
    fpr = _blank(); fpr[0:10, :] = 1
    with raster_io.write_grid_raster(tmp_path / "fp.tif", GRID, count=1) as dst:
        dst.write(fpr.astype("int32"), 1)
    area = _drawn((0, 20), (0, 40))  # the left half of the grid
    integrate.combine([(src, 0)], GRID, tmp_path / "c.tif", footprints=[tmp_path / "fp.tif"], area_geojson=area)
    with rasterio.open(tmp_path / "c.tif") as s:
        code = s.read(1)
    assert code[5, 5] == 0      # earlier footprint
    assert code[20, 5] == 1     # inside the drawn half
    assert code[20, 30] == 0    # outside it


def test_windows_do_not_change_the_result(tmp_path, monkeypatch):
    """The test grid is one window at the production size; 7-row windows cut
    it into six, so every per-window offset (reads, writes, the drawn area,
    the footprint) is exercised."""
    rng = np.random.default_rng(7)
    g = rng.integers(0, 3, (GRID.height, GRID.width)); r = rng.integers(0, 3, (GRID.height, GRID.width))
    gd = np.where(g > 0, 2026001 + rng.integers(0, 30, g.shape), 0)
    rd = np.where(r > 0, 2026001 + rng.integers(0, 30, r.shape), 0)
    inputs = [(_write(tmp_path / "g.tif", g, gd), 0), (_write(tmp_path / "r.tif", r, rd), 1)]
    fpr = _blank(); fpr[12:17, 3:30] = 1
    with raster_io.write_grid_raster(tmp_path / "fp.tif", GRID, count=1) as dst:
        dst.write(fpr.astype("int32"), 1)
    polys = gpd.GeoDataFrame(geometry=[box(500050, 8899700, 500170, 8899880), box(500200, 8899640, 500310, 8899990)],
                             crs=GRID.epsg)

    def run(tag):
        out = tmp_path / tag
        n = integrate.combine(inputs, GRID, out / "c.tif", footprints=[tmp_path / "fp.tif"],
                              area_geojson=_drawn((3, 33), (4, 31)))
        integrate.rasterize_footprint(polys, GRID, out / "f.tif")
        with rasterio.open(out / "c.tif") as c, rasterio.open(out / "f.tif") as f:
            return n, c.read(), f.read()

    n1, combined1, footprint1 = run("one")
    monkeypatch.setattr(integrate, "_windows", lambda grid: raster_io.row_windows(grid, 7))
    n7, combined7, footprint7 = run("seven")
    assert n1 == n7 > 0
    assert np.array_equal(combined1, combined7)
    assert np.array_equal(footprint1, footprint7) and footprint1.sum() == 12 * 18 + 11 * 35


def test_sort_and_cut():
    gdf = gpd.GeoDataFrame({"area_ha": [1.0, 3.0, 2.0], "alert_date_min": [2026.01, 2026.03, 2026.02],
                            "alert_date_max": [2026.01, 2026.03, 2026.02]},
                           geometry=[box(0, 0, 1, 1)] * 3)
    assert list(integrate.sort_and_cut(gdf, 1, 0)["area_ha"]) == [3.0, 2.0, 1.0]
    assert list(integrate.sort_and_cut(gdf, 2, 2)["area_ha"]) == [1.0, 2.0]
    assert list(integrate.sort_and_cut(gdf, 3, 1)["alert_date_max"]) == [2026.03]
    assert list(integrate.sort_and_cut(gdf, 4, 0)["alert_date_min"])[0] == 2026.01


def _prepare(p, pdir, period, arrays, day):
    """Record raw and harmonized artifacts per source, as Download & harmonize leaves them."""
    ws, we = period.search_window(p.look_back_days)
    for name, arr in arrays.items():
        dates = np.where(arr > 0, day, 0)
        raw = _write(paths.raw_path(pdir, period.id, name, ws, we), arr, dates)
        p.artifacts[fp.raw_key(period.id, name)] = fp.make_artifact(
            fp.raw_key(period.id, name), "raw_alert", pdir, raw, fp.raw_alert_inputs(p, name, period))
        harm = _write(paths.harmonized_path(pdir, period.id, name, ws, we), arr, dates)
        p.artifacts[fp.harm_key(period.id, name)] = fp.make_artifact(
            fp.harm_key(period.id, name), "harmonized", pdir, harm, fp.harmonized_inputs(p, name, period))


def _clusters():
    g, r = _blank(), _blank()
    g[2:10, 2:10] = 1     # 64 px cluster (bigger)
    r[20:24, 20:24] = 2   # 16 px cluster
    return {"GLAD-L": g, "RADD": r}


@pytest.fixture
def ready_project(tmp_path):
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="g", bounds=(0, 0, 1, 1))
    p.grid = GRID
    add_predefined_source(p, "GLAD-L")
    add_predefined_source(p, "RADD")
    p.periods[JAN.id] = JAN
    _prepare(p, tmp_path, JAN, _clusters(), 2026015)
    return p, tmp_path


def _job(p, pdir, period, params):
    return integrate.IntegrationJob(project=p.model_copy(deep=True), pdir=pdir, period=period,
                                    sources=["RADD", "GLAD-L"], params=params,
                                    workspace=Workspace(root=pdir, scratch_parent=pdir / "scratch"))


@_LONLAT_CENTROID
def test_run_integration_end_to_end(ready_project):
    p, pdir = ready_project
    params = AlertSetParams(min_size_ha=0.1, sorting=1, max_alerts=1)  # keep only the bigger alert
    job = integrate.IntegrationJob(project=p.model_copy(deep=True), pdir=pdir, period=JAN,
                                   sources=["RADD", "GLAD-L"], params=params,
                                   workspace=Workspace(root=pdir, scratch_parent=pdir / "scratch"))
    res = integrate.run_integration(job)
    s = res.alert_set
    assert s.sources == ["GLAD-L", "RADD"] and s.digit_map == {0: "GLAD-L", 1: "RADD"}
    assert s.n_alerts == 1 and len(res.alerts) == 1
    assert res.alerts.crs.to_epsg() == 4326
    assert res.alerts.iloc[0]["alert_sources"] == "GLAD-L"
    assert list(res.alerts["alert_id"]) == [0] and set(res.alerts["set_id"]) == {s.id}
    assert s.fingerprint == fp.fingerprint(fp.set_inputs(p, JAN, ["GLAD-L", "RADD"], params))
    # the footprint holds only the stored alert (the RADD cluster was cut by max_alerts)
    with rasterio.open(pdir / s.footprint_rel_path) as src:
        f = src.read(1)
    assert f[5, 5] == 1 and f[21, 21] == 0 and int(f.sum()) == 64
    stored = gpd.read_parquet(pdir / s.store_rel_path)
    assert len(stored) == 1
    assert {fp.combined_key(s.id), fp.footprint_key(s.id), fp.store_key(s.id)} <= set(res.artifacts)


def test_run_integration_refuses_a_source_not_ready(ready_project):
    p, pdir = ready_project
    del p.artifacts[fp.harm_key(JAN.id, "RADD")]
    job = integrate.IntegrationJob(project=p, pdir=pdir, period=JAN, sources=["RADD"],
                                   params=AlertSetParams(), workspace=Workspace(root=pdir))
    with pytest.raises(ValueError, match="RADD"):
        integrate.run_integration(job)


@_LONLAT_CENTROID
# raster_attribute's window reads go through rasterio helpers that still use `*` on Affine
@pytest.mark.filterwarnings("ignore:Use `@` matmul:PendingDeprecationWarning")
def test_run_integration_attributes_the_stored_alerts(ready_project):
    p, pdir = ready_project
    raster = pdir / "attribute.tif"
    with raster_io.write_grid_raster(raster, GRID, count=1) as dst:
        dst.write(np.full((GRID.height, GRID.width), 7, "int32"), 1)
    config = {"source": str(raster), "kind": "local",
              "bands": [{"band": "band_1", "stat": "max", "column": "attr"}]}
    res = integrate.run_integration(_job(p, pdir, JAN, AlertSetParams(min_size_ha=0.1, attribute_config=config)))
    assert list(res.alerts["attr"]) == [7, 7]
    assert list(gpd.read_parquet(pdir / res.alert_set.store_rel_path)["attr"]) == [7, 7]


def _record(p, res):
    """Apply a result to ``p`` like the UI does."""
    p.artifacts.update(res.artifacts)
    p.alert_sets[res.alert_set.id] = res.alert_set
    return res.alert_set


def _earlier_set(p, pdir):
    """December's set over the same pixels, applied to ``p`` like the UI does."""
    p.periods[DEC.id] = DEC
    _prepare(p, pdir, DEC, _clusters(), 2025340)
    return _record(p, integrate.run_integration(_job(p, pdir, DEC, AlertSetParams(min_size_ha=0.1))))


@_LONLAT_CENTROID
def test_every_pixel_already_reported_still_records_an_empty_set(ready_project):
    """Review Focus 1: January finds only pixels December's set reported."""
    p, pdir = ready_project
    dec = _earlier_set(p, pdir)
    assert dec.n_alerts == 2
    params = AlertSetParams(min_size_ha=0.1)
    res = integrate.run_integration(_job(p, pdir, JAN, params))
    s = res.alert_set
    assert s.n_alerts == 0 and len(res.alerts) == 0 and res.alerts.crs.to_epsg() == 4326
    assert {"alert_id", "set_id", "alert_sources"} <= set(res.alerts.columns)
    assert s.footprint_from == [dec.id]
    assert s.fingerprint == fp.fingerprint(fp.set_inputs(p, JAN, ["GLAD-L", "RADD"], params))
    with rasterio.open(pdir / s.combined_rel_path) as c, rasterio.open(pdir / s.footprint_rel_path) as f:
        assert not c.read().any() and not f.read(1).any()
    assert len(gpd.read_parquet(pdir / s.store_rel_path)) == 0


@_LONLAT_CENTROID
def test_run_integration_refuses_a_missing_earlier_footprint(ready_project):
    p, pdir = ready_project
    dec = _earlier_set(p, pdir)
    (pdir / dec.footprint_rel_path).unlink()
    with pytest.raises(ValueError, match="footprint"):
        integrate.run_integration(_job(p, pdir, JAN, AlertSetParams(min_size_ha=0.1)))


def _set_files(pdir, s):
    return [pdir / s.combined_rel_path, pdir / s.footprint_rel_path, pdir / s.store_rel_path]


def _snapshot(files):
    return {f.name: (hashlib.sha256(f.read_bytes()).hexdigest(), f.stat().st_mtime_ns) for f in files}


def _leftovers(pdir):
    """Staged or half-written files anywhere in the project folder."""
    return sorted(str(f) for f in pdir.rglob("*") if f.is_file() and (".staged" in f.name or f.name.endswith(".part")))


_STEPS = ["steps.integrate.step_combine", "steps.integrate.step_vectorize",
          "steps.integrate.step_store", "steps.integrate.step_footprint"]


@_LONLAT_CENTROID
def test_a_replace_swaps_all_three_files_and_reports_each_step_once(ready_project):
    p, pdir = ready_project
    old = _record(p, integrate.run_integration(_job(p, pdir, JAN, AlertSetParams(min_size_ha=0.1))))
    old_ids = {k: p.artifacts[k].content_id
               for k in (fp.combined_key(old.id), fp.footprint_key(old.id), fp.store_key(old.id))}
    steps = []
    res = integrate.run_integration(_job(p, pdir, JAN, AlertSetParams(min_size_ha=0.1, max_alerts=1)),
                                    on_step=steps.append)
    s = res.alert_set
    assert steps == _STEPS
    # same set, same files, new content
    assert (s.id, s.combined_rel_path, s.footprint_rel_path, s.store_rel_path) == (
        old.id, old.combined_rel_path, old.footprint_rel_path, old.store_rel_path)
    with rasterio.open(pdir / s.footprint_rel_path) as src:
        assert int(src.read(1).sum()) == 64
    assert len(gpd.read_parquet(pdir / s.store_rel_path)) == 1 == s.n_alerts
    for key, art in res.artifacts.items():
        assert art.content_id != old_ids[key]
        assert art.size_bytes == (pdir / art.rel_path).stat().st_size > 0
    assert _leftovers(pdir) == []


class _Interrupted(BaseException):
    """Not an Exception: the cleanup must not depend on the error's class."""


def _unreadable_attribute(monkeypatch, pdir):
    bad = pdir / "attribute.tif"
    bad.write_bytes(b"not a raster")
    return {"source": str(bad), "kind": "local", "bands": [{"band": "band_1", "stat": "max", "column": "attr"}]}


def _interrupted_after_footprint(monkeypatch, pdir):
    real = integrate.rasterize_footprint

    def rasterize_then_stop(*args, **kwargs):
        real(*args, **kwargs)  # every staged file now exists
        raise _Interrupted()

    monkeypatch.setattr(integrate, "rasterize_footprint", rasterize_then_stop)
    return None


@_LONLAT_CENTROID
@pytest.mark.parametrize("fail, error", [
    (_unreadable_attribute, rasterio.errors.RasterioIOError),  # the attribute step, like a failed GEE export
    (_interrupted_after_footprint, _Interrupted),                # the last step before the swap
], ids=["attribute", "after-footprint"])
def test_a_failed_replace_leaves_the_recorded_set_untouched(ready_project, monkeypatch, fail, error):
    """Spec §5.3/§12: the manifest keeps the old content ids, so a failed Replace
    must leave the recorded set's files byte-identical and untouched."""
    p, pdir = ready_project
    old = _record(p, integrate.run_integration(_job(p, pdir, JAN, AlertSetParams(min_size_ha=0.1))))
    files = _set_files(pdir, old)
    before, listing = _snapshot(files), sorted(f.name for f in files[0].parent.iterdir())
    config = fail(monkeypatch, pdir)
    # the replace would write a different set: one alert, a 64-px footprint
    params = AlertSetParams(min_size_ha=0.1, max_alerts=1, attribute_config=config)
    with pytest.raises(error):
        integrate.run_integration(_job(p, pdir, JAN, params))
    assert _snapshot(files) == before
    assert sorted(f.name for f in files[0].parent.iterdir()) == listing
    assert _leftovers(pdir) == []


@_LONLAT_CENTROID
def test_a_failed_first_run_leaves_no_files(ready_project, monkeypatch):
    p, pdir = ready_project
    config = _unreadable_attribute(monkeypatch, pdir)
    job = _job(p, pdir, JAN, AlertSetParams(min_size_ha=0.1, attribute_config=config))
    with pytest.raises(rasterio.errors.RasterioIOError):
        integrate.run_integration(job)
    sdir = paths.set_dir(pdir, JAN.id, ["GLAD-L", "RADD"])
    assert not sdir.exists() or list(sdir.iterdir()) == []
    assert _leftovers(pdir) == []
