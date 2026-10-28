"""The Analysis *Nearby alerts* section (spec 2026-10-26 group review §6.1-§6.3):
the list and its checkboxes, the badge, the outlines on the views and the map
click that ticks a row."""

from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pytest
import solara
from shapely.geometry import box

from deforestation_alerts.project import review_groups as rg
from gui.scripts import set_store
from gui.tile.analysis import nearby
from gui.tile.analysis.context import status_color
from gui.tile.analysis.state import AnalysisState
from tests._pane_helpers import make_panes

NR = "Not reviewed"
#: inside alert #2 (position 1); 0.001° of longitude ≈ 102 m at -24°
LNG1, LAT1 = -54.99825, -23.99975


pytestmark = pytest.mark.usefixtures("english_locale")


def _alerts(n=4, *, statuses=None, groups=None, evaluators=None, gap=0.001, generation=1):
    """``n`` boxes 0.0005° wide in a row along -24°, ``gap``° apart edge to
    edge, ids 100, 101..., ``area_ha`` 1, 2...; ``groups`` are ``review_group`` ids."""
    step = 0.0005 + gap
    polys = [box(-55.0 + i * step, -24.0, -55.0 + i * step + 0.0005, -23.9995) for i in range(n)]
    gdf = gpd.GeoDataFrame({"status": list(statuses or [NR] * n)}, geometry=polys, crs="EPSG:4326")
    gdf["alert_id"] = [100 + i for i in range(n)]
    gdf["bounding_box"] = gpd.GeoSeries(polys, crs="EPSG:4326")
    gdf["area_ha"] = [float(i + 1) for i in range(n)]
    gdf["alert_date_min"] = [2026.010] * n
    if groups is not None:
        gdf[rg.COLUMN] = pd.array(groups, dtype="Int64")
    if evaluators is not None:
        gdf["evaluator"] = evaluators
    gdf.attrs[set_store.STORE_GENERATION] = generation
    return gdf


def _project(enabled=True, radius=500.0, include=False):
    return SimpleNamespace(group_review=SimpleNamespace(enabled=enabled, radius_m=radius, include_reviewed=include),
                           grid=None, review_copy=None, questions=[], alert_sets={})


def _setup(monkeypatch, gdf=None, position=0, **project):
    monkeypatch.setattr(nearby, "spawn_in_context", lambda target, args=(), **_: target(*args))
    monkeypatch.setattr(nearby, "active_set", lambda p: SimpleNamespace(id="s1", split=None))
    earlier_runs = []
    monkeypatch.setattr(nearby.earlier, "refresh_in_background", lambda *a: earlier_runs.append(a))
    app_state = SimpleNamespace(alerts_gdf=solara.reactive(_alerts() if gdf is None else gdf),
                                actual_alert_id=solara.reactive(position),
                                project=solara.reactive(_project(**project)))
    widgets = SimpleNamespace(panes=make_panes(), imagery_ctx=solara.reactive(None))
    return SimpleNamespace(widgets=widgets, analysis=AnalysisState(), app_state=app_state,
                           earlier_runs=earlier_runs)


def _section(monkeypatch, **kw):
    ctx = _setup(monkeypatch, **kw)
    ctx.section = nearby.build_nearby_section(ctx.widgets, ctx.app_state, ctx.analysis)
    return ctx


def _listed(section):
    """The alert numbers the list shows, in order."""
    return [row.number for row in section.rows]


def _row(section, number):
    return next(row for row in section.rows if row.number == number)


def _words(row):
    out = []
    for child in row.root.children:
        for c in getattr(child, "children", ()) or ():
            if isinstance(c, str):
                out.append(c)
    return out


def _features(ctx):
    spec = ctx.widgets.panes.spec(nearby.NEARBY_ID)
    return {f["properties"]["position"]: f["properties"] for f in spec["data"]["features"]} if spec else {}


