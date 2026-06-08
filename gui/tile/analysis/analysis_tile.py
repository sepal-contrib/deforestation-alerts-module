"""Analysis tile parent component (Phase-4 Solara port of the OLD god-class).

Architecture layer: view. This is the FOUNDATION slice (Task 9.1) of porting the
2052-line ipyvuetify ``AnalysisTile`` god-class. It establishes:

  * the shared heavy widgets (``AnalysisWidgets``, built once via use_memo),
  * the two-map left panel + control right panel layout (ports OLD
    ``initialize_layout``),
  * the per-alert orchestration ``view_actual_alert`` as a ``use_effect`` keyed
    on ``app_state.actual_alert_id``,
  * the navigation row (``AlertNavigator``),
  * and the sub-component slots (imagery / cnn / edit / export — stubs for now).

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
import solara
from ipyleaflet import GeoJSON

from deforestation_alerts.message import cm
from deforestation_alerts.scripts.mosaics_helper import (
    convert_julian_to_date,
    multipolygon_to_geodataframe,
)
from deforestation_alerts.scripts.recipe_helper import update_saved_dictionary
from gui.tile.analysis.cnn_panel import AlertCnnPanel
from gui.tile.analysis.edit_tools import AlertEditTools
from gui.tile.analysis.export import AlertExport
from gui.tile.analysis.imagery import AlertImagery
from gui.tile.analysis.navigator import AlertNavigator
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

        # TODO(Task 9.2 AlertImagery): rebuild the Planet slide groups
        # (create_horizontal_slide_group_v3) and kick off the S2/Landsat
        # dictionary tasks (start_s2_dictionary_thread / start_landsat_...)
        # off the per-alert grid + dates computed above. Also restore the OLD
        # auto-switch of the visible tab to Sentinel-2 when Planet is "Not
        # available" (OLD set imgSelection{1,2}.children[0].v_model = 1); the
        # foundation only sets the show_image_collection flags above.
    except Exception:
        logger.exception("Analysis: view_actual_alert map update failed")


@solara.component
def AnalysisTile(
    aux_model,
    selected_alerts_model,
    analyzed_alerts_model,
    app_tile_model,
    app_state,
    gee_interface,
):
    """Solara port of the OLD ``AnalysisTile`` ipyvuetify widget (foundation).

    Parameters
    ----------
    aux_model : AuxModel
        Auxiliary model (custom report template; used by AlertExport later).
    selected_alerts_model : SelectedAlertsModel
        Filter selections + ``filtered_alert_raster`` (used by AlertImagery later).
    analyzed_alerts_model : AlertAnalysisModel
        Per-alert scratch state (imagery dicts, predictions, ``defo_dl_layer``,
        ``last_save_time``). The foundation only resets ``defo_dl_layer`` and reads
        the planet image dicts.
    app_tile_model : AppTileModel
        ``recipe_folder_path`` for the recipe json + output paths.
    app_state : AppState
        Source of truth for the alerts table + navigation.
    gee_interface : GEEInterface
        Session GEE interface, forwarded to both maps.
    """
    # Build the shared heavy widgets once.
    widgets = solara.use_memo(
        lambda: AnalysisWidgets(gee_interface=gee_interface), dependencies=[]
    )

    # Memoized alert-id field (raw ipyvuetify so view_actual_alert can imperatively
    # set .v_model). Owned here, passed to AlertNavigator + the effect.
    id_field = solara.use_memo(
        lambda: v.TextField(
            v_model=app_state.actual_alert_id.value,
            outlined=True,
            single_line=True,
            hide_details=True,
        ),
        dependencies=[],
    )

    # Inject the OLD tile's small CSS once (map height + tight text-field slots).
    solara.Style(
        ".custom-map-class2 { height: 55vh !important; } "
        ".v-text-field .v-input__control .v-input__slot { min-height: auto !important; "
        "min-width: 40px; display: flex !important; align-items: center !important; }"
    )

    # Per-alert orchestration: re-run whenever the current alert id changes
    # (navigation drives app_state.actual_alert_id). Guarded to no-op when no
    # alerts are loaded, so it is safe at mount/smoke time and idempotent.
    def _run_view_actual_alert():
        view_actual_alert(
            widgets, app_state, analyzed_alerts_model, app_tile_model, id_field
        )

    solara.use_effect(
        _run_view_actual_alert, dependencies=[app_state.actual_alert_id.value]
    )

    # Select alert 0 when an alert set first arrives (OLD create_gdf set
    # actual_alert_id = 0 on gdf arrival). The Filter publish / recipe load only
    # sets app_state.alerts_gdf and leaves actual_alert_id at -1, so without this
    # the tile would render empty until the user clicks "next". Only auto-selects
    # from the unset (-1) state, so it never fights manual navigation.
    def _select_first_alert():
        gdf = app_state.alerts_gdf.value
        if gdf is not None and len(gdf) > 0 and app_state.actual_alert_id.value < 0:
            app_state.go_to(0)

    solara.use_effect(
        _select_first_alert, dependencies=[app_state.alerts_gdf.value]
    )

    # TODO(recipe-load): the OLD tile rebuilt/merged alerts_gdf from the
    # selected-alerts traits here (create_gdf_partial/create_gdf_full + the
    # alerts_bbs/alerts_total_bbs observers). That is superseded by the Filter
    # tile publishing app_state.alerts_gdf. The recipe-LOAD re-discovery/merge
    # path (re-pointing previously reviewed alerts) is deferred to a later task.

    # ------------------------------------------------------------------
    # Layout: left two-map panel + right scrolling control panel (ports OLD
    # initialize_layout left_panel / right_panel).
    # ------------------------------------------------------------------
    with solara.Row(style="height: 100%; gap: 0;"):
        # LEFT: two map+selector columns side by side.
        with solara.Column(
            style="flex: 1 1 auto; min-width: 0; overflow: hidden; display: flex;"
        ):
            with solara.Row(style="gap: 0;"):
                with solara.Column(style="width: 50%;"):
                    solara.display(widgets.map31)
                    solara.display(widgets.imgSelection1)
                with solara.Column(style="width: 50%;"):
                    solara.display(widgets.map32)
                    solara.display(widgets.imgSelection2)
            # Imagery sub-component (stub): owns the slide-group orchestration
            # over the panes/selectors above in Task 9.2.
            AlertImagery(
                widgets=widgets,
                app_state=app_state,
                selected_alerts_model=selected_alerts_model,
                analyzed_alerts_model=analyzed_alerts_model,
            )

        # RIGHT: nav + alert-info + cnn/driver + edit + export control cards.
        with solara.Column(style="flex: 0 0 18rem; overflow: auto; padding: 0;"):
            # Alert-info card (its "Alert info" title is the always-rendered
            # smoke-test anchor).
            solara.display(
                v.Card(
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
            )
            AlertNavigator(app_state=app_state, id_field=id_field)
            AlertCnnPanel(
                widgets=widgets,
                app_state=app_state,
                analyzed_alerts_model=analyzed_alerts_model,
                app_tile_model=app_tile_model,
            )
            AlertEditTools(
                widgets=widgets,
                app_state=app_state,
                analyzed_alerts_model=analyzed_alerts_model,
            )
            AlertExport(
                widgets=widgets,
                app_state=app_state,
                aux_model=aux_model,
                analyzed_alerts_model=analyzed_alerts_model,
                app_tile_model=app_tile_model,
            )
