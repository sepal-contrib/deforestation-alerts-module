"""The Analysis "Earlier alerts" section and its map layer (spec 2026-10-20
§6.2–6.3).

The section lists the earlier periods' confirmed / needs-revision alerts near
the current one and every alert its draft links name, grouped by period,
newest first; a tick links, an untick unlinks (``state.set_links``, saved with
the review). The layer is one read-only GeoJSON spec on every view: each
feature carries its style in ``properties.style`` (colour = period, dashed =
needs revision, thick + fill = linked).

Everything comes from ``growth_index.earlier_view``, whose first call reads
every set's store: ``refresh_earlier`` blocks, so it runs on a worker
(``refresh_in_background``, or ``spawn_in_context`` for the switch, the
distance and a tick). Its result is applied under
``AnalysisState.apply_if_current``: an answer for an alert that is no longer
the current one is dropped, and one overtaken by a newer refresh too
(``earlier_seq``). A refresh also fills ``analysis.history`` and
``analysis.parent_windows``, the switch off included.

No hover tooltip: nothing in the Analysis panel does hover or popups, so the
layer stays a plain outline.
"""

import itertools
import json
import logging
import math
from types import SimpleNamespace

import ipyvuetify as v
import shapely

from deforestation_alerts.engine.fingerprint import earlier_sets
from deforestation_alerts.project import review_copy
from deforestation_alerts.project.review_status import CONFIRMED, FALSE_POSITIVE
from gui.i18n import format_date, format_number, t, tn
from gui.scripts import growth_index
from gui.scripts.solara_threads import spawn_in_context
from gui.store.workflow_steps import active_set
from gui.tile.analysis.context import status_color, status_label_key
from gui.tile.analysis.labels import Labels
from gui.tile.analysis.pane_layers import geojson_spec
from gui.tile.analysis.section import TYPE_BODY, TYPE_LABEL, build_section
from gui.tile.analysis.state import set_links

logger = logging.getLogger("SEPALUI.deforestation_alerts")

EARLIER_LAYER_NAME = "Earlier alerts"
#: The overlay's spec id on the views.
EARLIER_ID = "earlier"
MIN_KM, MAX_KM = 0.1, 50.0
#: the states whose item is linked to the current alert (a tick)
_LINKED = ("auto", "linked")
#: the period legend while the overlay is on (inline: Vuetify's ``d-flex`` is
#: ``!important`` and would beat a ``display: none``)
_LEGEND_SHOWN = "display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: center;"
#: the width, in pixels, a row's zoom fits the alert's box into (about half a view)
_ZOOM_PX = 200


