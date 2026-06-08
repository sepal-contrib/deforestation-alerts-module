"""Filter (alert) tile as a Solara component (Phase 4 port of the old ipyvuetify class).

Architecture layer: view — imports solara + ipyvuetify/pysepal/ipyleaflet for UI
only. The EE-orchestration + raster-export + local-vectorize work runs in
``solara.lab.use_task(prefer_threaded=True)``; the EE helpers themselves are
module-level functions taking explicit args (no ``self``) so the Recipe tile can
reuse them later.

Key port decisions (see Phase 4 plan, Step 5):

* **The 1 traitlets observer is kept as a traitlets observer.** The OLD tile did
  ``alert_filter_model.observe(self.update_tile, "available_alerts_raster_list")``
  — when AOI's search populates the raster dict, the source-select items + the map
  layers refresh. A Solara ``use_effect`` keyed on the trait would NOT fire
  (traitlets mutations don't trigger Solara re-renders), so we register a real
  ``alert_filter_model.observe`` exactly once via ``use_effect([])`` and tear it
  down on unmount. Same pattern AOI used for its ccdc observer.
* **This tile does NOT create its own map** (pysepal single-map model): the
  ``DrawControl`` + ``InspectorControl`` are attached to the **single shared**
  ``map_`` passed in from ``solara_app.Page``, the alert/AOI/aux layers are added
  to it, and the tile renders only its form cards (a right-panel section). It
  manages ONLY its own layers on the shared map — tracked by name so a refresh
  removes just this tile's layers (never ``map_.remove_all()``, which would wipe
  other tiles' layers + the basemap).
* **Heavy widgets are memoized once** (``dependencies=[]``): the ``DrawControl`` +
  ``InspectorControl`` and all the form widgets (source select, size/area/sorting
  inputs, max-count combobox). Identities are stable across re-renders so the
  observer can mutate ``.items``/``.v_model`` in place and the draw control stays
  attached to the shared map.
* **The "Analyze" flow is a button-triggered ``use_task``, not auto-run.**
  ``use_task(dependencies=None)`` only runs when called, so it never fires on
  mount (which would hit EE with no AOI/alerts). The task validates inputs via the
  shared ``check_alert_filter_inputs`` (through a thin ``_InputsShim`` around the
  memoized widgets — same approach as AOI), saves selections onto
  ``selected_alerts_model``, builds the filtered EE image, exports it with
  ``download_ee_image``, vectorizes locally with ``vectorize_alert_raster``, and
  publishes the assembled ``alerts_gdf`` to ``app_state.alerts_gdf.value`` (what
  Overview + the future analysis tile consume).

Deferred (with TODO comments) — intertwined with the Recipe tile (next task) and
the analysis tile (Task 9):

* Planet-imagery dictionaries / recipe-folder creation / save-recipe-parameters:
  these need ``app_tile_model.recipe_folder_path`` plumbing that is not wired into
  the Solara ``Page()`` until the Recipe tile is ported. See the TODOs in the task.
* Post-analyze navigation to the analysis tile: set ``current_page_view``; the
  shell's ``_NavBridge`` translates that into the ``MapApp`` step change.
* ``load_saved_parameters``: belongs to the Recipe tile; not ported here.
"""

import logging
import math

import ee
import ipyvuetify as v
import solara
from ipyleaflet import TileLayer
from pysepal.mapping.draw_control import DrawControl
from pysepal.mapping.inspector_control import InspectorControl

from deforestation_alerts.message import cm
from deforestation_alerts.parameter import module_dir
from deforestation_alerts.scripts.alert_filter_helper import (
    check_alert_filter_inputs,
    convert_to_geopandas,
    custom_reduce_image_collection,
)
from deforestation_alerts.scripts.raster_export import download_ee_image
from deforestation_alerts.scripts.vectorize import vectorize_alert_raster

logger = logging.getLogger("SEPALUI.deforestation_alerts.filter")

