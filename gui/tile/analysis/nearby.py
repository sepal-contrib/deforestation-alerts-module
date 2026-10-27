"""The Analysis *Nearby alerts* section and its map layer (spec 2026-10-26
group review §6.1-§6.3).

Shown only while grouping is effective for the active set
(``grouping.effective``): the list of the alerts near the one on screen, each
with a checkbox. The ticked rows are the *selection*
(``AnalysisState.selection``, positions), the alerts the next Save writes the
review to (``review_groups.plan_group``). The listed rows are outlined on
every view (layer ``analysis-nearby``), and a click on the maps ticks or
unticks the nearest one (the multiview ``pick`` plugin, ``wire_pick``). No
distance field: the distance is set, and locked, in the Evaluation step.

The search (``review_groups.nearby``) runs on a worker (``refresh_nearby``,
modelled on ``earlier.refresh_earlier``). It captures the alert token, a
``nearby_seq`` counter, the set id and the store generation, and its result
lands only while all four are unchanged: the cache key it passes
(set id, generation) speeds a search up but guards nothing.

A tick reorders the list on screen (selected rows first) without a new
search: the next search (an alert, store or setting change) lists them first
itself. Alerts are numbered as the jump menu numbers them: position + 1.
"""

import json
import logging
import math
from datetime import date
from types import SimpleNamespace

import ipyvuetify as v
import shapely
from shapely.geometry import Point

from deforestation_alerts.project import grouping as grouping_rules
from deforestation_alerts.project import review_groups
from deforestation_alerts.scripts.mosaics_helper import convert_julian_to_date
from gui.i18n import format_date, format_number, t, tn
from gui.scripts.set_store import STORE_GENERATION
from gui.scripts.solara_threads import spawn_in_context
from gui.store.workflow_steps import active_set
from gui.tile.analysis import earlier
from gui.tile.analysis.context import status_color
from gui.tile.analysis.imagery import area_box
from gui.tile.analysis.labels import Labels
from gui.tile.analysis.pane_layers import geojson_spec
from gui.tile.analysis.section import TYPE_BODY, TYPE_CAPTION, TYPE_LABEL, build_section
from gui.tile.analysis.state import IMAGE_AREAS, area_crs, toggle_selection, touch
from gui.widget.buttons import btn, set_label

logger = logging.getLogger("SEPALUI.deforestation_alerts")

#: The outlines' spec id on the views, and their layer-menu name.
NEARBY_ID = "analysis-nearby"
NEARBY_LAYER_NAME = "Nearby alerts"
#: at most this many candidates are listed (the selected rows always are)
LIMIT = 50
#: a map click ticks the nearest listed alert within this many screen pixels
PICK_PX = 6
#: Web Mercator metres per pixel at zoom 0 on the equator
_M_PER_PX_Z0 = 156_543.03392
_M_PER_DEG = 111_320.0
_HIDDEN = "display: none;"
#: the banner while shown (T10 shows it on a grouped alert). Inline: Vuetify's
#: ``d-flex`` is ``!important`` and would beat a ``display: none``.
BANNER_SHOWN = "display: flex; align-items: center; gap: 8px; margin-bottom: 8px;"
_ACTIONS = "display: flex; gap: 8px; margin-top: 4px;"


# ---- pure helpers ----------------------------------------------------------
def _current_grouping(app_state):
    """``(grouping, active set)`` of the project on hand; grouping None = off."""
    p = app_state.project.value
    s = active_set(p) if p is not None else None
    return grouping_rules.effective(p, s), s


def _generation(gdf):
    return (getattr(gdf, "attrs", None) or {}).get(STORE_GENERATION) if gdf is not None else None


def _valid(gdf, position) -> bool:
    return gdf is not None and position is not None and 0 <= position < len(gdf)


def hit_row(gdf, positions, lat, lng, zoom, px=PICK_PX):
    """The listed alert a click at ``(lat, lng)`` on a map at ``zoom`` hits:
    the nearest whose polygon is within ``px`` screen pixels of it (inside
    counts as 0), ties to the lowest id; None when none is. Pixels become
    metres at the click's zoom and latitude, and the polygons are measured
    in a local equirectangular frame around the click."""
    k = math.cos(math.radians(lat))
    tolerance = px * _M_PER_PX_Z0 * k / (2 ** zoom)
    scale = (_M_PER_DEG * k, _M_PER_DEG)
    origin = Point(0.0, 0.0)
    ids = review_groups.ids(gdf)
    best = None
    for p in positions:
        geom = gdf.geometry.iloc[p]
        if geom is None or geom.is_empty:
            continue
        local = shapely.transform(geom, lambda c: (c - (lng, lat)) * scale)
        d = float(shapely.distance(local, origin))
        if d <= tolerance and (best is None or (d, int(ids[p])) < best[:2]):
            best = (d, int(ids[p]), int(p))
    return None if best is None else best[2]


