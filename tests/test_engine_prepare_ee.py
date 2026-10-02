"""Tier 2: native download and harmonize against real Earth Engine. Skips without credentials."""

from datetime import date, datetime

import numpy as np
import pytest
import rasterio

from deforestation_alerts.engine import catalogue, download, raster_io, runner
from deforestation_alerts.engine import fingerprint as fp
from deforestation_alerts.engine.grid import auto_utm_epsg, build_grid
from deforestation_alerts.project.models import AoiRecord, Period, Project, add_predefined_source

BOUNDS = (-55.10, -10.10, -55.00, -10.00)  # tiny_aoi
# GLAD-L held 244 alert pixels in tiny_aoi over 2025 (checked 2026-09-30)
Y2025 = Period(id="2025-01-01_2025-12-31", start=date(2025, 1, 1), end=date(2025, 12, 31))

# geedim looks every exported image up in its STAC catalogue and warns when a
# computed image (no asset id) has no entry, and it still multiplies Affines
# with ``*``, which affine 3 deprecates: nothing to act on in either.
pytestmark = [
    pytest.mark.filterwarnings("ignore:Couldn't find STAC entry:RuntimeWarning"),
    pytest.mark.filterwarnings("ignore:Use `@` matmul:PendingDeprecationWarning:geedim"),
]


@pytest.mark.parametrize("key", catalogue.KEYS)
def test_native_table_matches_earth_engine(gee, key):
    import ee

    if key == "GLAD-L":
        image = ee.ImageCollection(catalogue.GLAD_L_UPD).first()
    elif key == "RADD":
        image = (ee.ImageCollection(catalogue.RADD_COLLECTION)
                 .filterMetadata("layer", "contains", "alert").sort("system:time_end", False).first())
    else:
        image = ee.Image(catalogue.GLAD_S2 + "/alert")
    proj = image.select(0).projection()
    info = ee.Dictionary({"crs": proj.crs(), "scale": proj.nominalScale()}).getInfo()
    crs, scale = catalogue.NATIVE[key]
    assert info["crs"] == crs and info["scale"] == pytest.approx(scale)


def test_glad_l_downloads_natively_and_harmonizes_onto_a_utm_grid(gee, tiny_aoi, tmp_path, real_cover):
    p = Project(name="t2", created_at=datetime.now(), updated_at=datetime.now(), look_back_days=0)
    p.aoi = AoiRecord(spec={}, name="tiny", method="DRAW", digest="tiny", bounds=BOUNDS)
    add_predefined_source(p, "GLAD-L")
    p.grid = build_grid(BOUNDS, auto_utm_epsg(BOUNDS), 30)
    p.periods[Y2025.id] = Y2025
    real_cover(p, tiny_aoi, "GLAD-L")
    meta = catalogue.meta_request("GLAD-L").getInfo()
    tiles = []
    arts = runner.run_source_row(
        runner.SourceJob(project=p.model_copy(deep=True), pdir=tmp_path, period=Y2025, source="GLAD-L",
                         meta=meta, aoi=tiny_aoi),
        on_tiles=lambda done, total: tiles.append((done, total)),
    )
    assert tiles and tiles[-1][0] == tiles[-1][1]
    raw_path = tmp_path / arts[fp.raw_key(Y2025.id, "GLAD-L")].rel_path
    harm_path = tmp_path / arts[fp.harm_key(Y2025.id, "GLAD-L")].rel_path

    # the raw file is GLAD-L's own lattice: 0.00025 deg pixels on multiples of
    # 0.00025 deg, so Earth Engine resampled nothing
    px = download.native_pixel_size(catalogue.NATIVE["GLAD-L"])
    with rasterio.open(raw_path) as src:
        assert src.crs.to_epsg() == 4326 and src.dtypes == ("int32", "int32") and src.nodata == 0
        t = src.transform
        assert t.a == pytest.approx(px, rel=1e-9) and t.e == pytest.approx(-px, rel=1e-9)
        assert t.c / px == pytest.approx(round(t.c / px), abs=1e-6)
        assert t.f / px == pytest.approx(round(t.f / px), abs=1e-6)
        raw_alert, raw_dates = src.read(1), src.read(2)

    raster_io.check_header(harm_path, p.grid)
    with rasterio.open(harm_path) as src:
        alert, dates = src.read(1), src.read(2)

    n_raw, n_harm = int((raw_alert > 0).sum()), int((alert > 0).sum())
    assert n_raw > 100, f"only {n_raw} GLAD-L alert pixels in tiny_aoi in 2025"
    # the alert pixels survive: the same area (27.8 m -> 30 m pixels) ...
    raw_area = n_raw * (px * download.METRES_PER_DEGREE) ** 2 * np.cos(np.radians(10.05))
    assert n_harm * 30.0**2 == pytest.approx(raw_area, rel=0.25)
    # ... with only the codes and dates the download holds
    assert set(np.unique(alert[alert > 0])) <= set(np.unique(raw_alert[raw_alert > 0]))
    assert set(np.unique(dates[alert > 0])) <= set(np.unique(raw_dates[raw_alert > 0]))
    assert not dates[alert == 0].any()