# Alert-raster visualization (same as the OLD tile's self.lista_vis_params[0]).
_ALERT_VIS_PARAMS = {"min": 1, "max": 2, "palette": ["orange", "purple"]}


class _InputsShim:
    """Adapter exposing the attribute shape ``check_alert_filter_inputs`` expects.

    The service helper ``check_alert_filter_inputs(self)`` reads, on the OLD
    widget-class ``self``:
      * ``self.alert_source_select.v_model``
      * ``self.min_area_input.v_model``
      * ``self.alert_selection_method_select.v_model``
      * ``self.alert_sorting_select.v_model``
      * ``self.number_of_alerts.v_model``
      * ``self.drawn_item.to_json()`` (only when the "by drawn polygon" method is
        selected)
    Rather than fork the still-shared helper, we hand it this thin shim around the
    memoized widgets so the validation logic stays in one place (same approach as
    the AOI tile's ``_InputsShim`` for ``check_aoi_inputs``).
    """

    def __init__(
        self,
        alert_source_select,
        min_area_input,
        alert_selection_method_select,
        alert_sorting_select,
        number_of_alerts,
        drawn_item,
    ):
        self.alert_source_select = alert_source_select
        self.min_area_input = min_area_input
        self.alert_selection_method_select = alert_selection_method_select
        self.alert_sorting_select = alert_sorting_select
        self.number_of_alerts = number_of_alerts
        self.drawn_item = drawn_item


def create_filtered_alert_raster(
    alerts_raster_dictionary,
    mask_layer,
    alert_source,
    alert_area_selection,
    user_selection_polygon,
):
    """Build a single filtered alert ``ee.Image`` from the selected sources.

    Module-level (no ``self``) so the Recipe tile can reuse it. Logic is identical
    to the OLD ``AlertsFilterTile.create_filtered_alert_raster`` — only the model
    accesses are now explicit args:

    * ``alerts_raster_dictionary`` <- ``alert_filter_model.available_alerts_raster_list``
    * ``mask_layer`` <- ``aux_model.mask_layer``

    Unioning multiple sources uses ``custom_reduce_image_collection`` (sum of
    alerts, min of date). When the area method is "drawn polygon" the result is
    clipped to that polygon; otherwise the AOI clip happens downstream at export.
    """
    if len(alert_source) == 1:
        alert_union = ee.Image(
            alerts_raster_dictionary[alert_source[0]]["alert_raster"]
        )
    else:
        selected_alert_list = []
        for label in alert_source:
            selected_alert_list.append(
                alerts_raster_dictionary[label]["alert_raster"]
            )
        img_collection_alertas = ee.ImageCollection.fromImages(selected_alert_list)
        alert_union = custom_reduce_image_collection(img_collection_alertas)

    alerta = alert_union

    # Mask out if a forest/no-forest (or other) mask layer exists.
    if mask_layer:
        mask_layer_img = ee.Image(mask_layer)
        mask_alert = alerta.updateMask(mask_layer_img).selfMask()
    else:
        mask_alert = alerta

    # Clip to the drawn polygon only when that area-selection method is chosen.
    if alert_area_selection == cm.filter_tile.area_selection_method_label1:
        clip_alert = mask_alert.clip(
            ee.FeatureCollection(user_selection_polygon).geometry()
        )
    else:
        clip_alert = mask_alert

    return clip_alert


