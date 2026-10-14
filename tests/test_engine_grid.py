from datetime import datetime

import pytest

from deforestation_alerts.engine import grid
from deforestation_alerts.project.models import Project, add_predefined_source

PY_BOUNDS = (-55.10, -10.10, -55.00, -10.00)  # WGS84, southern hemisphere


def test_auto_utm_south_and_north():
    assert grid.auto_utm_epsg(PY_BOUNDS) == "EPSG:32721"
    assert grid.auto_utm_epsg((2.0, 48.0, 2.5, 48.5)) == "EPSG:32631"


@pytest.mark.parametrize("epsg,res,err", [
    ("", 10, "need_epsg"),
    ("EPSG:999999", 10, "bad_epsg"),
    ("4326", 10, "geographic"),
    ("2229", 10, "geographic"),  # projected, but in US survey feet (ruling #42)
    ("32721", "abc", "bad_resolution"),
    ("32721", 0, "bad_resolution"),
    ("32721", 10, None),
])
def test_validate_projection(epsg, res, err):
    assert grid.validate_projection(epsg, res) == err


def test_build_grid_snaps_outward_to_resolution():
    g = grid.build_grid(PY_BOUNDS, "32721", 10)
    assert g.epsg == "EPSG:32721"
    assert g.xmin % 10 == 0 and g.ymax % 10 == 0
    # the snapped grid covers the transformed bounds
    from pyproj import Transformer
    t = Transformer.from_crs("EPSG:4326", "EPSG:32721", always_xy=True)
    xmin, ymin, xmax, ymax = t.transform_bounds(*PY_BOUNDS, densify_pts=21)
    assert g.xmin <= xmin and g.ymax >= ymax
    assert g.xmin + g.width * 10 >= xmax and g.ymax - g.height * 10 <= ymin


def test_grid_covers_an_aoi_spanning_several_utm_zones():
    wide = (-58.0, -25.0, -47.0, -18.0)  # ~11 degrees of longitude: three UTM zones
    g = grid.build_grid(wide, grid.auto_utm_epsg(wide), 30)
    from pyproj import Transformer
    t = Transformer.from_crs("EPSG:4326", g.epsg, always_xy=True)
    for lon, lat in [(-58.0, -25.0), (-47.0, -25.0), (-58.0, -18.0), (-47.0, -18.0), (-52.5, -21.5)]:
        x, y = t.transform(lon, lat)
        assert g.xmin <= x <= g.xmin + g.width * 30 and g.ymax - g.height * 30 <= y <= g.ymax


def test_estimate_bytes():
    g = grid.build_grid(PY_BOUNDS, "32721", 10)
    assert grid.estimate_bytes(g) == g.width * g.height * 2 * 4


def test_default_resolution_is_finest_known_source():
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    assert grid.default_resolution(p) == 30.0
    add_predefined_source(p, "GLAD-L")
    assert grid.default_resolution(p) == 30.0
    add_predefined_source(p, "RADD")
    assert grid.default_resolution(p) == 10.0


def test_default_resolution_counts_custom_native_scales():
    from datetime import datetime

    from deforestation_alerts.engine import grid as gridmod
    from deforestation_alerts.project.models import (
        BandMap,
        NativeGrid,
        Project,
        add_custom_source,
        add_predefined_source,
    )

    p = Project(name="g", created_at=datetime.now(), updated_at=datetime.now())
    add_predefined_source(p, "GLAD-L")
    add_custom_source(p, name="fine", origin="gee", ref="a", asset_type="IMAGE", format="radd",
                      date_encoding="yyddd", band_map=BandMap(alert="Alert", date="Date"),
                      native=NativeGrid(crs="EPSG:4326", scale=5.0))
    assert gridmod.default_resolution(p) == 5.0
