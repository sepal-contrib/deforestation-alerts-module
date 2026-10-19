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


def test_shift_months_clamps_the_day_to_the_target_month():
    assert scene_query.shift_months(date(2026, 3, 31), -1) == date(2026, 2, 28)
    assert scene_query.shift_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert scene_query.shift_months(date(2026, 1, 15), -1) == date(2025, 12, 15)
    assert scene_query.shift_months(date(2026, 12, 15), 1) == date(2027, 1, 15)
    assert scene_query.shift_months(date(2026, 5, 3), 0) == date(2026, 5, 3)


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


def test_a_narrow_track_still_leaves_the_open_months_their_share():
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 5, 31), {(2026, 3), (2026, 4)}, 100.0)
    assert segs[(2026, 3)].w == pytest.approx(100.0 * scene_query.FOLD_SHARE / 2)
    assert segs[(2026, 5)].w == pytest.approx(100.0 * (1 - scene_query.FOLD_SHARE))
    assert segs[(2026, 5)].ppd > 0.0


# A two-year alert (the reported Ghana case): 1 Jan 2023 -> 21 Dec 2024, so
# Jan 2023..Nov 2024 fold -- 23 months, 1518 px at 66 px on a 1120 px track.
LONG_A0, LONG_A1 = date(2023, 1, 1), date(2024, 12, 21)
LONG_VIEW = (date(2022, 11, 1), date(2025, 2, 21))


def _long_segments():
    collapsed = scene_query.auto_collapsed(*LONG_VIEW, LONG_A0, LONG_A1)
    assert len(collapsed) == 23
    return scene_query.month_segments(*LONG_VIEW, collapsed, 1120.0)


def test_a_long_alert_never_squeezes_the_months_around_it_to_nothing():
    segs = _long_segments()
    folded = [s for s in segs.values() if s.collapsed]
    opened = [s for s in segs.values() if not s.collapsed]
    assert sum(s.w for s in folded) == pytest.approx(1120.0 * scene_query.FOLD_SHARE)
    assert all(s.w > 0 and s.ppd > 0 for s in opened)
    assert sum(s.w for s in segs.values()) == pytest.approx(1120.0)  # nothing runs off the track
    assert scene_query.x_for(segs, LONG_VIEW[1], end=True) == pytest.approx(1120.0)


def test_a_short_alert_keeps_full_size_blocks():
    # 7 folded months fit under the cap (462 px < 504 px): nothing changes
    collapsed = {(2026, m) for m in range(3, 10)}
    segs = scene_query.month_segments(date(2026, 1, 1), date(2026, 12, 31), collapsed, 1120.0)
    assert all(segs[key].w == pytest.approx(66.0) for key in collapsed)


# ------------------------------------------------------------ fold groups

def test_narrow_folded_months_group_by_calendar_half_year():
    groups = scene_query.fold_groups(_long_segments())
    assert groups == [
        [(2023, m) for m in range(1, 7)],
        [(2023, m) for m in range(7, 13)],
        [(2024, m) for m in range(1, 7)],
        [(2024, m) for m in range(7, 12)],
    ]


def test_full_size_folded_months_stay_one_block_each():
    collapsed = {(2026, 4), (2026, 5), (2026, 6)}
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 7, 31), collapsed, 1120.0)
    assert scene_query.fold_groups(segs) == [[(2026, 4)], [(2026, 5)], [(2026, 6)]]


def test_an_unfolded_half_year_comes_back_as_single_months():
    groups = scene_query.fold_groups(_long_segments(), split={(2024, 1)})
    assert [g for g in groups if g[0][0] == 2024 and g[0][1] <= 6] == [[(2024, m)] for m in range(1, 7)]
    assert [(2023, m) for m in range(1, 7)] in groups  # the other halves stay grouped
    assert [(2024, m) for m in range(7, 12)] in groups


def test_an_open_month_breaks_a_half_year_group():
    segs = _long_segments()
    collapsed = {key for key, s in segs.items() if s.collapsed} - {(2023, 3)}
    segs = scene_query.month_segments(*LONG_VIEW, collapsed, 1120.0)
    groups = scene_query.fold_groups(segs)
    assert [(2023, 1), (2023, 2)] in groups
    assert [(2023, 4), (2023, 5), (2023, 6)] in groups
    assert all((2023, 3) not in g for g in groups)


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


# ----------------------------------------------------------- default_pick

A0, A1 = date(2026, 5, 3), date(2026, 8, 23)


