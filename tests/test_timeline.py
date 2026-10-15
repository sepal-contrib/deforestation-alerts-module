"""The shared image timeline widget (spec §4-§5): geometry, copy and wiring.

Everything here is offline. The builder reads reactives and one context dict,
so the whole widget is exercised by handing it fakes -- no Earth Engine, no
Solara render context, no map.

The geometry assertions compute their expected pixel from first principles
(days / total days of the view) rather than by calling ``x_for`` again, so a
stripe that is laid out with the wrong month's origin, against the wrong
denominator, or in pixels where percentages were meant, fails here.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pytest
from pysepal.i18n import set_locale
from shapely.geometry import box

from deforestation_alerts.project.models import AoiRecord, Period, Project
from deforestation_alerts.scripts import scene_index, scene_query
from gui.widget.handle_strip import HandleStrip
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


def _any_index(rows):
    """A scene index holding ``rows`` whatever the view window: the restore
    tests need rows the default window does not reach.

    Not ``_index``: that name is the five-column helper further down, which
    would shadow this one at import."""
    scenes = pd.DataFrame(
        [(src, pd.Timestamp(d), cloud, "W", f"k{i}") for i, (src, d, cloud) in enumerate(rows)],
        columns=list(scene_index.SCENE_COLUMNS),
    ).astype({"date": "datetime64[ns]"})
    return scene_index.SceneTables(scenes=scenes, tiles=TILES)


def _covering(start=date(2025, 1, 1), end=date(2027, 12, 31)):
    """A manifest saying the index has answered for ``start``..``end``."""
    return scene_index.SceneManifest(aoi_digest="d1", refreshed=datetime.now(timezone.utc),
                                     covered=scene_index.Coverage(start=start, end=end))


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
    """A pair saved before ``default_pick`` stopped returning one id twice for
    a single-day window can still reopen as one image on both sides. "0 days
    apart" would read as two images that happen to agree."""
    _sel([(S2, "2026-04-24", 8.0)])
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
    # theme variables: ``THEME_CSS`` holds both themes' values; the fallback
    # is the dark one
    assert timeline.SRC_COLOR == {"Sentinel 2": "var(--dat-s2, #7fa8e0)",
                                  "Landsat": "var(--dat-landsat, #c9a87c)",
                                  "Planet NICFI": "var(--dat-planet, #9bd98a)"}
    assert timeline.SELECTED_COLOR == "#bf8f2d"  # the theme's primary_contrast
    # the two greys/ambers the strip reads in: MUTED for what is secondary,
    # WARN for the two things the reviewer has to notice rather than read (a
    # failed index refresh, and one image standing in for both sides)
    assert (timeline.MUTED, timeline.WARN) == ("var(--dat-muted, #a8a39b)", "var(--dat-warn, #d9ab4e)")
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
            "set_before", "set_after", "collapsed_month", "no_cloud_filter", "swap_source"}
    for locale in ("en", "es-ES", "fr", "pt-BR"):
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
    # and the map title agrees with what was published, rather than still
    # naming the previous alert's image
    text = _text(widgets.map_title_before)
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
    # the 30% row is not a selection: a selected row keeps its full stripe
    state = _fake_state(rows=[(S2, "2026-04-24", 8.0), (LS, "2026-08-27", 3.0), (LS, "2026-09-04", 30.0)])
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    assert not [w for w in _walk(card) if "opacity: 0.3" in (getattr(w, "style_", "") or "")]
    slider = next(w for w in _walk(card) if (getattr(w, "label", None) or "").startswith(timeline.t(
        "analysis.timeline.max_cloud")))
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
    assert _count_stripes(card) == stripes + 2  # two rows, one lane


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


def test_repeated_widening_on_the_same_alert_is_cumulative():
    """Two presses ask for two months, not one -- that is the feature. The
    request is recomputed from ``extra``, which grows, rather than unioned
    with whatever was there, which is what made it leak across alerts."""
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


# ------------------------------------------------------------ the legend

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
        scene_window_request=solara.reactive(None),
    )


def _fake_analysis():
    from gui.tile.analysis.state import AnalysisState

    return AnalysisState()


