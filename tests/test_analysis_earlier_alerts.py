"""The Analysis "Earlier alerts" section and map layer (spec 2026-10-20 §6.2–6.3)."""

import threading
import time
from datetime import date, datetime
from types import SimpleNamespace

import geopandas as gpd
import solara
from shapely.geometry import box

from deforestation_alerts.project.growth import Candidate, Node
from deforestation_alerts.project.models import ReviewCopy
from gui.scripts import growth_index
from gui.scripts.growth_index import EarlierItem, EarlierView
from gui.tile.analysis import earlier
from gui.tile.analysis.earlier import EARLIER_ID, EARLIER_LAYER_NAME
from gui.tile.analysis.state import AnalysisState
from tests._pane_helpers import make_panes

AUG = SimpleNamespace(id="2026-08-01_2026-08-31", start=date(2026, 8, 1), end=date(2026, 8, 31))
SEP = SimpleNamespace(id="2026-09-01_2026-09-30", start=date(2026, 9, 1), end=date(2026, 9, 30))
COLORS = growth_index.period_colors([SEP.id, AUG.id])
RUN = "20261002080000000000"
SHAPE = box(-55.0005, -25.0, -55.0, -24.9995)


def _cand(sid, aid, period, status="Confirmed"):
    node = Node(set_id=sid, alert_id=aid, period_id=period.id, period_start=period.start,
                period_end=period.end, status=status, area_ha=0.25, date_min=2026.6, date_max=2026.62,
                shape=SHAPE, links=())
    return Candidate(node, RUN)


SEP12 = _cand("sep", 12, SEP)
SEP15 = _cand("sep", 15, SEP, "Need revision")
AUG4 = _cand("aug", 4, AUG)
AUG7 = _cand("aug", 7, AUG)
GONE = "old#3@19990101000000000000"


def _view(links, auto=(SEP12.link,)):
    """What ``earlier_view`` would answer for the draft ``links``: #12 auto (or
    linked, or none), #15 within 1 km, #4 via #12 while #12 is linked, #7 broken
    when the draft names it."""
    links, auto = set(links), set(auto)

    def state(c):
        if c.link not in links:
            return "none"
        return "auto" if c.link in auto else "linked"

    items = [EarlierItem(SEP12, 0.0, state(SEP12), None, COLORS[SEP.id]),
             EarlierItem(SEP15, 40.0, state(SEP15), None, COLORS[SEP.id])]
    if SEP12.link in links:
        items.append(EarlierItem(AUG4, 50.0, "via", "sep#12", COLORS[AUG.id]))
    if AUG7.link in links:
        items.append(EarlierItem(AUG7, 10_000.0, "broken", None, COLORS[AUG.id]))
    return EarlierView(items=tuple(items), n_within=2 + (SEP12.link in links),
                       n_linked=sum(1 for it in items if it.state in ("auto", "linked")),
                       periods=((SEP.id, SEP.start), (AUG.id, AUG.start)))


def _setup(monkeypatch, *, project=None, view=_view):
    calls = []

    def fake_view(p, set_id, gdf, alert_id, links, auto, radius_m, **kw):
        calls.append({"set_id": set_id, "alert_id": alert_id, "links": tuple(links),
                      "auto": tuple(auto), "radius_m": radius_m, "shape": kw.get("shape")})
        return view(links, auto)

    monkeypatch.setattr(earlier.growth_index, "earlier_view", fake_view)
    monkeypatch.setattr(earlier.growth_index, "history_summary",
                        lambda p, set_id, gdf, alert_id, links, **kw: ("summary", tuple(links)))
    monkeypatch.setattr(earlier, "active_set", lambda p: SimpleNamespace(id="oct"))
    monkeypatch.setattr(earlier, "has_earlier_sets", lambda p: True)
    monkeypatch.setattr(earlier, "spawn_in_context", lambda target, args=(): target(*args))
    gdf = gpd.GeoDataFrame({"alert_id": [0, 1]}, geometry=[SHAPE, SHAPE], crs="EPSG:4326")
    p = project if project is not None else SimpleNamespace(review_copy=None)
    app_state = SimpleNamespace(alerts_gdf=solara.reactive(gdf), actual_alert_id=solara.reactive(1),
                                project=solara.reactive(p))
    widgets = SimpleNamespace(panes=make_panes())
    return widgets, AnalysisState(), app_state, calls


