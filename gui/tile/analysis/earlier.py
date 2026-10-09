"""The Analysis overlay of reviewed alerts from earlier periods (spec §9).

One read-only GeoJSON layer spec (outline only, coloured by review status:
each feature carries its colour in ``properties.style``) on every view, drawn
from ``earlier_reviews.near_reviews``. ``refresh_earlier_reviews``
blocks on the first read of each earlier set's store, so it runs on a worker
(``view_actual_alert``'s, or ``spawn_in_context`` for the switch and the
distance). Its result is applied under ``AnalysisState.apply_if_current``: an
answer for an alert that is no longer the current one is dropped.

No hover tooltip: nothing in the Analysis panel does hover or popups, so the
layer stays a plain outline.
"""

import logging

import ipyvuetify as v

from deforestation_alerts.engine.pmtiles import STATUS_CODES
from gui.i18n import t
from gui.scripts import earlier_reviews
from gui.scripts.legend_specs import status_colors
from gui.scripts.solara_threads import spawn_in_context
from gui.store.workflow_steps import active_set
from gui.tile.analysis.pane_layers import geojson_spec
from gui.tile.analysis.section import panel_block

logger = logging.getLogger("SEPALUI.deforestation_alerts")

EARLIER_LAYER_NAME = "Earlier reviews"
#: The overlay's spec id on the views.
EARLIER_ID = "earlier"
MIN_KM, MAX_KM = 0.1, 50.0


def parse_km(text):
    """The distance typed in the field as a float within 0.1-50, else None."""
    try:
        km = float(str(text).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return km if MIN_KM <= km <= MAX_KM else None


def _style(feature, **_):
    colours = status_colors()
    code = STATUS_CODES.get((feature.get("properties") or {}).get("status"), 0)
    return {"color": colours[code if code in colours else 0], "weight": 2, "fill": False, "fillOpacity": 0}


def remove_earlier_layer(widgets) -> None:
    """Take the overlay off the views (a no-op without views or without it)."""
    panes = getattr(widgets, "panes", None)
    if panes is not None:
        panes.remove(EARLIER_ID)


def refresh_earlier_reviews(widgets, analysis, app_state, *, on_result=None) -> None:
    """Redraw (or remove) the overlay for the current alert. Blocking: call it
    from a worker. ``on_result(n)`` gets the feature count (None when off);
    by default the caption the controls registered on ``widgets``."""
    on_result = on_result or getattr(widgets, "earlier_on_result", None)
    with analysis._alert_lock:
        # every refresh, the switch-off included, supersedes the ones in flight
        analysis.earlier_seq += 1
        seq = analysis.earlier_seq
    if on_result:
        on_result(None)  # the previous alert's / distance's caption is stale
    if not analysis.show_earlier_reviews.value:
        remove_earlier_layer(widgets)
        return
    gdf = app_state.alerts_gdf.value
    alert_id = app_state.actual_alert_id.value
    p = app_state.project.value
    s = active_set(p)
    if gdf is None or alert_id is None or not 0 <= alert_id < len(gdf) or s is None:
        remove_earlier_layer(widgets)
        return
    token = analysis.alert_token.value
    km = analysis.earlier_radius_km.value
    point = gdf.iloc[alert_id].point
    data = earlier_reviews.near_reviews(p, s.id, point.x, point.y, km * 1000.0)
    # Each feature carries its status colour in ``properties.style``, which
    # the views merge over the layer's ``style`` (``layer_view.js``).
    styled = {**data, "features": [
        {**f, "properties": {**(f.get("properties") or {}), "style": _style(f)}} for f in data["features"]]}
    spec = geojson_spec(EARLIER_ID, "all", styled, name=EARLIER_LAYER_NAME, group="earlier",
                        style={"weight": 2, "fill": False})

    def _apply():
        if seq != analysis.earlier_seq:
            return  # a newer refresh (switch, distance) started meanwhile
        panes = getattr(widgets, "panes", None)
        if panes is not None:
            panes.add(spec)  # replaces the previous overlay (same id)
        if on_result:
            on_result(len(data["features"]))

    analysis.apply_if_current(token, _apply)


def _refresh_logged(widgets, analysis, app_state, **kw) -> None:
    """``refresh_earlier_reviews`` for a worker thread: a failure is logged
    (as ``view_actual_alert`` does for the inline path), not left to die in
    the thread."""
    try:
        refresh_earlier_reviews(widgets, analysis, app_state, **kw)
    except Exception:
        logger.exception("Analysis: the earlier-reviews overlay failed")


def refresh_in_background(widgets, analysis, app_state) -> None:
    """``refresh_earlier_reviews`` for a caller on the UI / navigation thread:
    the removal (switch off) is immediate, a load goes to a worker."""
    if analysis.show_earlier_reviews.value:
        spawn_in_context(_refresh_logged, (widgets, analysis, app_state))
    else:
        refresh_earlier_reviews(widgets, analysis, app_state)


def build_earlier_controls(widgets, app_state, analysis) -> v.Html:
    """The switch, the distance field and the "none found" caption."""
    switch = v.Switch(v_model=bool(analysis.show_earlier_reviews.value), dense=True,
                      class_="mt-0 pt-0", hide_details=True,
                      label=t("analysis_tile.earlier.show"))
    field_ = v.TextField(v_model=str(analysis.earlier_radius_km.value), type="number",
                         suffix="km", dense=True, hide_details="auto", outlined=True,
                         label=t("analysis_tile.earlier.radius"), style_="max-width: 150px;",
                         error_messages=[])
    caption = v.Html(tag="div", class_="caption mt-1", children=[], style_="display: none;")

    def _result(n):
        if n == 0:
            caption.children = [t("analysis_tile.earlier.none", km=f"{analysis.earlier_radius_km.value:g}")]
            caption.style_ = ""
        else:
            caption.children = []
            caption.style_ = "display: none;"

    def _refresh():
        _refresh_logged(widgets, analysis, app_state, on_result=_result)

    def _on_switch(change):
        analysis.show_earlier_reviews.set(bool(change["new"]))
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
        if analysis.show_earlier_reviews.value:
            spawn_in_context(_refresh)

    widgets.earlier_on_result = _result
    switch.observe(_on_switch, names="v_model")
    field_.observe(_on_km, names="v_model")
    row = v.Html(tag="div", class_="d-flex align-center", style_="gap: 12px;",
                 children=[field_])
    return panel_block([switch, row, caption])
