"""Analysis tile parent — raw ipyvuetify layout builder (Phase-4 Solara port).

Architecture layer: view (raw-widget builder, no ``@solara.component``). This is
the parent slice of porting the 2052-line ipyvuetify ``AnalysisTile`` god-class.
It establishes:

  * the shared heavy widgets (``AnalysisWidgets``, built once),
  * the two-map left panel + control right panel layout (ports OLD
    ``initialize_layout``), as a raw ipyvuetify ``v.Container`` tree,
  * the per-alert orchestration ``view_actual_alert`` registered as a
    ``app_state.actual_alert_id.subscribe`` (replacing the former ``use_effect``)
    plus one immediate call so a pre-selected alert renders on first build,
  * the navigation row (``build_navigator``),
  * and the sub-component builders (imagery / review / edit / export).

WHY A RAW BUILDER (not a ``@solara.component`` + ``render_fixed`` /
``solara.display``): MapApp renders the Analysis step content with ``v-if``, so it
DESTROYS the step DOM on close and REBUILDS it on reopen. A ``@solara.component``
rendered via ``render_fixed`` + ``solara.display`` goes blank on reopen, but a raw
ipyvuetify widget tree is re-viewed from its model on reopen, so it survives.
``build_analysis_layout`` builds the tree once and RETURNS ``(stage, controls)``
— the step content container and the right-panel controls — same pattern
as the sibling builders (``navigator`` / ``review_panel`` / ``build_edit_tools`` /
``build_export`` / ``build_imagery``) and ``recipe_tile.build_recipe_layout`` /
``aux_tile.build_aux_layout``. The CSS the OLD component injected via
``solara.Style`` (a hook) is now the first child of the returned container as a raw
``v.Html(tag="style", ...)`` element. The two former ``use_effect``s become
``.subscribe`` registrations on ``app_state.actual_alert_id`` /
``app_state.alerts_gdf`` (each also invoked once immediately).

Key architectural decisions (FIXED by the migration contract):

* **``app_state`` (AppState) is the source of truth** for the alerts table +
  navigation, NOT the traitlets model. The Filter tile publishes the assembled
  alerts GeoDataFrame to ``app_state.alerts_gdf.value`` and the user index lives
  in ``app_state.actual_alert_id.value``. So this tile reads alerts from
  ``app_state.alerts_gdf.value``, the current index from
  ``app_state.actual_alert_id.value``, and navigates via
  ``app_state.go_to/go_next/go_prev``. The OLD ``create_gdf_partial/full`` (which
  built ``analyzed_alerts_model.alerts_gdf`` from the selected-alerts traits) is
  superseded by that publish and is NOT ported (see TODO(recipe-load) below for
  the deferred re-discovery/merge path).
* **``analysis`` (``AnalysisState``, spec §10)** holds the per-session review
  state as Solara reactives: the chosen images and their info, the drawn defo
  layer, the CNN predictions, ``last_save_time``. ``view_actual_alert`` resets
  it on every alert change (``reset_alert``) and reads the Planet image lists.
* **Heavy widgets are imperative + memoized once.** The two maps / draw controls /
  AOI control / 3-tab selectors live on ``AnalysisWidgets`` and are mutated in
  place by ``view_actual_alert``; the per-alert effect is keyed on the alert id
  reactive so it re-runs on navigation. Guarded to no-op when no alerts are loaded
  (safe at smoke time + idempotent under render_fixed double-mount).

Deferred to later tasks (TODO-marked): the imagery slide-group rebuilds + S2 /
Landsat dictionary tasks (Task 9.2), draw-edit behavior (9.3), CNN runs (9.4), and
save/export (9.5).
"""

import logging

import ee
import ipyvuetify as v
from ipyleaflet import GeoJSON

