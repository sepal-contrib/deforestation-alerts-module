"""Download & harmonize rows for vector (CSV/GPKG/SHP) alert sources, offline."""

import numpy as np
import pytest
import rasterio
from pyproj import Transformer

from deforestation_alerts.engine import fingerprint as fp
from deforestation_alerts.engine import integrate, raster_io, runner, vector_input
from deforestation_alerts.engine.coverage import record_for
from deforestation_alerts.engine.vector_input import VectorInputError
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import (
    AlertSetParams, BandMap, VectorSpec, add_custom_source, add_predefined_source,
)
from tests.test_engine_custom_rows import GRID, JAN, _both, _job, _project

_TO_LONLAT = Transformer.from_crs(GRID.epsg, "EPSG:4326", always_xy=True)


@pytest.fixture
def no_ee(monkeypatch):
    """Records any Earth Engine use; a download fails the test (as in test_engine_custom_rows)."""
    calls = []

    def export(*a, **k):
        calls.append(a)
        raise AssertionError("Earth Engine was called")

    def build(*a, **k):
        calls.append(a)
        return "IMAGE"

    monkeypatch.setattr(runner, "export_native", export)
    monkeypatch.setattr(runner.catalogue, "build_image", build)
    monkeypatch.setattr(runner.catalogue, "build_custom_image", build)
    return calls


def _centre(col, row):
    """The lon/lat of grid pixel (col, row)'s centre."""
    return _TO_LONLAT.transform(GRID.xmin + (col + 0.5) * GRID.resolution,
                                GRID.ymax - (row + 0.5) * GRID.resolution)


def _write_csv(path, rows):
    lines = ["lon,lat,fecha"] + [f"{lon!r},{lat!r},{d}" for (lon, lat), d in rows]
    path.write_text("\n".join(lines) + "\n")


def _vector_source(p, tmp_path, rows, order="iso"):
    path = tmp_path / "user" / "pts.csv"
    path.parent.mkdir(exist_ok=True)
    _write_csv(path, rows)
    spec = VectorSpec(driver="csv", lon="lon", lat="lat", date="fecha", date_order=order, side_m=30)
    return add_custom_source(p, name="pts", origin="vector", ref=str(path), asset_type="FILE", format="date_only",
                             date_encoding="yyyyddd", band_map=BandMap(alert=None, date="fecha"), native=None,
                             vector=spec, period_id=p.active_period)


def _read(pdir, art):
    with rasterio.open(pdir / art.rel_path) as r:
        return r.read(1), r.read(2)


def test_a_csv_source_runs_and_the_aoi_clip_drops_the_eastern_point(tmp_path, no_ee):
    p = _project(tmp_path)
    _vector_source(p, tmp_path, [(_centre(5, 5), "2026-01-10"), (_centre(25, 5), "2026-01-10")])
    arts = _both(p, tmp_path, "pts")
    assert no_ee == [] and set(arts) == {fp.AOI_KEY, fp.raw_key(JAN.id, "pts"), fp.harm_key(JAN.id, "pts")}
    raw = arts[fp.raw_key(JAN.id, "pts")]
    assert raw.rel_path.endswith(".parquet") and raw.stats["features_in_window"] == 2
    harm = arts[fp.harm_key(JAN.id, "pts")]
    a, d = _read(tmp_path, harm)
    assert a[4:7, 4:7].min() == 1 and int(a.sum()) == 9 and a[:, 20:].max() == 0  # the western square only
    assert d[5, 5] == 2026010 and harm.stats["alert_pixels"] == 9
    runner.apply_artifacts(p, arts)
    for mode in ("download", "harmonize"):  # current: nothing to do
        assert runner.run_source_row(_job(p, tmp_path, "pts", mode=mode)) == {}


def test_a_download_needs_no_grid(tmp_path, no_ee):
    p = _project(tmp_path)
    _vector_source(p, tmp_path, [(_centre(5, 5), "2026-01-10")])
    p.grid = None  # option C: the Alerts step reads the file before any grid exists
    arts = runner.run_source_row(_job(p, tmp_path, "pts"))
    assert set(arts) == {fp.raw_key(JAN.id, "pts")}


def test_a_grid_change_reharmonizes_without_reading_the_user_file(tmp_path, no_ee, monkeypatch):
    p = _project(tmp_path)
    _vector_source(p, tmp_path, [(_centre(5, 5), "2026-01-10")])
    first = _both(p, tmp_path, "pts")
    runner.apply_artifacts(p, first)

    def refuse(*a, **k):
        raise AssertionError("the user file was read")

    monkeypatch.setattr(vector_input, "read_table", refuse)
    p.grid = GRID.model_copy(update={"resolution": 5, "width": 60, "height": 40})
    assert runner.run_source_row(_job(p, tmp_path, "pts")) == {}  # the raw file stays current
    again = runner.run_source_row(_job(p, tmp_path, "pts", mode="harmonize"))
    hkey = fp.harm_key(JAN.id, "pts")
    assert fp.raw_key(JAN.id, "pts") not in again and hkey in again
    assert again[hkey].content_id != first[hkey].content_id
    a, _ = _read(tmp_path, again[hkey])
    assert again[hkey].stats["alert_pixels"] == int(a.sum()) == 36  # the 30 m square at 5 m