def _features(widgets):
    spec = widgets.panes.spec(EARLIER_ID)
    return None if spec is None else spec["data"]["features"]


def _by_key(features):
    return {f"{f['properties']['set_id']}#{f['properties']['alert_id']}": f for f in features}


def test_rows_grouped_by_period_with_states(monkeypatch):
    widgets, analysis, app_state, calls = _setup(monkeypatch)
    analysis.links.set((SEP12.link, AUG7.link))
    analysis.auto_links.set((SEP12.link,))
    section = earlier.build_earlier_controls(widgets, app_state, analysis)
    earlier.refresh_earlier(widgets, analysis, app_state)
    assert calls[-1]["alert_id"] == 1 and calls[-1]["set_id"] == "oct" and calls[-1]["radius_m"] == 100.0
    groups = [(g.period_id, [r.key for r in g.rows]) for g in section.groups]
    assert groups == [(SEP.id, ["sep#12", "sep#15"]), (AUG.id, ["aug#4", "aug#7"])]
    rows = {r.key: r for g in section.groups for r in g.rows}
    assert rows["sep#12"].box.v_model is True and not rows["sep#12"].box.disabled
    assert rows["sep#15"].box.v_model is False and not rows["sep#15"].box.disabled
    assert rows["aug#4"].box is None
    # broken by a re-run: shown, unticked, not to tick
    assert rows["aug#7"].box is not None and rows["aug#7"].box.disabled and rows["aug#7"].box.v_model is False
    assert rows["sep#12"].note.children == [earlier.t("analysis_tile.earlier.touches")]
    assert rows["sep#15"].note.children == [earlier.t("analysis_tile.earlier.distance_m", m="40")]
    assert rows["aug#4"].note.children == [earlier.t("analysis_tile.earlier.via", id="12")]
    assert rows["aug#7"].note.children == [earlier.t("analysis_tile.earlier.broken_rerun")]
    assert section.badge.children == [earlier.t("analysis_tile.earlier.badge_off")]  # the switch is off
    section.switch.v_model = True
    assert section.badge.children == [earlier.tn("analysis_tile.earlier.badge_nearby", 3, distance="100 m", linked=1)]


def test_distance_and_broken_notes():
    assert earlier.distance_text(40.4) == earlier.t("analysis_tile.earlier.distance_m", m="40")
    assert earlier.distance_text(1234.0) == earlier.t("analysis_tile.earlier.distance_km",
                                                      km=earlier.format_number(1.2, 1))
    fp = EarlierItem(_cand("sep", 9, SEP, "False Positive"), 0.0, "broken", None, COLORS[SEP.id])
    assert earlier.note_text(fp) == earlier.t("analysis_tile.earlier.broken_fp")


def test_an_unticked_automatic_link_says_so():
    item = EarlierItem(SEP12, 0.0, "none", None, COLORS[SEP.id], touches=True)
    assert earlier.note_text(item) == earlier.t("analysis_tile.earlier.touches_unlinked")
    assert earlier.t("analysis_tile.earlier.touches_unlinked") == "touches · not linked"
    assert earlier.note_text(EarlierItem(SEP15, 40.0, "none", None, COLORS[SEP.id])) == earlier.t(
        "analysis_tile.earlier.distance_m", m="40")


def test_tick_and_untick_update_draft_links(monkeypatch):
    widgets, analysis, app_state, calls = _setup(monkeypatch)
    section = earlier.build_earlier_controls(widgets, app_state, analysis)
    earlier.refresh_earlier(widgets, analysis, app_state)
    assert analysis.dirty.value is False
    rows = {r.key: r for g in section.groups for r in g.rows}
    rows["sep#15"].box.v_model = True
    assert analysis.links.value == (SEP15.link,) and analysis.dirty.value is True
    assert calls[-1]["links"] == (SEP15.link,)  # recomputed from the draft
    rows = {r.key: r for g in section.groups for r in g.rows}
    assert rows["sep#15"].box.v_model is True
    rows["sep#15"].box.v_model = False
    assert analysis.links.value == ()
    assert calls[-1]["links"] == ()


def test_row_click_zooms_both_maps(monkeypatch):
    widgets, analysis, app_state, _ = _setup(monkeypatch)
    section = earlier.build_earlier_controls(widgets, app_state, analysis)
    earlier.refresh_earlier(widgets, analysis, app_state)
    rows = {r.key: r for g in section.groups for r in g.rows}
    rows["sep#15"].text.fire_event("click", {})
    view = widgets.panes.widget.view
    c = SHAPE.centroid
    assert abs(view["center"][0] - c.y) < 1e-9 and abs(view["center"][1] - c.x) < 1e-9
    assert 14 <= view["zoom"] <= 18