def _fake_widgets(bbox=None, **ctx_extra):
    import ipyvuetify as v
    import solara

    ctx = dict(_ctx())
    if bbox is not None:
        ctx["bbox"] = bbox
    ctx.update(ctx_extra)
    return SimpleNamespace(imagery_ctx=solara.reactive(ctx), timeline_filter=solara.reactive(None),
                           map_title_before=v.Html(tag="div", children=[]),
                           map_title_after=v.Html(tag="div", children=[]))


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
    """The stripes in the lane: one per drawn row, whatever its label says."""
    return sum(1 for w in _walk(_lane(card)) if getattr(w, "tag", None) == "button")


def _lane(card):
    """The one lane every stripe is drawn in."""
    [lane] = [w for w in _walk(card)
              if (getattr(w, "style_", "") or "") == f"position: relative; height: {timeline.LANE_H}px"]
    return lane


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

    # between the handles and after the alert start: an "after" look
    _click(_lane(card), in_window)
    assert analysis.after_info.value[3] == "Sentinel 2 2026-06-10 "
    assert analysis.after_keys.value  # and its granules are published with it


def test_a_pick_of_other_imagery_is_an_unsaved_change_that_drops_the_predictions():
    """The reviewer's own pick costs the review something; the auto-pick does not.

    The store keeps the pair a save was made with (``before_img`` /
    ``after_img``), so choosing other imagery is a change to save -- and a CNN
    prediction is a function of the composite it ran on, so what a run of the
    previous pair found is not about what is on screen any more. The auto-pick
    in ``render`` is this alert's default, not a change: an alert nobody
    touched must not open claiming unsaved work.
    """
    state = _fake_state(rows=[(S2, "2026-04-10", 8.0), (S2, "2026-04-24", 8.0),
                              (LS, "2026-08-27", 3.0)])
    analysis = _fake_analysis()
    widgets = _fake_widgets()
    ctx = dict(_ctx())
    ctx["token"] = analysis.alert_token.value
    widgets.imagery_ctx.set(ctx)
    card = timeline.build_timeline(state, analysis, widgets)

    assert analysis.before_info.value[3] == "Sentinel 2 2026-04-24 "
    assert analysis.dirty.value is False, "the auto-pick is not the reviewer's change"

    # a CNN run on that pair
    analysis.prediction_files.set({1: "/f/alert_0_s_240426_l_270826.tif"})
    analysis.prediction_shapes.set({1: (2, 3.9)})

    _click(_lane(card), scene_index.row_id(S2, date(2026, 4, 10)))

    assert analysis.before_info.value[3] == "Sentinel 2 2026-04-10 "
    assert analysis.dirty.value is True
    assert analysis.prediction_files.value == {}
    assert analysis.prediction_shapes.value == {}

    # Clicking the stripe that is already picked publishes nothing new, so it
    # is not an unsaved change either.
    analysis.dirty.set(False)
    _click(_lane(card), scene_index.row_id(S2, date(2026, 4, 10)))
    assert analysis.dirty.value is False


def test_the_same_image_on_both_sides_is_flagged_rather_than_read_as_a_gap():
    """``default_pick`` no longer pairs one image with itself, but a review
    saved before that change can reopen on one. Both panes then draw the
    identical image, which is confusing unless the strip says so, so the note
    is rendered in the warning colour the refresh failure uses -- not in the
    muted grey a neutral "N days apart" gets."""
    state = _fake_state(rows=[(S2, "2026-06-10", 8.0)])
    analysis = _fake_analysis()
    widgets = _fake_widgets()
    ctx = dict(_ctx(date_min=date(2026, 6, 10), date_max=date(2026, 6, 10)))
    ctx["token"] = analysis.alert_token.value
    ctx["saved_before"] = ctx["saved_after"] = "Sentinel 2 2026-06-10 "
    widgets.imagery_ctx.set(ctx)
    card = timeline.build_timeline(state, analysis, widgets)

    note = timeline.t("analysis.timeline.same_image")
    strip = next(w for w in _walk(card) if isinstance(w, HandleStrip))
    # the bracket carries it, in the warning colour (``gap_warn``)
    assert strip.gap == note and strip.gap_warn is True


