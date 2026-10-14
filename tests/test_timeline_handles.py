"""The one-lane timeline's two handles: where they point, what they may reach,
which handle a click on a stripe moves, and the widget that carries them.

The arithmetic is pure and tested here directly; the drag itself runs in the
browser and reaches the kernel as one ``pick`` event, which the widget tests
fire by hand.
"""

from datetime import date

import pytest

from deforestation_alerts.scripts import scene_index, scene_query
from gui.widget.handle_strip import HandleStrip
from gui.tile.analysis import timeline
from tests.test_timeline import (
    S2, LS, PL, _click, _fake_analysis, _fake_state, _fake_widgets, _sel, _text, _walk,
)

VIEW = (date(2026, 3, 1), date(2026, 6, 30))
#: the alert window the click rule reads its "between the handles" case from
A0 = date(2026, 4, 15)


def _segs(collapsed=()):
    return scene_query.month_segments(VIEW[0], VIEW[1], set(collapsed), timeline.TRACK_PX)


def _rid(src, iso):
    return scene_index.row_id(src, date.fromisoformat(iso))


ROWS = [(S2, "2026-03-05", 5.0), (LS, "2026-03-05", 5.0), (S2, "2026-03-20", 5.0),
        (S2, "2026-04-10", 95.0), (S2, "2026-04-20", 5.0), (LS, "2026-05-02", 5.0),
        (S2, "2026-05-25", 5.0), (PL, "2026-06-01", float("nan"))]


def _view_sel():
    return _sel(ROWS, start=VIEW[0], end=VIEW[1])


# ------------------------------------------------------------ handle_x

def test_a_handle_points_at_a_stripes_own_date():
    segs = _segs()
    assert timeline.handle_x(segs, _rid(S2, "2026-03-20")) == pytest.approx(
        scene_query.x_for(segs, date(2026, 3, 20)))


def test_a_handle_on_a_planet_month_points_at_the_middle_of_its_bar():
    segs = _segs()
    left = scene_query.x_for(segs, date(2026, 6, 1))
    right = scene_query.x_for(segs, date(2026, 6, 30), end=True)
    assert timeline.handle_x(segs, _rid(PL, "2026-06-01")) == pytest.approx((left + right) / 2)


def test_a_handle_outside_the_laid_out_months_has_no_position():
    assert timeline.handle_x(_segs(), _rid(S2, "2026-09-01")) is None
    assert timeline.handle_x(_segs(), None) is None


# ------------------------------------------------------------ reachable rows

def test_the_before_handle_reaches_only_rows_left_of_the_after_handle():
    sel, segs = _view_sel(), _segs()
    got = timeline.reachable(sel, segs, "b", before_id=None, after_id=_rid(S2, "2026-04-20"))
    assert got == [_rid(S2, "2026-03-05"), _rid(LS, "2026-03-05"), _rid(S2, "2026-03-20")]


def test_the_after_handle_reaches_only_rows_right_of_the_before_handle():
    sel, segs = _view_sel(), _segs()
    got = timeline.reachable(sel, segs, "a", before_id=_rid(S2, "2026-04-20"), after_id=None)
    assert got == [_rid(LS, "2026-05-02"), _rid(S2, "2026-05-25"), _rid(PL, "2026-06-01")]


def test_a_handle_never_reaches_a_row_the_cloud_limit_hides():
    got = timeline.reachable(_view_sel(), _segs(), "b", before_id=None, after_id=None)
    assert _rid(S2, "2026-04-10") not in got


def test_a_handle_never_reaches_a_row_inside_a_collapsed_month():
    got = timeline.reachable(_view_sel(), _segs(collapsed=[(2026, 3)]), "b", before_id=None, after_id=None)
    assert not [rid for rid in got if rid[1:7] == "202603"]


def test_on_one_date_sentinel2_comes_before_landsat():
    """The drag snaps to the first of two equally near rows, so the order is
    the same-day preference: Sentinel-2 first, though "Landsat" sorts first
    as a string."""
    got = timeline.reachable(_view_sel(), _segs(), "b", before_id=None, after_id=None)
    assert got.index(_rid(S2, "2026-03-05")) < got.index(_rid(LS, "2026-03-05"))


# ------------------------------------------------------------ which handle a click moves

@pytest.mark.parametrize("clicked, side", [
    (_rid(S2, "2026-03-05"), "b"),   # left of the before handle
    (_rid(S2, "2026-05-25"), "a"),   # right of the after handle
    (_rid(S2, "2026-04-10"), "b"),   # between the handles, before the alert start
    (_rid(S2, "2026-04-20"), "a"),   # between the handles, on or after the alert start
])
def test_a_click_moves_the_handle_the_rule_names(clicked, side):
    before, after = _rid(S2, "2026-03-20"), _rid(LS, "2026-05-02")
    assert timeline.side_for_click(_segs(), clicked, before, after, A0) == side