def _sel(rows, max_cloud=40.0):
    tables = _tables([(src, pd.Timestamp(d), cloud, "W", f"k{i}")
                      for i, (src, d, cloud) in enumerate(rows)])
    return scene_query.scenes_for(tables, ALERT, date(2026, 3, 1), date(2026, 10, 31), max_cloud=max_cloud)


def test_the_pick_is_the_nearest_clear_image_on_each_side():
    sel = _sel([
        (S2, "2026-04-02", 5.0),
        (S2, "2026-04-24", 8.0),   # nearest before
        (S2, "2026-06-10", 2.0),   # inside the window
        (S2, "2026-08-27", 3.0),   # nearest after
        (S2, "2026-09-30", 1.0),
    ])
    assert scene_query.default_pick(sel, A0, A1) == (
        scene_index.row_id(S2, date(2026, 4, 24)),
        scene_index.row_id(S2, date(2026, 8, 27)),
    )


def test_nothing_inside_the_alert_window_is_ever_picked():
    sel = _sel([(S2, "2026-06-10", 1.0), (S2, "2026-07-01", 1.0)])
    assert scene_query.default_pick(sel, A0, A1) == (None, None)


def test_a_scene_on_the_window_start_date_is_a_legal_before():
    sel = _sel([(S2, "2026-04-01", 5.0), (S2, "2026-05-03", 5.0)])
    assert scene_query.default_pick(sel, A0, A1)[0] == scene_index.row_id(S2, date(2026, 5, 3))


def test_a_scene_on_the_window_end_date_is_a_legal_after():
    sel = _sel([(S2, "2026-08-23", 5.0), (S2, "2026-09-15", 5.0)])
    assert scene_query.default_pick(sel, A0, A1)[1] == scene_index.row_id(S2, date(2026, 8, 23))


def test_a_side_whose_every_candidate_is_over_the_limit_is_left_unselected():
    # The spec's rule (§3): *before* is the latest scene **with cloud at or
    # under the limit**. With none, that side stays empty and the readout
    # points at the slider — auto-picking an 88%-cloud image instead would
    # make the one message that tells the reviewer to raise the limit
    # unreachable, and would look like a considered choice.
    sel = _sel([(S2, "2026-04-24", 88.0), (S2, "2026-04-02", 90.0), (S2, "2026-08-27", 4.0)])
    before, after = scene_query.default_pick(sel, A0, A1)
    assert before is None
    assert after == scene_index.row_id(S2, date(2026, 8, 27))


def test_raising_the_limit_makes_that_side_pick_again():
    rows = [(S2, "2026-04-24", 88.0), (S2, "2026-04-02", 90.0), (S2, "2026-08-27", 4.0)]
    assert scene_query.default_pick(_sel(rows, max_cloud=89.0), A0, A1)[0] == scene_index.row_id(
        S2, date(2026, 4, 24)
    )


def test_a_clear_scene_further_out_beats_a_cloudy_nearer_one():
    sel = _sel([(S2, "2026-04-28", 88.0), (S2, "2026-04-02", 6.0), (S2, "2026-08-27", 4.0)])
    assert scene_query.default_pick(sel, A0, A1)[0] == scene_index.row_id(S2, date(2026, 4, 2))


def test_planet_is_never_auto_picked():
    # A NICFI basemap is a whole month's mosaic: it cannot witness a date, so
    # it is a reviewer's deliberate choice, never a default. Each Planet row
    # here is placed *nearer* its boundary than the S2 row on the same side,
    # so if the Planet exclusion were ever dropped, the nearer Planet row
    # would win and this test would fail -- the property is load-bearing.
    sel = _sel([
        (PL, "2026-04-30", float("nan")),  # nearer a0 than the S2 row below
        (S2, "2026-04-02", 6.0),
        (PL, "2026-08-24", float("nan")),  # nearer a1 than the S2 row below
        (S2, "2026-08-27", 4.0),
    ])
    before, after = scene_query.default_pick(sel, A0, A1)
    assert before == scene_index.row_id(S2, date(2026, 4, 2))
    assert after == scene_index.row_id(S2, date(2026, 8, 27))


def test_only_planet_available_picks_nothing():
    sel = _sel([(PL, "2026-04-01", float("nan")), (PL, "2026-09-01", float("nan"))])
    assert scene_query.default_pick(sel, A0, A1) == (None, None)


def test_an_empty_selection_picks_nothing():
    assert scene_query.default_pick(_EMPTY_SEL(), A0, A1) == (None, None)


def _EMPTY_SEL():
    return scene_query.scenes_for(scene_index.empty_tables(), ALERT, date(2026, 3, 1), date(2026, 10, 31))