from deforestation_alerts.engine.fingerprint import Status, set_status
from deforestation_alerts.message import cm
from deforestation_alerts.project import paths
from deforestation_alerts.scripts.mosaics_helper import (
    convert_julian_to_date,
    multipolygon_to_geodataframe,
)
from gui.tile.analysis.review_panel import build_review_panel
from gui.tile.analysis.edit_tools import build_edit_tools
from gui.tile.analysis.export import build_export
from gui.tile.analysis.imagery import build_imagery
from gui.tile.analysis.navigator import build_navigator
from gui.i18n import t
from gui.store.workflow_steps import active_set
from gui.tile.analysis.widgets import AnalysisWidgets, info_button

logger = logging.getLogger("SEPALUI.deforestation_alerts.analysis")

# Alert bounding-box overlay style (OLD view_actual_alert geojson_layer).
_ALERT_BB_STYLE = {
    "color": "yellow",
    "fillColor": "#3366cc",
    "opacity": 0.5,
    "weight": 1,
    "dashArray": "2",
    "fillOpacity": 0,
}


def refresh_alert_info(widgets, app_state):
    """Fill the Alert info card (source, dates, status) for the selected alert.

    Returns the alert's ``(first date, last date)`` as shown, or None with no
    alert selected (the card is left as it is). Run on every alert change and
    again when the alerts are rebound with the same alert still selected: a
    review Save writes the status into the row and then rebinds
    ``alerts_gdf`` to a copy (``export._apply_save``), and the card kept
    reading "Not reviewed" until the next alert change.
    """
    gdf = app_state.alerts_gdf.value
    alert_id = app_state.actual_alert_id.value
    if gdf is None or alert_id is None or alert_id < 0 or alert_id >= len(gdf):
        return None
    alerta = gdf.iloc[alert_id]
    fecha1 = convert_julian_to_date(alerta["alert_date_min"])
    fecha2 = convert_julian_to_date(alerta["alert_date_max"])
    alert_source = alerta["alert_sources"]

    if alerta["status"] != "Not reviewed":
        alert_st = cm.analysis_tile.alert_info.status_reviewed
    else:
        alert_st = cm.analysis_tile.alert_info.status_not_reviewed

    widgets.selected_alert_info.children = [
        v.Html(tag="strong", children=[cm.analysis_tile.alert_info.alert_source]),
        str(alert_source),
        v.Html(tag="br"),
        v.Html(tag="strong", children=[cm.analysis_tile.alert_info.first_date]),
        fecha1,
        v.Html(tag="br"),
        v.Html(tag="strong", children=[cm.analysis_tile.alert_info.last_date]),
        fecha2,
        v.Html(tag="br"),
        v.Html(tag="strong", children=[cm.analysis_tile.alert_info.status]),
        alert_st,
    ]
    return fecha1, fecha2