# ---- visibility and the list -----------------------------------------------------
def test_the_section_is_hidden_when_grouping_is_off(monkeypatch):
    ctx = _section(monkeypatch, enabled=False)
    assert ctx.section.root.style_ == "display: none;"
    assert ctx.analysis.nearby_result.value is None and ctx.widgets.panes.spec(nearby.NEARBY_ID) is None
    ctx.app_state.project.set(_project(enabled=True, radius=300.0))
    assert ctx.section.root.style_ != "display: none;"
    assert _listed(ctx.section) == ["#2", "#3"]
    ctx.app_state.project.set(_project(enabled=False))
    assert ctx.section.root.style_ == "display: none;" and ctx.widgets.panes.spec(nearby.NEARBY_ID) is None


def test_selected_rows_come_first_then_the_nearest_up_to_fifty(monkeypatch):
    ctx = _section(monkeypatch, gdf=_alerts(60, gap=0.0001), radius=5000.0)
    assert _listed(ctx.section) == [f"#{p + 1}" for p in range(1, 51)]
    assert ctx.section.more.children == ["9 more in range not listed"]
    assert ctx.section.badge.children == ["59 within 5,000 m"]
    _row(ctx.section, "#41").box.v_model = True
    nearby.refresh_nearby(ctx.widgets, ctx.analysis, ctx.app_state)
    assert _listed(ctx.section)[:3] == ["#41", "#2", "#3"] and len(ctx.section.rows) == 51
    assert ctx.section.badge.children == ["1 selected"]


def test_a_row_reads_its_number_date_area_and_distance(monkeypatch):
    ctx = _section(monkeypatch, radius=300.0)
    words = _words(_row(ctx.section, "#2"))
    assert words[:4] == ["#2", "Jan 10, 2026", "2.0 ha", "102 m"]


def test_a_pre_grouping_review_is_not_in_the_list(monkeypatch):
    ctx = _section(monkeypatch, gdf=_alerts(3, statuses=[NR, "Confirmed", NR]), radius=300.0)
    assert _listed(ctx.section) == ["#3"]


def test_a_pre_grouping_review_is_listed_with_include_reviewed(monkeypatch):
    ctx = _section(monkeypatch, gdf=_alerts(3, statuses=[NR, "Confirmed", NR]), radius=300.0, include=True)
    assert _listed(ctx.section) == ["#2", "#3"]
    assert "Reviewed: confirmed" in _words(_row(ctx.section, "#2"))


def test_another_groups_row_names_its_group(monkeypatch):
    gdf = _alerts(4, statuses=[NR, NR, "Confirmed", "Confirmed"], groups=[None, None, 103, 103])
    ctx = _section(monkeypatch, gdf=gdf, radius=300.0)
    assert "group #4" in _words(_row(ctx.section, "#3"))
    # a group of one reads as a single row
    single = _alerts(3, statuses=[NR, "Confirmed", NR], groups=[None, 101, None])
    ctx = _section(monkeypatch, gdf=single, radius=300.0)
    assert not [w for w in _words(_row(ctx.section, "#2")) if w.startswith("group #")]


def test_opening_a_grouped_member_seeds_its_group_ticked(monkeypatch):
    gdf = _alerts(4, statuses=["Confirmed"] * 3 + [NR], groups=[100, 100, 100, None])
    ctx = _setup(monkeypatch, gdf=gdf, position=2, radius=10.0)
    nearby.seed_selection(ctx.analysis, ctx.app_state)
    assert ctx.analysis.selection.value == frozenset({1, 2})
    assert ctx.analysis.dirty.value is False and ctx.analysis.edit_rev.value == 0
    ctx.section = nearby.build_nearby_section(ctx.widgets, ctx.app_state, ctx.analysis)
    assert _listed(ctx.section) == ["#2", "#3"]
    assert all(row.box.v_model for row in ctx.section.rows)
    assert "in this group" in _words(_row(ctx.section, "#2"))


