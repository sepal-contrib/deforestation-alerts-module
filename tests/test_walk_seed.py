"""The browser-walk seeder writes a project the app opens as if the AOI had been drawn."""

import json

import geopandas as gpd

from deforestation_alerts.project import io, paths
from gui.scripts import aoi_io
from tools.walk.seed_project import seed


def test_seed_writes_a_loadable_project_with_its_sidecar(tmp_path):
    p = seed("walk_a", (-55.10, -10.10, -55.00, -10.00), sources=("GLAD-L", "RADD"),
             period=("2026-06-01", "2026-06-30"), look_back=30, root=tmp_path)
    loaded = io.load_project("walk_a", root=tmp_path)
    assert loaded.aoi.method == "DRAW" and loaded.aoi.geometry_file == paths.AOI_SIDECAR
    assert loaded.aoi.spec["method"] == "DRAW" and loaded.aoi.spec["geo_json"]["type"] == "FeatureCollection"
    assert list(loaded.sources) == ["GLAD-L", "RADD"]
    assert loaded.active_period == "2026-06-01_2026-06-30" and loaded.look_back_days == 30
    sidecar = gpd.read_file(tmp_path / "walk_a" / paths.AOI_SIDECAR)
    assert aoi_io.geometry_digest(sidecar) == p.aoi.digest == loaded.aoi.digest
    minx, miny, maxx, maxy = loaded.aoi.bounds
    assert (round(minx, 6), round(maxy, 6)) == (-55.1, -10.0)


def test_seed_without_period_or_sources(tmp_path):
    seed("walk_b", (-55.5, -24.5, -55.4, -24.4), root=tmp_path)
    loaded = io.load_project("walk_b", root=tmp_path)
    assert loaded.sources == {} and loaded.periods == {} and loaded.active_period is None
    assert json.loads((tmp_path / "walk_b" / paths.MANIFEST).read_text())["name"] == "walk_b"