def view_actual_alert(
    widgets, app_state, analysis, app_tile_model, id_field
):
    """Per-alert orchestration owned by the foundation.

    Ports the foundation-relevant parts of the OLD ``view_actual_alert``: select
    the alert row, refresh the alert-info card + nav id field, forget the
    previous alert's review state (``analysis.reset_alert``), rebuild the bbox overlay on both maps, center/zoom, compute
    the ee bbox + covering grid (stored on ``widgets``), register the AOI, set the
    default image-collection flags, and remember the position on the active
    alert set (``SetContext.remember_position``; spec §10 replaces the recipe
    json's ``actual_alert_id``).

    Guards: returns early when no alerts are loaded or the id is out of range — so
    it is safe at smoke time (no EE/imagery work runs) and idempotent under the
    render_fixed double-mount. The heavy imagery loading + slide-group rebuilds are
    deferred to Task 9.2 (AlertImagery).
    """
    gdf = app_state.alerts_gdf.value
    alert_id = app_state.actual_alert_id.value

    if gdf is None or alert_id is None or alert_id < 0 or alert_id >= len(gdf):
        return

    alerta = gdf.iloc[alert_id]

    # --- alert-info card (source / dates / status) ---
    fecha1, fecha2 = refresh_alert_info(widgets, app_state)

    # --- sync the nav id field ---
    id_field.v_model = alert_id

    # --- forget the previous alert's drawn layer, chosen images and CNN
    # predictions: none of them may survive an alert change (the images are
    # chosen again by the imagery panes below) ---
    analysis.reset_alert()

    # --- remember the position on the alert set (auto-saved with the project) ---
    remember = getattr(app_tile_model, "remember_position", None)
    if remember is not None:
        try:
            remember(alert_id)
        except Exception:
            logger.exception("Analysis: failed to remember the alert position")

    # ------------------------------------------------------------------
    # EE-touching map work. Wrapped so a transient EE failure (or a malformed
    # geometry) doesn't break navigation; with no alerts loaded we already
    # returned above, so this only runs for a real alert.
    # ------------------------------------------------------------------
    try:
        alerta_bb_geojson = alerta["bounding_box"].__geo_interface__

        # hide the draw controls between alerts (Task 9.3 shows them on edit)
        widgets.draw_before.hide()
        widgets.draw_after.hide()

        # center/zoom (the two maps are jslink'd; write both explicitly)
        widgets.set_synced_view((alerta.point.y, alerta.point.x), 16)

        # clear + rebuild the bbox overlay on both maps
        widgets.map_before.remove_all()
        widgets.map_after.remove_all()
        geojson_layer = GeoJSON(
            data=alerta_bb_geojson, style=_ALERT_BB_STYLE, name="Alert BB"
        )
        widgets.map_before.add_layer(geojson_layer)
        widgets.map_after.add_layer(geojson_layer)

        # re-add the saved defo polygon if this alert was already reviewed
        if (
            alerta["status"] in {"Confirmed", "Need revision"}
            and alerta["alert_polygon"] is not None
        ):
            analysis.defo_layer.set(multipolygon_to_geodataframe(
                alerta["alert_polygon"]
            ).set_crs(epsg="4326", allow_override=True, inplace=True))

        # ee bbox (buffer + bounds) + covering grid for downloads
        alerta_bb_geojson_ee = ee.Feature(alerta_bb_geojson).buffer(100, 1).bounds(1)
        widgets.aoi_control.add_aoi("AOI", alerta_bb_geojson_ee)
        widgets.actual_bb = alerta_bb_geojson_ee

        grid_descarga = alerta_bb_geojson_ee.geometry().coveringGrid(
            "EPSG:3857", 1 * 256 * 4.77
        )
        widgets.actual_alert_grid = grid_descarga.geometry().bounds(1)

        # default image-collection load order (Planet, then S2 fallback). The
        # planet dicts are populated by the Recipe/Filter tile; guard if absent.
        widgets.show_image_collection = [True, False, False]
        before_planet = analysis.before_planet.value
        after_planet = analysis.after_planet.value
        if before_planet and before_planet[0].get("value") == "Not available":
            widgets.show_image_collection = [False, True, False]
        if after_planet and after_planet[0].get("value") == "Not available":
            widgets.show_image_collection = [False, True, False]

        # Task 9.2 (AlertImagery) handoff: publish the per-alert imagery context.
        # AlertImagery keys its Planet slide-group build + S2/Landsat use_tasks on
        # this reactive, so the slide-group rebuilds and EE fetches only run once a
        # fresh context exists (and the auto-switch to the Sentinel-2 tab when
        # Planet is "Not available" lands there). The grid/bbox/dates computed above
        # are passed through so the worker tasks never re-touch this thread's state.
        widgets.imagery_ctx.value = {
            "alert_id": alert_id,
            "grid": widgets.actual_alert_grid,
            "fecha1": fecha1,
            "fecha2": fecha2,
            "before_planet": before_planet,
            "after_planet": after_planet,
            "show": list(widgets.show_image_collection),
        }
    except Exception:
        logger.exception("Analysis: view_actual_alert map update failed")