def test_an_alert_whose_dates_are_missing_publishes_no_window_rather_than_a_wrong_one():
    """``_as_date`` is what decides whether the gold alert band draws at all.

    ``vectorize`` writes NaN into ``alert_date_min`` when a polygon's date
    pixels are all nodata, and ``convert_julian_to_date`` renders that "N/A";
    the ctx has to carry the *absence* so ``view_window`` / ``alert_window``
    fall back to the period's window instead of banding a bogus date.
    """
    from gui.tile.analysis.analysis_tile import _as_date

    assert _as_date(2026.010) == date(2026, 1, 10)
    assert _as_date(2026.365) == date(2026, 12, 31)
    assert _as_date(float("nan")) is None
    assert _as_date(None) is None


# ------------------------------------- what reaches the pane (final fix wave)

#: two tiles, one over the alert's bbox and one 8 degrees away
TILES_TWO = gpd.GeoDataFrame(
    {"tile": ["W", "E"]},
    geometry=gpd.GeoSeries([box(0, 0, 2, 2), box(8, 8, 9, 9)], crs=scene_index.WGS84),
    crs=scene_index.WGS84)


def _index(rows, tiles=TILES):
    """A ``SceneTables`` from explicit ``(source, date, cloud, tile, key)`` rows.

    ``_fake_state``'s default index gives every row the same tile and the same
    key, which is enough for geometry and copy but cannot tell one granule
    from another. The two tests below are about exactly *which* granule keys
    reach ``imagery.rebuild_image``, so they need distinguishable ones.
    """
    scenes = pd.DataFrame(
        [(src, pd.Timestamp(d), cloud, tile, key) for src, d, cloud, tile, key in rows],
        columns=list(scene_index.SCENE_COLUMNS),
    ).astype({"date": "datetime64[ns]"})
    return scene_index.SceneTables(scenes=scenes, tiles=tiles)


def test_the_granule_keys_are_published_before_the_info_that_wakes_the_pane():
    """``imagery.wire_panes`` subscribes to ``before_info`` and reads
    ``before_keys`` *at that publish* (imagery.py), so ``_assign`` has to set
    the keys first. With the two publishes swapped the pane is rebuilt from the
    previous pick's granules -- the new date in the readout, the old image on
    the map -- and the order was asserted in prose in three places and nowhere
    in a test.
    """
    state = _fake_state(index=_index([(S2, "2026-04-24", 8.0, "W", "kA"),
                                      (LS, "2026-04-10", 3.0, "W", "kB"),
                                      (LS, "2026-08-27", 3.0, "W", "kC")]))
    analysis = _fake_analysis()
    card = timeline.build_timeline(state, analysis, _fake_widgets())
    assert analysis.before_keys.value == ["kA"]  # the auto-pick's own granule
    seen = []
    # exactly what the pane does: read the keys when the info lands
    analysis.before_info.subscribe(lambda _info: seen.append(list(analysis.before_keys.value)))
    _click(_lane(card), scene_index.row_id(LS, date(2026, 4, 10)))
    assert analysis.before_info.value[0] == LS
    assert seen == [["kB"]], "the pane was woken while the keys were still the previous pick's"


def test_only_the_granules_that_cover_the_alert_are_handed_to_the_rebuild():
    """Review Focus #5 on the *assignment* side rather than the counting side.

    Two granules of one date, one on a tile over the alert and one on a tile
    eight degrees away. ``rebuild_image`` mosaics whatever keys it is given
    and does no ``filterBounds`` of its own, so handing it both would draw a
    mosaic of imagery that does not cover the alert -- while the row's cloud
    figure is the mean over the covering granule only. The map and the readout
    would then describe different granule sets.
    """
    state = _fake_state(index=_index([(S2, "2026-04-24", 8.0, "W", "near"),
                                      (S2, "2026-04-24", 90.0, "E", "far")],
                                     tiles=TILES_TWO))
    analysis = _fake_analysis()
    timeline.build_timeline(state, analysis, _fake_widgets())
    assert analysis.before_keys.value == ["near"]
    # the same granule the cloud figure describes: 8.00, not the mean of 8 and 90
    assert analysis.before_info.value[2] == "8.00"


# ------------------------------------------------- the chips (final fix wave)

def _chips(card) -> list:
    """``[(label, pressed)]`` for the three collection chips, in their order."""
    return [(_text(w).strip(), (w.attributes or {}).get("aria-pressed") == "true")
            for w in _walk(card)
            if (getattr(w, "attributes", None) or {}).get("aria-pressed") is not None]