def test_layer_styles_follow_period_and_status(monkeypatch):
    widgets, analysis, app_state, _ = _setup(monkeypatch)
    analysis.links.set((SEP12.link, AUG7.link))
    analysis.show_earlier_reviews.set(True)
    earlier.refresh_earlier(widgets, analysis, app_state)
    spec = widgets.panes.spec(EARLIER_ID)
    assert (spec["view"], spec["group"], spec["name"]) == ("all", "earlier", EARLIER_LAYER_NAME)
    assert spec["meta"] == {"menuSection": "alerts"}
    feats = _by_key(_features(widgets))
    assert set(feats) == {"sep#12", "sep#15", "aug#4"}  # broken aug#7 is listed, not drawn
    linked, revision, via = (feats[k]["properties"]["style"] for k in ("sep#12", "sep#15", "aug#4"))
    assert linked == {"color": COLORS[SEP.id], "weight": 4, "dashArray": None, "fill": True, "fillOpacity": 0.2}
    assert revision == {"color": COLORS[SEP.id], "weight": 2, "dashArray": "6 4", "fill": False,
                        "fillOpacity": 0.2}
    assert via["color"] == COLORS[AUG.id] and via["weight"] == 2 and via["fill"] is False
    assert feats["sep#12"]["geometry"]["type"] == "Polygon"


def test_switch_off_removes_the_layer(monkeypatch):
    widgets, analysis, app_state, _ = _setup(monkeypatch)
    analysis.show_earlier_reviews.set(True)
    earlier.refresh_earlier(widgets, analysis, app_state)
    assert _features(widgets)
    analysis.show_earlier_reviews.set(False)
    earlier.refresh_earlier(widgets, analysis, app_state)
    assert _features(widgets) is None


def _open(analysis, links):
    """What ``view_actual_alert`` does for an alert storing ``links``."""
    analysis.reset_alert()
    analysis.links.set(tuple(links))
    earlier.seed_switch(analysis, bool(links))


def test_switch_turns_on_for_growth_alert_only(monkeypatch):
    """Spec §6.3: a growth alert opens with its parents on the maps; any other
    alert keeps the user's last choice."""
    widgets, analysis, app_state, calls = _setup(monkeypatch)
    section = earlier.build_earlier_controls(widgets, app_state, analysis)
    assert section.switch.v_model is False
    _open(analysis, (SEP12.link,))  # a growth alert, its link manual (no auto links)
    assert section.switch.v_model is True and analysis.show_earlier_reviews.value is True
    _open(analysis, ())  # the next New alert: back to the user's choice, off
    assert section.switch.v_model is False and analysis.show_earlier_reviews.value is False
    assert calls == []  # the seeded switch is not a refresh of its own
    section.switch.v_model = True  # the user's own choice
    _open(analysis, ())
    assert section.switch.v_model is True
    _open(analysis, (SEP12.link,))
    section.switch.v_model = False  # a manual flip on a growth alert is the user's choice too
    _open(analysis, ())
    assert section.switch.v_model is False and analysis.show_earlier_reviews.value is False
    _open(analysis, (SEP12.link,))
    assert section.switch.v_model is True


def test_periods_menu_hides_rows_and_features(monkeypatch):
    widgets, analysis, app_state, _ = _setup(monkeypatch)
    analysis.links.set((SEP12.link,))
    analysis.show_earlier_reviews.set(True)
    section = earlier.build_earlier_controls(widgets, app_state, analysis)
    earlier.refresh_earlier(widgets, analysis, app_state)
    assert [i["value"] for i in section.periods.items] == [SEP.id, AUG.id]
    assert section.periods.v_model == [SEP.id, AUG.id]  # all on by default
    section.periods.v_model = [SEP.id]
    assert analysis.earlier_hidden_periods.value == frozenset({AUG.id})
    assert [g.period_id for g in section.groups] == [SEP.id]
    assert set(_by_key(_features(widgets))) == {"sep#12", "sep#15"}
    # the choice survives the next alert (session state)
    analysis.reset_alert()
    analysis.links.set((SEP12.link,))
    earlier.refresh_earlier(widgets, analysis, app_state)
    assert [g.period_id for g in section.groups] == [SEP.id]
    assert section.periods.v_model == [SEP.id]
    section.periods.v_model = [SEP.id, AUG.id]
    assert analysis.earlier_hidden_periods.value == frozenset()
    assert "aug#4" in _by_key(_features(widgets))