def select_start_alert(app_state, app_tile_model):
    """Select an alert when alerts arrive with none selected: the active set's
    saved position (``go_to`` clamps it), else the first alert.

    Starting from the saved position matters because a set load publishes its
    alerts before it reads ``last_alert_index``: going to 0 here would make
    ``view_actual_alert`` save 0 over that position first. The recipe-era
    models carry no ``alert_set``, so they start at 0. Only auto-selects from
    the unset (-1) state, so it never fights manual navigation.
    """
    gdf = app_state.alerts_gdf.value
    if gdf is not None and len(gdf) > 0 and app_state.actual_alert_id.value < 0:
        start = getattr(getattr(app_tile_model, "alert_set", None), "last_alert_index", 0)
        app_state.go_to(start)


def wire_alert_navigation(widgets, app_state, analysis, app_tile_model, id_field) -> None:
    """The subscriptions that drive the per-alert view (``build_analysis_layout``
    registers them once; the layout is built once per session).

    - ``actual_alert_id``: the per-alert orchestration re-runs whenever the
      current alert changes (navigation drives it), plus one immediate call so
      a pre-selected alert renders on first build. ``view_actual_alert`` no-ops
      with no alerts loaded, so this is safe at mount/smoke time and idempotent.
    - ``alerts_gdf``, first: the Alert info card is refreshed for the alert
      still selected when the alerts are rebound (a review Save, see
      ``refresh_alert_info``); with none selected it does nothing.
    - ``alerts_gdf``, then: an alert is selected when a set first arrives (a
      publish leaves ``actual_alert_id`` at -1, and the tile would otherwise
      stay empty until "next"), plus one immediate call. Registered after the
      refresh so a new set's first alert is drawn once, by the selection.
    """

    def _on_alert_change(*_):
        view_actual_alert(widgets, app_state, analysis, app_tile_model, id_field)

    app_state.actual_alert_id.subscribe(_on_alert_change)
    view_actual_alert(widgets, app_state, analysis, app_tile_model, id_field)

    def _refresh_info_cb(*_):
        refresh_alert_info(widgets, app_state)

    app_state.alerts_gdf.subscribe(_refresh_info_cb)

    def _select_first_alert_cb(*_):
        select_start_alert(app_state, app_tile_model)

    app_state.alerts_gdf.subscribe(_select_first_alert_cb)
    _select_first_alert_cb()


#: set status -> the banner's message key (the same text Overview shows)
_SET_STATUS_KEYS = {Status.STALE: "steps.overview.stale", Status.MISSING: "steps.overview.missing"}


def build_set_status_banner(app_state) -> v.Html:
    """The spec §5.3 banner over the Analysis controls: the active set was
    built from inputs that have changed since (out of date), or its files are
    gone (missing), in the words Overview uses. It never blocks the review
    (rebuilding discards it). A raw widget, like the rest of the layout,
    updated on every project publish: the status is the manifest plus one
    ``os.stat``, so the subscriber is cheap and calls nothing back."""
    banner = v.Html(tag="div", class_="warning--text px-4 py-2", children=[], style_="display: none;")

    def _sync(*_):
        p = app_state.project.value
        s = active_set(p)
        status = set_status(p, paths.project_dir(p.name), s) if s is not None else None
        key = _SET_STATUS_KEYS.get(status)
        banner.children = [t(key)] if key else []
        banner.style_ = "" if key else "display: none;"

    app_state.project.subscribe(_sync)
    _sync()
    return banner


