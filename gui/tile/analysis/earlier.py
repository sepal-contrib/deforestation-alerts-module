"""The Analysis overlay of reviewed alerts from earlier periods (spec §9).

One read-only ``GeoJSON`` layer (outline only, coloured by review status) on
both maps, drawn from ``earlier_reviews.near_reviews``. ``refresh_earlier_reviews``
blocks on the first read of each earlier set's store, so it runs on a worker
(``view_actual_alert``'s, or ``spawn_in_context`` for the switch and the
distance). Its result is applied under ``AnalysisState.apply_if_current``: an
answer for an alert that is no longer the current one is dropped.

No hover tooltip: nothing in the Analysis panel does hover or popups, so the
layer stays a plain outline.
"""

import logging

import ipyvuetify as v
from ipyleaflet import GeoJSON

from deforestation_alerts.engine.pmtiles import STATUS_CODES
from gui.i18n import t
from gui.scripts import earlier_reviews
from gui.scripts.legend_specs import status_colors
from gui.scripts.solara_threads import spawn_in_context
from gui.store.workflow_steps import active_set

logger = logging.getLogger("SEPALUI.deforestation_alerts")

EARLIER_LAYER_NAME = "Earlier reviews"
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


def _maps(widgets):
    return [m for m in (getattr(widgets, "map_before", None), getattr(widgets, "map_after", None))
            if m is not None]


def _drop(a_map) -> None:
    with getattr(a_map, "layers_lock", None) or _Null():
        a_map.layers = tuple(lyr for lyr in a_map.layers
                             if getattr(lyr, "name", None) != EARLIER_LAYER_NAME)


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def remove_earlier_layer(widgets) -> None:
    for a_map in _maps(widgets):
        _drop(a_map)


def refresh_earlier_reviews(widgets, analysis, app_state, *, on_result=None) -> None:
    """Redraw (or remove) the overlay for the current alert. Blocking: call it
    from a worker. ``on_result(n)`` gets the feature count (None when off);
    by default the caption the controls registered on ``widgets``."""
    on_result = on_result or getattr(widgets, "earlier_on_result", None)
    if not analysis.show_earlier_reviews.value:
        remove_earlier_layer(widgets)
        if on_result:
            on_result(None)
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
    layer = GeoJSON(data=data, style={"weight": 2, "fill": False}, style_callback=_style,
                    name=EARLIER_LAYER_NAME)

    def _apply():
        for a_map in _maps(widgets):
            _drop(a_map)
            a_map.add_layer(layer)
        if on_result:
            on_result(len(data["features"]))

    analysis.apply_if_current(token, _apply)


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
        refresh_earlier_reviews(widgets, analysis, app_state, on_result=_result)

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
    return v.Card(class_="pa-3 ma-2", hover=True, children=[switch, row, caption])