def _chip_click(card, label_start: str) -> None:
    hit = [w for w in _walk(card)
           if (getattr(w, "attributes", None) or {}).get("aria-pressed") is not None
           and _text(w).strip().startswith(label_start)]
    assert len(hit) == 1, f"{len(hit)} chips starting {label_start!r}"
    hit[0].fire_event("click", None)


def test_a_chip_counts_what_its_collection_holds_in_the_window():
    """Spec §4.6's in-view count, which is the number that tells the reviewer
    whether re-enabling a collection is worth it. Before the final fix the
    source filter ran inside ``_selection()``, so every chip but the enabled
    ones read 0 and the count said nothing at all."""
    state = _fake_state(index=_index([(S2, "2026-04-24", 8.0, "W", "kA"),
                                      (LS, "2026-08-27", 3.0, "W", "kB"),
                                      (LS, "2026-09-02", 2.0, "W", "kC"),
                                      (PL, "2026-05-01", float("nan"), "", "kP")]))
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    assert _chips(card) == [("Planet 1", True), ("Sentinel-2 1", True), ("Landsat 2", True)]

    _chip_click(card, "Landsat")
    # the two Landsat scenes are no longer drawn, but the chip still says how
    # many of them turning it back on would bring back
    assert _chips(card) == [("Planet 1", True), ("Sentinel-2 1", True), ("Landsat 2", False)]
    # two drawn rows: the S2 stripe and the Planet bar
    assert _count_stripes(card) == 2


def test_a_chip_still_splits_the_cloud_hidden_rows_while_its_collection_is_off():
    state = _fake_state(index=_index([(S2, "2026-04-24", 8.0, "W", "kA"),
                                      (S2, "2026-05-11", 77.0, "W", "kB")]))
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    assert _chips(card)[1] == ("Sentinel-2 1 +1", True)  # one shown, one over the limit
    _chip_click(card, "Sentinel-2")
    assert _chips(card)[1] == ("Sentinel-2 1 +1", False)


def test_with_every_collection_off_the_strip_says_so_rather_than_claiming_no_images():
    state = _fake_state(index=_index([(S2, "2026-04-24", 8.0, "W", "kA"),
                                      (LS, "2026-08-27", 3.0, "W", "kB")]))
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    for name in ("Planet", "Sentinel-2", "Landsat"):
        _chip_click(card, name)
    assert _chips(card) == [("Planet 0", False), ("Sentinel-2 1", False), ("Landsat 1", False)]
    assert _count_stripes(card) == 0
    shown = _text(card)
    off = timeline.t("analysis.timeline.sources_off")
    assert off in shown, f"expected the switched-off message, got: {shown!r}"
    assert timeline.t("analysis.timeline.no_images") not in shown
    # the rendered string, not the key: ``t`` returns the bare key for a
    # template given no values, and that would pass an assertion on the key
    assert "analysis.timeline" not in off


def test_an_index_with_no_rows_in_the_window_still_says_no_images():
    """The other half of the pair: ``no_images`` must keep meaning exactly
    what it says, so this is the case it is now reserved for."""
    state = _fake_state(index=_index([(S2, "2025-01-05", 8.0, "W", "kA")]))
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    assert timeline.t("analysis.timeline.no_images") in _text(card)
    assert timeline.t("analysis.timeline.sources_off") not in _text(card)
    assert _chips(card) == [("Planet 0", True), ("Sentinel-2 0", True), ("Landsat 0", True)]


def test_a_collapsed_month_counts_only_the_collections_that_are_on():
    """The other side of the split: the chips widen to the whole window, the
    month counts must not -- a collapsed month claiming hidden stripes would
    expand to fewer than it promised."""
    # June lies wholly inside the alert window, so it opens collapsed
    state = _fake_state(index=_index([(S2, "2026-06-04", 8.0, "W", "kA"),
                                      (LS, "2026-06-11", 3.0, "W", "kB"),
                                      (S2, "2026-04-24", 5.0, "W", "kC")]))
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    june = [lab for lab in _month_labels(card) if lab.startswith("Jun 2026,")]
    assert june == [timeline.t("analysis.timeline.collapsed_month", month="Jun 2026", count=2)]
    _chip_click(card, "Landsat")
    june = [lab for lab in _month_labels(card) if lab.startswith("Jun 2026,")]
    assert june == [timeline.t("analysis.timeline.collapsed_month", month="Jun 2026", count=1)]


