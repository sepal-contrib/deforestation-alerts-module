"""Alert ids start at 1, and every name a person reads takes the stored id
(officer critique 2026-11-11). Old sets keep their 0-based ids and names."""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from deforestation_alerts.engine import integrate
from deforestation_alerts.project.models import AlertSetParams
from deforestation_alerts.project.review_groups import group_record, number
from tests.test_analysis_download import _run, download_ctx  # noqa: F401 - fixture
from tests.test_engine_integrate import _LONLAT_CENTROID, _job, JAN, ready_project  # noqa: F401 - fixture


pytestmark = pytest.mark.usefixtures("no_admin_boundaries")

@_LONLAT_CENTROID
def test_integrate_numbers_from_one(ready_project):  # noqa: F811
    p, pdir = ready_project
    result = integrate.run_integration(_job(p, pdir, JAN, AlertSetParams(min_size_ha=0.1, sorting=1)))
    assert len(result.alerts) > 1
    assert list(result.alerts["alert_id"]) == list(range(1, len(result.alerts) + 1))


def _gdf(alert_ids=None, base=None):
    gdf = gpd.GeoDataFrame(
        pd.concat([base] * 3, ignore_index=True) if base is not None else {"area_ha": [1.0] * 3},
        geometry=[box(i * 2, 0, i * 2 + 1, 1) for i in range(3)], crs="EPSG:4326")
    if alert_ids is not None:
        gdf["alert_id"] = alert_ids
    return gdf


def test_number_is_the_stored_id():
    assert number(_gdf([1, 2, 3]), 0) == 1
    assert number(_gdf([0, 1, 2]), 0) == 0
    assert number(_gdf(), 2) == 2


def _grouped(gdf):
    """Rows 0 and 2 in one group, row 0 its main."""
    main = int(gdf["alert_id"].iloc[0])
    gdf["review_group"] = pd.array([main, pd.NA, main], dtype="Int64")
    gdf["status"] = ["Confirmed", None, "Confirmed"]
    return gdf


def test_group_alerts_lists_stored_ids():
    assert group_record(_grouped(_gdf([1, 2, 3])), 0)[0]["group_alerts"] == "1, 3"


def _build(ctx, alert_ids):
    gdf = _gdf(alert_ids, base=ctx.app_state.alerts_gdf.value.drop(columns="geometry"))
    gdf["bounding_box"] = gdf.geometry.envelope
    ctx.app_state.alerts_gdf.value = _grouped(gdf)
    ctx.app_state.actual_alert_id.value = 0
    return _run(ctx)


@pytest.mark.parametrize("alert_ids, n, members", [([1, 2, 3], 1, "1, 3"), ([0, 1, 2], 0, "0, 2")],
                         ids=["new", "old"])
def test_alert_files_are_named_by_stored_id(download_ctx, alert_ids, n, members):  # noqa: F811
    _build(download_ctx, alert_ids)
    folder = download_ctx.folder
    for ext in ("gpkg", "zip", "docx"):
        assert (folder / f"alert_{n}.{ext}").exists()
    assert [path.name for path in folder.glob("*.tif")] == [f"alert_{n}_s_010124_s_020224.tif"]
    assert gpd.read_file(folder / f"alert_{n}.gpkg", layer=f"alert_{n}").iloc[0]["group_alerts"] == members


def test_old_store_keeps_its_numbers():
    assert group_record(_grouped(_gdf([0, 1, 2])), 0)[0]["group_alerts"] == "0, 2"