def build_analysis_layout(
    aux_model,
    selected_alerts_model,
    analysis,
    app_tile_model,
    app_state,
    gee_interface,
    theme_state=None,
):
    """Build the raw ipyvuetify analysis layout (parent of the analysis tile).

    Returns ``(stage, controls)`` — the two-map workspace ``v.Container`` and the
    right-panel controls ``v.Html``. The shell renders ``stage`` as the MapApp step
    content and ``controls`` in the docked right panel (wrapper 7). Built once (no
    re-render) so the sub-builders' handlers + subscriptions are wired exactly once
    and the raw widget trees survive the step container's ``v-if`` rebuild.

    Parameters
    ----------
    aux_model : SetContext
        ``custom_report_template`` (the project's; used by ``build_export``).
    selected_alerts_model : SetContext
        ``combined_path``, the set's combined raster: the "Original Alert"
        layer (used by ``build_imagery``).
    analysis : AnalysisState
        The session's review state (chosen images and their info, drawn defo
        layer, CNN predictions, ``last_save_time``), handed to every
        sub-builder. The parent resets it per alert and reads the Planet image
        lists (in ``view_actual_alert``).
    app_tile_model : SetContext
        ``recipe_folder_path`` (the active set's folder) for the output paths,
        ``alert_set`` + ``remember_position`` for the review position and
        ``record_review`` for the reviewed count. The shell passes the same
        ``SetContext`` for all three models (spec §10).
    app_state : AppState
        Source of truth for the alerts table + navigation.
    gee_interface : GEEInterface
        Session GEE interface, forwarded to both maps.
    theme_state : ThemeState, optional
        Shared app theme state, forwarded to both maps so they track the app's
        light/dark theme exactly as the shared main map does.
    """
    # Build the shared heavy widgets once.
    widgets = AnalysisWidgets(gee_interface=gee_interface, theme_state=theme_state)

    # Alert-id field (raw ipyvuetify so view_actual_alert can imperatively set
    # .v_model). Owned here, passed to build_navigator + the subscription.
    id_field = v.TextField(
        v_model=app_state.actual_alert_id.value,
        outlined=True,
        single_line=True,
        hide_details=True,
    )

    # Full-stage analysis workspace (Option C / variant 1). The stage fills the
    # MapApp step container; two equal map columns sit side by side, each map
    # fills the stage height (viewport-relative), with a compact internally-scrolling
    # image-selector strip beneath it. Was injected via solara.Style; lives here as the stage's first
    # child so the raw widget tree survives the step container's v-if rebuild.
    style_el = v.Html(
        tag="style",
        children=[
            ".analysis-stage { height: 100%; display: flex; flex-direction: column; } "
            # Stage background follows the app light/dark theme. The default is the
            # light surface; the ``analysis-stage--dark`` marker class (toggled from
            # ``theme_state.dark`` below — the SAME trait the maps bind to) swaps it
            # to the dark surface so the workspace matches the rest of the app.
            ".analysis-stage { background-color: #ffffff; transition: background-color 0.2s; } "
            ".analysis-stage.analysis-stage--dark { background-color: #121212; } "
            # Outline drawing the limits of each image-selector strip. The border
            # colour tracks the theme (subtle dark line on light, subtle light line
            # on dark) via the same ``--dark`` marker on the stage. The maps are NOT
            # bordered: a light line framing the near-black map panels reads as a
            # harsh white border, so the maps draw their own limit via the basemap.
            ".analysis-stage .analysis-selector { border: 1px solid rgba(0, 0, 0, 0.18); "
            "border-radius: 4px; } "
            ".analysis-stage.analysis-stage--dark .analysis-selector { "
            "border-color: rgba(255, 255, 255, 0.18); } "
            ".analysis-row { display: flex; gap: 8px; flex: 1 1 auto; min-height: 0; "
            "width: 100%; } "
            # min-width:0 is THE fix for the selector overflow: without it the flex
            # column defaults to min-width:auto, so the image slide group stretches
            # the column and resizes the maps. With it the column holds its half and
            # the slide group scrolls inside its strip.
            ".analysis-col { flex: 1 1 0; min-width: 0; overflow: hidden; "
            "display: flex; flex-direction: column; } "
            ".analysis-mapbox { display: flex; flex-direction: column; } "
            # The ipyleaflet map sits inside an auto-generated ipywidgets wrapper
            # (display:block) that breaks the flex-grow chain, so the map cannot be
            # grown to fill the column via flex. Use a viewport-relative fixed
            # height instead (the MapApp step container is full-viewport): tall maps
            # that fill the stage, leaving ~300px for the title + selector strip +
            # selected-image info line.
            ".custom-map-class2 { height: calc(100vh - 300px) !important; min-height: 320px; } "
            ".analysis-col .v-slide-group__content { flex-wrap: nowrap; } "
            ".v-text-field .v-input__control .v-input__slot { min-height: auto !important; "
            "min-width: 40px; display: flex !important; align-items: center !important; }"
        ],
    )

    wire_alert_navigation(widgets, app_state, analysis, app_tile_model, id_field)

    # TODO(recipe-load): the OLD tile rebuilt/merged alerts_gdf from the
    # selected-alerts traits here (create_gdf_partial/create_gdf_full + the
    # alerts_bbs/alerts_total_bbs observers). That is superseded by the Filter
    # tile publishing app_state.alerts_gdf. The recipe-LOAD re-discovery/merge
    # path (re-pointing previously reviewed alerts) is deferred to a later task.

    # ------------------------------------------------------------------
    # Layout: left two-map panel + right scrolling control panel (ports OLD
    # initialize_layout left_panel / right_panel), as a raw ipyvuetify tree.
    # ------------------------------------------------------------------
    # LEFT/STAGE: two equal map+selector columns side by side. Each column gets
    # min-width:0 (the overflow fix) so the slide group scrolls instead of
    # resizing the maps.
    left_inner_row = v.Html(
        tag="div",
        class_="analysis-row",
        children=[
            v.Html(
                tag="div",
                class_="analysis-col",
                children=[widgets.map31, widgets.imgSelection1],
            ),
            v.Html(
                tag="div",
                class_="analysis-col",
                children=[widgets.map32, widgets.imgSelection2],
            ),
        ],
    )

    # Imagery sub-component: owns the slide-group orchestration over the
    # panes/selectors above. Returns None (side-effect only: registers the
    # imagery_ctx subscription + fires once), so just call it.
    build_imagery(
        widgets=widgets,
        app_state=app_state,
        selected_alerts_model=selected_alerts_model,
        analysis=analysis,
    )

    # RIGHT/CONTROLS: alert-info + nav + review + edit + export cards. Rendered in
    # MapApp's docked right panel (a panel wrapper in the shell), NOT inside the
    # stage — same chrome as AOI/Filter/Overview.
    alert_info_card = v.Card(
        class_="pa-2 ma-2",
        hover=True,
        children=[
            v.CardTitle(
                class_="pa-0 ma-1",
                children=[cm.analysis_tile.alert_info.card_title],
            ),
            widgets.selected_alert_info,
        ],
    )
    controls = v.Html(
        tag="div",
        class_="pa-0 ma-0",
        children=[
            info_button(cm.analysis_tile.description, cm.analysis_tile.info_header, cm.analysis_tile.info_md),
            build_set_status_banner(app_state),
            alert_info_card,
            build_navigator(app_state=app_state, id_field=id_field),
            build_review_panel(
                widgets=widgets,
                app_state=app_state,
                analysis=analysis,
                app_tile_model=app_tile_model,
            ),
            build_edit_tools(
                widgets=widgets,
                app_state=app_state,
                analysis=analysis,
                app_tile_model=app_tile_model,
            ),
            build_export(
                widgets=widgets,
                app_state=app_state,
                aux_model=aux_model,
                analysis=analysis,
                app_tile_model=app_tile_model,
            ),
        ],
    )

    # STAGE: the two-map workspace, handed to MapApp as the step content.
    stage = v.Container(
        fluid=True,
        class_="pa-0 ma-0 analysis-stage",
        children=[style_el, left_inner_row],
    )

    # Drive the stage background + outline colours off the app theme by toggling
    # the ``analysis-stage--dark`` marker class. We observe the SAME
    # ``theme_state.dark`` trait the two maps bind to, so the workspace chrome and
    # the maps flip together. Built once with the rest of the raw widget tree, so
    # the observer is wired exactly once.
    def _sync_stage_theme(dark):
        stage.class_ = "pa-0 ma-0 analysis-stage" + (
            " analysis-stage--dark" if dark else ""
        )

    if theme_state is not None:
        theme_state.observe(
            lambda change: _sync_stage_theme(bool(change["new"])), "dark"
        )
    _sync_stage_theme(bool(getattr(theme_state, "dark", False)))

    return stage, controls