def test_opening_another_alert_asks_for_its_own_window_not_the_widened_one():
    """Finding I2 from the final review. ``scene_window_request`` was absolute
    and monotone and was reset only on a project switch, so "+1 month" on a
    Jan-2024 alert still widened the foreground fetch of a Dec-2026 one opened
    afterwards -- one unchunked ``getInfo`` over roughly three years, where
    spec §5.5 promises one foreground single-month fetch."""
    state = _fake_state()
    widgets = _fake_widgets()
    card = timeline.build_timeline(state, _fake_analysis(), widgets)
    left = [w for w in _walk(card)
            if timeline.t("analysis.timeline.one_more_month") in _text(w)
            and "click" in (getattr(w, "_events", None) or [])][0]
    left.fire_event("click", None)
    # alert A's own view: its window padded by the two months of ``VIEW_MONTHS``
    # plus the one just asked for
    assert state.scene_window_request.value == (date(2026, 2, 3), date(2026, 10, 23))

    # a different alert, two years earlier, with nothing in the index for it
    widgets.imagery_ctx.set({"alert_id": 9, "token": 2, "bbox": box(0.5, 0.5, 1.5, 1.5),
                             "date_min": date(2024, 1, 10), "date_max": date(2024, 1, 20)})
    asked = state.scene_window_request.value
    assert asked == (date(2023, 10, 10), date(2024, 3, 20)), (
        "the foreground request still carries the other alert's dates")
    # the widened width persists as a preference (spec §4.7) -- three months
    # back, not two -- but the months are this alert's
    assert asked[1] < date(2026, 1, 1)

    # and coming back to A asks for A's window again, not the union of both
    widgets.imagery_ctx.set(dict(_ctx()))
    assert state.scene_window_request.value == (date(2026, 2, 3), date(2026, 10, 23))


def test_an_alert_whose_window_was_never_widened_asks_for_nothing():
    """``extra == (0, 0)`` has to publish ``None`` rather than the alert's own
    ±2 months: that span lies inside the padding ``wanted_window`` already
    puts around the active period, so a request for it would make every alert
    change look like a widen to the loader."""
    state = _fake_state()
    widgets = _fake_widgets()
    card = timeline.build_timeline(state, _fake_analysis(), widgets)
    assert state.scene_window_request.value is None
    left = [w for w in _walk(card)
            if timeline.t("analysis.timeline.one_more_month") in _text(w)
            and "click" in (getattr(w, "_events", None) or [])][0]
    left.fire_event("click", None)
    assert state.scene_window_request.value is not None
    # the alert goes away entirely: there is no view to ask for
    widgets.imagery_ctx.set(None)
    assert state.scene_window_request.value is None


def test_a_collection_this_build_has_no_band_for_is_dropped_rather_than_raising(caplog):
    """T10 Minor 7 / review m1's reachable trigger. Every drawing helper
    indexes ``BAND``, ``SRC_COLOR`` and ``SRC_LABEL`` by the source string and
    ``scene_query.row_id`` indexes ``SOURCE_CODE``, so one row naming a fourth
    collection raised a ``KeyError`` out of the middle of ``render`` -- from
    where it travelled back through ``reactive.set`` into the loader and was
    reported as a failed index refresh. The index is data read from disk and
    from Earth Engine, so a release that adds a collection and a session that
    then opens the project on an older build reach this with no bug here."""
    state = _fake_state(index=_index([(S2, "2026-04-24", 8.0, "W", "kA"),
                                      ("Sentinel 3", "2026-05-02", 4.0, "W", "kX"),
                                      (LS, "2026-08-27", 3.0, "W", "kB")]))
    with caplog.at_level(logging.WARNING, logger="SEPALUI.deforestation_alerts"):
        card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    assert _chips(card) == [("Planet 0", True), ("Sentinel-2 1", True), ("Landsat 1", True)]
    assert _count_stripes(card) == 2  # the two drawable rows
    dropped = [r for r in caplog.records if "no band for" in r.getMessage()]
    assert dropped and "Sentinel 3" in dropped[0].getMessage()


# ------------------------------------------------------ the saved pick (D1-D5)

_FOUR = [(S2, "2026-03-20", 5.0), (S2, "2026-04-24", 8.0), (LS, "2026-08-27", 3.0), (LS, "2026-10-01", 4.0)]