def test_refresh_sets_history_and_parent_windows_with_switch_off(monkeypatch):
    widgets, analysis, app_state, _ = _setup(monkeypatch)
    analysis.links.set((SEP12.link,))
    assert analysis.show_earlier_reviews.value is False
    earlier.refresh_earlier(widgets, analysis, app_state)
    assert analysis.history.value == ("summary", (SEP12.link,))
    assert analysis.parent_windows.value == ((2026.6, 2026.62),)
    assert _features(widgets) is None


def test_broken_link_dropped_from_draft(monkeypatch):
    widgets, analysis, app_state, _ = _setup(monkeypatch)
    analysis.links.set((SEP12.link, AUG7.link, GONE))
    section = earlier.build_earlier_controls(widgets, app_state, analysis)
    earlier.refresh_earlier(widgets, analysis, app_state)
    assert analysis.links.value == (SEP12.link,)
    assert analysis.dirty.value is False
    rows = {r.key: r for g in section.groups for r in g.rows}
    assert rows["aug#7"].box.disabled and rows["aug#7"].box.v_model is False  # still shown, greyed


def test_link_to_a_since_false_positive_parent_stays(monkeypatch):
    fp = _cand("sep", 9, SEP, "False Positive")

    def view(links, auto):
        return EarlierView((EarlierItem(fp, 0.0, "broken", None, COLORS[SEP.id]),), 0, 0, (SEP.id,))

    widgets, analysis, app_state, _ = _setup(monkeypatch, view=view)
    analysis.links.set((fp.link,))
    earlier.refresh_earlier(widgets, analysis, app_state)
    assert analysis.links.value == (fp.link,)
    assert analysis.dirty.value is False


def test_a_link_to_a_since_false_positive_parent_can_be_unticked(monkeypatch):
    """Ruling 13 keeps the link stored: the reviewer clears it by hand."""
    fp = _cand("sep", 9, SEP, "False Positive")

    def view(links, auto):
        items = (EarlierItem(fp, 0.0, "broken", None, COLORS[SEP.id]),) if fp.link in links else ()
        return EarlierView(items, 0, 0, ((SEP.id, SEP.start),))

    widgets, analysis, app_state, calls = _setup(monkeypatch, view=view)
    analysis.links.set((fp.link,))
    section = earlier.build_earlier_controls(widgets, app_state, analysis)
    earlier.refresh_earlier(widgets, analysis, app_state)
    [row] = [r for g in section.groups for r in g.rows]
    assert row.box.v_model is True and not row.box.disabled
    assert row.note.children == [earlier.t("analysis_tile.earlier.broken_fp")]
    row.box.v_model = False
    assert analysis.links.value == () and analysis.dirty.value is True
    assert calls[-1]["links"] == ()


def test_a_copy_without_snapshot_keeps_links_it_cannot_resolve(monkeypatch):
    rc = ReviewCopy(evaluator="a", evaluator_index=1, set_id="oct", split_id="x", set_fingerprint="f",
                    origin_project="o", imported_at=datetime(2026, 10, 1))
    p = SimpleNamespace(review_copy=rc)
    widgets, analysis, app_state, _ = _setup(monkeypatch, project=p)
    analysis.links.set((SEP12.link, GONE))
    earlier.refresh_earlier(widgets, analysis, app_state)
    assert analysis.links.value == (SEP12.link, GONE)


def test_review_copy_lines(monkeypatch):
    rc = ReviewCopy(evaluator="a", evaluator_index=1, set_id="oct", split_id="x", set_fingerprint="f",
                    origin_project="o", imported_at=datetime(2026, 10, 1),
                    snapshot_at=datetime(2026, 10, 5, 9, 0))
    p = SimpleNamespace(review_copy=rc)
    empty = lambda links, auto: EarlierView((), 0, 0, ())  # noqa: E731
    widgets, analysis, app_state, _ = _setup(monkeypatch, project=p, view=empty)
    section = earlier.build_earlier_controls(widgets, app_state, analysis)
    earlier.refresh_earlier(widgets, analysis, app_state)
    texts = [w.children[0] for w in section.copy_lines.children]
    assert texts == [earlier.t("analysis_tile.earlier.as_of", date=earlier.format_date(date(2026, 10, 5))),
                     earlier.t("analysis_tile.earlier.none_in_copy")]
    assert ReviewCopy(**{**rc.model_dump(), "snapshot_at": None}).snapshot_at is None


