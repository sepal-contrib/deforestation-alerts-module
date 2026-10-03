"""The shared image timeline widget (spec §4-§5): geometry, copy and wiring.

Everything here is offline. The builder reads reactives and one context dict,
so the whole widget is exercised by handing it fakes -- no Earth Engine, no
Solara render context, no map.

The geometry assertions compute their expected pixel from first principles
(days / total days of the view) rather than by calling ``x_for`` again, so a
stripe that is laid out with the wrong month's origin, against the wrong
denominator, or in pixels where percentages were meant, fails here.
"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pytest
from pysepal.i18n import set_locale
from shapely.geometry import box

from deforestation_alerts.project.models import AoiRecord, Period, Project
from deforestation_alerts.scripts import scene_index, scene_query
from gui.tile.analysis import timeline

S2, LS, PL = scene_index.SOURCE_S2, scene_index.SOURCE_LANDSAT, scene_index.SOURCE_PLANET

TILES = gpd.GeoDataFrame({"tile": ["W"]},
                         geometry=gpd.GeoSeries([box(0, 0, 2, 2)], crs=scene_index.WGS84),
                         crs=scene_index.WGS84)


def _sel(rows, max_cloud=40.0, start=date(2026, 3, 3), end=date(2026, 10, 23)):
    scenes = pd.DataFrame(
        [(src, pd.Timestamp(d), cloud, "W", f"k{i}") for i, (src, d, cloud) in enumerate(rows)],
        columns=list(scene_index.SCENE_COLUMNS),
    ).astype({"date": "datetime64[ns]"})
    tables = scene_index.SceneTables(scenes=scenes, tiles=TILES)
    return scene_query.scenes_for(tables, box(0.5, 0.5, 1.5, 1.5), start, end, max_cloud=max_cloud)


def _ctx(date_min=date(2026, 5, 3), date_max=date(2026, 8, 23)):
    return {"alert_id": 7, "token": 1, "bbox": box(0.5, 0.5, 1.5, 1.5),
            "date_min": date_min, "date_max": date_max}


def _project():
    p = Project(name="t", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="d1",
                      bounds=(0.0, 0.0, 2.0, 2.0), geometry_file="aoi.geojson")
    p.periods = {"x": Period(id="x", start=date(2026, 4, 1), end=date(2026, 9, 30))}
    p.active_period = "x"
    return p


# ------------------------------------------------------------------ the view

def test_the_view_is_the_alert_window_padded_by_two_months():
    assert timeline.view_window(_ctx(), _project(), (0, 0)) == (date(2026, 3, 3), date(2026, 10, 23))


def test_the_side_buttons_add_whole_months_to_the_view():
    assert timeline.view_window(_ctx(), _project(), (1, 2)) == (date(2026, 2, 3), date(2026, 12, 23))


def test_an_alert_without_dates_falls_back_to_the_active_period():
    got = timeline.view_window(_ctx(date_min=None, date_max=None), _project(), (0, 0))
    assert got == (date(2026, 2, 1), date(2026, 11, 30))


def test_without_an_alert_or_a_period_there_is_no_view():
    assert timeline.view_window(None, _project(), (0, 0)) is None
    bare = _project()
    bare.active_period = None
    assert timeline.view_window(_ctx(date_min=None, date_max=None), bare, (0, 0)) is None


def test_the_gold_band_follows_the_same_fallback_as_the_view():
    assert timeline.alert_window(_ctx(), _project()) == (date(2026, 5, 3), date(2026, 8, 23))
    assert timeline.alert_window(_ctx(date_min=None, date_max=None), _project()) == (
        date(2026, 4, 1), date(2026, 9, 30))
    assert timeline.alert_window(None, _project()) == (None, None)


# ------------------------------------------------------- the string contracts

def test_the_info_tuple_keeps_the_five_element_contract():
    sel = _sel([(S2, "2026-04-24", 8.0)])
    info = timeline.info_tuple(sel.iloc[0])
    assert info[0] == S2
    assert info[1] == 1776988800000  # 2026-04-24T00:00:00Z
    assert info[2] == "8.00"
    assert info[3] == "Sentinel 2 2026-04-24 "  # the trailing space is part of the contract
    assert info[4] == "_240426"


def test_the_info_tuple_carries_the_data_source_string_not_the_ui_label():
    """"Sentinel-2" with a hyphen is a display label; the saved store and
    ``mosaics_helper`` switch on "Sentinel 2" with a space."""
    info = timeline.info_tuple(_sel([(S2, "2026-04-24", 8.0)]).iloc[0])
    assert "Sentinel-2" not in info[0] and "Sentinel-2" not in info[3]
    assert timeline.SRC_LABEL[S2] == "Sentinel-2" != S2


def test_the_info_tuple_of_a_planet_row_has_no_cloud_number():
    sel = _sel([(PL, "2026-04-01", float("nan"))])
    info = timeline.info_tuple(sel.iloc[0])
    assert info[0] == PL and info[2] == "N/A"


def test_the_readout_names_the_source_the_date_and_the_cloud():
    sel = _sel([(S2, "2026-04-24", 8.0)])
    text = timeline.readout(sel, scene_index.row_id(S2, date(2026, 4, 24)))
    assert "2026-04-24" in text and "8" in text


def test_the_readout_of_nothing_selected_says_so():
    sel = _sel([(S2, "2026-04-24", 8.0)])
    assert timeline.readout(sel, None) == timeline.t("analysis.timeline.unset")


def test_the_readout_of_an_id_the_window_no_longer_holds_says_so():
    """A saved selection from a wider view is an ordinary thing to meet."""
    sel = _sel([(S2, "2026-04-24", 8.0)])
    assert timeline.readout(sel, scene_index.row_id(LS, date(2020, 1, 1))) == timeline.t(
        "analysis.timeline.unset")


def test_days_apart_counts_the_gap_the_pair_brackets():
    assert timeline.days_apart(scene_index.row_id(S2, date(2026, 4, 24)),
                               scene_index.row_id(S2, date(2026, 8, 27))) == 125
    assert timeline.days_apart(None, scene_index.row_id(S2, date(2026, 8, 27))) is None


def test_one_scene_on_both_sides_is_named_as_that_and_not_as_zero_change():
    """``default_pick`` returns one id twice when the alert window is a single
    day and a scene lands on it (pinned behaviour). "0 days apart" would read
    as two images that happen to agree."""
    sel = _sel([(S2, "2026-04-24", 8.0)])
    rid = scene_index.row_id(S2, date(2026, 4, 24))
    assert timeline.days_apart(rid, rid) == 0
    assert timeline.pair_note(rid, rid) == timeline.t("analysis.timeline.same_image")
    other = scene_index.row_id(S2, date(2026, 8, 27))
    assert timeline.pair_note(rid, other) == timeline.t("analysis.timeline.days_apart", days=125)
    assert timeline.pair_note(rid, None) is None


# ------------------------------------------------------------- the geometry

def test_the_fixed_numbers_are_the_ones_the_spec_fixed():
    """Every other geometry test reads these through ``timeline.*``, so it
    would still pass on a 500px track in the wrong colours. These are the
    literals from the spec and the approved mockup, written out here on
    purpose: nothing below may be "adjusted" without this test saying so."""
    assert (timeline.TRACK_PX, timeline.LANE_H, timeline.AXIS_H) == (1120.0, 45, 27)
    assert timeline.VIEW_MONTHS == 2  # the default view is the alert window +/- 2 months
    assert timeline.BAND == {"Sentinel 2": (0, 17), "Landsat": (19, 14), "Planet NICFI": (35, 10)}
    assert timeline.SRC_COLOR == {"Sentinel 2": "#7fa8e0", "Landsat": "#c9a87c",
                                  "Planet NICFI": "#9bd98a"}
    assert timeline.SELECTED_COLOR == "#bf8f2d"  # the theme's primary_contrast
    assert (timeline.STRIPE_PX, timeline.SELECTED_STRIPE_PX, timeline.HIT_PX) == (2.5, 4.0, 11)
    assert (timeline.HIDDEN_SCALE, timeline.HIDDEN_OPACITY) == (0.45, 0.3)
    assert scene_query.COLLAPSED_PX == 66.0  # a collapsed month is a fixed block
    # the display labels, which are UI-only: the keys above are the data strings
    assert timeline.SRC_LABEL == {"Sentinel 2": "Sentinel-2", "Landsat": "Landsat",
                                  "Planet NICFI": "Planet"}


def test_a_stripe_lands_on_its_own_date():
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 4, 30), set(), timeline.TRACK_PX)
    style = timeline.stripe_hit_style(segs, date(2026, 3, 11), S2)
    pct = 100 * scene_query.x_for(segs, date(2026, 3, 11)) / timeline.TRACK_PX
    assert _left_pct(style) == pytest.approx(pct, abs=0.001)
    assert "width: 11px" in style


def test_a_stripes_percentage_is_its_own_share_of_the_view_not_a_pixel_count():
    """The whole widget is addressed in percentages. 10 days into a 61-day
    all-expanded view is 10/61 of the track; a builder that emitted ``x_for``'s
    pixels, or divided by the wrong total, lands elsewhere."""
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 4, 30), set(), timeline.TRACK_PX)
    for day, offset in ((date(2026, 3, 1), 0), (date(2026, 3, 11), 10), (date(2026, 4, 30), 60)):
        got = _left_pct(timeline.stripe_hit_style(segs, day, S2))
        assert got == pytest.approx(100 * offset / 61, abs=0.01), day
    # and in pixels the two neighbours of a five-day revisit stay ~13px apart,
    # which is why an 11px hit area never covers its neighbour's date.
    step = (_left_pct(timeline.stripe_hit_style(segs, date(2026, 3, 11), S2))
            - _left_pct(timeline.stripe_hit_style(segs, date(2026, 3, 6), S2)))
    assert step / 100 * timeline.TRACK_PX == pytest.approx(5 * timeline.TRACK_PX / 61, abs=0.1)
    assert step / 100 * timeline.TRACK_PX > timeline.HIT_PX


def test_a_collapsed_month_puts_every_one_of_its_dates_on_the_block():
    """A collapsed month has no pixels per day, so its dates share the block's
    origin and the months after it shift left by what the block saved."""
    expanded = scene_query.month_segments(date(2026, 3, 1), date(2026, 6, 30), set(), timeline.TRACK_PX)
    folded = scene_query.month_segments(date(2026, 3, 1), date(2026, 6, 30), {(2026, 4)}, timeline.TRACK_PX)
    first = _left_pct(timeline.stripe_hit_style(folded, date(2026, 4, 2), S2))
    last = _left_pct(timeline.stripe_hit_style(folded, date(2026, 4, 29), S2))
    assert first == pytest.approx(last, abs=0.001)
    assert _left_pct(timeline.stripe_hit_style(folded, date(2026, 5, 1), S2)) < _left_pct(
        timeline.stripe_hit_style(expanded, date(2026, 5, 1), S2))


def test_the_three_collections_get_three_bands_that_cannot_collide():
    tops = {src: timeline.BAND[src] for src in (S2, LS, PL)}
    assert tops == {S2: (0, 17), LS: (19, 14), PL: (35, 10)}
    ordered = sorted(tops.values())
    for (top, height), (next_top, _) in zip(ordered, ordered[1:]):
        assert top + height <= next_top  # no overlap
    assert ordered[-1][0] + ordered[-1][1] <= timeline.LANE_H
    for src in (S2, LS, PL):
        style = timeline.stripe_hit_style(
            scene_query.month_segments(date(2026, 3, 1), date(2026, 4, 30), set(), timeline.TRACK_PX),
            date(2026, 3, 11), src)
        assert f"top: {timeline.BAND[src][0]}px" in style
        assert f"height: {timeline.BAND[src][1]}px" in style


def test_each_collection_keeps_its_own_band_height_on_the_stripe():
    heights = {}
    for src, cloud in ((S2, 8.0), (LS, 8.0), (PL, float("nan"))):
        row = _sel([(src, "2026-04-24", cloud)]).iloc[0]
        heights[src] = timeline.stripe_bar_style(row, selected=False).split("height: ")[1].split(";")[0]
    assert heights == {S2: "17px", LS: "14px", PL: "10px"}
    assert len(set(heights.values())) == 3


def test_a_hidden_stripe_is_drawn_short_faint_and_not_clickable():
    row = _sel([(S2, "2026-04-24", 88.0)]).iloc[0]
    assert row["hidden"]
    style = timeline.stripe_bar_style(row, selected=False)
    assert "opacity: 0.3" in style and "height: 7.65px" in style  # 45% of the 17px band
    assert timeline.stripe_cursor(row) == "default"
    shown = _sel([(S2, "2026-04-24", 8.0)]).iloc[0]
    assert "opacity" not in timeline.stripe_bar_style(shown, selected=False)
    assert timeline.stripe_cursor(shown) == "pointer"


def test_a_selected_stripe_is_wider_and_takes_the_selection_colour():
    row = _sel([(S2, "2026-04-24", 8.0)]).iloc[0]
    assert "width: 4px" in timeline.stripe_bar_style(row, selected=True)
    assert timeline.SELECTED_COLOR in timeline.stripe_bar_style(row, selected=True)
    plain = timeline.stripe_bar_style(row, selected=False)
    assert "width: 2.5px" in plain and timeline.SRC_COLOR[S2] in plain
    assert _bar_width(row, selected=True) > _bar_width(row, selected=False)


def test_a_planet_row_is_a_bar_across_its_whole_month_not_a_stripe():
    """A NICFI basemap composites a month; a 2.5px stripe would claim an
    instant it does not have (spec §4.1)."""
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 6, 30), set(), timeline.TRACK_PX)
    bar = timeline.month_bar_style(segs, date(2026, 4, 1))
    width = float(bar.split("width: ")[1].split("%")[0])
    left = float(bar.split("left: ")[1].split("%")[0])
    # April is 30 of the 122 days on an all-expanded four-month axis.
    assert width == pytest.approx(100 * 30 / 122, abs=0.5)
    assert left == pytest.approx(100 * 31 / 122, abs=0.5)
    # and the bar sits in the Planet band, not the Sentinel-2 one
    assert f"top: {timeline.BAND[PL][0]}px" in bar
    # any day of the month addresses the same bar: the month is the unit
    assert timeline.month_bar_style(segs, date(2026, 4, 17)) == bar


def test_a_planet_bar_in_a_collapsed_month_is_the_block_width():
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 6, 30), {(2026, 4)}, timeline.TRACK_PX)
    bar = timeline.month_bar_style(segs, date(2026, 4, 1))
    width = float(bar.split("width: ")[1].split("%")[0])
    assert width == pytest.approx(100 * scene_query.COLLAPSED_PX / timeline.TRACK_PX, abs=0.5)


def test_a_planet_bar_outside_the_view_is_not_drawn():
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 6, 30), set(), timeline.TRACK_PX)
    assert timeline.month_bar_style(segs, date(2025, 1, 1)) == "display: none"


# --------------------------------------------------------------- the locales

def test_every_locale_has_the_timeline_keys():
    import json
    from pathlib import Path

    import deforestation_alerts.message as msg

    root = Path(msg.__file__).parent
    keys = {"before", "after", "unset", "max_cloud", "one_more_month", "collapse_all", "expand_all",
            "images", "no_images", "days_apart", "stale", "refresh_failed", "retry", "loading",
            "legend", "legend_label", "lane_before", "lane_after",
            "set_before", "set_after", "collapsed_month", "no_cloud_filter"}
    for locale in ("en", "es-ES", "fr"):
        data = json.loads((root / locale / "ui.json").read_text())
        assert keys <= set(data["analysis"]["timeline"]), locale


# ---------------------------------------------------------------- the widget

def test_the_builder_returns_a_raw_widget_and_not_a_solara_component():
    import ipyvuetify as v

    card = timeline.build_timeline(_fake_state(), _fake_analysis(), _fake_widgets())
    assert isinstance(card, v.VuetifyWidget)
    assert not hasattr(timeline.build_timeline, "component")  # never @solara.component


def test_assigning_a_before_image_publishes_its_info_and_its_keys():
    state = _fake_state()
    analysis = _fake_analysis()
    timeline.build_timeline(state, analysis, _fake_widgets())
    assert analysis.before_info.value is not None and analysis.before_info.value[0] == S2
    assert analysis.before_keys.value  # the granules the rebuild mosaics
    assert analysis.after_info.value is not None and analysis.after_info.value[0] == LS


def test_reset_alert_forgets_the_granule_keys():
    analysis = _fake_analysis()
    analysis.before_keys.set(["k1"])
    analysis.after_keys.set(["k2"])
    analysis.reset_alert()
    assert analysis.before_keys.value == [] and analysis.after_keys.value == []


def test_the_upper_lane_sets_the_before_image_and_the_lower_one_the_after():
    state = _fake_state()
    analysis = _fake_analysis()
    card = timeline.build_timeline(state, analysis, _fake_widgets())
    lanes = [w for w in _walk(card) if (w.style_ or "").startswith(f"position: relative; height: "
                                                                   f"{timeline.LANE_H}px")]
    assert len(lanes) == 2
    upper, lower = lanes
    # the only stripe outside the alert window's collapsed months and not
    # already chosen on that side: the Landsat one for "before"
    target = scene_index.row_id(LS, date(2026, 8, 27))
    was_after = analysis.after_info.value
    _click(upper, target)
    assert analysis.before_info.value[0] == LS
    # the upper lane is the before side *only*: the after publish is untouched
    assert analysis.after_info.value is was_after
    _click(lower, scene_index.row_id(S2, date(2026, 4, 24)))
    assert analysis.after_info.value[0] == S2
    assert analysis.before_info.value[0] == LS


def test_a_hidden_row_is_drawn_but_carries_no_click_handler():
    state = _fake_state(rows=[(S2, "2026-04-24", 8.0), (LS, "2026-04-25", 88.0)])
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    faint = [w for w in _walk(card) if "opacity: 0.3" in (getattr(w, "style_", "") or "")]
    assert faint, "the over-the-limit row must still be drawn"
    aria = f"{timeline.SRC_LABEL[LS]}, 2026-04-25"
    buttons = [w for w in _walk(card)
               if (getattr(w, "attributes", None) or {}).get("aria-label", "").startswith(aria)]
    assert buttons and all("click" not in (b._events or []) for b in buttons)
    assert all("cursor: default" in (b.style_ or "") for b in buttons)
    # ``disabled``, not only ``aria-disabled``: the native attribute is what
    # takes the stripe out of the tab order, which Task 11's keyboard
    # navigation needs for hidden rows.
    assert all(b.attributes.get("disabled") == "disabled" for b in buttons)
    assert all(b.attributes.get("aria-disabled") == "true" for b in buttons)


def test_the_builder_renders_an_empty_timeline_before_any_index_arrives():
    state = _fake_state(index=None)
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    assert timeline.t("analysis.timeline.no_images") in _text(card)


def test_no_images_over_the_alert_says_so_rather_than_drawing_an_empty_track():
    # Review Focus #3 reaching the UI.
    state = _fake_state(index=scene_index.SceneTables(
        scenes=_sel([(S2, "2026-04-24", 8.0)]).assign(tile="W", key="k0")[list(scene_index.SCENE_COLUMNS)],
        tiles=TILES))
    widgets = _fake_widgets(bbox=box(80, 80, 81, 81))
    card = timeline.build_timeline(state, _fake_analysis(), widgets)
    assert timeline.t("analysis.timeline.no_images") in _text(card)


def test_an_index_that_lands_after_the_build_fills_the_strip():
    """The builder subscribes; nothing re-mounts it."""
    state = _fake_state(index=None)
    analysis = _fake_analysis()
    card = timeline.build_timeline(state, analysis, _fake_widgets())
    assert analysis.before_info.value is None
    scenes = _sel([(S2, "2026-04-24", 8.0), (LS, "2026-08-27", 3.0)])
    state.scene_index.set(scene_index.SceneTables(
        scenes=scenes.assign(tile="W", key="k")[list(scene_index.SCENE_COLUMNS)], tiles=TILES))
    assert timeline.t("analysis.timeline.no_images") not in _text(card)
    assert analysis.before_info.value[0] == S2


def test_a_second_alert_on_the_same_index_still_gets_its_own_pick():
    """``ctx["alert_id"]`` is ``actual_alert_id``, an *index* into the active
    set's rows, so a different alert can land on the same number -- switching
    sets, or reloading one. Keyed on the index alone the builder treats the
    second alert as the first and skips the auto-collapse and the auto-pick,
    while ``reset_alert`` has already cleared ``before_info``: the readout then
    names an image nobody published. The identity is the index *and*
    ``AnalysisState.alert_token``, which ``reset_alert`` bumps.
    """
    rows = [(S2, "2026-04-24", 8.0), (LS, "2026-06-10", 8.0), (S2, "2026-08-27", 3.0)]
    state = _fake_state(rows=rows)
    analysis = _fake_analysis()
    widgets = _fake_widgets()
    # the tile publishes ``analysis.alert_token.value`` as the ctx's token
    # (analysis_tile.py), so the fake has to carry the live one, not a literal
    first = dict(_ctx())
    first["token"] = analysis.alert_token.value
    widgets.imagery_ctx.set(first)
    card = timeline.build_timeline(state, analysis, widgets)
    assert analysis.before_info.value[0] == S2  # 2026-04-24, the first alert's pick
    # June is inside the first alert's window, so it is folded away and the
    # Landsat row has no stripe at all
    assert not _clickable_for(card, date(2026, 6, 10))

    # what the tile does on every alert change, in its order
    analysis.reset_alert()
    assert analysis.before_info.value is None
    moved = dict(_ctx(date_min=date(2026, 6, 20), date_max=date(2026, 7, 10)))
    assert moved["alert_id"] == 7  # the same index as the first alert
    moved["token"] = analysis.alert_token.value  # bumped by reset_alert
    assert moved["token"] != first["token"]
    widgets.imagery_ctx.set(moved)

    # a fresh pick for the new dates, not the first alert's
    assert analysis.before_info.value is not None
    assert analysis.before_info.value[0] == LS
    assert analysis.before_info.value[3] == "Landsat 2026-06-10 "
    assert analysis.before_keys.value
    # and the readout agrees with what was published, rather than still
    # naming the previous alert's image
    text = _text(card)
    assert "Landsat 2026-06-10" in text and "Sentinel-2 2026-04-24" not in text
    # the new window re-collapsed from scratch: June is a margin month now
    assert _clickable_for(card, date(2026, 6, 10))


def test_a_stale_index_whose_refresh_failed_reports_both_the_age_and_the_failure():
    """Review Focus #2 in full. The error alone leaves the reviewer unable to
    tell whether they are looking at yesterday's index or last month's."""
    state = _fake_state()
    state.scene_manifest.set(_manifest(hours_ago=30))
    state.scene_index_error.set("Earth Engine unavailable")
    text = _text(timeline.build_timeline(state, _fake_analysis(), _fake_widgets()))
    assert "Earth Engine unavailable" in text
    assert timeline.t("analysis.timeline.stale",
        when=(datetime.now(timezone.utc) - timedelta(hours=30)).date().isoformat()) in text
    assert timeline.t("analysis.timeline.retry") in text


def test_try_again_bumps_the_loaders_retry_counter():
    state = _fake_state()
    state.scene_index_error.set("Earth Engine unavailable")
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    btn = next(w for w in _walk(card) if timeline.t("analysis.timeline.retry") in _text(w)
               and "click" in (getattr(w, "_events", None) or []))
    btn.fire_event("click", None)
    assert state.scene_index_retry.value == 1


def test_a_fresh_index_says_nothing_about_its_age():
    state = _fake_state()
    state.scene_manifest.set(_manifest(hours_ago=1))
    assert "Index from" not in _text(timeline.build_timeline(state, _fake_analysis(), _fake_widgets()))


def test_an_index_that_has_not_arrived_yet_says_it_is_loading():
    """The flag alone can read ``False`` mid-fetch (scene_loader's ``finally``),
    so the first-load line is driven by the absence of an index as well."""
    state = _fake_state(index=None)
    assert timeline.t("analysis.timeline.loading") in _text(
        timeline.build_timeline(state, _fake_analysis(), _fake_widgets()))
    ready = _fake_state()
    ready.scene_manifest.set(_manifest(hours_ago=1))
    assert timeline.t("analysis.timeline.loading") not in _text(
        timeline.build_timeline(ready, _fake_analysis(), _fake_widgets()))


def test_the_planet_toggle_is_disabled_and_says_why_without_nicfi_access():
    """Spec §5.4: a licence-gated collection must not read as an empty one."""
    state = _fake_state()
    state.scene_manifest.set(_manifest(planet=scene_index.PlanetState(region="americas", access=False)))
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    chip = next(w for w in _walk(card)
                if (getattr(w, "attributes", None) or {}).get("title", "").startswith(
                    timeline.t("steps.layers.err_nicfi", region="americas")[:20]))
    assert chip.attributes.get("disabled") == "disabled"
    assert chip._events == [] or "click" not in chip._events  # nothing to click


def test_the_planet_toggle_is_live_when_access_was_granted():
    state = _fake_state()
    state.scene_manifest.set(_manifest(planet=scene_index.PlanetState(region="americas", access=True)))
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    chips = [w for w in _walk(card) if (getattr(w, "attributes", None) or {}).get("aria-pressed")]
    planet = next(w for w in chips if timeline.SRC_LABEL[PL] in _text(w))
    assert "disabled" not in (planet.attributes or {})
    assert timeline.t("analysis.timeline.no_cloud_filter") == planet.attributes.get("title")


def test_turning_a_collection_off_takes_its_stripes_off_the_track():
    state = _fake_state()
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    before = _count_stripes(card)
    chip = next(w for w in _walk(card)
                if (getattr(w, "attributes", None) or {}).get("aria-pressed") == "true"
                and timeline.SRC_LABEL[LS] in _text(w))
    chip.fire_event("click", None)
    assert _count_stripes(card) < before


def test_the_cloud_limit_hides_a_row_without_dropping_it():
    state = _fake_state(rows=[(S2, "2026-04-24", 8.0), (LS, "2026-08-27", 30.0)])
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    assert not [w for w in _walk(card) if "opacity: 0.3" in (getattr(w, "style_", "") or "")]
    slider = next(w for w in _walk(card) if getattr(w, "label", None) == timeline.t(
        "analysis.timeline.max_cloud"))
    slider.fire_event("change", 10)
    faint = [w for w in _walk(card) if "opacity: 0.3" in (getattr(w, "style_", "") or "")]
    assert faint, "a row over the new limit is faded, not removed"


def test_a_collapsed_month_shows_its_visible_count_and_expands_on_click():
    state = _fake_state(rows=[(S2, "2026-06-05", 8.0), (S2, "2026-06-15", 8.0), (S2, "2026-04-24", 8.0)])
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    block = next(w for w in _walk(card)
                 if (getattr(w, "attributes", None) or {}).get("aria-label", "").startswith("Jun 2026"))
    assert "2" in _text(block) and timeline.t("analysis.timeline.images") in _text(block)
    stripes = _count_stripes(card)
    block.fire_event("click", None)
    assert _count_stripes(card) == stripes + 4  # two rows, in both lanes


def test_the_month_labels_are_in_the_readers_language():
    """``strftime("%b")`` gives "JUN" in every locale, and that label is
    interpolated into the translated ``collapsed_month`` / ``collapse_month``
    aria-labels -- so a French reader would hear "JUN 2026, 2 images, repli\u00e9."
    """
    rows = [(S2, "2026-06-05", 8.0), (S2, "2026-06-15", 8.0), (S2, "2026-04-24", 8.0)]
    english = _month_labels(timeline.build_timeline(_fake_state(rows=rows), _fake_analysis(),
                                                   _fake_widgets()))
    assert any(label.startswith("Jun 2026") for label in english)   # the collapsed block
    assert any(label.startswith("Apr") for label in english)        # an axis month
    set_locale("fr")
    try:
        french = _month_labels(timeline.build_timeline(_fake_state(rows=rows), _fake_analysis(),
                                                       _fake_widgets()))
    finally:
        set_locale("en")
    assert any(label.startswith("juin 2026") for label in french)
    assert any(label.startswith("avr.") for label in french)
    assert not [label for label in french if "Jun" in label or "Apr" in label]


def test_widening_past_the_coverage_asks_the_loader_for_the_month():
    """Finding from review: the button widened the *view* only, so a month
    outside the cache drew empty and no fetch was ever started."""
    state = _fake_state()
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    assert state.scene_window_request.value is None
    btn = next(w for w in _walk(card)
               if timeline.t("analysis.timeline.one_more_month") in _text(w)
               and "click" in (getattr(w, "_events", None) or []))
    btn.fire_event("click", None)
    assert state.scene_window_request.value is not None
    assert state.scene_window_request.value[0] < date(2026, 3, 3)


def test_a_widened_request_only_ever_grows():
    state = _fake_state()
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    buttons = [w for w in _walk(card)
               if timeline.t("analysis.timeline.one_more_month") in _text(w)
               and "click" in (getattr(w, "_events", None) or [])]
    assert len(buttons) == 2
    buttons[1].fire_event("click", None)
    forward = state.scene_window_request.value
    assert forward[1] > date(2026, 10, 23)
    buttons[0].fire_event("click", None)
    both = state.scene_window_request.value
    assert both[0] < forward[0] and both[1] == forward[1]


def test_the_legend_is_reachable_from_the_toolbar():
    card = timeline.build_timeline(_fake_state(), _fake_analysis(), _fake_widgets())
    assert any((getattr(w, "attributes", None) or {}).get("title") == timeline.t(
        "analysis.timeline.legend") for w in _walk(card))


# ------------------------------------------------------------ keyboard navigation

VIEW = (date(2026, 3, 1), date(2026, 6, 30))


def _segs(collapsed=()):
    return scene_query.month_segments(VIEW[0], VIEW[1], set(collapsed), timeline.TRACK_PX)


def test_arrowing_moves_to_the_next_visible_row():
    sel = _sel([(S2, "2026-03-05", 5.0), (S2, "2026-03-10", 5.0), (S2, "2026-03-15", 5.0)],
               start=VIEW[0], end=VIEW[1])
    mid = scene_index.row_id(S2, date(2026, 3, 10))
    assert timeline.step(sel, _segs(), mid, 1) == scene_index.row_id(S2, date(2026, 3, 15))
    assert timeline.step(sel, _segs(), mid, -1) == scene_index.row_id(S2, date(2026, 3, 5))


def test_arrowing_past_the_end_stays_put():
    sel = _sel([(S2, "2026-03-05", 5.0), (S2, "2026-03-10", 5.0)], start=VIEW[0], end=VIEW[1])
    last = scene_index.row_id(S2, date(2026, 3, 10))
    assert timeline.step(sel, _segs(), last, 1) == last
    first = scene_index.row_id(S2, date(2026, 3, 5))
    assert timeline.step(sel, _segs(), first, -1) == first


def test_arrowing_skips_a_row_the_cloud_limit_hides():
    sel = _sel([(S2, "2026-03-05", 5.0), (S2, "2026-03-10", 95.0), (S2, "2026-03-15", 5.0)],
               start=VIEW[0], end=VIEW[1])
    first = scene_index.row_id(S2, date(2026, 3, 5))
    assert timeline.step(sel, _segs(), first, 1) == scene_index.row_id(S2, date(2026, 3, 15))


def test_arrowing_skips_a_row_inside_a_collapsed_month():
    sel = _sel([(S2, "2026-03-05", 5.0), (S2, "2026-04-10", 5.0), (S2, "2026-05-15", 5.0)],
               start=VIEW[0], end=VIEW[1])
    first = scene_index.row_id(S2, date(2026, 3, 5))
    assert timeline.step(sel, _segs(collapsed=[(2026, 4)]), first, 1) == \
        scene_index.row_id(S2, date(2026, 5, 15))


def test_arrowing_skips_a_row_outside_the_laid_out_months():
    """A row whose month the segments do not hold at all is not drawn either,
    so it is not reachable: ``_navigable`` must not assume ``segments`` covers
    every row's month."""
    sel = _sel([(S2, "2026-03-05", 5.0), (S2, "2026-07-10", 5.0), (S2, "2026-03-20", 5.0)],
               start=VIEW[0], end=date(2026, 7, 31))
    assert timeline.step(sel, _segs(), scene_index.row_id(S2, date(2026, 3, 20)), 1) == \
        scene_index.row_id(S2, date(2026, 3, 20))


def test_arrowing_from_nothing_selected_takes_the_first_visible_row():
    sel = _sel([(S2, "2026-03-05", 5.0), (S2, "2026-03-10", 5.0)], start=VIEW[0], end=VIEW[1])
    assert timeline.step(sel, _segs(), None, 1) == scene_index.row_id(S2, date(2026, 3, 5))
    assert timeline.step(sel, _segs(), None, -1) == scene_index.row_id(S2, date(2026, 3, 10))


def test_arrowing_walks_the_rows_in_date_order_whatever_order_they_arrive_in():
    """``scenes_for`` happens to sort by date today, but its contract does not
    promise it; a cursor that walked the frame's row order would jump about."""
    sel = _sel([(S2, "2026-03-15", 5.0), (S2, "2026-03-05", 5.0), (S2, "2026-03-10", 5.0)],
               start=VIEW[0], end=VIEW[1])
    shuffled = sel.iloc[[2, 0, 1]].reset_index(drop=True)
    mid = scene_index.row_id(S2, date(2026, 3, 10))
    assert timeline.step(shuffled, _segs(), mid, 1) == scene_index.row_id(S2, date(2026, 3, 15))
    assert timeline.step(shuffled, _segs(), mid, -1) == scene_index.row_id(S2, date(2026, 3, 5))
    assert timeline.edge(shuffled, _segs()) == scene_index.row_id(S2, date(2026, 3, 5))
    assert timeline.edge(shuffled, _segs(), last=True) == scene_index.row_id(S2, date(2026, 3, 15))


def test_the_edges_are_the_first_and_last_visible_rows():
    sel = _sel([(S2, "2026-03-05", 5.0), (S2, "2026-03-10", 95.0), (S2, "2026-03-15", 5.0)],
               start=VIEW[0], end=VIEW[1])
    assert timeline.edge(sel, _segs()) == scene_index.row_id(S2, date(2026, 3, 5))
    assert timeline.edge(sel, _segs(), last=True) == scene_index.row_id(S2, date(2026, 3, 15))


def test_the_edges_of_an_empty_selection_are_nothing():
    sel = _sel([(S2, "2026-03-05", 95.0)], start=VIEW[0], end=VIEW[1])
    assert timeline.edge(sel, _segs()) is None
    assert timeline.step(sel, _segs(), None, 1) is None


# ---- which key a ``keydown`` payload asks for

def test_a_keydown_payload_is_read_by_its_key_name():
    assert timeline.key_action({"key": "ArrowLeft"}) == "left"
    assert timeline.key_action({"key": "ArrowRight"}) == "right"
    assert timeline.key_action({"key": "Home"}) == "home"
    assert timeline.key_action({"key": "End"}) == "end"
    assert timeline.key_action({"key": "Enter"}) == "enter"


def test_a_keydown_payload_falls_back_to_the_legacy_key_code():
    """Older WebKit sends ``key`` as "Left"/"Right"; some send neither, only
    ``keyCode``. ipyvue forwards every enumerable property of the event, so
    both are there to read."""
    assert timeline.key_action({"key": "Left"}) == "left"
    assert timeline.key_action({"keyCode": 39}) == "right"
    assert timeline.key_action({"keyCode": 13}) == "enter"


def test_a_key_the_timeline_does_not_use_asks_for_nothing():
    assert timeline.key_action({"key": "a", "keyCode": 65}) is None
    assert timeline.key_action({}) is None
    assert timeline.key_action(None) is None


# ---- the lanes

def _kb_state():
    return _fake_state(rows=[(S2, "2026-04-05", 5.0), (S2, "2026-04-12", 5.0),
                             (S2, "2026-04-24", 5.0), (LS, "2026-08-27", 3.0)])


def test_the_lanes_are_the_only_elements_claiming_an_explicit_tab_stop():
    """Not the only tab stops: the toolbar's buttons, the chips, the slider and
    the legend button are all natively focusable and meant to be. What this
    pins is that the two lanes are the only elements that *claim* a tab stop
    with ``tabindex="0"``, and that no stripe claims one at all."""
    card = timeline.build_timeline(_fake_state(), _fake_analysis(), _fake_widgets())
    stops = [w for w in _walk(card)
             if (getattr(w, "attributes", None) or {}).get("tabindex") == "0"]
    assert len(stops) == 2  # the before lane and the after lane
    assert {(w.attributes or {}).get("role") for w in stops} == {"listbox"}
    # and every stripe is taken out of the tab order, so Tab is two presses
    stripes = [w for w in _walk(card) if (w.attributes or {}).get("role") == "option"]
    assert stripes and all((w.attributes or {}).get("tabindex") == "-1" for w in stripes)


def test_one_bare_keydown_handler_per_lane_is_the_only_shape_ipyvue_allows():
    """``ipyvue.VueWidget.Events.on_event`` deletes every registration sharing
    the new one's base event, so ``on_event("keydown.left", ...)`` followed by
    ``on_event("keydown.right", ...)`` leaves only the right arrow -- and the
    front end, which merges one compiled ``on`` map per ``_events`` entry,
    would keep only the last one too. Five ``keydown.<key>`` handlers can
    never all fire; one ``keydown`` reading the payload can."""
    card = timeline.build_timeline(_kb_state(), _fake_analysis(), _fake_widgets())
    for which in ("0", "1"):
        assert _lane(card, which)._events == ["keydown"]


def test_the_arrows_walk_the_right_way_and_home_and_end_reach_the_right_ends():
    """The glue between ``key_action``'s string and ``step``'s ``delta`` is two
    lines, and it is the likeliest typo site in the widget: ``step(..., -1)``
    is pinned as a pure function and ``key_action({"key": "ArrowLeft"})`` is
    pinned as a lookup, but nothing tied the two together through the handler.
    Inverting the direction left every other test passing.

    ``_kb_state``'s navigable rows are, in date order: 2026-04-05, 04-12,
    04-24 (all Sentinel 2) and 2026-08-27 (Landsat). June and July are
    collapsed by ``auto_collapsed``, and the auto-pick starts the cursor on
    04-24, the last row before the alert window opens.
    """
    card = timeline.build_timeline(_kb_state(), _fake_analysis(), _fake_widgets())
    lane = _lane(card, "0")
    first = f"b-{scene_index.row_id(S2, date(2026, 4, 5))}"
    second = f"b-{scene_index.row_id(S2, date(2026, 4, 12))}"
    third = f"b-{scene_index.row_id(S2, date(2026, 4, 24))}"
    last = f"b-{scene_index.row_id(LS, date(2026, 8, 27))}"
    assert _cursor_id(lane) == third
    _press(lane, "ArrowLeft")
    assert _cursor_id(lane) == second, "Left goes to the earlier row"
    _press(lane, "ArrowLeft")
    assert _cursor_id(lane) == first
    _press(lane, "ArrowLeft")
    assert _cursor_id(lane) == first, "and stops at the first, it does not wrap"
    _press(lane, "ArrowRight")
    assert _cursor_id(lane) == second, "Right goes to the later row"
    _press(lane, "ArrowRight")
    _press(lane, "ArrowRight")
    assert _cursor_id(lane) == last
    _press(lane, "ArrowRight")
    assert _cursor_id(lane) == last, "and stops at the last"
    _press(lane, "Home")
    assert _cursor_id(lane) == first, "Home is the first row, not the last"
    _press(lane, "End")
    assert _cursor_id(lane) == last, "End is the last row, not the first"


def test_arrowing_moves_the_cursor_without_assigning_anything():
    """Spec 4.8: arrows move, Enter assigns. If an arrow assigned, walking ten
    scenes would queue ten map redraws."""
    analysis = _fake_analysis()
    card = timeline.build_timeline(_kb_state(), analysis, _fake_widgets())
    lane = _lane(card, "0")
    before = analysis.before_info.value
    start = _cursor_id(lane)
    assert start is not None, "the cursor starts on the auto-picked row"
    _press(lane, "ArrowRight")
    assert analysis.before_info.value is before            # nothing published
    assert _cursor_id(lane) is not None                    # but the cursor moved
    assert _cursor_id(lane) != start


def test_each_lane_has_a_cursor_of_its_own():
    """Asserting the landed id and not merely its ``a-`` prefix: the prefix
    comes from the lane that *rendered* the id, not from the cursor it read, so
    one shared reactive behind both lanes would satisfy ``startswith("a-")``
    while the after lane's own cursor had never moved. It did."""
    analysis = _fake_analysis()
    card = timeline.build_timeline(_kb_state(), analysis, _fake_widgets())
    before_lane, after_lane = _lane(card, "0"), _lane(card, "1")
    b_pick = f"b-{scene_index.row_id(S2, date(2026, 4, 24))}"
    a_pick = f"a-{scene_index.row_id(LS, date(2026, 8, 27))}"
    assert (_cursor_id(before_lane), _cursor_id(after_lane)) == (b_pick, a_pick)

    _press(after_lane, "Home")
    assert _cursor_id(after_lane) == f"a-{scene_index.row_id(S2, date(2026, 4, 5))}"
    assert _cursor_id(before_lane) == b_pick, "the before lane did not move with it"

    _press(before_lane, "ArrowRight")
    assert _cursor_id(before_lane) == f"b-{scene_index.row_id(LS, date(2026, 8, 27))}"
    assert _cursor_id(after_lane) == f"a-{scene_index.row_id(S2, date(2026, 4, 5))}", \
        "and the after lane stayed where its own Home put it"

    # and neither walk published anything: both sides still read their auto-pick
    assert analysis.before_info.value[3] == "Sentinel 2 2026-04-24 "
    assert analysis.after_info.value[3] == "Landsat 2026-08-27 "


def test_a_key_the_lane_does_not_use_does_nothing():
    analysis = _fake_analysis()
    card = timeline.build_timeline(_kb_state(), analysis, _fake_widgets())
    lane = _lane(card, "0")
    was, published = _cursor_id(lane), analysis.before_info.value
    _press(lane, "a")
    assert _cursor_id(lane) == was and analysis.before_info.value is published


def test_enter_assigns_the_row_the_cursor_is_on():
    """``row_id`` is ``<source letter><YYYYMMDD>`` and ``info_tuple``'s fourth
    element carries the ISO date, so the two can be checked against each other
    without the test knowing which scene End landed on."""
    analysis = _fake_analysis()
    card = timeline.build_timeline(_kb_state(), analysis, _fake_widgets())
    lane = _lane(card, "0")
    _press(lane, "End")
    cursor = _cursor_id(lane)
    assert cursor != f"b-{scene_index.row_id(S2, date(2026, 4, 24))}", "End is not where it started"
    _press(lane, "Enter")
    info = analysis.before_info.value
    assert info is not None
    iso = info[3].strip().rsplit(" ", 1)[-1]
    assert cursor == f"b-{scene_index.row_id(info[0], date.fromisoformat(iso))}"
    assert _cursor_id(lane) == cursor             # Enter assigns; it does not move
    assert analysis.before_keys.value             # and the granule keys came with it


def test_enter_on_a_lane_whose_cursor_has_not_moved_assigns_the_selection():
    analysis = _fake_analysis()
    card = timeline.build_timeline(_kb_state(), analysis, _fake_widgets())
    lane = _lane(card, "0")
    cursor = _cursor_id(lane)
    analysis.before_info.set(None)
    _press(lane, "Enter")
    assert analysis.before_info.value is not None
    assert _cursor_id(lane) == cursor


def test_the_focused_stripe_is_named_for_a_screen_reader():
    card = timeline.build_timeline(_kb_state(), _fake_analysis(), _fake_widgets())
    lane = _lane(card, "0")
    _press(lane, "Home")
    named = _cursor_id(lane)
    ids = {(getattr(w, "attributes", None) or {}).get("id") for w in _walk(card)}
    assert named in ids  # aria-activedescendant points at a stripe that exists
    assert named == f"b-{scene_index.row_id(S2, date(2026, 4, 5))}"


def test_the_cursor_is_drawn_as_a_ring_on_exactly_one_stripe_per_lane():
    card = timeline.build_timeline(_kb_state(), _fake_analysis(), _fake_widgets())
    _press(_lane(card, "0"), "End")
    ringed = [w for w in _walk(card) if "outline: 1px dashed" in (getattr(w, "style_", "") or "")]
    assert len(ringed) == 2  # one per lane: each lane has its own cursor
    assert {(w.attributes or {}).get("id")[0] for w in ringed} == {"b", "a"}


def test_a_lane_names_no_cursor_when_there_is_nothing_to_point_at():
    """``aria-activedescendant`` is dropped rather than left empty: an empty
    attribute is still an attribute, and it must never name a missing id."""
    state = _fake_state(rows=[(S2, "2026-04-24", 95.0)])
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    lane = _lane(card, "0")
    assert "aria-activedescendant" not in (lane.attributes or {})
    _press(lane, "End")
    assert "aria-activedescendant" not in (lane.attributes or {})


def test_emptying_the_track_takes_the_cursor_with_it():
    """Turning every collection off clears the lanes; an
    ``aria-activedescendant`` left behind would name a stripe that is gone."""
    card = timeline.build_timeline(_kb_state(), _fake_analysis(), _fake_widgets())
    lane = _lane(card, "0")
    assert _cursor_id(lane) is not None
    for chip in [w for w in _walk(card)
                 if (getattr(w, "attributes", None) or {}).get("aria-pressed") == "true"]:
        chip.fire_event("click", None)
    assert timeline.t("analysis.timeline.no_images") in _text(card)
    assert "aria-activedescendant" not in (lane.attributes or {})


def test_dropping_the_alert_takes_the_cursor_off_the_lane():
    """``imagery_ctx`` going to ``None`` empties the track through
    ``_clear_track``, which is the one emptying path ``_render_lane`` never
    runs for -- so the ``aria-activedescendant`` drop has to happen there too,
    or the lane goes on naming a stripe that is no longer in the tree.

    ``test_emptying_the_track_takes_the_cursor_with_it`` does not cover this:
    the chips are switched off one at a time, so the last render before the
    track empties is a ``_render_lane`` that has already dropped the key.
    """
    widgets = _fake_widgets()
    card = timeline.build_timeline(_kb_state(), _fake_analysis(), widgets)
    lane = _lane(card, "0")
    _press(lane, "End")
    assert _cursor_id(lane) == f"b-{scene_index.row_id(LS, date(2026, 8, 27))}"
    widgets.imagery_ctx.set(None)
    assert timeline.t("analysis.timeline.no_images") in _text(card)
    assert list(lane.children) == []
    assert "aria-activedescendant" not in (lane.attributes or {})


def test_a_new_alert_clears_the_cursor_with_the_selection():
    widgets = _fake_widgets()
    card = timeline.build_timeline(_kb_state(), _fake_analysis(), widgets)
    lane = _lane(card, "0")
    _press(lane, "End")
    walked = _cursor_id(lane)
    widgets.imagery_ctx.set({**_ctx(), "alert_id": 8, "token": 2})
    assert _cursor_id(lane) != walked  # back to wherever the new auto-pick is


def test_the_legend_is_a_button_that_opens_the_explanation():
    """Review finding: the only description of the lanes, bands, fade and gold
    band hung off ``aria-label`` on a ``role="note"`` span, which is not
    focusable and is not reliably announced."""
    card = timeline.build_timeline(_fake_state(), _fake_analysis(), _fake_widgets())
    badge = next(w for w in _walk(card)
                 if (getattr(w, "attributes", None) or {}).get("aria-expanded") is not None)
    assert badge.tag == "button"
    assert "tabindex" not in (badge.attributes or {})  # a button is focusable already
    assert timeline.t("analysis.timeline.legend") not in _text(card)
    badge.fire_event("click", None)
    assert badge.attributes["aria-expanded"] == "true"
    assert timeline.t("analysis.timeline.legend") in _text(card)
    badge.fire_event("click", None)
    assert badge.attributes["aria-expanded"] == "false"
    assert timeline.t("analysis.timeline.legend") not in _text(card)


def test_the_legend_button_is_named_by_something_short():
    card = timeline.build_timeline(_fake_state(), _fake_analysis(), _fake_widgets())
    badge = next(w for w in _walk(card)
                 if (getattr(w, "attributes", None) or {}).get("aria-expanded") is not None)
    name = badge.attributes["aria-label"]
    assert name == timeline.t("analysis.timeline.legend_label")
    assert len(name) < 60 and name != timeline.t("analysis.timeline.legend")
    # and it points at a region that is really in the tree, so the expanded
    # explanation is the button's own
    region = badge.attributes["aria-controls"]
    assert region in {(getattr(w, "attributes", None) or {}).get("id") for w in _walk(card)}


def _lane(card, which):
    lanes = [w for w in _walk(card)
             if (getattr(w, "attributes", None) or {}).get("role") == "listbox"]
    return lanes[0] if which == "0" else lanes[1]


def _cursor_id(lane):
    return (lane.attributes or {}).get("aria-activedescendant") or None


def _press(lane, key: str) -> None:
    lane.fire_event("keydown", {"key": key})


# ---- fakes: the builder reads reactives and a context dict, nothing else
def _manifest(hours_ago=0, planet=None):
    return scene_index.SceneManifest(
        aoi_digest="d1",
        refreshed=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        covered=scene_index.Coverage(start=date(2026, 1, 1), end=date(2026, 12, 31)),
        counts={S2: 1, LS: 1},
        planet=planet or scene_index.PlanetState(),
    )


def _fake_state(index="default", rows=None):
    import solara

    from deforestation_alerts.scripts.scene_index import SceneTables

    if index == "default":
        scenes = _sel(rows or [(S2, "2026-04-24", 8.0), (LS, "2026-08-27", 3.0)], max_cloud=None)
        scenes = scenes.assign(tile="W", key="k")[list(scene_index.SCENE_COLUMNS)]
        index = SceneTables(scenes=scenes, tiles=TILES)
    return SimpleNamespace(
        project=solara.reactive(_project(), equals=lambda a, b: a is b),
        scene_index=solara.reactive(index, equals=lambda a, b: a is b),
        scene_manifest=solara.reactive(None, equals=lambda a, b: a is b),
        scene_index_pending=solara.reactive(False),
        scene_index_error=solara.reactive(None),
        scene_index_retry=solara.reactive(0),
        scene_widening=solara.reactive(False),
        scene_window_request=solara.reactive(None),
    )


def _fake_analysis():
    from gui.tile.analysis.state import AnalysisState

    return AnalysisState()


def _fake_widgets(bbox=None):
    import solara

    ctx = dict(_ctx())
    if bbox is not None:
        ctx["bbox"] = bbox
    return SimpleNamespace(imagery_ctx=solara.reactive(ctx))


def _text(widget) -> str:
    """Every string anywhere in a widget tree, for asserting on rendered copy."""
    if isinstance(widget, str):
        return widget
    return " ".join(_text(c) for c in (getattr(widget, "children", None) or ()))


def _walk(widget):
    yield widget
    for child in (getattr(widget, "children", None) or ()):
        if not isinstance(child, str):
            yield from _walk(child)


def _month_labels(card) -> list:
    """Every month aria-label in the tree: the blocks' and the axis ticks'."""
    out = []
    for w in _walk(card):
        label = (getattr(w, "attributes", None) or {}).get("aria-label", "")
        static = (timeline.t("analysis.timeline.legend"),
                  timeline.t("analysis.timeline.legend_label"),
                  timeline.t("analysis.timeline.lane_before"),
                  timeline.t("analysis.timeline.lane_after"))
        if label and label not in static:
            out.append(label)
    return out


def _clickable_for(card, when: date) -> list:
    """The clickable stripes whose aria-label names ``when``."""
    return [w for w in _walk(card)
            if when.isoformat() in ((getattr(w, "attributes", None) or {}).get("aria-label", ""))
            and "click" in (getattr(w, "_events", None) or [])]


def _left_pct(style: str) -> float:
    """The ``left:`` percentage of a ``calc(x% - Npx)`` offset."""
    return float(style.split("left: calc(")[1].split("%")[0])


def _bar_width(row, *, selected: bool) -> float:
    return float(timeline.stripe_bar_style(row, selected=selected).split(
        "width: ")[1].split("px")[0])


def _count_stripes(card) -> int:
    return sum(1 for w in _walk(card)
               if (getattr(w, "attributes", None) or {}).get("aria-label", "").endswith(
                   (timeline.t("analysis.timeline.set_before"),
                    timeline.t("analysis.timeline.set_after"))))


def _click(lane, row_id_: str) -> None:
    """Fire the click of the stripe whose aria-label names ``row_id_``'s date."""
    stamp = datetime.strptime(row_id_[1:], "%Y%m%d").date().isoformat()
    hit = [w for w in _walk(lane)
           if stamp in (getattr(w, "attributes", None) or {}).get("aria-label", "")
           and "click" in (getattr(w, "_events", None) or [])]
    assert len(hit) == 1, f"{len(hit)} clickable stripes for {row_id_}"
    hit[0].fire_event("click", None)


# ------------------------------------------------- the rebuild from the keys

def test_rebuilding_a_sentinel2_image_mosaics_exactly_the_given_granules(monkeypatch):
    from gui.tile.analysis import imagery

    calls = {}

    class FakeImage:
        def __init__(self, key):
            calls.setdefault("images", []).append(key)

    class FakeCollection:
        def __init__(self, images):
            calls["collection"] = len(images)

        def mosaic(self):
            return self

        def clip(self, geom):
            calls["clipped"] = geom
            return self

    monkeypatch.setattr(imagery.ee, "Image", FakeImage)
    monkeypatch.setattr(imagery.ee, "ImageCollection", FakeCollection)
    imagery.rebuild_image(S2, ["a", "b"], "GEOM")
    assert calls["images"] == ["a", "b"] and calls["collection"] == 2
    assert calls["clipped"] == "GEOM"


def test_rebuilding_without_keys_returns_nothing_rather_than_an_empty_mosaic():
    from gui.tile.analysis import imagery

    assert imagery.rebuild_image(S2, [], "GEOM") is None


def test_rebuilding_a_landsat_image_looks_the_scene_ids_up_in_both_collections(monkeypatch):
    from gui.tile.analysis import imagery

    seen = []
    monkeypatch.setattr(imagery, "_landsat_pair", lambda keys, geom: seen.append((keys, geom)) or "IMG")
    assert imagery.rebuild_image(LS, ["LC8X"], "GEOM") == "IMG"
    assert seen == [(["LC8X"], "GEOM")]
    # the two halves of one acquisition, named where a test can reach them:
    # the surface-reflectance product the index is built from, and the TOA
    # product band 8 pan-sharpens it
    assert imagery.LANDSAT_SR_COLLECTIONS == scene_index.LANDSAT_COLLECTIONS
    assert imagery.LANDSAT_TOA_COLLECTIONS == ("LANDSAT/LC08/C02/T1_TOA", "LANDSAT/LC09/C02/T1_TOA")


def test_an_in_window_image_is_offered_by_hand_even_though_nothing_picks_it():
    """The invariant this plan inverts, from the widget's side. The retired
    selectors only ever *listed* out-of-window dates, so an in-window image
    was unreachable; the timeline draws every date and leaves the refusal to
    ``default_pick``, because a long GLAD window can leave a reviewer no other
    choice. So: nothing in the gold band is auto-picked, and a click inside it
    still assigns."""
    rows = [(S2, "2026-04-24", 8.0), (S2, "2026-06-10", 8.0), (LS, "2026-08-27", 3.0)]
    state = _fake_state(rows=rows)
    analysis = _fake_analysis()
    widgets = _fake_widgets()
    ctx = dict(_ctx())
    ctx["token"] = analysis.alert_token.value
    widgets.imagery_ctx.set(ctx)
    card = timeline.build_timeline(state, analysis, widgets)

    in_window = scene_index.row_id(S2, date(2026, 6, 10))
    # not chosen for the reviewer: 2026-06-10 is inside [2026-05-03, 2026-08-23]
    assert analysis.before_info.value[3] == "Sentinel 2 2026-04-24 "
    assert analysis.after_info.value[3] == "Landsat 2026-08-27 "
    # June lies wholly inside the window, so it opens folded away -- and the
    # reviewer can unfold it, which is what "offered, just not chosen" means
    assert not _clickable_for(card, date(2026, 6, 10))
    june = timeline.format_date(date(2026, 6, 1), "MMM y")
    [block] = [w for w in _walk(card)
               if (getattr(w, "attributes", None) or {}).get("aria-label", "").startswith(june)]
    block.fire_event("click", None)
    assert _clickable_for(card, date(2026, 6, 10))

    lanes = [w for w in _walk(card)
             if (w.style_ or "").startswith(f"position: relative; height: {timeline.LANE_H}px")]
    _click(lanes[0], in_window)
    assert analysis.before_info.value[3] == "Sentinel 2 2026-06-10 "
    assert analysis.before_keys.value  # and its granules are published with it


def test_the_same_image_on_both_sides_is_flagged_rather_than_read_as_a_gap():
    """A single-day alert window with one scene on it: ``default_pick`` legally
    returns the same id twice. Both panes then draw the identical image, which
    is confusing unless the strip says so, so the note is rendered in the
    warning colour the refresh failure uses -- not in the muted grey a
    neutral "N days apart" gets."""
    state = _fake_state(rows=[(S2, "2026-06-10", 8.0)])
    analysis = _fake_analysis()
    widgets = _fake_widgets()
    ctx = dict(_ctx(date_min=date(2026, 6, 10), date_max=date(2026, 6, 10)))
    ctx["token"] = analysis.alert_token.value
    widgets.imagery_ctx.set(ctx)
    card = timeline.build_timeline(state, analysis, widgets)

    note = timeline.t("analysis.timeline.same_image")
    assert note in _text(card)
    [span] = [w for w in _walk(card) if note in (getattr(w, "children", None) or ())]
    assert timeline.WARN in (span.style_ or "")
    assert timeline.MUTED not in (span.style_ or "")