def create_vector_download_params(
    aoi,
    user_selection_polygon,
    clip_alert,
    alert_source,
    user_min_alert_size,
    alert_area_selection,
):
    """Compute export region + pixel size + min cluster-size in pixels.

    Module-level (no ``self``) so the Recipe tile can reuse it. Logic is identical
    to the OLD ``AlertsFilterTile.create_vector_download_params``. Returns the
    5-tuple the OLD caller expected; ``alerta_reducir`` / ``grid_size`` are kept
    for tuple-shape parity but are unused since the move to local vectorization.
    """
    # Reproduce the GEE band stack (unused downstream now, kept for parity).
    i = clip_alert.select("alert").gt(0).rename("mask_alert")
    a = clip_alert.select("alert")
    d = clip_alert.select("date")
    alerta_reducir = i.addBands(i).addBands(a).addBands(d)

    # Pixel size by sensor family (Sentinel sources are finer).
    pixel_landsat = 28
    pixel_sentinel = 10

    if set(["GLAD-S2", "RADD"]).intersection(alert_source):
        pixel_size = pixel_sentinel
        grid_size = 20000
    else:
        pixel_size = pixel_landsat
        grid_size = 20000

    # Minimum cluster size in pixels from the user's min hectares.
    min_size_hectareas = float(user_min_alert_size) * 10000
    min_size_pixels = math.ceil(min_size_hectareas / (pixel_size**2))

    # Export region: drawn polygon when chosen, else the whole AOI.
    if alert_area_selection == cm.filter_tile.area_selection_method_label1:
        poly = ee.FeatureCollection(user_selection_polygon)
    else:
        poly = aoi

    return poly, alerta_reducir, pixel_size, min_size_pixels, grid_size


def _clear_filter_layers(map_widget, tracked):
    """Remove only THIS tile's layers from the shared map, by tracked name.

    Single-map isolation: called when the Filter tile is deactivated so its
    AOI / alert-raster / aux layers don't bleed into AOI / Overview. Never
    ``remove_all()`` (that would wipe other tiles' layers + the basemap).
    """
    for name in tracked["names"]:
        map_widget.remove_layer(name, none_ok=True)
    tracked["names"] = []


def _refresh_layout(
    map_widget,
    drawn_item,
    alert_source_select,
    cards,
    aoi_date_model,
    aux_model,
    alert_filter_model,
    tracked,
):
    """Refresh the source-select items + map layers from the alert dictionaries.

    Module-level so the traitlets observer handler can call it. Mirrors the OLD
    tile's ``update_layout``: when no alerts are available, show only the "no
    alerts" card; otherwise populate the select, reveal the form cards, and
    rebuild the map (AOI + each alert source's alert/date layers + optional
    aux/mask layers). ``cards`` is a dict of the show/hide-able cards.

    ``map_widget`` is the SINGLE shared map, so we must NOT ``remove_all()`` (that
    wipes other tiles' layers + the basemap). ``tracked`` is a ``{"names": [...]}``
    holder of the layer names THIS tile last added; we remove exactly those before
    adding the new set, leaving every other tile's layers intact.
    """
    alerts_names_list = alert_filter_model.available_alerts_list
    alerts_raster_dictionary = alert_filter_model.available_alerts_raster_list

    if not alerts_names_list or len(alerts_names_list) == 0:
        cards["no_alerts"].show()
        for key in ("source", "min_area", "area_method", "sorting", "max_number"):
            cards[key].hide()
        return

    alert_source_select.items = alerts_names_list
    alert_source_select.v_model = [alerts_names_list[0]]

    cards["no_alerts"].hide()
    for key in ("source", "min_area", "area_method", "sorting", "max_number"):
        cards[key].show()

    # Rebuild ONLY this tile's layers on the shared map.
    try:
        # Drop the layers this tile added last time, then track + add the new set.
        _clear_filter_layers(map_widget, tracked)

        def _add(ee_object, name, **kw):
            map_widget.add_ee_layer(ee_object, name=name, **kw)
            tracked["names"].append(name)

        _add(aoi_date_model.feature_collection, "AOI")

        for nombre in alerts_raster_dictionary:
            _add(
                alerts_raster_dictionary[nombre]["alert_raster"].select("alert"),
                nombre,
                vis_params=_ALERT_VIS_PARAMS,
                shown=False,
            )
            _add(
                alerts_raster_dictionary[nombre]["alert_raster"]
                .select("date")
                .randomVisualizer(),
                nombre + cm.filter_tile.layer_date_helper1,
                shown=False,
            )

        if aux_model.aux_layer:
            if aux_model.aux_layer_vis:
                vis_aux = {aux_model.aux_layer_vis}
            else:
                vis_aux = {"min": 0, "max": 1, "palette": ["white", "brown"]}
            _add(
                ee.Image(aux_model.aux_layer).selfMask(),
                cm.filter_tile.layer_aux_name,
                vis_params=vis_aux,
            )

        if aux_model.mask_layer:
            _add(
                ee.Image(aux_model.mask_layer).selfMask(),
                cm.filter_tile.layer_mask_name,
                vis_params={"min": 0, "max": 1, "palette": ["white", "gray"]},
            )
        # Sync layer visibility only after a successful rebuild, so a partial
        # failure above doesn't toggle visibility on an inconsistent layer set.
        _link_checkbox_map(map_widget, alert_source_select)
    except Exception:
        logger.exception("Filter: failed to refresh map layers")