# ---- ticks ---------------------------------------------------------------------
def test_a_tick_touches_the_alert(monkeypatch):
    ctx = _section(monkeypatch, radius=300.0)
    _row(ctx.section, "#3").box.v_model = True
    assert ctx.analysis.selection.value == frozenset({2})
    assert ctx.analysis.dirty.value is True and ctx.analysis.edit_rev.value == 1
    assert len(ctx.earlier_runs) == 1  # the earlier alerts measure from the selection
    assert _features(ctx)[2]["selected"] is True and _features(ctx)[1]["selected"] is False


def test_select_all_skips_blocked_rows(monkeypatch):
    gdf = _alerts(4, evaluators=["ana", "bea", "ana", "ana"])
    ctx = _section(monkeypatch, gdf=gdf, radius=300.0)
    blocked = _row(ctx.section, "#2")
    assert blocked.box.disabled and "assigned to bea" in _words(blocked)
    ctx.section.select_all.fire_event("click", None)
    assert ctx.analysis.selection.value == frozenset({2})
    assert ctx.analysis.edit_rev.value == 1
    ctx.section.clear.fire_event("click", None)
    assert ctx.analysis.selection.value == frozenset() and ctx.analysis.edit_rev.value == 2
    ctx.section.clear.fire_event("click", None)
    assert ctx.analysis.edit_rev.value == 2  # nothing to clear: no change


# ---- the worker's guards -------------------------------------------------------
def test_a_stale_result_is_dropped(monkeypatch):
    ctx = _setup(monkeypatch)
    real = rg.nearby

    def overtaken(*a, **kw):
        ctx.analysis.nearby_seq += 1  # a newer refresh started meanwhile
        return real(*a, **kw)

    monkeypatch.setattr(nearby.review_groups, "nearby", overtaken)
    nearby.refresh_nearby(ctx.widgets, ctx.analysis, ctx.app_state)
    assert ctx.analysis.nearby_result.value is None


def test_a_result_for_a_replaced_store_is_dropped(monkeypatch):
    ctx = _setup(monkeypatch)
    real = rg.nearby

    def replaced(*a, **kw):
        ctx.app_state.alerts_gdf.set(_alerts(generation=2))  # same alert position, another store
        return real(*a, **kw)

    monkeypatch.setattr(nearby.review_groups, "nearby", replaced)
    nearby.refresh_nearby(ctx.widgets, ctx.analysis, ctx.app_state)
    assert ctx.analysis.nearby_result.value is None


def test_a_result_for_another_alert_is_dropped(monkeypatch):
    ctx = _setup(monkeypatch)
    real = rg.nearby

    def moved(*a, **kw):
        ctx.analysis.reset_alert()
        return real(*a, **kw)

    monkeypatch.setattr(nearby.review_groups, "nearby", moved)
    nearby.refresh_nearby(ctx.widgets, ctx.analysis, ctx.app_state)
    assert ctx.analysis.nearby_result.value is None


def test_a_search_keys_the_metric_cache_by_set_and_generation(monkeypatch):
    ctx = _setup(monkeypatch)
    seen = {}
    real = rg.nearby

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(nearby.review_groups, "nearby", spy)
    nearby.refresh_nearby(ctx.widgets, ctx.analysis, ctx.app_state)
    assert seen["cache_key"] == ("s1", 1) and seen["limit"] == nearby.LIMIT
    assert ctx.widgets.panes.widget.pick_tag == f"{ctx.analysis.alert_token.value}:{ctx.analysis.nearby_seq}"


# ---- zoom and the image area ---------------------------------------------------
def test_zoom_to_keeps_the_selection_and_imagery(monkeypatch):
    ctx = _section(monkeypatch, radius=300.0)
    _row(ctx.section, "#3").box.v_model = True
    image = object()
    ctx.analysis.before_image.set(image)
    imagery = {"bbox": ctx.app_state.alerts_gdf.value["bounding_box"].iloc[0], "token": 1}
    ctx.widgets.imagery_ctx.set(imagery)
    before = dict(ctx.widgets.panes.widget.view or {})
    rev = ctx.analysis.edit_rev.value
    _row(ctx.section, "#2").zoom.fire_event("click", None)
    view = ctx.widgets.panes.widget.view
    assert view != before and view["center"][1] == pytest.approx(-54.99825)
    assert ctx.analysis.selection.value == frozenset({2}) and ctx.analysis.edit_rev.value == rev
    assert ctx.analysis.before_image.value is image and ctx.widgets.imagery_ctx.value is imagery