def test_a_click_on_the_alert_start_itself_moves_the_after_handle():
    before, after = _rid(S2, "2026-03-20"), _rid(S2, "2026-05-25")
    assert timeline.side_for_click(_segs(), _rid(S2, "2026-04-15"), before, after, A0) == "a"


def test_a_click_on_the_other_collection_of_a_selected_day_switches_that_handles_source():
    before, after = _rid(S2, "2026-03-05"), _rid(S2, "2026-05-25")
    assert timeline.side_for_click(_segs(), _rid(LS, "2026-03-05"), before, after, A0) == "b"
    # and the same on the after side, though the day is left of the alert start
    before, after = _rid(S2, "2026-02-01"), _rid(S2, "2026-03-05")
    assert timeline.side_for_click(_segs(), _rid(LS, "2026-03-05"), before, after, A0) == "a"


def test_a_click_on_a_selected_stripe_moves_nothing():
    before, after = _rid(S2, "2026-03-20"), _rid(LS, "2026-05-02")
    assert timeline.side_for_click(_segs(), before, before, after, A0) is None
    assert timeline.side_for_click(_segs(), after, before, after, A0) is None


def test_a_click_with_one_side_unset_still_picks_a_side():
    after = _rid(LS, "2026-05-02")
    assert timeline.side_for_click(_segs(), _rid(S2, "2026-03-20"), None, after, A0) == "b"
    assert timeline.side_for_click(_segs(), _rid(S2, "2026-05-25"), None, after, A0) == "a"
    assert timeline.side_for_click(_segs(), _rid(S2, "2026-03-20"), None, None, A0) == "b"
    assert timeline.side_for_click(_segs(), _rid(S2, "2026-04-20"), None, None, A0) == "a"


def test_without_an_alert_start_a_click_between_the_handles_moves_the_nearer_one():
    before, after = _rid(S2, "2026-03-05"), _rid(S2, "2026-05-25")
    assert timeline.side_for_click(_segs(), _rid(S2, "2026-03-20"), before, after, None) == "b"
    assert timeline.side_for_click(_segs(), _rid(LS, "2026-05-02"), before, after, None) == "a"


# ------------------------------------------------------------ the same-day alternative

def test_a_selected_day_names_its_other_collection():
    sel = _view_sel()
    assert timeline.same_day_alternative(sel, _rid(S2, "2026-03-05")) == _rid(LS, "2026-03-05")
    assert timeline.same_day_alternative(sel, _rid(LS, "2026-03-05")) == _rid(S2, "2026-03-05")


def test_a_day_with_one_collection_has_no_alternative():
    sel = _view_sel()
    assert timeline.same_day_alternative(sel, _rid(S2, "2026-03-20")) is None
    assert timeline.same_day_alternative(sel, None) is None


def test_a_hidden_row_and_a_planet_month_are_never_the_alternative():
    sel = _sel([(S2, "2026-03-05", 5.0), (LS, "2026-03-05", 95.0),
                (PL, "2026-03-01", float("nan")), (S2, "2026-03-01", 5.0)],
               start=VIEW[0], end=VIEW[1])
    assert timeline.same_day_alternative(sel, _rid(S2, "2026-03-05")) is None
    assert timeline.same_day_alternative(sel, _rid(S2, "2026-03-01")) is None


# ------------------------------------------------------------ the widget

def _strip(card):
    return next(w for w in _walk(card) if isinstance(w, HandleStrip))


def _pick(strip, side, rid):
    strip._handle_event(None, {"event": "pick", "data": {"side": side, "id": rid}}, [])


def _state():
    return _fake_state(rows=[(S2, "2026-04-05", 5.0), (S2, "2026-04-12", 5.0), (LS, "2026-04-12", 5.0),
                             (S2, "2026-04-24", 5.0), (LS, "2026-08-27", 3.0), (S2, "2026-09-10", 3.0)])


def test_the_strip_carries_both_handles_and_every_reachable_row():
    card = timeline.build_timeline(_state(), _fake_analysis(), _fake_widgets())
    strip = _strip(card)
    assert strip.before["id"] == _rid(S2, "2026-04-24")
    assert strip.after["id"] == _rid(LS, "2026-08-27")
    ids = [c["id"] for c in strip.candidates]
    assert ids[:3] == [_rid(S2, "2026-04-05"), _rid(S2, "2026-04-12"), _rid(LS, "2026-04-12")]
    assert all(0 <= c["x"] <= 100 for c in strip.candidates)
    # the gap the bracket shows is the pair's own
    assert strip.gap == timeline.t("analysis.timeline.days_apart", days=125)