def _link_checkbox_map(map_widget, alert_source_select):
    """Show only the EE layers whose name matches the source-select v_model.

    Identical logic to the OLD tile's ``link_checkbox_map``: enable layers named
    in the select's value, hide every other GEE layer except the AOI.
    """
    check_box_v_model = alert_source_select.v_model or []
    for layer in map_widget.layers:
        if isinstance(layer, TileLayer) and hasattr(layer, "name"):
            if layer.name in check_box_v_model:
                layer.visible = True
            elif (
                "Google Earth Engine" in (layer.attribution or "")
                and layer.name != "AOI"
            ):
                layer.visible = False


def _run_local_vectorize(image, region, scale, min_size_pixels):
    """Export the filtered alert raster to a local GeoTIFF, then vectorize it.

    Body of the OLD ``run_local_vectorize._work`` (minus the threading — the
    caller is already on the use_task worker thread) and minus the model writes,
    which the task does. Returns the raw vectorize GeoDataFrame.
    """
    # geedim exports over an ee.Geometry; the AOI / drawn poly is a
    # FeatureCollection, so pass its geometry.
    export_region = (
        region.geometry() if isinstance(region, ee.FeatureCollection) else region
    )
    out_tif = str(module_dir / "filtered_alerts.tif")
    # alert raster: band order [alert, date]; 0 = no-alert so unmask to 255.
    download_ee_image(
        image.select(["alert", "date"]),
        out_tif,
        region=export_region,
        scale=scale,
        crs="EPSG:4326",
        unmask_value=255,
        nodata_value=255,
    )
    return vectorize_alert_raster(
        out_tif, min_cluster_size=min_size_pixels, connectivity=8
    )


