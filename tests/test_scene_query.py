# tests/test_scene_query.py
from datetime import date, datetime, timedelta, timezone

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon, box

from deforestation_alerts.scripts import scene_index, scene_query

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
WANT = (date(2026, 3, 3), date(2026, 10, 23))


def _manifest(start, end, *, age_hours=1.0, digest="abc", version=scene_index.INDEX_VERSION):
    return scene_index.SceneManifest(
        version=version,
        aoi_digest=digest,
        refreshed=NOW - timedelta(hours=age_hours),
        covered=scene_index.Coverage(start=start, end=end),
        counts={},
    )


def test_no_manifest_means_rebuild():
    assert scene_query.needs_refresh(None, WANT, digest="abc", now=NOW) == "rebuild"


def test_a_different_aoi_means_rebuild():
    m = _manifest(*WANT, digest="other")
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == "rebuild"


def test_an_older_index_version_means_rebuild():
    m = _manifest(*WANT, version=scene_index.INDEX_VERSION - 1)
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == "rebuild"


def test_a_fresh_covering_index_needs_nothing():
    m = _manifest(date(2026, 1, 1), date(2026, 12, 31))
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == ""


def test_a_window_past_the_covered_end_is_a_widen_not_a_rebuild():
    m = _manifest(date(2026, 1, 1), date(2026, 9, 1))
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == "widen"


def test_coverage_beats_age_when_both_apply():
    m = _manifest(date(2026, 1, 1), date(2026, 9, 1), age_hours=40)
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == "widen"


def test_a_covering_index_older_than_the_ttl_needs_its_tail():
    m = _manifest(date(2026, 1, 1), date(2026, 12, 31), age_hours=24.5)
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == "tail"


def test_exactly_at_the_ttl_is_not_yet_stale():
    m = _manifest(date(2026, 1, 1), date(2026, 12, 31), age_hours=24.0)
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == ""


def test_a_rebuild_fetches_the_whole_wanted_window():
    assert scene_query.refresh_window(None, WANT, "rebuild") == WANT


def test_a_tail_refetch_starts_ten_days_before_the_covered_end():
    m = _manifest(date(2026, 1, 1), date(2026, 9, 20))
    assert scene_query.refresh_window(m, WANT, "tail") == (date(2026, 9, 10), date(2026, 10, 23))


def test_a_widen_takes_the_later_side_first():
    m = _manifest(date(2026, 5, 1), date(2026, 9, 1))
    assert scene_query.refresh_window(m, WANT, "widen") == (date(2026, 9, 1), date(2026, 10, 23))


def test_a_widen_takes_the_earlier_side_once_the_later_one_is_covered():
    m = _manifest(date(2026, 5, 1), date(2026, 11, 1))
    assert scene_query.refresh_window(m, WANT, "widen") == (date(2026, 3, 3), date(2026, 5, 1))


def test_nothing_to_fetch_raises_rather_than_returning_an_empty_range():
    m = _manifest(date(2026, 1, 1), date(2026, 12, 31))
    with pytest.raises(ValueError):
        scene_query.refresh_window(m, WANT, "")


def test_scene_query_never_imports_earth_engine():
    src = open(scene_query.__file__).read()
    assert "import ee" not in src and "from ee" not in src


# --------------------------------------------------------------- scenes_for

S2, LS, PL = scene_index.SOURCE_S2, scene_index.SOURCE_LANDSAT, scene_index.SOURCE_PLANET

#  west tile [0,0]-[2,2]   east tile [2,0]-[4,2]   wide tile [-1,-1]-[5,3]
TILES = gpd.GeoDataFrame(
    {"tile": ["W", "E", "WIDE", "SLANT"]},
    geometry=gpd.GeoSeries(
        [
            box(0, 0, 2, 2),
            box(2, 0, 4, 2),
            box(-1, -1, 5, 3),
            # a parallelogram whose envelope covers (1, 1) but which does not
            Polygon([(3, -1), (5, -1), (3, 3), (1, 3)]),
        ],
        crs="EPSG:4326",
    ),
    crs="EPSG:4326",
)


def _scenes(rows):
    return pd.DataFrame(rows, columns=list(scene_index.SCENE_COLUMNS)).astype({"date": "datetime64[ns]"})


def _tables(rows):
    return scene_index.SceneTables(scenes=_scenes(rows), tiles=TILES)


ALERT = box(0.5, 0.5, 1.5, 1.5)  # inside W only


def test_only_the_tiles_that_overlap_the_alert_contribute():
    tables = _tables([
        (S2, pd.Timestamp("2026-05-10"), 10.0, "W", "kw"),
        (S2, pd.Timestamp("2026-05-10"), 90.0, "E", "ke"),
    ])
    sel = scene_query.scenes_for(tables, ALERT, date(2026, 5, 1), date(2026, 5, 31))
    assert len(sel) == 1
    assert sel.iloc[0]["n"] == 1
    assert sel.iloc[0]["cloud"] == pytest.approx(10.0)  # not 50.0: E never touches the alert