def parse_km(text):
    """The distance typed in the field as a float within 0.1-50, else None."""
    try:
        km = float(str(text).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return km if MIN_KM <= km <= MAX_KM else None


def has_earlier_sets(p) -> bool:
    """Whether the active set has sets of earlier periods to show alerts of:
    without one the section has nothing to offer and is hidden. A review copy
    holds one set and brings its earlier alerts in the package (spec §6.6):
    there the section always shows, and says when the package has none."""
    s = active_set(p) if p is not None else None
    if s is not None and review_copy.read_only(p):
        return True
    period = p.periods.get(s.period_id) if s is not None else None
    return period is not None and bool(earlier_sets(p, period))


def distance_text(metres) -> str:
    """"40 m" under a kilometre, "1.2 km" from there, "" when unknown."""
    if metres is None or not math.isfinite(metres):
        return ""
    if metres < 1000:
        return t("analysis_tile.earlier.distance_m", m=format_number(round(metres)))
    return t("analysis_tile.earlier.distance_km", km=format_number(metres / 1000.0, 1))


def note_text(item) -> str:
    """What a row says after its status: *touches* (an automatic link), *via
    #12*, why a link is broken, or the distance."""
    if item.state == "auto":
        return t("analysis_tile.earlier.touches")
    if item.state == "via":
        return t("analysis_tile.earlier.via", id=(item.via or "").rpartition("#")[2])
    if item.state == "broken":
        return t("analysis_tile.earlier.broken_fp" if item.cand.node.status == FALSE_POSITIVE
                 else "analysis_tile.earlier.broken_rerun")
    return distance_text(item.distance_m)


def period_label(start) -> str:
    return format_date(start, "MMM y")


def _style(item) -> dict:
    linked = item.state in _LINKED
    return {"color": item.color, "weight": 4 if linked else 2,
            "dashArray": None if item.cand.node.status == CONFIRMED else "6 4",
            "fill": linked, "fillOpacity": 0.2}


def layer_spec(view, hidden=frozenset()) -> dict:
    """The overlay's spec: every item with a shape, but broken links (their
    shape is not the alert that was linked) and the hidden periods."""
    features = []
    for it in view.items:
        n = it.cand.node
        if it.state == "broken" or n.shape is None or n.period_id in hidden:
            continue
        features.append({"type": "Feature",
                         "properties": {"set_id": n.set_id, "alert_id": n.alert_id, "status": n.status,
                                        "period_id": n.period_id, "state": it.state, "style": _style(it)},
                         "geometry": json.loads(shapely.to_geojson(n.shape))})
    return geojson_spec(EARLIER_ID, "all", {"type": "FeatureCollection", "features": features},
                        name=EARLIER_LAYER_NAME, group="earlier", style={"weight": 2, "fill": False},
                        section="alerts")


def remove_earlier_layer(widgets) -> None:
    """Take the overlay off the views (a no-op without views or without it)."""
    panes = getattr(widgets, "panes", None)
    if panes is not None:
        panes.remove(EARLIER_ID)


def _alert_id(gdf, pos) -> int:
    """The ``alert_id`` of the row at position ``pos``."""
    return int(gdf.iloc[pos]["alert_id"]) if "alert_id" in gdf.columns else int(pos)


def _kept_links(p, view, links) -> tuple:
    """The draft ``links`` without those broken by a re-run of their set or
    that cannot resolve at all (their set is gone). A link to a parent that has
    since become a false positive stays, greyed and out of the totals (spec
    §11.5). In a review copy whose package has no snapshot, nothing earlier
    resolves: those links are kept as they are."""
    items = {it.cand.link: it for it in view.items}
    rc = getattr(p, "review_copy", None)
    drop_missing = rc is None or getattr(rc, "snapshot_at", None) is not None

    def _kept(lk):
        if lk not in items:
            return not drop_missing
        item = items[lk]
        return item.state != "broken" or item.cand.node.status == FALSE_POSITIVE

    return tuple(lk for lk in links if _kept(lk))


def refresh_earlier(widgets, analysis, app_state, *, on_result=None) -> None:
    """Compute the current alert's earlier alerts and apply them: the History
    row, the parent windows, the list and, when the switch is on, the layer.
    Blocking: call it from a worker. ``on_result(view)`` gets the
    ``EarlierView`` (None when a refresh starts); by default the callback the
    section registered on ``widgets``."""
    on_result = on_result or getattr(widgets, "earlier_on_result", None)
    with analysis._alert_lock:
        # every refresh, the switch-off included, supersedes the ones in flight
        analysis.earlier_seq += 1
        seq = analysis.earlier_seq
    if on_result:
        on_result(None)  # the previous alert's / distance's list is stale
    if not analysis.show_earlier_reviews.value:
        remove_earlier_layer(widgets)
    gdf = app_state.alerts_gdf.value
    pos = app_state.actual_alert_id.value
    p = app_state.project.value
    s = active_set(p) if p is not None else None
    if gdf is None or pos is None or not 0 <= pos < len(gdf) or s is None:
        remove_earlier_layer(widgets)
        return
    token = analysis.alert_token.value
    links = tuple(analysis.links.value)
    auto = tuple(analysis.auto_links.value)
    km = analysis.earlier_radius_km.value
    alert_id = _alert_id(gdf, pos)
    view = growth_index.earlier_view(p, s.id, gdf, alert_id, links, auto, km * 1000.0)
    summary = growth_index.history_summary(p, s.id, gdf, alert_id, links)
    windows = growth_index.parent_windows(view)
    hidden = analysis.earlier_hidden_periods.value
    kept = _kept_links(p, view, links)

    def _apply():
        if seq != analysis.earlier_seq:
            return  # a newer refresh (switch, distance, tick) started meanwhile
        if kept != links and tuple(analysis.links.value) == links:
            # not ``set_links``: opening an alert must not mark it unsaved,
            # and the Save drops broken links anyway
            analysis.links.set(kept)
        analysis.history.set(summary)
        analysis.parent_windows.set(windows)
        panes = getattr(widgets, "panes", None)
        if panes is not None:
            if analysis.show_earlier_reviews.value:
                panes.add(layer_spec(view, hidden))  # replaces the previous overlay (same id)
            else:
                panes.remove(EARLIER_ID)
        if on_result:
            on_result(view)

    analysis.apply_if_current(token, _apply)


def _refresh_logged(widgets, analysis, app_state, **kw) -> None:
    """``refresh_earlier`` for a worker thread: a failure is logged (as
    ``view_actual_alert`` does for the inline path), not left to die in the
    thread."""
    try:
        refresh_earlier(widgets, analysis, app_state, **kw)
    except Exception:
        logger.exception("Analysis: the earlier-alerts section failed")


def refresh_in_background(widgets, analysis, app_state) -> None:
    """``refresh_earlier`` for a caller on the UI / navigation thread: a
    removal (switch off) is immediate, the load goes to a worker (it runs with
    the switch off too: the History row and the timeline read it)."""
    if not analysis.show_earlier_reviews.value:
        remove_earlier_layer(widgets)
    spawn_in_context(_refresh_logged, (widgets, analysis, app_state))


def badge_text(on, view, km):
    """The section's badge: "off", what the last refresh found, or nothing
    while a refresh is running (``view`` is None)."""
    if not on:
        return t("analysis_tile.earlier.badge_off")
    if view is None:
        return ""
    if view.n_within == 0 and view.n_linked == 0:
        return t("analysis_tile.earlier.badge_none", km=f"{km:g}")
    return tn("analysis_tile.earlier.badge_nearby", view.n_within, km=f"{km:g}",
              linked=format_number(view.n_linked))


def _zoom_for(bounds) -> float:
    """The zoom that fits ``bounds`` (degrees) in about half a view."""
    minx, miny, maxx, maxy = bounds
    span = max(maxx - minx, maxy - miny, 1e-7)
    return max(3, min(18, math.floor(math.log2(360.0 * _ZOOM_PX / 256.0 / span))))


def _status_chip(status):
    swatch = v.Html(tag="span", style_=("width: 8px; height: 8px; border-radius: 2px; display: inline-block;"
                                        f" margin-right: 4px; background: {status_color(status)};"))
    return v.Chip(x_small=True, outlined=True, children=[swatch, t(status_label_key(status))])


def build_earlier_controls(widgets, app_state, analysis, *, labels=None):
    """The *Earlier alerts* section: the switch, the distance, the *Periods*
    menu, the list and the period legend, under a header whose badge says what
    was found. Returns the ``build_section`` namespace plus ``switch``,
    ``field``, ``periods`` (the menu), ``items`` (the list's container),
    ``groups`` (the rows on screen, by period) and ``copy_lines``. ``labels``
    is the screen's ``labels.Labels`` (a fresh one when none is given): the
    section's words follow a language switch."""
    labels = labels if labels is not None else Labels()
    section = build_section(t("analysis_tile.earlier.title"), v.Html(tag="div"))
    switch = v.Switch(v_model=bool(analysis.show_earlier_reviews.value), dense=True,
                      class_="mt-0 pt-0", hide_details=True, inset=True,
                      label=t("analysis_tile.earlier.show"))
    field_ = v.TextField(v_model=str(analysis.earlier_radius_km.value), type="number",
                         suffix="km", dense=True, hide_details="auto", outlined=True,
                         style_="max-width: 110px;", error_messages=[])
    radius = v.Html(tag="span", class_=TYPE_BODY, children=[t("analysis_tile.earlier.radius")])
    distance = v.Html(tag="div", class_="d-flex align-center mt-3", style_="gap: 12px;",
                      children=[radius, field_])
    periods = v.Select(items=[], v_model=[], multiple=True, dense=True, outlined=True, hide_details=True,
                       class_="mt-3", label=t("analysis_tile.earlier.periods"))
    copy_lines = v.Html(tag="div", class_="mt-2", children=[])
    items = v.Html(tag="div", class_="mt-2", children=[])
    legend = v.Html(tag="div", class_="mt-3", style_="display: none;", children=[])
    section.body_box.children = [v.Html(tag="div", children=[switch, distance, periods, copy_lines, items,
                                                             legend])]
    last = {"view": None, "token": None}
    syncing = {"menu": False, "switch": False}

    def _sync_badge():
        on = bool(analysis.show_earlier_reviews.value)
        section.set_badge(badge_text(on, last["view"], analysis.earlier_radius_km.value))

    def _refresh():
        _refresh_logged(widgets, analysis, app_state, on_result=_result)

    def _on_tick(link, change):
        current = list(analysis.links.value)
        set_links(analysis, current + [link] if change["new"] else [lk for lk in current if lk != link])
        spawn_in_context(_refresh)  # the view follows the draft (a cache hit)

    def _zoom(shape):
        panes = getattr(widgets, "panes", None)
        if panes is None or shape is None:
            return
        c = shape.centroid
        panes.set_view((c.y, c.x), _zoom_for(shape.bounds))

    def _row(it):
        n = it.cand.node
        bar = v.Html(tag="span", style_=f"width: 4px; height: 28px; border-radius: 2px; flex: none;"
                                        f" background: {it.color};")
        box = None
        if it.state == "via":
            lead = v.Html(tag="span", style_="width: 24px; flex: none;")
        else:
            # a link to a parent that has since become a false positive stays
            # stored (Ruling 13): ticked, and the reviewer can untick it; one
            # broken by a re-run names nothing left to tick
            fp = it.state == "broken" and n.status == FALSE_POSITIVE
            box = v.Checkbox(v_model=it.state in _LINKED or fp, disabled=it.state == "broken" and not fp,
                             dense=True, hide_details=True, class_="ma-0 pa-0")
            box.observe(lambda change, link=it.cand.link: _on_tick(link, change), names="v_model")
            lead = box
        text = v.Html(tag="span", class_=f"{TYPE_BODY} dat-num", style_="cursor: pointer;",
                      children=[f"#{n.alert_id} · {format_number(n.area_ha, 1)} ha"])
        text.on_event("click", lambda *_, shape=n.shape: _zoom(shape))
        note = v.Html(tag="span", class_=TYPE_LABEL, children=[note_text(it)])
        root = v.Html(tag="div", class_="d-flex align-center",
                      style_="gap: 6px; padding: 4px 0;" + (" opacity: 0.5;" if it.state == "broken" else ""),
                      children=[bar, lead, text, _status_chip(n.status), note])
        return SimpleNamespace(key=it.cand.key, link=it.cand.link, root=root, box=box, text=text, note=note)

    def _render():
        view = last["view"]
        hidden = analysis.earlier_hidden_periods.value
        p = app_state.project.value
        # the Periods menu: every period of the view, those not hidden ticked
        syncing["menu"] = True
        try:
            shown_periods = list(view.periods) if view is not None else []
            periods.items = [{"text": period_label(start), "value": pid} for pid, start in shown_periods]
            periods.v_model = [pid for pid, _ in shown_periods if pid not in hidden]
        finally:
            syncing["menu"] = False
        periods.style_ = "" if len(shown_periods) > 1 else "display: none;"
        # a review copy says how old its snapshot is, and when it has nothing
        lines = []
        rc = getattr(p, "review_copy", None)
        if rc is not None and view is not None:
            if getattr(rc, "snapshot_at", None) is not None:
                lines.append(t("analysis_tile.earlier.as_of", date=format_date(rc.snapshot_at.date())))
            if not view.periods:
                lines.append(t("analysis_tile.earlier.none_in_copy"))
        copy_lines.children = [v.Html(tag="div", class_=TYPE_LABEL, children=[line]) for line in lines]
        # the rows, under their period
        groups = []
        visible = [it for it in (view.items if view is not None else ()) if it.cand.node.period_id not in hidden]
        for pid, group in itertools.groupby(visible, key=lambda it: it.cand.node.period_id):
            group = list(group)
            header = v.Html(tag="div", class_=f"{TYPE_LABEL} mt-2",
                            children=[period_label(group[0].cand.node.period_start)])
            groups.append(SimpleNamespace(period_id=pid, header=header, rows=[_row(it) for it in group]))
        section.groups = groups
        items.children = [w for g in groups for w in [g.header, *(r.root for r in g.rows)]]
        # the legend: the periods drawn, while the overlay is on
        on = bool(analysis.show_earlier_reviews.value)
        drawn = [(pid, start) for pid, start in shown_periods if pid not in hidden]
        colours = growth_index.period_colors([pid for pid, _ in shown_periods])
        legend.children = [
            *(v.Html(tag="span", class_=f"d-inline-flex align-center {TYPE_LABEL}", children=[
                v.Html(tag="span", style_=f"width: 14px; border-top: 3px solid {colours[pid]};"
                                          " display: inline-block; margin-right: 5px;"),
                period_label(start)]) for pid, start in drawn),
            v.Html(tag="span", class_=TYPE_LABEL, children=[t("analysis_tile.earlier.legend_lines")]),
        ]
        legend.style_ = _LEGEND_SHOWN if on and drawn else "display: none;"
        _sync_badge()

    def _result(view):
        if view is None:
            # a refresh started: the badge waits for it; the rows stay while
            # it is for the same alert (a tick), not for the next one
            if last["token"] != analysis.alert_token.value:
                last["view"] = None
                _render()
            else:
                section.set_badge(badge_text(bool(analysis.show_earlier_reviews.value), None, 0))
            return
        last["view"], last["token"] = view, analysis.alert_token.value
        _render()

    def _on_switch(change):
        analysis.show_earlier_reviews.set(bool(change["new"]))
        _sync_badge()
        if not syncing["switch"]:
            spawn_in_context(_refresh)

    def _on_km(change):
        km = parse_km(change["new"])
        if km is None:
            field_.error_messages = [t("analysis_tile.earlier.radius_error")]
            return
        field_.error_messages = []
        if km == analysis.earlier_radius_km.value:
            return
        analysis.earlier_radius_km.set(km)
        spawn_in_context(_refresh)

    def _on_periods(change):
        if syncing["menu"] or last["view"] is None:
            return
        listed = {pid for pid, _ in last["view"].periods}
        hidden = frozenset((analysis.earlier_hidden_periods.value - listed) | (listed - set(change["new"] or [])))
        analysis.earlier_hidden_periods.set(hidden)
        _render()
        view, token = last["view"], last["token"]

        def _redraw():
            panes = getattr(widgets, "panes", None)
            if panes is not None and analysis.show_earlier_reviews.value:
                panes.add(layer_spec(view, hidden))

        analysis.apply_if_current(token, _redraw)

    def _on_auto(auto):
        # A growth alert opens with its parents on the maps (spec §6.3). No
        # refresh of its own: the alert is opening (``view_actual_alert``
        # seeds ``auto_links``), and the open's ``refresh_in_background``,
        # which now sees the switch on, draws the layer.
        if auto and not switch.v_model:
            syncing["switch"] = True
            try:
                switch.v_model = True
            finally:
                syncing["switch"] = False

    shown_style = section.root.style_

    def _sync_shown(*_):
        shown = has_earlier_sets(app_state.project.value)
        section.root.style_ = shown_style if shown else "display: none;"
        if not shown and switch.v_model:
            switch.v_model = False  # nothing to draw; ``_on_switch`` clears the overlay

    widgets.earlier_on_result = _result
    switch.observe(_on_switch, names="v_model")
    field_.observe(_on_km, names="v_model")
    periods.observe(_on_periods, names="v_model")
    analysis.auto_links.subscribe(_on_auto)

    def _label():
        section.set_title(t("analysis_tile.earlier.title"))
        switch.label = t("analysis_tile.earlier.show")
        radius.children = [t("analysis_tile.earlier.radius")]
        periods.label = t("analysis_tile.earlier.periods")
        if field_.error_messages:
            field_.error_messages = [t("analysis_tile.earlier.radius_error")]
        _render()

    section.groups = []
    # in place of a direct first ``_render()``: ``add`` runs it now
    labels.add(_label)
    app_state.project.subscribe(_sync_shown)
    _sync_shown()
    section.switch, section.field, section.periods, section.items = switch, field_, periods, items
    section.copy_lines = copy_lines
    return section
