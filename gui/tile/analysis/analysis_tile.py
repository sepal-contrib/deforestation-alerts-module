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
* **``analyzed_alerts_model`` (AlertAnalysisModel) remains** as per-alert scratch
  state for later tasks (imagery dicts, model predictions, ``defo_dl_layer``,
  ``last_save_time``). The foundation only resets ``defo_dl_layer`` and reads
  ``before/after_planet_monthly_images`` in ``view_actual_alert``.
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

from deforestation_alerts.message import cm
from deforestation_alerts.scripts.mosaics_helper import (
    convert_julian_to_date,
    multipolygon_to_geodataframe,
)
from deforestation_alerts.scripts.recipe_helper import update_saved_dictionary
from gui.tile.analysis.review_panel import build_review_panel
from gui.tile.analysis.edit_tools import build_edit_tools
from gui.tile.analysis.export import build_export
from gui.tile.analysis.imagery import build_imagery
from gui.tile.analysis.navigator import build_navigator
from gui.tile.analysis.widgets import AnalysisWidgets

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


def view_actual_alert(widgets, app_state, analyzed_alerts_model, app_tile_model, id_field):
    """Per-alert orchestration owned by the foundation.

    Ports the foundation-relevant parts of the OLD ``view_actual_alert``: select
    the alert row, refresh the alert-info card + nav id field, reset
    ``defo_dl_layer``, rebuild the bbox overlay on both maps, center/zoom, compute
    the ee bbox + covering grid (stored on ``widgets``), register the AOI, set the
    default image-collection flags, and persist ``actual_alert_id`` to the recipe
    json (only when a recipe folder is set).

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

    # --- sync the nav id field ---
    id_field.v_model = alert_id

    # --- reset per-alert defo layer scratch (Task 9.3 repopulates it) ---
    analyzed_alerts_model.defo_dl_layer = None

    # --- persist actual_alert_id to the recipe json (only if a folder is set) ---
    recipe_folder = app_tile_model.recipe_folder_path
    if recipe_folder:
        try:
            update_saved_dictionary(
                recipe_folder + "/recipe_parameters.json",
                "actual_alert_id",
                alert_id,
            )
        except Exception:
            logger.exception("Analysis: failed to persist actual_alert_id to recipe")

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

        # center/zoom (the two maps are center/zoom-linked)
        widgets.map_before.center = (alerta.point.y, alerta.point.x)
        widgets.map_before.zoom = 16

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
            analyzed_alerts_model.defo_dl_layer = multipolygon_to_geodataframe(
                alerta["alert_polygon"]
            ).set_crs(epsg="4326", allow_override=True, inplace=True)

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
        before_planet = analyzed_alerts_model.before_planet_monthly_images
        after_planet = analyzed_alerts_model.after_planet_monthly_images
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


def build_analysis_layout(
    aux_model,
    selected_alerts_model,
    analyzed_alerts_model,
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
    aux_model : AuxModel
        Auxiliary model (custom report template; used by ``build_export``).
    selected_alerts_model : SelectedAlertsModel
        Filter selections + ``filtered_alert_raster`` (used by ``build_imagery``).
    analyzed_alerts_model : AlertAnalysisModel
        Per-alert scratch state (imagery dicts, predictions, ``defo_dl_layer``,
        ``last_save_time``). The parent only resets ``defo_dl_layer`` and reads
        the planet image dicts (in ``view_actual_alert``).
    app_tile_model : AppTileModel
        ``recipe_folder_path`` for the recipe json + output paths.
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

    # Per-alert orchestration: re-run whenever the current alert id changes
    # (navigation drives app_state.actual_alert_id). Replaces the former
    # use_effect with a subscribe + one immediate call (so a pre-selected alert
    # renders on first build). Guarded inside view_actual_alert to no-op when no
    # alerts are loaded, so it is safe at mount/smoke time and idempotent.
    def _on_alert_change(*_):
        view_actual_alert(
            widgets, app_state, analyzed_alerts_model, app_tile_model, id_field
        )

    app_state.actual_alert_id.subscribe(_on_alert_change)
    view_actual_alert(
        widgets, app_state, analyzed_alerts_model, app_tile_model, id_field
    )

    # Select alert 0 when an alert set first arrives (OLD create_gdf set
    # actual_alert_id = 0 on gdf arrival). The Filter publish / recipe load only
    # sets app_state.alerts_gdf and leaves actual_alert_id at -1, so without this
    # the tile would render empty until the user clicks "next". Only auto-selects
    # from the unset (-1) state, so it never fights manual navigation. Replaces the
    # former use_effect with a subscribe + one immediate call.
    def _select_first_alert_cb(*_):
        gdf = app_state.alerts_gdf.value
        if gdf is not None and len(gdf) > 0 and app_state.actual_alert_id.value < 0:
            app_state.go_to(0)

    app_state.alerts_gdf.subscribe(_select_first_alert_cb)
    _select_first_alert_cb()

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
        analyzed_alerts_model=analyzed_alerts_model,
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
            alert_info_card,
            build_navigator(app_state=app_state, id_field=id_field),
            build_review_panel(
                widgets=widgets,
                app_state=app_state,
                analyzed_alerts_model=analyzed_alerts_model,
                app_tile_model=app_tile_model,
            ),
            build_edit_tools(
                widgets=widgets,
                app_state=app_state,
                analyzed_alerts_model=analyzed_alerts_model,
                app_tile_model=app_tile_model,
            ),
            build_export(
                widgets=widgets,
                app_state=app_state,
                aux_model=aux_model,
                analyzed_alerts_model=analyzed_alerts_model,
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