def _style(row, selected) -> dict:
    """Selected: solid and thicker, lightly filled. A candidate: thin and
    dashed. The colour is the row's status colour (a reviewed row's verdict)."""
    style = {"color": status_color(row.status), "opacity": 0.5 if row.blocked else 1.0}
    if selected:
        return {**style, "weight": 3, "dashArray": None, "fill": True, "fillOpacity": 0.15}
    return {**style, "weight": 1.5, "dashArray": "4 4", "fill": False, "fillOpacity": 0}


def layer_spec(gdf, rows, selection) -> dict:
    """The outlines of the listed ``rows`` (``NearbyRow``), each styled by
    whether it is in ``selection``: one GeoJSON spec on every view, hidden
    while the reviewer draws, in the menu's *Alerts* section."""
    features = []
    for r in rows:
        geom = gdf.geometry.iloc[r.position] if 0 <= r.position < len(gdf) else None
        if geom is None or geom.is_empty:
            continue
        selected = r.position in selection
        features.append({"type": "Feature",
                         "properties": {"position": int(r.position), "alert": int(r.position) + 1,
                                        "status": r.status, "selected": selected, "blocked": r.blocked,
                                        "style": _style(r, selected)},
                         "geometry": json.loads(shapely.to_geojson(geom))})
    return geojson_spec(NEARBY_ID, "all", {"type": "FeatureCollection", "features": features},
                        name=NEARBY_LAYER_NAME, group="nearby", style={"weight": 1.5, "fill": False},
                        hide_while_editing=True, section="alerts")


def remove_layer(widgets) -> None:
    """Take the outlines off the views (a no-op without views or without them)."""
    panes = getattr(widgets, "panes", None)
    if panes is not None:
        panes.remove(NEARBY_ID)


def draw_layer(widgets, analysis, app_state) -> None:
    """The outlines of the last search's rows, as the selection styles them;
    none without a result or without rows."""
    panes = getattr(widgets, "panes", None)
    if panes is None:
        return
    result = analysis.nearby_result.value
    gdf = app_state.alerts_gdf.value
    if result is None or gdf is None or not result.rows:
        panes.remove(NEARBY_ID)
        return
    panes.add(layer_spec(gdf, result.rows, frozenset(analysis.selection.value or ())))


def seed_selection(analysis, app_state) -> None:
    """Tick the stored group of the alert on screen (spec §6.3): its rows
    other than the main, the opened member included. Called as an alert opens,
    after ``reset_alert``; not a change to the review, so ``dirty`` stays as
    it is. Nothing while grouping is off or for an alert in no group."""
    gdf = app_state.alerts_gdf.value
    position = app_state.actual_alert_id.value
    grouping, _s = _current_grouping(app_state)
    if grouping is None or not _valid(gdf, position):
        return
    _main, members = review_groups.group_of(gdf, position)
    if members:
        analysis.selection.set(frozenset(members))


