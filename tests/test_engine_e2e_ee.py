"""Tier 2: grid -> download -> harmonize -> integrate against real Earth Engine.
Skips without credentials (the ``gee`` fixture)."""

from datetime import date, datetime

import pytest
import rasterio

from deforestation_alerts.engine import catalogue, grid as gridmod
from deforestation_alerts.engine.integrate import IntegrationJob, run_integration
from deforestation_alerts.engine.runner import SourceJob, apply_artifacts, run_source_row
from deforestation_alerts.project.models import (
    AlertSetParams, AoiRecord, Period, Project, add_predefined_source,
)
from deforestation_alerts.scripts.workspace import Workspace

BOUNDS = (-55.10, -10.10, -55.00, -10.00)
# A single month (ruling #95: the brief's Jan-Mar window did not match this
# test's name); GLAD-L Q1 2024 is served by the 2023final archive (cut
# 2024-07-16, see catalogue.GLAD_L_FINAL), so the window is covered.
PERIOD = Period(id="2024-01-01_2024-01-31", start=date(2024, 1, 1), end=date(2024, 1, 31))

# geedim looks every exported image up in its STAC catalogue and warns when a
# computed image (no asset id) has no entry, and it still multiplies Affines
# with ``*``, which affine 3 deprecates: nothing to act on in either (ported
# from tests/test_engine_prepare_ee.py, which every export_native call hits).
# convert_to_geopandas's centroid on EPSG:4326 triggers geopandas's
# geographic-CRS warning for alert-sized polygons (tests/test_engine_integrate.py).
pytestmark = [
    pytest.mark.filterwarnings("ignore:Couldn't find STAC entry:RuntimeWarning"),
    pytest.mark.filterwarnings("ignore:Use `@` matmul:PendingDeprecationWarning:geedim"),
    pytest.mark.filterwarnings("ignore:Geometry is in a geographic CRS:UserWarning"),
]


def test_glad_l_and_radd_month_end_to_end(gee, tiny_aoi, tmp_path):
    p = Project(name="e2e", created_at=datetime.now(), updated_at=datetime.now(), look_back_days=0)
    p.aoi = AoiRecord(spec={}, name="tiny", method="DRAW", digest="tiny", bounds=BOUNDS)
    add_predefined_source(p, "GLAD-L")
    add_predefined_source(p, "RADD")
    p.grid = gridmod.build_grid(BOUNDS, gridmod.auto_utm_epsg(BOUNDS), 30)
    p.periods[PERIOD.id] = PERIOD
    p.active_period = PERIOD.id
    for name in ("GLAD-L", "RADD"):
        meta = catalogue.meta_request(name).getInfo()
        arts = run_source_row(SourceJob(project=p.model_copy(deep=True), pdir=tmp_path, period=PERIOD,
                                        source=name, meta=meta, aoi=tiny_aoi))
        apply_artifacts(p, arts)
    harm = [a for k, a in p.artifacts.items() if k.startswith("harm:")]
    assert len(harm) == 2
    for a in harm:
        with rasterio.open(tmp_path / a.rel_path) as src:
            assert src.dtypes == ("int32", "int32") and src.nodata == 0
            assert (src.width, src.height) == (p.grid.width, p.grid.height)
    workspace = Workspace(root=tmp_path, scratch_parent=tmp_path / "scratch")
    res = run_integration(IntegrationJob(project=p, pdir=tmp_path, period=PERIOD, sources=["GLAD-L", "RADD"],
                                         params=AlertSetParams(min_size_ha=0.0), workspace=workspace))
    assert (tmp_path / res.alert_set.store_rel_path).exists()
    if len(res.alerts):
        assert set(res.alerts["alert_sources"]) <= {"GLAD-L", "RADD", "GLAD-L and RADD", "RADD and GLAD-L"}
