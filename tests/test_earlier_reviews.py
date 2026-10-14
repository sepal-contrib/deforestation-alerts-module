from datetime import date, datetime

import geopandas as gpd
import pytest
from shapely.geometry import box

from deforestation_alerts.engine.fingerprint import earlier_sets
from deforestation_alerts.project.models import AlertSet, AlertSetParams, Period, Project
from gui.scripts import earlier_reviews

DEC = Period(id="2025-12-01_2025-12-31", start=date(2025, 12, 1), end=date(2025, 12, 31))
DEC_B = Period(id="2025-12-01_2025-12-31b", start=date(2025, 12, 1), end=date(2025, 12, 31))
JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
FEB = Period(id="2026-02-01_2026-02-28", start=date(2026, 2, 1), end=date(2026, 2, 28))


def _set(p, sid, period):
    p.alert_sets[sid] = AlertSet(
        id=sid, period_id=period.id, sources=["GLAD-L"], digit_map={0: "GLAD-L"}, params=AlertSetParams(),
        store_rel_path=f"periods/{period.id}/{sid}/alert_db.parquet", combined_rel_path="c.tif",
        footprint_rel_path="f.tif", fingerprint="x", created_at=datetime.now())
    return p.alert_sets[sid]


@pytest.fixture
def project_with_two_periods():
    now = datetime.now()
    p = Project(name="er", created_at=now, updated_at=now)
    for per in (DEC, DEC_B, JAN, FEB):
        p.periods[per.id] = per
    _set(p, "dec", DEC)
    _set(p, "jan", JAN)
    _set(p, "dec_b", DEC_B)
    _set(p, "feb", FEB)
    assert [s.id for s in earlier_sets(p, JAN)] == ["dec", "dec_b"]
    earlier_reviews.clear_cache()
    yield p
    earlier_reviews.clear_cache()


def _alerts(rows):
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def _row(i, status, poly, cen=None):
    return {"alert_id": i, "status": status, "alert_polygon": poly,
            "geometry": (cen or poly).centroid if poly is not None else cen,
            "alert_date_min": 2025.340, "alert_date_max": 2025.345}


NEAR = box(-55.0001, -25.0001, -54.9999, -24.9999)
FAR = box(-54.9, -25.0001, -54.8998, -24.9999)  # ~10 km east


def test_keeps_reviewed_polygons_within_the_radius(project_with_two_periods):
    p = project_with_two_periods
    gdf = _alerts([
        _row(0, "Confirmed", NEAR), _row(1, "Need revision", FAR),
        _row(2, "False Positive", NEAR), _row(3, "Not reviewed", None, NEAR.centroid),
        _row(4, "Confirmed", None, NEAR.centroid),
    ])
    load = lambda p_, s: gdf  # noqa: E731
    fc = earlier_reviews.near_reviews(p, "jan", -55.0, -25.0, 1000, load=load)
    ids = [(f["properties"]["set_id"], f["properties"]["alert_id"]) for f in fc["features"]]
    assert ids == [("dec", 0), ("dec_b", 0)]
    assert {f["properties"]["alert_id"] for f in fc["features"]} == {0}
    f = fc["features"][0]
    assert f["properties"]["status"] == "Confirmed"
    assert f["properties"]["date_min"] == 2025.340 and f["properties"]["date_max"] == 2025.345
    assert f["geometry"]["type"] == "Polygon"
    fc = earlier_reviews.near_reviews(p, "jan", -55.0, -25.0, 20000, load=load)
    assert sorted({f["properties"]["alert_id"] for f in fc["features"]}) == [0, 1]


def test_only_sets_of_earlier_periods_are_read(project_with_two_periods):
    p = project_with_two_periods
    seen = []

    def load(p_, s):
        seen.append(s.id)
        return _alerts([_row(0, "Confirmed", NEAR)])

    # DEC_B starts the same day as DEC: both are earlier than JAN; FEB is later, JAN itself is excluded
    earlier_reviews.near_reviews(p, "jan", -55.0, -25.0, 1000, load=load)
    assert sorted(seen) == ["dec", "dec_b"]
    seen.clear()
    earlier_reviews.near_reviews(p, "dec", -55.0, -25.0, 1000, load=load)
    assert seen == []  # nothing is earlier than the first period; dec_b (same period) is excluded


def test_a_missing_store_is_skipped(project_with_two_periods):
    fc = earlier_reviews.near_reviews(project_with_two_periods, "jan", -55.0, -25.0, 1000,
                                      load=lambda p_, s: None)
    assert fc == {"type": "FeatureCollection", "features": []}


def test_loads_are_cached_per_set_and_store_generation(project_with_two_periods):
    p = project_with_two_periods
    calls = []

    def load(p_, s):
        calls.append(s.id)
        return _alerts([_row(0, "Confirmed", NEAR)])

    earlier_reviews.near_reviews(p, "jan", -55.0, -25.0, 1000, load=load)
    earlier_reviews.near_reviews(p, "jan", -55.0, -25.0, 5000, load=load)
    assert sorted(calls) == ["dec", "dec_b"]
    earlier_reviews.clear_cache()
    earlier_reviews.near_reviews(p, "jan", -55.0, -25.0, 1000, load=load)
    assert len(calls) == 4


def test_a_rewritten_store_is_reloaded(project_with_two_periods, tmp_path, monkeypatch):
    p = project_with_two_periods
    gens = iter([(1,), (1,), (2,), (2,)])
    monkeypatch.setattr(earlier_reviews.set_store, "store_generation", lambda store: next(gens))
    calls = []

    def load(p_, s):
        calls.append(s.id)
        return None

    for _ in range(2):
        earlier_reviews.near_reviews(p, "jan", -55.0, -25.0, 1000, load=load)
    assert len(calls) == 4  # generation of each set changed between the calls


def test_distance_is_measured_in_the_grid_crs(project_with_two_periods):
    from deforestation_alerts.project.models import GridSpec
    p = project_with_two_periods
    p.grid = GridSpec(epsg="EPSG:32721", resolution=10, xmin=0, ymax=0, width=1, height=1)
    gdf = _alerts([_row(0, "Confirmed", NEAR), _row(1, "Need revision", FAR)])
    fc = earlier_reviews.near_reviews(p, "jan", -55.0, -25.0, 1000, load=lambda p_, s: gdf)
    assert {f["properties"]["alert_id"] for f in fc["features"]} == {0}