def test_one_side_available_still_picks_that_side():
    sel = _sel([(S2, "2026-04-02", 6.0)])
    assert scene_query.default_pick(sel, A0, A1) == (scene_index.row_id(S2, date(2026, 4, 2)), None)


def test_a_single_day_window_gives_a_scene_on_that_day_to_after_only():
    # When alert_date_min == alert_date_max, a scene dated exactly on that
    # day satisfies both "<= a0" and ">= a1"; picking it for both sides would
    # be a pair that shows no change. It is the after, and the before comes
    # from strictly earlier dates.
    d = date(2026, 6, 15)
    sel = _sel([(S2, "2026-06-01", 5.0), (S2, "2026-06-15", 5.0)])
    assert scene_query.default_pick(sel, d, d) == (
        scene_index.row_id(S2, date(2026, 6, 1)),
        scene_index.row_id(S2, d),
    )


def test_a_single_day_window_with_only_a_scene_on_that_day_leaves_before_unset():
    d = date(2026, 6, 15)
    sel = _sel([(S2, "2026-06-15", 5.0)])
    assert scene_query.default_pick(sel, d, d) == (None, scene_index.row_id(S2, d))


def test_a_single_day_window_never_picks_two_collections_on_that_day():
    # Sentinel-2 and Landsat on the same day are different rows, but both
    # would still be one look at the alert day.
    d = date(2026, 6, 15)
    sel = _sel([(S2, "2026-06-15", 5.0), (LS, "2026-06-15", 5.0), (S2, "2026-06-05", 5.0)])
    before, after = scene_query.default_pick(sel, d, d)
    assert before == scene_index.row_id(S2, date(2026, 6, 5))
    assert scene_query.parse_row_id(after)[1] == d


def test_a_single_day_window_with_scenes_only_on_either_side():
    # Same degenerate window, but with no scene landing exactly on the day:
    # the two sides fall back to their ordinary nearest-before/nearest-after
    # candidates, which are distinct here.
    d = date(2026, 6, 15)
    sel = _sel([(S2, "2026-06-10", 5.0), (S2, "2026-06-20", 5.0)])
    assert scene_query.default_pick(sel, d, d) == (
        scene_index.row_id(S2, date(2026, 6, 10)),
        scene_index.row_id(S2, date(2026, 6, 20)),
    )


# ------------------------------------------------- saved picks and the GIF

S2, LS, PL = scene_index.SOURCE_S2, scene_index.SOURCE_LANDSAT, scene_index.SOURCE_PLANET


def _rows(spec):
    """Timeline rows (``SELECTION_COLUMNS``) from ``(source, "YYYY-MM-DD", hidden)``."""
    out = pd.DataFrame(
        [(scene_index.row_id(src, date.fromisoformat(d)), src, pd.Timestamp(d), 1.0, 1, hidden)
         for src, d, hidden in spec],
        columns=list(scene_query.SELECTION_COLUMNS),
    )
    return out.sort_values(["date", "source"], kind="stable").reset_index(drop=True)


def _rid(src, d):
    return scene_index.row_id(src, date.fromisoformat(d))


def test_a_row_id_reads_back_into_its_source_and_date():
    assert scene_query.parse_row_id("s20260424") == (S2, date(2026, 4, 24))
    assert scene_query.parse_row_id("l20260827") == (LS, date(2026, 8, 27))
    assert scene_query.parse_row_id("p20260401") == (PL, date(2026, 4, 1))
    for bad in ("", "x20260424", "s2026-04-24", None, "s20261340"):
        with pytest.raises(ValueError):
            scene_query.parse_row_id(bad)


def test_a_saved_pick_is_the_row_id_of_its_source_and_date():
    assert scene_query.saved_pick_id("Sentinel 2 2026-04-24 ") == "s20260424"
    assert scene_query.saved_pick_id("Landsat 2026-08-27 ") == "l20260827"
    assert scene_query.saved_pick_id("Planet NICFI 2026-04-01 ") == "p20260401"
    # the trailing space is the contract, but a store trimmed by hand still reads
    assert scene_query.saved_pick_id("Landsat 2026-08-27") == "l20260827"


def test_what_is_not_a_saved_pick_is_none():
    for value in (None, float("nan"), "", "S2_before", "Sentinel-2 2026-04-24 ",
                  "Sentinel 2 2026-13-01 ", "MODIS 2026-04-24 ", 1776988800000):
        assert scene_query.saved_pick_id(value) is None, value