@solara.component
def AlertsFilterTile(
    aoi_date_model,
    aux_model,
    alert_filter_model,
    selected_alerts_model,
    analyzed_alerts_model,
    app_state,
    gee_interface,
    map_,
):
    """Solara port of the old AlertsFilterTile ipyvuetify widget.

    Parameters
    ----------
    aoi_date_model : AoiDateModel
        Recipe domain model providing ``feature_collection`` (the AOI) used as the
        default export region.
    aux_model : AuxModel
        Auxiliary model; ``mask_layer`` masks the alert raster, ``aux_layer`` /
        ``mask_layer`` add reference layers to the map.
    alert_filter_model : AlertFilterModel
        Holds ``available_alerts_list`` / ``available_alerts_raster_list`` (set by
        the AOI search). The observer on the raster list drives this tile.
    selected_alerts_model : SelectedAlertsModel
        Where the user's filter selections + the resulting vectorized alert sets
        are written (parity with the old tile).
    analyzed_alerts_model : AlertAnalysisModel
        Reserved for the deferred planet-imagery dictionaries (see TODOs).
    app_state : AppState
        Shared ephemeral reactive store. The assembled ``alerts_gdf`` is published
        to ``app_state.alerts_gdf`` (Overview + analysis tile consume it).
    gee_interface : GEEInterface
        Session GEE interface, forwarded to the map.
    map_ : SepalMap
        The single shared map; this tile attaches its draw/inspector controls and
        alert/AOI/aux layers to it.
    """
    # ------------------------------------------------------------------
    # Attach this tile's draw + inspector controls to the SHARED map (no own map),
    # and track the layer names this tile adds so a refresh removes only its own
    # layers (never remove_all on the shared map).
    # ------------------------------------------------------------------
    map_widget = map_
    drawn_item = solara.use_memo(
        lambda: _build_draw_control(map_widget), dependencies=[]
    )
    solara.use_memo(lambda: _add_inspector(map_widget), dependencies=[])
    tracked = solara.use_memo(lambda: {"names": []}, dependencies=[])

    # ------------------------------------------------------------------
    # Memoized form widgets. Created once so identities are stable: the observer
    # mutates the source select's .items/.v_model in place, and the draw control
    # toggles its own visibility from the area-method callback. (Raw ipyvuetify,
    # not reacton rv.*, on purpose — a reacton element is rebuilt each render and
    # can't be imperatively mutated; same rationale as overview_tile's SimpleTable.)
    # ------------------------------------------------------------------
    widgets = solara.use_memo(
        lambda: _build_form_widgets(alert_filter_model, drawn_item), dependencies=[]
    )
    (
        alert_source_select,
        min_area_input,
        alert_selection_method_select,
        alert_sorting_select,
        number_of_alerts,
        cards,
    ) = widgets

    # Single shared map -> per-tile isolation. Initial show/hide is established by
    # _build_form_widgets (only the "no alerts" card visible). Draw this tile's
    # layers (AOI + alert rasters) on the shared map ONLY while it is the active
    # tile, and clear them when navigating away, so they don't bleed into AOI /
    # Overview. Keyed on the reactive current_page_view so it re-runs on every nav:
    # on mount it clears (a no-op), and in the AOI-search flow (search populates
    # the model, then navigates here) it draws on the main thread.
    active = app_state.current_page_view.value == "filter_alerts"

    def _sync_active():
        if active:
            _refresh_layout(
                map_widget,
                drawn_item,
                alert_source_select,
                cards,
                aoi_date_model,
                aux_model,
                alert_filter_model,
                tracked,
            )
        else:
            _clear_filter_layers(map_widget, tracked)

    solara.use_effect(_sync_active, dependencies=[active])

    # ------------------------------------------------------------------
    # The 1 traitlets observer — registered exactly once, torn down on unmount.
    # (A Solara use_effect keyed on the trait would NOT fire: traitlets mutations
    # don't trigger Solara re-renders.) Refreshes the select items + map layers
    # when AOI's search populates available_alerts_raster_list.
    # ------------------------------------------------------------------
    def _register_observer():
        def handler(change):
            # Single-map isolation: only touch the shared map while this tile is
            # active. Otherwise (e.g. AOI's search populates the raster list while
            # the user is still on AOI) skip — the activation effect above redraws
            # from the model when the tile becomes active, on the main thread.
            if app_state.current_page_view.value != "filter_alerts":
                return
            _refresh_layout(
                map_widget,
                drawn_item,
                alert_source_select,
                cards,
                aoi_date_model,
                aux_model,
                alert_filter_model,
                tracked,
            )

        alert_filter_model.observe(handler, "available_alerts_raster_list")
        return lambda: alert_filter_model.unobserve(
            handler, "available_alerts_raster_list"
        )

    solara.use_effect(_register_observer, dependencies=[])

    # ------------------------------------------------------------------
    # Source-select "change" -> sync visible map layers (OLD link_checkbox_map_btn).
    # Wired once here (not in the builder) because it needs the map widget. The
    # callback only reads/sets layer.visible, so registering it more than once
    # would be harmless, but we guard with use_effect([]) for cleanliness.
    # ------------------------------------------------------------------
    def _register_source_change():
        def on_change(widget, event, data):
            _link_checkbox_map(map_widget, alert_source_select)

        alert_source_select.on_event("change", on_change)
        return lambda: alert_source_select.on_event("change", on_change, remove=True)

    solara.use_effect(_register_source_change, dependencies=[])

    # ------------------------------------------------------------------
    # Analyze task. dependencies=None => never auto-runs; only fires when the
    # click handler calls analyze_task(). Does the EE-orchestration + export +
    # local vectorize on a worker thread and publishes the assembled gdf.
    # ------------------------------------------------------------------
    def _run_analyze():
        # 1. Validate inputs via the shared helper through the shim.
        shim = _InputsShim(
            alert_source_select,
            min_area_input,
            alert_selection_method_select,
            alert_sorting_select,
            number_of_alerts,
            drawn_item,
        )
        (
            alert_source,
            user_min_alert_size,
            alert_area_selection,
            alert_sorting_method,
            user_max_number_alerts,
            user_selection_polygon,
        ) = check_alert_filter_inputs(shim)

        # 2. Save the user selections onto selected_alerts_model (parity).
        # NOTE: this runs on the use_task worker thread (prefer_threaded=True), so
        # any traitlets `.observe` callbacks on selected_alerts_model fire on THIS
        # thread (traitlets, unlike Solara reactives, has no scope marshaling).
        # When the analysis tile is ported (Task 9), its observers on this model
        # must not mutate ipyvuetify widgets directly from here.
        selected_alerts_model.selected_alert_sources = alert_source
        selected_alerts_model.alert_selection_area = alert_area_selection
        selected_alerts_model.alert_sorting_method = alert_sorting_method
        selected_alerts_model.min_area = float(user_min_alert_size)
        selected_alerts_model.max_number_alerts = int(user_max_number_alerts)
        selected_alerts_model.alert_selection_polygons = user_selection_polygon

        alert_selection_dict_n = {
            cm.filter_tile.area_selection_method_label1: 1,
            cm.filter_tile.area_selection_method_label2: 2,
        }
        selected_alerts_model.alert_selection_area_n = alert_selection_dict_n.get(
            alert_area_selection, 0
        )
        alert_sorting_method_dict_n = {
            cm.filter_tile.alert_sorting_method_label1: 1,
            cm.filter_tile.alert_sorting_method_label2: 2,
            cm.filter_tile.alert_sorting_method_label3: 3,
            cm.filter_tile.alert_sorting_method_label4: 4,
        }
        selected_alerts_model.alert_sorting_method_n = (
            alert_sorting_method_dict_n.get(alert_sorting_method, 0)
        )

        # TODO(Recipe tile): the OLD tile also created the recipe directory
        # (create_recipe_directory) and saved recipe parameters
        # (save_recipe_parameters) here, plus built the before/after Planet
        # monthly-image dictionaries (create_planet_images_dictionary) onto
        # analyzed_alerts_model. Those pull in app_tile_model.recipe_folder_path
        # plumbing that is not wired into the Solara Page() yet, so they are
        # deferred to the Recipe tile task. The alerts_gdf publish below does not
        # need them.

        # 3. Build the filtered alert raster (EE image).
        filtered_alert_raster = create_filtered_alert_raster(
            alert_filter_model.available_alerts_raster_list,
            aux_model.mask_layer,
            alert_source,
            alert_area_selection,
            user_selection_polygon,
        )
        selected_alerts_model.filtered_alert_raster = filtered_alert_raster

        # 4. Export region + pixel size + min cluster-size in pixels.
        (
            poly,
            _alerta_reducir,  # unused (legacy GEE band stack)
            pixel_size,
            min_size_pixels,
            _grid_size,  # unused (legacy GEE covering grid)
        ) = create_vector_download_params(
            aoi_date_model.feature_collection,
            user_selection_polygon,
            filtered_alert_raster,
            alert_source,
            user_min_alert_size,
            alert_area_selection,
        )

        # 5. Download + vectorize locally (replaces the OLD GEE reduceToVectors).
        gdf = _run_local_vectorize(
            image=filtered_alert_raster,
            region=poly,
            scale=pixel_size,
            min_size_pixels=min_size_pixels,
        )

        # Parity: publish the raw vectorize sets onto selected_alerts_model
        # (the OLD tile set these; the still-traitlets analysis tile reads them).
        max_number_alerts = int(user_max_number_alerts)
        selected_alerts_model.alerts_total_bbs = gdf
        selected_alerts_model.alerts_bbs = gdf.head(max_number_alerts)
        selected_alerts_model.received_alerts = True

        # 6. Assemble the alerts_gdf (status/point/alert_sources/... columns) and
        # publish it — this is what Overview + the future analysis tile consume.
        app_state.alerts_gdf.value = convert_to_geopandas(gdf)

        # Jump to the analysis tile (as the OLD tile did). The shell's _NavBridge
        # translates this current_page_view write into the MapApp step change.
        app_state.current_page_view.value = "analysis_tile"
        return True

    analyze_task = solara.lab.use_task(
        _run_analyze, dependencies=None, prefer_threaded=True, raise_error=False
    )

    def _on_analyze_click(*_):
        analyze_task()

    # ------------------------------------------------------------------
    # Render layout: controls only (this widget IS the right-panel section
    # content). The alert/AOI/aux layers are drawn on the single shared map.
    # ------------------------------------------------------------------
    with solara.Column(style="padding: 0;"):
        solara.display(cards["no_alerts"])
        solara.display(cards["source"])
        solara.display(cards["min_area"])
        solara.display(cards["area_method"])
        solara.display(cards["sorting"])
        solara.display(cards["max_number"])
        solara.Button(
            label=cm.filter_tile.analyze_alerts_button,
            on_click=_on_analyze_click,
            loading=analyze_task.pending,
            disabled=analyze_task.pending,
            color="primary",
            class_="ma-3",
        )
        if analyze_task.error:
            solara.Error(
                f"Analysis failed: {analyze_task.exception}", class_="ma-3"
            )