def test_old_review_copy_has_no_snapshot_field_value():
    rc = ReviewCopy.model_validate({"evaluator": "a", "evaluator_index": 1, "set_id": "s", "split_id": "x",
                                    "set_fingerprint": "f", "origin_project": "o",
                                    "imported_at": "2026-10-01T00:00:00"})
    assert rc.snapshot_at is None


def test_a_stale_result_is_dropped(monkeypatch):
    widgets, analysis, app_state, _ = _setup(monkeypatch)
    analysis.show_earlier_reviews.set(True)

    def slow(p, sid, gdf, aid, links, auto, radius_m, **kw):
        analysis.reset_alert()  # the alert changes while the read blocks
        return _view(links, auto)

    monkeypatch.setattr(earlier.growth_index, "earlier_view", slow)
    earlier.refresh_earlier(widgets, analysis, app_state)
    assert _features(widgets) is None
    assert analysis.history.value is None


def test_a_move_after_the_token_is_read_does_not_drop_the_new_alerts_links(monkeypatch):
    """The token is read with the links, before the position: a move made
    between those reads leaves this refresh stale; the new alert's draft (here
    with a link the fake view calls broken) is not trimmed by it."""
    widgets, analysis, app_state, _ = _setup(monkeypatch)
    moved = []

    class _MovesOnRead:
        @property
        def value(self):
            if not moved:  # A -> B, as ``view_actual_alert`` does it
                moved.append(True)
                analysis.reset_alert()
                analysis.links.set((SEP12.link, AUG7.link))
            return 1

    app_state.actual_alert_id = _MovesOnRead()
    earlier.refresh_earlier(widgets, analysis, app_state)
    assert moved
    assert analysis.links.value == (SEP12.link, AUG7.link)
    assert analysis.history.value is None


def test_an_older_load_does_not_overwrite_a_newer_one(monkeypatch):
    widgets, analysis, app_state, _ = _setup(monkeypatch)
    gates, radii = [], []

    def blocking(p, sid, gdf, aid, links, auto, radius_m, **kw):
        gate = threading.Event()
        gates.append(gate)
        radii.append(radius_m)
        gate.wait(5)
        return _view(links, auto) if radius_m > 1000 else EarlierView((), 0, 0, ())

    monkeypatch.setattr(earlier.growth_index, "earlier_view", blocking)
    analysis.show_earlier_reviews.set(True)
    old = threading.Thread(target=earlier.refresh_earlier, args=(widgets, analysis, app_state))
    old.start()
    _wait_for(lambda: len(gates) == 1)
    analysis.earlier_radius_m.set(5000.0)
    new = threading.Thread(target=earlier.refresh_earlier, args=(widgets, analysis, app_state))
    new.start()
    _wait_for(lambda: len(gates) == 2)
    gates[1].set()
    new.join(5)
    gates[0].set()
    old.join(5)
    assert len(_features(widgets)) == 2