def test_a_saved_alert_reopens_on_its_saved_pair_without_unsaved_changes():
    analysis = _fake_analysis()
    timeline.build_timeline(_fake_state(index=_any_index(_FOUR)), analysis,
                            _fake_widgets(saved_before="Sentinel 2 2026-03-20 ",
                                          saved_after="Landsat 2026-10-01 "))
    assert analysis.before_info.value[3] == "Sentinel 2 2026-03-20 "
    assert analysis.after_info.value[3] == "Landsat 2026-10-01 "
    assert analysis.before_keys.value and analysis.after_keys.value
    assert analysis.dirty.value is False and analysis.edit_rev.value == 0


def test_a_side_with_no_saved_value_still_gets_the_automatic_pick():
    analysis = _fake_analysis()
    timeline.build_timeline(_fake_state(index=_any_index(_FOUR)), analysis,
                            _fake_widgets(saved_after="Landsat 2026-10-01 "))
    assert analysis.before_info.value[3] == "Sentinel 2 2026-04-24 "  # default_pick's nearest
    assert analysis.after_info.value[3] == "Landsat 2026-10-01 "


def test_a_legacy_or_empty_saved_value_opens_on_the_automatic_pick():
    """Review Focus #2: what the old selector wrote, or nothing at all."""
    for before, after in (("S2_before", "S2_after"), (float("nan"), None), ("", "MODIS 2026-04-24 ")):
        analysis = _fake_analysis()
        timeline.build_timeline(_fake_state(index=_any_index(_FOUR)), analysis,
                                _fake_widgets(saved_before=before, saved_after=after))
        assert analysis.before_info.value[3] == "Sentinel 2 2026-04-24 "
        assert analysis.after_info.value[3] == "Landsat 2026-08-27 "
        assert analysis.dirty.value is False and analysis.edit_rev.value == 0


def test_a_saved_image_over_the_cloud_limit_is_restored():
    rows = [(S2, "2026-03-20", 90.0), (S2, "2026-04-24", 8.0), (LS, "2026-08-27", 3.0)]
    analysis = _fake_analysis()
    card = timeline.build_timeline(_fake_state(index=_any_index(rows)), analysis,
                                   _fake_widgets(saved_before="Sentinel 2 2026-03-20 "))
    assert analysis.before_info.value[3] == "Sentinel 2 2026-03-20 "
    # drawn as the selection: full height and gold, not faded. The row's own
    # stripe, looked up by its label: the gold alert-window band also carries
    # "background: <gold>" and would satisfy a tree-wide search on its own.
    hit = [w for w in _walk(_lane(card))
           if (getattr(w, "attributes", None) or {}).get("aria-label", "").startswith(
               f"{timeline.SRC_LABEL[S2]}, 2026-03-20")]
    assert len(hit) == 1 and hit[0].attributes["aria-current"] == "true"
    bar = hit[0].children[0].style_
    assert f"background: {timeline.BEFORE_COLOR}" in bar and "opacity" not in bar


def test_a_saved_image_in_a_switched_off_collection_switches_it_back_on():
    state = _fake_state(index=_any_index(_FOUR))
    analysis = _fake_analysis()
    widgets = _fake_widgets()
    card = timeline.build_timeline(state, analysis, widgets)
    chip = next(w for w in _walk(card)
                if (getattr(w, "attributes", None) or {}).get("aria-pressed") == "true"
                and timeline.SRC_LABEL[LS] in _text(w))
    chip.fire_event("click", None)
    assert LS not in widgets.timeline_filter.value[0]

    # the next alert: another id, because ``_ctx``'s token 1 is also what the
    # fresh state's first ``reset_alert`` bumps to, so (7, 1) would read as
    # the alert already on screen
    analysis.reset_alert()
    nxt = dict(_ctx(), alert_id=8, saved_after="Landsat 2026-10-01 ", token=analysis.alert_token.value)
    widgets.imagery_ctx.set(nxt)
    assert analysis.after_info.value[3] == "Landsat 2026-10-01 "
    assert LS in widgets.timeline_filter.value[0]