def _add_inspector(map_widget):
    """Attach an InspectorControl to the shared map once (inside use_memo).

    The OLD per-tile map added this in ``_build_map``; on the shared map we add it
    once when the Filter tile mounts. Guarded so a failure does not crash the memo.
    """
    try:
        map_widget.add(InspectorControl(map_widget))
    except Exception:
        logger.exception("Failed to add InspectorControl to the shared map")
    return True


def _build_draw_control(map_widget):
    """Build the DrawControl once and attach it (hidden) to the map.

    Kept as pysepal ``DrawControl`` — there is no native Solara equivalent for an
    on-map polygon editor. ``check_alert_filter_inputs`` reads ``.to_json()`` and
    the area-method callback toggles ``.show()``/``.hide()``. It starts hidden
    (the default area method is "Whole area").
    """
    drawn_item = DrawControl(map_widget)
    map_widget.add(drawn_item)
    drawn_item.hide()
    return drawn_item


def _build_form_widgets(alert_filter_model, drawn_item):
    """Build all the right-panel form widgets + cards once (inside use_memo).

    Returns the 5 input widgets + a dict of show/hide-able cards. The OLD tile
    used SepalCard/SepalCardTitle subclasses (``sw.SepalWidget`` + ``v.Card``);
    pysepal's ``sw.Card`` already mixes ``SepalWidget`` into ``v.Card`` so it has
    the imperative ``.show()/.hide()`` API directly, with the same ``pa-3 ma-3``
    styling.
    """
    from pysepal import sepalwidgets as sw

    def _card(children, extra_class=""):
        return sw.Card(
            class_=f"pa-3 ma-3 {extra_class}".strip(), children=children
        )

    def _title(text):
        return v.CardTitle(class_="pa-1 ma-1", children=[text])

    main_title = _title(cm.filter_tile.available_alerts_title)

    # "No alerts" card.
    no_alerts_card = _card(
        [
            main_title,
            v.Html(
                tag="div",
                class_="pa-1 ma-2",
                children=[cm.filter_tile.no_alerts_message],
            ),
        ]
    )

    # Alert source select (grouped multi-select with chips).
    alert_source_select = sw.Select(
        items=alert_filter_model.available_alerts_list or [],
        v_model=alert_filter_model.available_alerts_list or [],
        label=cm.filter_tile.available_alerts_hint,
        multiple=True,
        clearable=True,
        chips=True,
    )
    source_card = _card(
        [_title(cm.filter_tile.available_alerts_title), alert_source_select]
    )

    # Min alert area.
    min_area_input = sw.TextField(v_model=0.5, suffix="ha")
    min_area_card = _card(
        [_title(cm.filter_tile.set_min_alert_siz_title), min_area_input]
    )

    # Area selection method.
    alert_selection_method_select = sw.Select(
        items=[
            cm.filter_tile.area_selection_method_label1,
            cm.filter_tile.area_selection_method_label2,
        ],
        v_model=cm.filter_tile.area_selection_method_label2,
        multiple=False,
        clearable=True,
        chips=True,
    )
    area_method_card = _card(
        [_title(cm.filter_tile.area_selection_method_title), alert_selection_method_select]
    )

    # Alert sorting method.
    alert_sorting_select = sw.Select(
        items=[
            cm.filter_tile.alert_sorting_method_label1,
            cm.filter_tile.alert_sorting_method_label2,
            cm.filter_tile.alert_sorting_method_label3,
            cm.filter_tile.alert_sorting_method_label4,
        ],
        v_model=cm.filter_tile.alert_sorting_method_label1,
        multiple=False,
        clearable=True,
        chips=True,
        small_chips=True,
    )
    sorting_card = _card(
        [_title(cm.filter_tile.alert_sorting_method_title), alert_sorting_select]
    )

    # Max number of alerts.
    number_of_alerts = sw.Combobox(
        items=[cm.filter_tile.max_number_of_alerts_option1],
        v_model=[cm.filter_tile.max_number_of_alerts_option1],
        label=cm.filter_tile.max_number_of_alerts_hint,
        multiple=False,
        clearable=True,
        chips=True,
    )
    max_number_card = _card(
        [_title(cm.filter_tile.max_number_of_alerts_title), number_of_alerts]
    )

    # The Analyze button lives in the render tree as a Solara Button (its
    # loading state is the loader), so there is no analyze card here.

    # Area-method callback: reveal max-count + hide draw control for "Whole area",
    # or hide max-count + show draw control for "drawn polygon" (OLD prior_option).
    def _prior_option(widget, event, data):
        if data == cm.filter_tile.area_selection_method_label1:
            max_number_card.hide()
            drawn_item.show()
        else:
            max_number_card.show()
            drawn_item.hide()

    alert_selection_method_select.on_event("change", _prior_option)

    # NOTE: the source-select "change" handler (which re-syncs which map layers
    # are visible via _link_checkbox_map) needs the map, which is not available in
    # this builder. The component wires it once via use_effect([]) after the map +
    # select both exist.

    cards = {
        "no_alerts": no_alerts_card,
        "source": source_card,
        "min_area": min_area_card,
        "area_method": area_method_card,
        "sorting": sorting_card,
        "max_number": max_number_card,
    }

    # Initial visibility: only the no-alerts card (OLD __init__).
    no_alerts_card.show()
    for c in (source_card, min_area_card, area_method_card, sorting_card, max_number_card):
        c.hide()

    return (
        alert_source_select,
        min_area_input,
        alert_selection_method_select,
        alert_sorting_select,
        number_of_alerts,
        cards,
    )