def test_overlapping_granules_of_one_date_become_one_row_with_their_mean_cloud():
    tables = _tables([
        (S2, pd.Timestamp("2026-05-10"), 10.0, "W", "kw"),
        (S2, pd.Timestamp("2026-05-10"), 30.0, "WIDE", "kd"),
    ])
    sel = scene_query.scenes_for(tables, ALERT, date(2026, 5, 1), date(2026, 5, 31))
    assert len(sel) == 1 and sel.iloc[0]["n"] == 2
    assert sel.iloc[0]["cloud"] == pytest.approx(20.0)
    assert sel.iloc[0]["id"] == scene_index.row_id(S2, date(2026, 5, 10))


def test_a_footprint_whose_envelope_covers_the_alert_is_still_excluded():
    tables = _tables([(LS, pd.Timestamp("2026-05-12"), 1.0, "SLANT", "ks")])
    assert TILES.set_index("tile").loc["SLANT", "geometry"].envelope.intersects(ALERT)
    sel = scene_query.scenes_for(tables, ALERT, date(2026, 5, 1), date(2026, 5, 31))
    assert len(sel) == 0


def test_two_collections_on_one_date_stay_two_rows():
    tables = _tables([
        (S2, pd.Timestamp("2026-05-10"), 10.0, "W", "kw"),
        (LS, pd.Timestamp("2026-05-10"), 20.0, "WIDE", "kd"),
    ])
    sel = scene_query.scenes_for(tables, ALERT, date(2026, 5, 1), date(2026, 5, 31))
    assert sorted(sel["source"]) == [LS, S2]


def test_a_planet_row_has_no_tile_and_covers_every_alert():
    tables = _tables([(PL, pd.Timestamp("2026-05-01"), float("nan"), "", "kp")])
    sel = scene_query.scenes_for(tables, box(40, 40, 41, 41), date(2026, 5, 1), date(2026, 5, 31))
    assert len(sel) == 1 and sel.iloc[0]["source"] == PL


def test_the_date_window_is_inclusive_at_both_ends():
    tables = _tables([
        (S2, pd.Timestamp("2026-05-01"), 1.0, "W", "a"),
        (S2, pd.Timestamp("2026-05-31"), 1.0, "W", "b"),
        (S2, pd.Timestamp("2026-06-01"), 1.0, "W", "c"),
    ])
    sel = scene_query.scenes_for(tables, ALERT, date(2026, 5, 1), date(2026, 5, 31))
    assert list(sel["date"]) == [pd.Timestamp("2026-05-01"), pd.Timestamp("2026-05-31")]


def test_a_row_over_the_cloud_limit_is_kept_and_marked_hidden():
    tables = _tables([
        (S2, pd.Timestamp("2026-05-10"), 12.0, "W", "a"),
        (S2, pd.Timestamp("2026-05-20"), 72.0, "W", "b"),
    ])
    sel = scene_query.scenes_for(tables, ALERT, date(2026, 5, 1), date(2026, 5, 31), max_cloud=40.0)
    assert list(sel["hidden"]) == [False, True]


def test_a_planet_row_is_never_hidden_by_the_cloud_limit():
    tables = _tables([(PL, pd.Timestamp("2026-05-01"), float("nan"), "", "kp")])
    sel = scene_query.scenes_for(tables, ALERT, date(2026, 5, 1), date(2026, 5, 31), max_cloud=0.0)
    assert list(sel["hidden"]) == [False]


def test_an_alert_outside_every_tile_returns_an_empty_frame_with_the_columns():
    # Review Focus #3: an alert on the AOI edge, or in a gap in the index.
    tables = _tables([(S2, pd.Timestamp("2026-05-10"), 10.0, "W", "kw")])
    sel = scene_query.scenes_for(tables, box(80, 80, 81, 81), date(2026, 5, 1), date(2026, 5, 31))
    assert len(sel) == 0
    assert list(sel.columns) == list(scene_query.SELECTION_COLUMNS)
    assert scene_query.source_counts(sel) == {S2: (0, 0), LS: (0, 0), PL: (0, 0)}
    assert scene_query.month_counts(sel) == {}


def test_an_empty_index_returns_an_empty_frame_with_the_columns():
    sel = scene_query.scenes_for(scene_index.empty_tables(), ALERT, date(2026, 5, 1), date(2026, 5, 31))
    assert len(sel) == 0 and list(sel.columns) == list(scene_query.SELECTION_COLUMNS)


def test_source_counts_split_visible_from_hidden():
    tables = _tables([
        (S2, pd.Timestamp("2026-05-10"), 12.0, "W", "a"),
        (S2, pd.Timestamp("2026-05-20"), 72.0, "W", "b"),
        (LS, pd.Timestamp("2026-05-21"), 5.0, "WIDE", "c"),
    ])
    sel = scene_query.scenes_for(tables, ALERT, date(2026, 5, 1), date(2026, 5, 31), max_cloud=40.0)
    assert scene_query.source_counts(sel) == {S2: (1, 1), LS: (1, 0), PL: (0, 0)}


def test_month_counts_count_only_the_visible_rows():
    tables = _tables([
        (S2, pd.Timestamp("2026-05-10"), 12.0, "W", "a"),
        (S2, pd.Timestamp("2026-05-20"), 72.0, "W", "b"),
        (S2, pd.Timestamp("2026-06-04"), 3.0, "W", "c"),
    ])
    sel = scene_query.scenes_for(tables, ALERT, date(2026, 5, 1), date(2026, 6, 30), max_cloud=40.0)
    assert scene_query.month_counts(sel) == {(2026, 5): 1, (2026, 6): 1}