def test_a_saved_date_outside_the_view_widens_the_view_and_asks_for_it():
    rows = [(S2, "2025-12-10", 5.0), (S2, "2026-04-24", 8.0), (LS, "2026-08-27", 3.0)]
    state = _fake_state(index=_any_index(rows))
    analysis = _fake_analysis()
    timeline.build_timeline(state, analysis, _fake_widgets(saved_before="Sentinel 2 2025-12-10 "))
    assert analysis.before_info.value[3] == "Sentinel 2 2025-12-10 "
    assert state.scene_window_request.value is not None
    assert state.scene_window_request.value[0] <= date(2025, 12, 10)


def test_extra_to_include_adds_just_enough_whole_months():
    project = _project()
    assert timeline.extra_to_include(_ctx(), project, (0, 0), [date(2025, 12, 10)]) == (3, 0)
    assert timeline.extra_to_include(_ctx(), project, (0, 0), [date(2027, 1, 2)]) == (0, 3)
    # never narrower than what the reviewer already widened
    assert timeline.extra_to_include(_ctx(), project, (5, 1), [date(2026, 5, 1)]) == (5, 1)
    assert timeline.extra_to_include(None, project, (0, 0), [date(2025, 1, 1)]) == (0, 0)


def test_a_saved_image_the_index_has_not_reached_waits_then_falls_back_with_a_note():
    rows = [(S2, "2026-04-24", 8.0), (LS, "2026-08-27", 3.0)]  # no 2026-03-20 row
    state = _fake_state(index=_any_index(rows))
    analysis = _fake_analysis()
    widgets = _fake_widgets(saved_before="Sentinel 2 2026-03-20 ")
    timeline.build_timeline(state, analysis, widgets)
    # not answered yet (no manifest): the before side waits, the after side is picked
    assert analysis.before_info.value is None
    assert analysis.after_info.value[3] == "Landsat 2026-08-27 "
    assert timeline.t("analysis.timeline.saved_missing") not in _text(widgets.map_title_before)

    state.scene_manifest.set(_covering())  # the index has answered for that date
    assert analysis.before_info.value[3] == "Sentinel 2 2026-04-24 "
    # in the before pane's own title: it is that pane's image the note is about
    assert timeline.t("analysis.timeline.saved_missing") in _text(widgets.map_title_before)
    assert timeline.t("analysis.timeline.saved_missing") not in _text(widgets.map_title_after)
    assert analysis.dirty.value is False and analysis.edit_rev.value == 0


def test_a_saved_image_the_index_had_not_reached_is_restored_once_it_arrives():
    """Spec D4's main path: the saved row is not in the index yet, so the side
    waits (no automatic pick stands in); once an index holding the row is
    published, the saved image is restored -- not an edit, and no note."""
    rows = [(S2, "2026-04-24", 8.0), (LS, "2026-08-27", 3.0), (S2, "2026-03-20", 5.0)]
    state = _fake_state(index=_any_index(rows[:2]))  # without the saved row
    analysis = _fake_analysis()
    card = timeline.build_timeline(state, analysis, _fake_widgets(saved_before="Sentinel 2 2026-03-20 "))
    assert analysis.before_info.value is None  # waiting for its row

    state.scene_index.set(_any_index(rows))  # the row arrives
    assert analysis.before_info.value[3] == "Sentinel 2 2026-03-20 "
    assert analysis.dirty.value is False and analysis.edit_rev.value == 0
    assert timeline.t("analysis.timeline.saved_missing") not in _text(card)

    state.scene_manifest.set(_covering())  # a later answer changes nothing
    assert analysis.before_info.value[3] == "Sentinel 2 2026-03-20 "
    assert timeline.t("analysis.timeline.saved_missing") not in _text(card)


def test_no_automatic_pick_to_stand_in_says_nothing_was_picked_automatically():
    """A saved image that is gone, on a side with no automatic candidate (no
    image before the alert window): nothing was picked automatically, so the
    readout must not say so."""
    rows = [(LS, "2026-08-27", 3.0), (LS, "2026-10-01", 4.0)]  # nothing before the alert
    state = _fake_state(index=_any_index(rows))
    state.scene_manifest.set(_covering())
    analysis = _fake_analysis()
    card = timeline.build_timeline(state, analysis, _fake_widgets(saved_before="Sentinel 2 2026-02-01 "))
    assert analysis.before_info.value is None
    assert timeline.t("analysis.timeline.saved_missing") not in _text(card)