def _wait_for(cond):
    for _ in range(500):
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def test_a_raising_read_is_logged_and_leaves_the_panel_usable(monkeypatch, caplog):
    widgets, analysis, app_state, _ = _setup(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("store unreadable")

    monkeypatch.setattr(earlier.growth_index, "earlier_view", boom)
    section = earlier.build_earlier_controls(widgets, app_state, analysis)
    with caplog.at_level("ERROR", logger="SEPALUI.deforestation_alerts"):
        section.switch.v_model = True
    assert any("earlier-alerts" in r.message and r.exc_info for r in caplog.records)
    assert _features(widgets) is None


def test_refresh_in_background_always_loads_off_the_calling_thread(monkeypatch):
    widgets, analysis, app_state, calls = _setup(monkeypatch)
    spawned = []
    monkeypatch.setattr(earlier, "spawn_in_context", lambda fn, args=(): spawned.append((fn, args)))
    earlier.refresh_in_background(widgets, analysis, app_state)
    assert calls == [] and len(spawned) == 1


def test_bad_distance_shows_the_error_and_keeps_the_last_value(monkeypatch):
    widgets, analysis, app_state, _ = _setup(monkeypatch)
    controls = earlier.build_earlier_controls(widgets, app_state, analysis)
    field = controls.field
    assert field.v_model == "100" and field.suffix == "m"
    for bad in ("0", "9.5", "abc", "50001", "inf"):
        field.v_model = bad
        assert field.error_messages and analysis.earlier_radius_m.value == 100.0
    field.v_model = "2500,5"
    assert not field.error_messages and analysis.earlier_radius_m.value == 2500.5


def test_the_section_badge():
    view = _view((SEP12.link,))
    assert earlier.badge_text(False, view, 100.0) == earlier.t("analysis_tile.earlier.badge_off")
    assert earlier.badge_text(True, None, 100.0) == ""
    empty = EarlierView((), 0, 0, ())
    assert earlier.badge_text(True, empty, 2500.0) == "none within 2.5 km"
    assert earlier.badge_text(True, view, 100.0) == "3 within 100 m · 1 linked"
    assert earlier.t("analysis_tile.earlier.title") == "Earlier alerts"


def test_has_earlier_sets(monkeypatch):
    periods = {"p1": SimpleNamespace(start=1), "p2": SimpleNamespace(start=2)}
    s1, s2 = SimpleNamespace(id="s1", period_id="p1"), SimpleNamespace(id="s2", period_id="p2")
    only_one = SimpleNamespace(periods=periods, alert_sets={"s2": s2}, review_copy=None)
    both = SimpleNamespace(periods=periods, alert_sets={"s1": s1, "s2": s2}, review_copy=None)
    monkeypatch.setattr(earlier, "active_set", lambda p: s2)
    assert not earlier.has_earlier_sets(only_one)
    assert earlier.has_earlier_sets(both)
    # a review copy holds one set: its earlier alerts come with the package
    copy = SimpleNamespace(periods=periods, alert_sets={"s2": s2}, review_copy=object())
    assert earlier.has_earlier_sets(copy)
    monkeypatch.setattr(earlier, "active_set", lambda p: s1)
    assert not earlier.has_earlier_sets(both)
    assert not earlier.has_earlier_sets(None)


def test_the_section_is_hidden_without_earlier_sets_and_switches_off(monkeypatch):
    widgets, analysis, app_state, _ = _setup(monkeypatch)
    has = {"v": True}
    monkeypatch.setattr(earlier, "has_earlier_sets", lambda p: has["v"])
    section = earlier.build_earlier_controls(widgets, app_state, analysis)
    assert "display: none" not in section.root.style_
    section.switch.v_model = True
    assert _features(widgets)
    has["v"] = False
    app_state.project.set(SimpleNamespace(review_copy=None, name="other"))
    assert "display: none" in section.root.style_
    assert section.switch.v_model is False and not analysis.show_earlier_reviews.value
    assert _features(widgets) is None
    assert analysis.earlier_choice.value is True  # not the reviewer's flip: their choice stays
    earlier.seed_switch(analysis, False)  # the next New alert in a project with earlier sets
    assert analysis.show_earlier_reviews.value is True


# ---- group review (spec 2026-10-26 §7) ------------------------------------------

def test_a_grouped_alert_measures_from_its_group(monkeypatch):
    import pandas as pd
    import shapely

    widgets, analysis, app_state, calls = _setup(monkeypatch)
    a, b, c = box(-55.0, -25.0, -54.999, -24.999), box(-54.99, -25.0, -54.989, -24.999), box(-54.9, -25.0, -54.899, -24.999)
    gdf = gpd.GeoDataFrame({"alert_id": [0, 1, 2], "review_group": pd.array([0, 0, pd.NA], dtype="Int64")},
                           geometry=[a, b, c], crs="EPSG:4326")
    app_state.alerts_gdf.set(gdf)
    on = {"value": None}
    monkeypatch.setattr(earlier, "grouping_rules", SimpleNamespace(effective=lambda p, s: on["value"]))
    earlier.refresh_earlier(widgets, analysis, app_state)
    assert calls[-1]["shape"] is None  # grouping off: the row's own
    on["value"] = object()
    earlier.refresh_earlier(widgets, analysis, app_state)
    assert calls[-1]["shape"].equals(shapely.union_all([a, b]))  # row 1 is grouped
    app_state.actual_alert_id.set(2)
    earlier.refresh_earlier(widgets, analysis, app_state)
    assert calls[-1]["shape"] is None  # alone, nothing selected
    analysis.selection.set(frozenset({0}))
    earlier.refresh_earlier(widgets, analysis, app_state)
    assert calls[-1]["shape"].equals(shapely.union_all([c, a]))


def test_unticking_a_groups_main_drops_its_member_links(monkeypatch):
    member = "sep#16@" + RUN

    def view(links, auto):
        item = EarlierItem(SEP12, 0.0, "linked", None, COLORS[SEP.id], links=(member, SEP12.link))
        return EarlierView(items=(item,), n_within=1, n_linked=1, periods=((SEP.id, SEP.start),))

    widgets, analysis, app_state, calls = _setup(monkeypatch, view=view)
    analysis.links.set((member, SEP12.link))
    section = earlier.build_earlier_controls(widgets, app_state, analysis)
    earlier.refresh_earlier(widgets, analysis, app_state)
    assert analysis.links.value == (member, SEP12.link)  # the member link is kept
    rows = {r.key: r for g in section.groups for r in g.rows}
    rows["sep#12"].box.v_model = False
    assert analysis.links.value == ()


# ---- unlinking an alert Integrate kept only for its link -------------------------
def _small_setup(monkeypatch, *, below_min=True):
    widgets, analysis, app_state, calls = _setup(monkeypatch)
    monkeypatch.setattr(earlier, "active_set",
                        lambda p: SimpleNamespace(id="oct", params=SimpleNamespace(min_size_ha=0.5)))
    gdf = gpd.GeoDataFrame({"alert_id": [0, 1], "area_ha": [1.0, 0.3], "below_min": [False, below_min]},
                           geometry=[SHAPE, SHAPE], crs="EPSG:4326")
    app_state.alerts_gdf.set(gdf)
    asked = []
    section = earlier.build_earlier_controls(widgets, app_state, analysis,
                                             ask=lambda lines, on_ok, **kw: asked.append((lines, on_ok, kw)))
    analysis.links.set((SEP12.link,))
    analysis.auto_links.set((SEP12.link,))
    earlier.refresh_earlier(widgets, analysis, app_state)
    return SimpleNamespace(section=section, analysis=analysis, app_state=app_state, asked=asked, calls=calls)


def _rows(section):
    return {r.key: r for g in section.groups for r in g.rows}


def test_unlinking_the_last_link_of_a_small_alert_asks_first(monkeypatch):
    h = _small_setup(monkeypatch)
    _rows(h.section)["sep#12"].box.v_model = False
    assert h.analysis.links.value == (SEP12.link,)  # nothing unlinked yet
    assert _rows(h.section)["sep#12"].box.v_model is True
    [(lines, on_ok, kw)] = h.asked
    assert lines == ["Alert #2 is 0.30 ha, under the 0.50 ha minimum. It is an alert only because it is "
                     "linked to #12.", "Without the link it stays in the set as New · under minimum area."]
    assert kw == {"title": "analysis_tile.earlier.unlink_title", "ok": "analysis_tile.earlier.unlink_ok"}
    on_ok()
    assert h.analysis.links.value == () and h.calls[-1]["links"] == ()
    assert _rows(h.section)["sep#12"].box.v_model is False


def test_a_cancelled_unlink_keeps_the_link(monkeypatch):
    h = _small_setup(monkeypatch)
    _rows(h.section)["sep#12"].box.v_model = False
    assert len(h.asked) == 1  # Cancel only closes the dialog: on_ok never runs
    assert h.analysis.links.value == (SEP12.link,) and h.analysis.dirty.value is False


def test_an_unlink_confirmed_after_the_alert_changed_does_nothing(monkeypatch):
    h = _small_setup(monkeypatch)
    _rows(h.section)["sep#12"].box.v_model = False
    h.analysis.reset_alert()  # another alert opened (a new token), linked to #12 too
    h.analysis.links.set((SEP12.link,))
    h.asked[0][1]()
    assert h.analysis.links.value == (SEP12.link,)


def test_an_alert_over_the_minimum_unlinks_at_once(monkeypatch):
    h = _small_setup(monkeypatch, below_min=False)
    _rows(h.section)["sep#12"].box.v_model = False
    assert h.asked == [] and h.analysis.links.value == ()


def test_unlinking_one_of_two_links_does_not_ask(monkeypatch):
    h = _small_setup(monkeypatch)
    _rows(h.section)["sep#15"].box.v_model = True
    _rows(h.section)["sep#12"].box.v_model = False
    assert h.asked == [] and h.analysis.links.value == (SEP15.link,)