def test_a_changed_file_fails_the_row(tmp_path, no_ee):
    p = _project(tmp_path)
    src = _vector_source(p, tmp_path, [(_centre(5, 5), "01/10/2026")], order="mdy")
    _write_csv(tmp_path / "user" / "pts.csv", [(_centre(5, 5), "01/10/2026"), (_centre(6, 5), "25/03/2026")])
    assert src.ref.endswith("pts.csv")
    job = _job(p, tmp_path, "pts")
    with pytest.raises(VectorInputError) as e:
        runner.run_source_row(job)
    assert e.value.key == "steps.prepare.vector_file_changed"
    assert fp.raw_key(JAN.id, "pts") not in job.project.artifacts
    assert not list(tmp_path.glob("periods/**/raw/*"))


def test_vector_sources_never_need_earth_engine_coverage(tmp_path, no_ee):
    from gui.scripts import remote_facts

    p = _project(tmp_path)
    _vector_source(p, tmp_path, [(_centre(5, 5), "2026-01-10")])
    cov = record_for(p, "pts")
    assert cov.fraction == 1.0 and cov.aoi_digest == p.aoi.digest
    assert p.coverage == {}
    assert remote_facts.coverage_needed(p, set())[1] == ()
    p.aoi = p.aoi.model_copy(update={"digest": "other"})
    assert record_for(p, "pts").aoi_digest == "other"
    arts = _both(p, tmp_path, "pts")
    assert fp.harm_key(JAN.id, "pts") in arts


def _record_harmonized(p, pdir, name, arr, day):
    """Record raw and harmonized artifacts on ``p.grid`` for a predefined source."""
    ws, we = JAN.search_window()
    dates = np.where(arr > 0, day, 0)
    for kind, key, path in (
        ("raw_alert", fp.raw_key(JAN.id, name), paths.raw_path(pdir, JAN.id, name, ws, we)),
        ("harmonized", fp.harm_key(JAN.id, name), paths.harmonized_path(pdir, JAN.id, name, ws, we)),
    ):
        with raster_io.write_grid_raster(path, p.grid, count=2) as dst:
            dst.write(arr.astype("int32"), 1)
            dst.write(dates.astype("int32"), 2)
        inputs = (fp.raw_alert_inputs(p, name, JAN) if kind == "raw_alert"
                  else fp.harmonized_inputs(p, name, JAN))
        p.artifacts[key] = fp.make_artifact(key, kind, pdir, path, inputs)


@pytest.mark.filterwarnings("ignore:Geometry is in a geographic CRS:UserWarning")
def test_a_square_touching_a_glad_cluster_is_one_alert_with_both_sources(tmp_path, no_ee):
    p = _project(tmp_path)
    add_predefined_source(p, "GLAD-L", period_id=JAN.id)
    glad = np.zeros((GRID.height, GRID.width), "int64")
    glad[2:6, 2:6] = 1  # a 4x4 cluster, columns 2-5
    _record_harmonized(p, tmp_path, "GLAD-L", glad, 2026015)
    _vector_source(p, tmp_path, [(_centre(7, 4), "2026-01-10")])  # square on columns 6-8: touches column 5
    runner.apply_artifacts(p, _both(p, tmp_path, "pts"))
    job = integrate.IntegrationJob(project=p.model_copy(deep=True), pdir=tmp_path, period=JAN,
                                   sources=["GLAD-L", "pts"], params=AlertSetParams(min_size_ha=0))
    res = integrate.run_integration(job)
    assert len(res.alerts) == 1
    names = res.alerts.iloc[0]["alert_sources"]  # "GLAD-L and pts"
    assert "GLAD-L" in names and "pts" in names


def test_a_polygon_touching_a_glad_cluster_is_one_alert_with_both_sources(tmp_path, no_ee):
    import geopandas as gpd
    from shapely.geometry import box

    p = _project(tmp_path)
    add_predefined_source(p, "GLAD-L", period_id=JAN.id)
    glad = np.zeros((GRID.height, GRID.width), "int64")
    glad[2:6, 2:6] = 1  # a 4x4 cluster, columns 2-5
    _record_harmonized(p, tmp_path, "GLAD-L", glad, 2026015)
    path = tmp_path / "user" / "polys.gpkg"
    path.parent.mkdir(exist_ok=True)
    x0, y0 = GRID.xmin + 6 * GRID.resolution, GRID.ymax - 6 * GRID.resolution
    poly = box(x0, y0, x0 + 3 * GRID.resolution, y0 + 4 * GRID.resolution)  # columns 6-8, rows 2-5
    gpd.GeoDataFrame({"fecha": ["2026-01-10"]}, geometry=[poly], crs=GRID.epsg).to_file(path, layer="alerts")
    spec = VectorSpec(driver="gpkg", layer="alerts", date="fecha", date_order="iso")
    add_custom_source(p, name="polys", origin="vector", ref=str(path), asset_type="FILE", format="date_only",
                      date_encoding="yyyyddd", band_map=BandMap(alert=None, date="fecha"), native=None, vector=spec,
                      period_id=p.active_period)
    runner.apply_artifacts(p, _both(p, tmp_path, "polys"))
    job = integrate.IntegrationJob(project=p.model_copy(deep=True), pdir=tmp_path, period=JAN,
                                   sources=["GLAD-L", "polys"], params=AlertSetParams(min_size_ha=0))
    res = integrate.run_integration(job)
    assert len(res.alerts) == 1
    names = res.alerts.iloc[0]["alert_sources"]
    assert "GLAD-L" in names and "polys" in names