# ---- the search ------------------------------------------------------------
def refresh_nearby(widgets, analysis, app_state) -> None:
    """Search around the alert on screen and publish the result to
    ``analysis.nearby_result``, draw the outlines and tag the picks with it.
    With grouping off or no alert, the result and the outlines go. Blocking:
    call it from a worker."""
    with analysis._alert_lock:
        # every refresh supersedes the ones in flight
        analysis.nearby_seq += 1
        seq = analysis.nearby_seq
        token = analysis.alert_token.value
        selection = frozenset(analysis.selection.value or ())
    gdf = app_state.alerts_gdf.value
    position = app_state.actual_alert_id.value
    p = app_state.project.value
    grouping, s = _current_grouping(app_state)
    set_id = s.id if s is not None else None
    generation = _generation(gdf)

    if grouping is None or not _valid(gdf, position):
        def _clear():
            if seq == analysis.nearby_seq:
                analysis.nearby_result.set(None)
                remove_layer(widgets)

        analysis.apply_if_current(token, _clear)
        return

    rc = getattr(p, "review_copy", None)
    main = review_groups.group_of(gdf, position)[0]
    key = (set_id, generation) if set_id is not None and generation is not None else None
    result = review_groups.nearby(gdf, main, grouping.radius_m, area_crs(p), include=selection,
                                  offer_reviewed=grouping.include_reviewed,
                                  evaluator=rc.evaluator if rc is not None else None,
                                  limit=LIMIT, cache_key=key)

    def _apply():
        if seq != analysis.nearby_seq:
            return  # a newer refresh started meanwhile
        _now, live_set = _current_grouping(app_state)
        if (live_set.id if live_set is not None else None) != set_id:
            return  # another set is active: its refresh follows
        if _generation(app_state.alerts_gdf.value) != generation:
            return  # the store was replaced: the search of the new one follows
        analysis.nearby_result.set(result)
        draw_layer(widgets, analysis, app_state)
        panes = getattr(widgets, "panes", None)
        if panes is not None:
            panes.set_pick_tag(f"{token}:{seq}")

    analysis.apply_if_current(token, _apply)


def _refresh_logged(widgets, analysis, app_state) -> None:
    """``refresh_nearby`` for a worker thread: a failure is logged, not left
    to die in the thread."""
    try:
        refresh_nearby(widgets, analysis, app_state)
    except Exception:
        logger.exception("Analysis: the nearby-alerts search failed")


def refresh_in_background(widgets, analysis, app_state) -> None:
    """``refresh_nearby`` on a worker, for a caller on the UI / navigation thread."""
    spawn_in_context(_refresh_logged, (widgets, analysis, app_state))


def _ticked(widgets, analysis, app_state) -> None:
    """After a tick: the earlier alerts measure from the selection too (T11)."""
    earlier.refresh_in_background(widgets, analysis, app_state)


# ---- the map click ---------------------------------------------------------
def wire_pick(widgets, app_state, analysis):
    """Tick (or untick) the listed row a map click hits. A click is dropped
    when its tag is not the current ``"<alert token>:<nearby_seq>"`` (a list
    since replaced), when its view has the nearby layer switched off, or when
    it hits no listed row another evaluator does not hold. Returns the
    unsubscribe, or None without views."""
    panes = getattr(widgets, "panes", None)
    if panes is None:
        return None

    def _on_pick(event):
        event = event or {}
        if event.get("tag") != f"{analysis.alert_token.value}:{analysis.nearby_seq}":
            return
        if panes.layer_off(event.get("view"), NEARBY_ID):
            return
        result = analysis.nearby_result.value
        gdf = app_state.alerts_gdf.value
        if result is None or gdf is None:
            return
        try:
            lat, lng, zoom = float(event["lat"]), float(event["lng"]), float(event["zoom"])
        except (KeyError, TypeError, ValueError):
            return
        positions = [r.position for r in result.rows if not r.blocked and 0 <= r.position < len(gdf)]
        hit = hit_row(gdf, positions, lat, lng, zoom)
        if hit is not None:
            toggle_selection(analysis, hit)
            _ticked(widgets, analysis, app_state)

    return panes.on_pick(_on_pick)


# ---- the section -----------------------------------------------------------
def _radius_text(metres) -> str:
    return format_number(metres, 0 if float(metres).is_integer() else 1)


def _date_text(julian) -> str:
    try:
        return format_date(date.fromisoformat(convert_julian_to_date(julian)))
    except (TypeError, ValueError):
        return ""


def _span(text, class_=TYPE_LABEL):
    return v.Html(tag="span", class_=class_, children=[text])


def _big_group_ids(gdf) -> frozenset:
    """The ids of the groups of two rows or more: a group of one reads as a
    single row."""
    counts = review_groups.group_values(gdf).value_counts()
    return frozenset(int(value) for value, n in counts.items() if n >= 2)