def test_the_outside_caption_reads_the_local_box(monkeypatch):
    ctx = _section(monkeypatch, gdf=_alerts(3, gap=0.02), radius=5000.0)  # ≈ 2 km apart
    ctx.widgets.actual_bb = None  # the EE feature is never read
    ctx.widgets.imagery_ctx.set({"bbox": ctx.app_state.alerts_gdf.value["bounding_box"].iloc[0]})
    assert ctx.section.outside.style_ == "display: none;"
    _row(ctx.section, "#2").box.v_model = True
    assert ctx.section.outside.style_ != "display: none;"
    assert ctx.section.outside.children == [
        "Some selected alerts are outside the image area: pick a larger area on the timeline."]
    ctx.analysis.image_area.set("xlarge")  # 2.5 km + a grid cell
    assert ctx.section.outside.style_ == "display: none;"


# ---- the map click -------------------------------------------------------------
def _pick(ctx, tag=None, view="before", lat=LAT1, lng=LNG1, seq=1):
    tag = f"{ctx.analysis.alert_token.value}:{ctx.analysis.nearby_seq}" if tag is None else tag
    ctx.widgets.panes.widget.set_state({"pick_event": {"view": view, "lat": lat, "lng": lng, "zoom": 18,
                                                       "tag": tag, "seq": seq}})


def test_a_pick_near_a_listed_row_toggles_it(monkeypatch):
    ctx = _section(monkeypatch, radius=300.0)
    _pick(ctx)
    assert ctx.analysis.selection.value == frozenset({1}) and ctx.analysis.dirty.value
    _pick(ctx, seq=2)
    assert ctx.analysis.selection.value == frozenset()


def test_a_pick_with_a_stale_tag_is_ignored(monkeypatch):
    ctx = _section(monkeypatch, radius=300.0)
    _pick(ctx, tag=f"{ctx.analysis.alert_token.value - 1}:{ctx.analysis.nearby_seq}")
    _pick(ctx, tag=f"{ctx.analysis.alert_token.value}:{ctx.analysis.nearby_seq - 1}", seq=2)
    assert ctx.analysis.selection.value == frozenset()


def test_a_pick_on_a_view_with_the_layer_off_is_ignored(monkeypatch):
    ctx = _section(monkeypatch, radius=300.0)
    ctx.widgets.panes.widget.layer_menu_toggles = {"before": {nearby.NEARBY_ID: False}}
    _pick(ctx, view="before")
    assert ctx.analysis.selection.value == frozenset()
    _pick(ctx, view="after", seq=2)
    assert ctx.analysis.selection.value == frozenset({1})


def test_a_pick_on_a_blocked_row_is_ignored(monkeypatch):
    ctx = _section(monkeypatch, gdf=_alerts(3, evaluators=["ana", "bea", "ana"]), radius=300.0)
    _pick(ctx)
    assert ctx.analysis.selection.value == frozenset()


def test_wire_pick_unsubscribes(monkeypatch):
    ctx = _setup(monkeypatch, radius=300.0)
    nearby.refresh_nearby(ctx.widgets, ctx.analysis, ctx.app_state)
    off = nearby.wire_pick(ctx.widgets, ctx.app_state, ctx.analysis)
    off()
    _pick(ctx)
    assert ctx.analysis.selection.value == frozenset()
    assert nearby.wire_pick(SimpleNamespace(), ctx.app_state, ctx.analysis) is None