def test_a_drop_on_the_strip_assigns_that_side():
    analysis = _fake_analysis()
    card = timeline.build_timeline(_state(), analysis, _fake_widgets())
    _pick(_strip(card), "b", _rid(S2, "2026-04-05"))
    assert analysis.before_info.value[3] == "Sentinel 2 2026-04-05 "
    assert analysis.after_info.value[3] == "Landsat 2026-08-27 "
    assert _strip(card).before["id"] == _rid(S2, "2026-04-05")


def test_a_drop_past_the_other_handle_is_refused():
    analysis = _fake_analysis()
    card = timeline.build_timeline(_state(), analysis, _fake_widgets())
    _pick(_strip(card), "b", _rid(S2, "2026-09-10"))
    assert analysis.before_info.value[3] == "Sentinel 2 2026-04-24 "


def test_a_malformed_drop_is_ignored():
    analysis = _fake_analysis()
    card = timeline.build_timeline(_state(), analysis, _fake_widgets())
    before = analysis.before_info.value
    for data in (None, {}, {"side": "x", "id": _rid(S2, "2026-04-05")}, {"side": "b", "id": 7}):
        _strip(card)._handle_event(None, {"event": "pick", "data": data}, [])
    assert analysis.before_info.value is before


def test_one_lane_and_a_click_moves_the_handle_the_rule_names():
    analysis = _fake_analysis()
    card = timeline.build_timeline(_state(), analysis, _fake_widgets())
    # left of the before handle: before
    _click(card, _rid(S2, "2026-04-05"))
    assert analysis.before_info.value[3] == "Sentinel 2 2026-04-05 "
    # right of the after handle: after
    _click(card, _rid(S2, "2026-09-10"))
    assert analysis.after_info.value[3] == "Sentinel 2 2026-09-10 "


def test_clicking_the_other_collection_of_the_selected_day_switches_source():
    analysis = _fake_analysis()
    card = timeline.build_timeline(_state(), analysis, _fake_widgets())
    _click_row(card, S2, "2026-04-12")
    assert analysis.before_info.value[0] == S2
    _click_row(card, LS, "2026-04-12")
    assert analysis.before_info.value[0] == LS
    assert analysis.before_info.value[3] == "Landsat 2026-04-12 "


def test_the_map_titles_name_the_selected_images():
    widgets = _fake_widgets()
    timeline.build_timeline(_state(), _fake_analysis(), widgets)
    assert "Sentinel-2 2026-04-24" in _text(widgets.map_title_before)
    assert "Landsat 2026-08-27" in _text(widgets.map_title_after)


def test_a_title_offers_the_other_collection_of_the_same_day():
    analysis = _fake_analysis()
    widgets = _fake_widgets()
    card = timeline.build_timeline(_state(), analysis, widgets)
    assert not _swap(widgets.map_title_before)
    _pick(_strip(card), "b", _rid(S2, "2026-04-12"))
    swap = _swap(widgets.map_title_before)
    assert len(swap) == 1 and "Landsat" in _text(swap[0])
    swap[0].fire_event("click", None)
    assert analysis.before_info.value[3] == "Landsat 2026-04-12 "
    # and now it offers the way back
    assert "Sentinel-2" in _text(_swap(widgets.map_title_before)[0])


def test_the_cloud_limit_value_is_always_on_screen():
    card = timeline.build_timeline(_state(), _fake_analysis(), _fake_widgets())
    slider = next(w for w in _walk(card)
                  if (getattr(w, "label", None) or "").startswith(timeline.t("analysis.timeline.max_cloud")))
    assert slider.label.endswith(" 40%")
    slider.fire_event("change", 25)
    assert slider.label.endswith(" 25%")


def _click_row(card, src, iso):
    """Click the one stripe of ``src`` on ``iso``: ``_click`` matches by date
    only, and two collections on one day are two stripes in one lane now."""
    head = f"{timeline.SRC_LABEL[src]}, {iso}"
    hits = [w for w in _walk(card)
            if (getattr(w, "attributes", None) or {}).get("aria-label", "").startswith(head)
            and "click" in (getattr(w, "_events", None) or [])]
    assert len(hits) == 1, f"{len(hits)} clickable stripes for {head}"
    hits[0].fire_event("click", None)


def _swap(title):
    return [w for w in _walk(title)
            if getattr(w, "tag", None) == "button" and "click" in (getattr(w, "_events", None) or [])]