def test_the_granule_keys_are_those_of_the_date_and_collection_over_the_alert():
    tiles = gpd.GeoDataFrame({"tile": ["A", "B"]},
                             geometry=gpd.GeoSeries([box(0, 0, 1, 1), box(5, 5, 6, 6)], crs=scene_index.WGS84),
                             crs=scene_index.WGS84)
    scenes = pd.DataFrame([
        (S2, pd.Timestamp("2026-04-24"), 1.0, "A", "kA"),
        (S2, pd.Timestamp("2026-04-24"), 1.0, "B", "kB"),   # another tile: not over the alert
        (LS, pd.Timestamp("2026-04-24"), 1.0, "A", "kL"),   # another collection
        (S2, pd.Timestamp("2026-04-25"), 1.0, "A", "kD"),   # another date
        (PL, pd.Timestamp("2026-04-01"), None, "", "kP"),   # AOI-wide mosaic
    ], columns=list(scene_index.SCENE_COLUMNS))
    tables = scene_index.SceneTables(scenes=scenes, tiles=tiles)
    alert = box(0.2, 0.2, 0.4, 0.4)
    assert scene_query.granule_keys(tables, alert, S2, date(2026, 4, 24)) == ["kA"]
    assert scene_query.granule_keys(tables, alert, S2, pd.Timestamp("2026-04-24")) == ["kA"]
    assert scene_query.granule_keys(tables, alert, PL, date(2026, 4, 1)) == ["kP"]
    assert scene_query.granule_keys(tables, alert, LS, date(2026, 8, 1)) == []


def test_the_gif_takes_two_images_either_side_of_the_picks():
    rows = _rows([(S2, "2026-01-05", False), (S2, "2026-02-05", False), (S2, "2026-03-05", False),
                  (S2, "2026-04-05", False), (LS, "2026-06-01", False), (S2, "2026-09-05", False),
                  (S2, "2026-10-05", False), (S2, "2026-11-05", False), (S2, "2026-12-05", False)])
    got = scene_query.gif_frames(rows, _rid(S2, "2026-04-05"), _rid(S2, "2026-09-05"))
    assert list(got["id"]) == [_rid(S2, "2026-02-05"), _rid(S2, "2026-03-05"), _rid(S2, "2026-04-05"),
                               _rid(S2, "2026-09-05"), _rid(S2, "2026-10-05"), _rid(S2, "2026-11-05")]
    assert list(got["role"]) == ["", "", "before", "after", "", ""]


def test_a_side_with_fewer_neighbours_gives_what_it_has():
    rows = _rows([(S2, "2026-03-05", False), (S2, "2026-04-05", False), (S2, "2026-09-05", False)])
    got = scene_query.gif_frames(rows, _rid(S2, "2026-04-05"), _rid(S2, "2026-09-05"))
    assert list(got["id"]) == [_rid(S2, "2026-03-05"), _rid(S2, "2026-04-05"), _rid(S2, "2026-09-05")]


def test_neighbours_skip_planet_hidden_rows_and_anything_between_the_picks():
    rows = _rows([(S2, "2026-02-05", False), (S2, "2026-03-05", True), (PL, "2026-03-01", False),
                  (S2, "2026-04-05", False), (S2, "2026-06-05", False), (S2, "2026-09-05", False),
                  (PL, "2026-10-01", False), (S2, "2026-11-05", True), (S2, "2026-12-05", False)])
    got = scene_query.gif_frames(rows, _rid(S2, "2026-04-05"), _rid(S2, "2026-09-05"))
    assert list(got["id"]) == [_rid(S2, "2026-02-05"), _rid(S2, "2026-04-05"), _rid(S2, "2026-09-05"),
                               _rid(S2, "2026-12-05")]


def test_a_picked_planet_or_hidden_image_is_still_a_frame():
    rows = _rows([(PL, "2026-03-01", False), (S2, "2026-09-05", True)])
    got = scene_query.gif_frames(rows, _rid(PL, "2026-03-01"), _rid(S2, "2026-09-05"))
    assert list(got["role"]) == ["before", "after"]


def test_on_one_date_sentinel2_comes_before_landsat():
    rows = _rows([(LS, "2026-03-05", False), (S2, "2026-03-05", False), (S2, "2026-04-05", False),
                  (S2, "2026-09-05", False)])
    got = scene_query.gif_frames(rows, _rid(S2, "2026-04-05"), _rid(S2, "2026-09-05"))
    assert list(got["id"])[:2] == [_rid(S2, "2026-03-05"), _rid(LS, "2026-03-05")]


def test_a_pick_the_rows_do_not_hold_gives_no_frames():
    rows = _rows([(S2, "2026-04-05", False)])
    assert scene_query.gif_frames(rows, _rid(S2, "2026-04-05"), _rid(S2, "2026-09-05")).empty
    assert scene_query.gif_frames(rows, None, _rid(S2, "2026-04-05")).empty