def test_a_failed_index_refresh_also_settles_the_wait():
    rows = [(S2, "2026-04-24", 8.0), (LS, "2026-08-27", 3.0)]
    state = _fake_state(index=_any_index(rows))
    analysis = _fake_analysis()
    timeline.build_timeline(state, analysis, _fake_widgets(saved_before="Sentinel 2 2026-03-20 "))
    state.scene_index_error.set("boom")
    assert analysis.before_info.value[3] == "Sentinel 2 2026-04-24 "


def test_a_pick_made_while_the_saved_one_waits_wins():
    """Review Focus #1: the reviewer's click outranks a restore still pending."""
    rows = [(S2, "2026-04-24", 8.0), (LS, "2026-08-27", 3.0), (S2, "2026-03-20", 5.0)]
    state = _fake_state(index=_any_index(rows[:2]))
    analysis = _fake_analysis()
    widgets = _fake_widgets(saved_before="Sentinel 2 2026-03-20 ")
    card = timeline.build_timeline(state, analysis, widgets)
    _click(_lane(card), scene_index.row_id(S2, date(2026, 4, 24)))
    assert analysis.before_info.value[3] == "Sentinel 2 2026-04-24 "
    state.scene_index.set(_any_index(rows))  # the saved row arrives late
    state.scene_manifest.set(_covering())
    assert analysis.before_info.value[3] == "Sentinel 2 2026-04-24 "
    assert timeline.t("analysis.timeline.saved_missing") not in _text(widgets.map_title_before)


def test_the_timeline_publishes_its_filter():
    widgets = _fake_widgets()
    card = timeline.build_timeline(_fake_state(), _fake_analysis(), widgets)
    assert widgets.timeline_filter.value == (frozenset({S2, LS, PL}), 40.0)
    slider = next(w for w in _walk(card) if (getattr(w, "label", None) or "").startswith(timeline.t(
        "analysis.timeline.max_cloud")))
    slider.fire_event("change", 10)
    assert widgets.timeline_filter.value == (frozenset({S2, LS, PL}), 10.0)


def test_a_hand_pick_after_a_fallback_takes_the_note_away():
    """The note says the automatic pick stood in; once the reviewer has chosen
    that side themselves it no longer describes what is selected."""
    rows = [(S2, "2026-04-24", 8.0), (LS, "2026-08-27", 3.0), (S2, "2026-03-20", 5.0)]
    state = _fake_state(index=_any_index(rows))
    state.scene_manifest.set(_covering())
    analysis = _fake_analysis()
    widgets = _fake_widgets(saved_before="Sentinel 2 2026-02-01 ")
    card = timeline.build_timeline(state, analysis, widgets)
    assert timeline.t("analysis.timeline.saved_missing") in _text(widgets.map_title_before)
    _click(_lane(card), scene_index.row_id(S2, date(2026, 3, 20)))
    assert analysis.before_info.value[3] == "Sentinel 2 2026-03-20 "
    assert timeline.t("analysis.timeline.saved_missing") not in _text(widgets.map_title_before)


def test_the_alert_row_publishes_its_saved_pair_as_text_or_none():
    from gui.tile.analysis.analysis_tile import _saved_pick

    assert _saved_pick("Landsat 2026-10-01 ") == "Landsat 2026-10-01 "
    for missing in (None, float("nan"), "", "   "):
        assert _saved_pick(missing) is None


def test_every_colour_the_strip_draws_follows_the_theme():
    """The light theme showed the dark theme's chips, month blocks and
    greys: the strip's colours were literals. Every one is a variable now,
    defined once per theme on the stage, so no dark literal may come back
    in the module's drawing code."""
    import re
    from pathlib import Path

    css = timeline.THEME_CSS
    light, dark = css.split(".analysis-stage.analysis-stage--dark")
    names = set(re.findall(r"--dat-[a-z0-9-]+", light))
    assert names and names == set(re.findall(r"--dat-[a-z0-9-]+", dark))
    used = set(re.findall(r"var\((--dat-[a-z0-9-]+)", Path(timeline.__file__).read_text()))
    assert used <= names
    src = Path(timeline.__file__).read_text().split("THEME_CSS = (", 1)[1].split("\n)\n", 1)[1]
    for literal in ("#24221f", "#1e1c1a", "#44403a", "#f0ece6"):
        assert literal not in src
