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


# ----------------------------------------------------------------- axis


def test_months_between_spans_the_end_month_inclusively():
    assert scene_query.months_between(date(2026, 11, 20), date(2027, 2, 3)) == [
        (2026, 11), (2026, 12), (2027, 1), (2027, 2)
    ]


def test_months_between_a_single_month():
    assert scene_query.months_between(date(2026, 5, 3), date(2026, 5, 20)) == [(2026, 5)]


def test_the_first_and_last_segments_are_clipped_to_the_view():
    segs = scene_query.month_segments(date(2026, 3, 10), date(2026, 5, 20), set(), 1000.0)
    assert segs[(2026, 3)].start == date(2026, 3, 10) and segs[(2026, 3)].days == 22
    assert segs[(2026, 4)].days == 30
    assert segs[(2026, 5)].end == date(2026, 5, 20) and segs[(2026, 5)].days == 20


def test_expanded_months_share_one_pixels_per_day_and_fill_the_track():
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 5, 31), set(), 920.0)
    ppds = {round(s.ppd, 9) for s in segs.values()}
    assert len(ppds) == 1
    assert segs[(2026, 3)].x == pytest.approx(0.0)
    assert sum(s.w for s in segs.values()) == pytest.approx(920.0)
    # 31 + 30 + 31 days: March is wider than April, which is the whole point
    assert segs[(2026, 3)].w > segs[(2026, 4)].w


def test_a_collapsed_month_is_a_fixed_block_and_the_rest_reflow():
    collapsed = {(2026, 4)}
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 5, 31), collapsed, 920.0)
    assert segs[(2026, 4)].collapsed and segs[(2026, 4)].w == pytest.approx(66.0)
    assert segs[(2026, 4)].ppd == 0.0
    assert sum(s.w for s in segs.values()) == pytest.approx(920.0)
    assert segs[(2026, 5)].x == pytest.approx(segs[(2026, 4)].x + 66.0)


def test_every_month_collapsed_leaves_no_division_by_zero():
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 5, 31), {(2026, 3), (2026, 4), (2026, 5)}, 920.0)
    assert [s.w for s in segs.values()] == [66.0, 66.0, 66.0]
    assert all(s.ppd == 0.0 for s in segs.values())


def test_a_track_too_narrow_for_its_collapsed_blocks_does_not_go_negative():
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 5, 31), {(2026, 3), (2026, 4)}, 100.0)
    assert segs[(2026, 5)].ppd == 0.0 and segs[(2026, 5)].w == 0.0


def test_x_for_places_a_date_by_its_offset_inside_its_expanded_month():
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 4, 30), set(), 610.0)
    ppd = segs[(2026, 3)].ppd
    assert scene_query.x_for(segs, date(2026, 3, 1)) == pytest.approx(0.0)
    assert scene_query.x_for(segs, date(2026, 3, 11)) == pytest.approx(10 * ppd)


def test_x_for_end_marks_the_right_edge_of_a_date_in_an_expanded_month():
    # The brief's own tests exercise end=True only on a collapsed month;
    # this closes the gap for the far more common expanded case, where the
    # right edge must be exactly one day-width to the right of the left edge.
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 4, 30), set(), 610.0)
    ppd = segs[(2026, 3)].ppd
    left = scene_query.x_for(segs, date(2026, 3, 11))
    right = scene_query.x_for(segs, date(2026, 3, 11), end=True)
    assert right == pytest.approx(left + ppd)
    # The last day of the view, end=True, reaches exactly the track's width.
    assert scene_query.x_for(segs, date(2026, 4, 30), end=True) == pytest.approx(610.0)


def test_x_for_collapses_every_date_of_a_collapsed_month_onto_its_block():
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 4, 30), {(2026, 3)}, 610.0)
    left = scene_query.x_for(segs, date(2026, 3, 4))
    assert left == pytest.approx(scene_query.x_for(segs, date(2026, 3, 29)))
    assert scene_query.x_for(segs, date(2026, 3, 29), end=True) == pytest.approx(left + 66.0)


def test_x_for_clamps_a_date_outside_the_view():
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 4, 30), set(), 610.0)
    assert scene_query.x_for(segs, date(2025, 1, 1)) == pytest.approx(0.0)
    assert scene_query.x_for(segs, date(2027, 1, 1)) == pytest.approx(610.0)


def test_auto_collapse_takes_the_months_wholly_inside_the_alert_window():
    got = scene_query.auto_collapsed(date(2026, 3, 3), date(2026, 10, 23), date(2026, 5, 3), date(2026, 8, 23))
    assert got == {(2026, 6), (2026, 7)}


def test_the_boundary_months_of_the_window_stay_expanded():
    # May holds the before candidates and August the after ones: collapsing
    # them would hide exactly the dates the reviewer has to choose between.
    got = scene_query.auto_collapsed(date(2026, 3, 3), date(2026, 10, 23), date(2026, 5, 3), date(2026, 8, 23))
    assert (2026, 5) not in got and (2026, 8) not in got


def test_a_short_alert_window_collapses_nothing():
    got = scene_query.auto_collapsed(date(2026, 3, 3), date(2026, 7, 23), date(2026, 5, 3), date(2026, 5, 20))
    assert got == set()