def build_nearby_section(widgets, app_state, analysis, *, run_remove=None, labels=None):
    """The *Nearby alerts* section: a banner slot (with *Remove from group*),
    the outside-the-image-area caption, *Select all* / *Clear*, the list and
    the "more in range" note, under a header whose badge counts the selection
    or what is in range. Returns the ``build_section`` namespace plus
    ``rows_box``, ``rows`` (the rows on screen), ``banner``, ``banner_text``,
    ``remove_btn``, ``select_all``, ``clear``, ``outside``, ``more``,
    ``sync`` and ``run_remove`` (the *Remove from group* click; T10 fills it,
    and shows the banner and the button). ``labels`` is the screen's
    ``labels.Labels`` (a fresh one when none is given)."""
    labels = labels if labels is not None else Labels()
    section = build_section(t("analysis_tile.sections.nearby"), v.Html(tag="div"))
    banner_text = v.Html(tag="span", class_=TYPE_BODY, style_="flex: 1 1 auto;", children=[])
    remove_btn = btn("secondary", "", style_=_HIDDEN)
    banner = v.Html(tag="div", style_=_HIDDEN, children=[banner_text, remove_btn])
    outside = v.Html(tag="div", class_=f"{TYPE_CAPTION} warning--text mt-2", style_=_HIDDEN, children=[])
    select_all = btn("tertiary", t("analysis_tile.nearby.select_all"))
    clear = btn("tertiary", t("analysis_tile.nearby.clear"))
    rows_box = v.Html(tag="div", class_="mt-2", children=[])
    more = v.Html(tag="div", class_=f"{TYPE_LABEL} mt-1", style_=_HIDDEN, children=[])
    section.body_box.children = [v.Html(tag="div", children=[
        banner, outside, v.Html(tag="div", style_=_ACTIONS, children=[select_all, clear]), rows_box, more])]
    #: the group sizes of the result on screen, computed once per result
    memo = {"result": None, "big": frozenset(), "ids": None}

    def _selection():
        return frozenset(analysis.selection.value or ())

    def _groups_of(result, gdf):
        if memo["result"] is not result:
            memo["result"] = result
            memo["big"] = _big_group_ids(gdf) if gdf is not None else frozenset()
            memo["ids"] = review_groups.ids(gdf) if gdf is not None else None
        return memo["big"], memo["ids"]

    def _on_tick(position, change):
        if bool(change["new"]) != (position in _selection()):
            toggle_selection(analysis, position)
            _ticked(widgets, analysis, app_state)

    def _zoom(position):
        panes = getattr(widgets, "panes", None)
        gdf = app_state.alerts_gdf.value
        if panes is None or not _valid(gdf, position):
            return
        shape = gdf.geometry.iloc[position]
        if shape is None or shape.is_empty:
            return
        c = shape.centroid
        panes.set_view((c.y, c.x), earlier._zoom_for(shape.bounds))

    def _row(r, selected, big, ids):
        number = f"#{r.position + 1}"
        box = v.Checkbox(v_model=selected, disabled=r.blocked, dense=True, hide_details=True,
                         class_="ma-0 pa-0")
        if not r.blocked:
            box.observe(lambda change, p=r.position: _on_tick(p, change), names="v_model")
        zoom = v.Html(tag="span", class_=f"{TYPE_BODY} dat-num", style_="cursor: pointer;", children=[number])
        zoom.on_event("click", lambda *_, p=r.position: _zoom(p))
        children = [box, zoom, _span(_date_text(r.date_min)),
                    _span(f"{format_number(r.area_ha, 1)} ha", f"{TYPE_LABEL} dat-num")]
        if r.distance_m is None:
            children.append(_span(t("analysis_tile.nearby.in_group")))
        else:
            children.append(_span(t("analysis_tile.nearby.distance", m=format_number(round(r.distance_m)))))
            if r.group is not None and ids is not None and int(ids[r.group]) in big:
                children.append(_span(t("analysis_tile.nearby.group_of_main", n=r.group + 1)))
        if r.status != review_groups.NOT_REVIEWED:
            children.append(earlier._status_chip(r.status))
        if r.blocked:
            children.append(_span(t("analysis_tile.nearby.assigned_to", name=r.evaluator)))
        root = v.Html(tag="div", class_="d-flex align-center flex-wrap",
                      style_="gap: 6px; padding: 4px 0;" + (" opacity: 0.5;" if r.blocked else ""),
                      children=children)
        return SimpleNamespace(position=r.position, number=number, root=root, box=box, zoom=zoom)

    def _sync_badge():
        result = analysis.nearby_result.value
        grouping, _s = _current_grouping(app_state)
        selection = _selection()
        if result is None or grouping is None:
            section.set_badge("")
        elif selection:
            section.set_badge(tn("analysis_tile.nearby.badge_selected", len(selection)), color="primary")
        else:
            n = sum(1 for r in result.rows if r.distance_m is not None) + result.more
            radius = _radius_text(grouping.radius_m)
            section.set_badge(tn("analysis_tile.nearby.badge_within", n, radius=radius) if n
                              else t("analysis_tile.nearby.badge_none", radius=radius))

    def _sync_outside(*_):
        ctx = getattr(widgets, "imagery_ctx", None)
        value = ctx.value if ctx is not None else None
        bbox = value.get("bbox") if isinstance(value, dict) else None
        gdf = app_state.alerts_gdf.value
        selection = _selection()
        out = False
        if bbox is not None and gdf is not None and selection:
            key = analysis.image_area.value if analysis.image_area.value in IMAGE_AREAS else "normal"
            frame = area_box(bbox, IMAGE_AREAS[key])
            out = any(0 <= p < len(gdf) and gdf.geometry.iloc[p] is not None
                      and not frame.contains(gdf.geometry.iloc[p]) for p in selection)
        outside.children = [t("analysis_tile.nearby.outside")] if out else []
        outside.style_ = "" if out else _HIDDEN

    def _render(*_):
        result = analysis.nearby_result.value
        gdf = app_state.alerts_gdf.value
        selection = _selection()
        rows = [r for r in (result.rows if result is not None else ()) if _valid(gdf, r.position)]
        # selected first, in the search's order: a tick moves its row up
        # before the next search lists it there itself
        ordered = [r for r in rows if r.position in selection] + [r for r in rows if r.position not in selection]
        big, ids = _groups_of(result, gdf)
        section.rows = [_row(r, r.position in selection, big, ids) for r in ordered]
        rows_box.children = [row.root for row in section.rows]
        n_more = result.more if result is not None else 0
        more.children = [tn("analysis_tile.nearby.more", n_more)] if n_more else []
        more.style_ = "" if n_more else _HIDDEN
        _sync_badge()
        _sync_outside()

    def _on_selection(*_):
        _render()
        draw_layer(widgets, analysis, app_state)

    def _select_all(*_):
        result = analysis.nearby_result.value
        selection = _selection()
        gdf = app_state.alerts_gdf.value
        add = {r.position for r in (result.rows if result is not None else ())
               if not r.blocked and _valid(gdf, r.position)} - selection
        if add:
            analysis.selection.set(selection | add)
            touch(analysis)
            _ticked(widgets, analysis, app_state)

    def _clear(*_):
        if _selection():
            analysis.selection.set(frozenset())
            touch(analysis)
            _ticked(widgets, analysis, app_state)

    def _remove(*_):
        if section.run_remove is not None:
            section.run_remove()

    shown_style = section.root.style_
    last = {"key": object()}

    def sync(*_):
        """Show the section while grouping is effective, and search again when
        the set or its grouping changed."""
        grouping, s = _current_grouping(app_state)
        section.root.style_ = shown_style if grouping is not None else _HIDDEN
        key = (s.id if s is not None else None, grouping)
        if key != last["key"]:
            last["key"] = key
            refresh_in_background(widgets, analysis, app_state)
        _sync_badge()

    def _on_alerts(*_):
        last["key"] = object()  # another table: search it
        sync()

    def _label():
        section.set_title(t("analysis_tile.sections.nearby"))
        set_label(select_all, t("analysis_tile.nearby.select_all"))
        set_label(clear, t("analysis_tile.nearby.clear"))
        _render()

    select_all.on_event("click", _select_all)
    clear.on_event("click", _clear)
    remove_btn.on_event("click", _remove)
    analysis.nearby_result.subscribe(_render)
    analysis.selection.subscribe(_on_selection)
    analysis.image_area.subscribe(_sync_outside)
    ctx = getattr(widgets, "imagery_ctx", None)
    if ctx is not None:
        ctx.subscribe(_sync_outside)
    app_state.project.subscribe(sync)
    app_state.alerts_gdf.subscribe(_on_alerts)

    section.rows = []
    section.rows_box, section.banner, section.banner_text = rows_box, banner, banner_text
    section.remove_btn, section.select_all, section.clear = remove_btn, select_all, clear
    section.outside, section.more, section.sync = outside, more, sync
    section.run_remove = run_remove
    section.unpick = wire_pick(widgets, app_state, analysis)
    # in place of a direct first ``_render()``: ``add`` runs it now
    labels.add(_label)
    sync()
    return section