def test_hit_row_takes_the_nearest_within_six_pixels():
    gdf = _alerts()
    assert nearby.hit_row(gdf, [0, 1, 2], LAT1, LNG1, 18) == 1  # inside
    # zoom 18: ≈ 0.55 m per pixel at -24°, so 6 px ≈ 3.3 m; 1° of longitude ≈ 101.7 km
    edge = -54.998
    assert nearby.hit_row(gdf, [0, 1, 2], LAT1, edge + 2 / 101_700, 18) == 1
    assert nearby.hit_row(gdf, [0, 1, 2], LAT1, edge + 5 / 101_700, 18) is None
    assert nearby.hit_row(gdf, [0, 2], LAT1, LNG1, 18) is None  # not listed
    # zoom 10: 6 px ≈ 840 m, both neighbours in reach: the nearer one
    assert nearby.hit_row(gdf, [1, 2], LAT1, -54.9973, 10) == 2
    # a tie goes to the lowest id
    twins = _alerts(3)
    twins.loc[2, "geometry"] = twins.geometry.iloc[1]
    assert nearby.hit_row(twins, [2, 1], LAT1, LNG1, 18) == 1


# ---- the layer -----------------------------------------------------------------
def test_the_layer_styles_selected_and_candidate_rows():
    gdf = _alerts(4, statuses=[NR, NR, "Confirmed", NR], groups=[None, None, 102, None])
    result = rg.nearby(gdf, 0, 1000.0)
    spec = nearby.layer_spec(gdf, result.rows, frozenset({1}))
    assert (spec["id"], spec["view"], spec["group"], spec["name"]) == (
        nearby.NEARBY_ID, "all", "nearby", nearby.NEARBY_LAYER_NAME)
    assert spec["meta"] == {"hideWhileEditing": True, "menuSection": "alerts"}
    styles = {f["properties"]["position"]: f["properties"]["style"] for f in spec["data"]["features"]}
    assert set(styles) == {1, 2, 3}
    assert styles[1]["dashArray"] is None and styles[1]["weight"] > styles[3]["weight"]
    assert styles[3]["dashArray"] and styles[2]["dashArray"]
    assert styles[2]["color"] == status_color("Confirmed")


# ---- the panel -----------------------------------------------------------------
# ---- fix round 1 ----------------------------------------------------------------
def test_a_row_ticked_while_a_search_runs_stays_listed(monkeypatch):
    ctx = _section(monkeypatch, radius=300.0)  # lists #2, #3; #4 is ≈ 407 m away
    real = rg.nearby

    def ticked_meanwhile(*a, **kw):
        result = real(*a, **kw)  # captured the empty selection
        ctx.analysis.selection.set(frozenset({3}))  # a tick from a list that offered #4
        return result

    monkeypatch.setattr(nearby.review_groups, "nearby", ticked_meanwhile)
    nearby.refresh_nearby(ctx.widgets, ctx.analysis, ctx.app_state)
    assert _listed(ctx.section)[0] == "#4" and _row(ctx.section, "#4").box.v_model is True
    assert _features(ctx)[3]["selected"] is True
    assert ctx.section.badge.children == ["1 selected"]
    # and a click on it unticks it
    _pick(ctx, lng=-55.0 + 3 * 0.0015 + 0.00025)
    assert ctx.analysis.selection.value == frozenset()


def test_replaced_row_widgets_are_closed(monkeypatch):
    ctx = _section(monkeypatch, radius=300.0)
    old = _row(ctx.section, "#2")
    _row(ctx.section, "#3").box.v_model = True
    assert old.box.comm is None and old.root.comm is None and old.zoom.comm is None
    assert all(row.box.comm is not None for row in ctx.section.rows)


def test_a_result_for_another_set_is_dropped(monkeypatch):
    ctx = _setup(monkeypatch)
    real = rg.nearby

    def switched(*a, **kw):
        monkeypatch.setattr(nearby, "active_set", lambda p: SimpleNamespace(id="s2", split=None))
        return real(*a, **kw)

    monkeypatch.setattr(nearby.review_groups, "nearby", switched)
    nearby.refresh_nearby(ctx.widgets, ctx.analysis, ctx.app_state)
    assert ctx.analysis.nearby_result.value is None
