"""Filter (alert) tile as a Solara component (Phase 4 port of the old ipyvuetify class).

Architecture layer: view — imports solara + ipyvuetify/pysepal/ipyleaflet for UI
only. The EE-orchestration + raster-export + local-vectorize work runs as an
**async** ``solara.lab.use_task(prefer_threaded=False)`` on Solara's kernel loop,
with every blocking step handed to ``asyncio.to_thread``; the EE helpers
themselves are module-level functions taking explicit args (no ``self``) so the
Recipe tile can reuse them later.

Task lifecycle (the same four-part pattern the AOI tile documents):

1. **pure sync workers** take a frozen snapshot and return frozen values —
   ``_prepare_analyze_sync`` / ``_download_alert_raster`` /
   ``_vectorize_alert_tif`` / ``_finish_analyze_sync`` mutate no model, no
   reactive, no widget, and never navigate;
2. the **async task** awaits each of them through ``asyncio.to_thread``. The
   await points double as cancellation checkpoints: a cancel between two stages
   stops the flow before the next (much more expensive) one starts;
3. the **click handler** snapshots the form and holds a synchronous ``pending``
   guard (a render-time ``disabled=`` cannot stop a double click);
4. the **completion effect** — full dependency list — performs every model write
   and the ``current_page_view`` navigation, and applies nothing when the run was
   cancelled.

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
  mount (which would hit EE with no AOI/alerts). The workers validate inputs via
  the shared ``check_alert_filter_inputs`` (through a thin ``_InputsShim`` built
  from the click-time snapshot — same approach as AOI), build the filtered EE
  image, export it with ``download_ee_image`` and vectorize locally with
  ``vectorize_alert_raster``; the completion effect then saves the selections
  onto ``selected_alerts_model`` and publishes the assembled ``alerts_gdf`` to
  ``app_state.alerts_gdf.value`` (what Overview + the analysis tile consume).

Deferred (with TODO comments) — intertwined with the Recipe tile (next task) and
the analysis tile (Task 9):

* Planet-imagery dictionaries / recipe-folder creation / save-recipe-parameters:
  these need ``app_tile_model.recipe_folder_path`` plumbing that is not wired into
  the Solara ``Page()`` until the Recipe tile is ported. See the TODOs in the task.
* Post-analyze navigation to the analysis tile: set ``current_page_view``; the
  shell's ``_NavBridge`` translates that into the ``MapApp`` step change.
* ``load_saved_parameters``: belongs to the Recipe tile; not ported here.
"""

import asyncio
import dataclasses
import logging
import math
import time
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any

import ee
import ipyvuetify as v
import solara
from ipyleaflet import TileLayer
from pysepal.mapping.draw_control import DrawControl
from pysepal.mapping.inspector_control import InspectorControl
from pysepal.solara.components.task_button import TaskButtonComponent, use_task_button
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.message import cm
from deforestation_alerts.scripts.alert_filter_helper import (
    check_alert_filter_inputs,
    convert_to_geopandas,
    custom_reduce_image_collection,
)
from deforestation_alerts.scripts.raster_export import download_ee_image
from deforestation_alerts.scripts.vectorize import vectorize_alert_raster
from deforestation_alerts.scripts.attribute_source import attribute_alerts_from_config
from deforestation_alerts.scripts.naming import alert_artifact_name
from deforestation_alerts.scripts.recipe_helper import save_gdf_to_parquet
from gui.widget.help import InfoButton

logger = logging.getLogger("SEPALUI.deforestation_alerts.filter")

# Alert-raster visualization (same as the OLD tile's self.lista_vis_params[0]).
_ALERT_VIS_PARAMS = {"min": 1, "max": 2, "palette": ["orange", "purple"]}

# See gui/tile/aoi_tile.py's _guard_cancel: the pysepal TaskButton is a single
# start/cancel toggle whose cancel is never disabled, so the second press of a
# fast double click could otherwise cancel the run the first press started.
_CANCEL_GRACE_SECONDS = 1.0


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
    Rather than fork the still-shared helper, we hand it this thin shim built from
    the immutable ``AnalyzeRequest`` snapshot, so the validation logic stays in one
    place AND the worker never touches a live widget (same approach as the AOI
    tile's ``_InputsShim`` for ``check_aoi_inputs``).
    """

    def __init__(
        self,
        alert_sources,
        min_area,
        area_selection,
        sorting_method,
        max_alerts,
        drawn_geojson,
    ):
        self.alert_source_select = SimpleNamespace(v_model=alert_sources)
        self.min_area_input = SimpleNamespace(v_model=min_area)
        self.alert_selection_method_select = SimpleNamespace(v_model=area_selection)
        self.alert_sorting_select = SimpleNamespace(v_model=sorting_method)
        self.number_of_alerts = SimpleNamespace(v_model=max_alerts)
        self.drawn_item = SimpleNamespace(to_json=lambda: drawn_geojson)


@dataclasses.dataclass(frozen=True)
class AnalyzeRequest:
    """Immutable snapshot of the form + models, taken at Analyze-click time."""

    alert_sources: Any
    min_area: Any
    area_selection: Any
    sorting_method: Any
    max_alerts: Any
    drawn_geojson: Any
    alerts_raster_dictionary: Any
    mask_layer: Any
    feature_collection: Any
    start_date: Any
    end_date: Any
    recipe_folder: Any
    attribute_raster: Any
    custom_alert_name: Any


@dataclasses.dataclass(frozen=True)
class AnalyzeSelections:
    """The validated user selections, as they will be written to the model."""

    selected_alert_sources: Any
    alert_selection_area: Any
    alert_sorting_method: Any
    min_area: float
    max_number_alerts: int
    alert_selection_polygons: Any
    alert_selection_area_n: int
    alert_sorting_method_n: int


@dataclasses.dataclass(frozen=True)
class AnalyzePlan:
    """Everything the export/vectorize stages need — built by the first worker."""

    selections: AnalyzeSelections
    filtered_alert_raster: Any
    region: Any
    pixel_size: Any
    min_size_pixels: int
    # Where geedim writes and the vectorizer reads (container-local scratch)...
    out_tif: str
    # ...and where it is published in the user's workspace (None: no recipe yet).
    remote_tif: Any


@dataclasses.dataclass(frozen=True)
class AnalyzeOutcome:
    """What the analyze run produced. Applied by the completion effect only."""

    selections: AnalyzeSelections
    filtered_alert_raster: Any
    alerts_total_bbs: Any
    alerts_bbs: Any
    alerts_gdf: Any
    alert_count: int
    recipe_folder: Any
    # Why the alert store could not be written, or ``None`` when it was (or when
    # there is no recipe folder to write it into). Set inside the task, read by
    # the completion effect to pick the toast — a failed store write must not be
    # reported as a success.
    persist_error: str | None = None


@dataclasses.dataclass(frozen=True)
class FilterLayerRequest:
    """Immutable snapshot for one Filter map-layer sync (draw when ``show``)."""

    show: bool
    token: int


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


def _refresh_form(alert_source_select, cards, alert_filter_model) -> bool:
    """Refresh the source-select items + card visibility. Returns "has alerts".

    The widget half of the OLD tile's ``update_layout``: when no alerts are
    available show only the "no alerts" card; otherwise populate the select and
    reveal the form cards. Pure ipyvuetify mutation and no GEE, so it stays on the
    render thread (the map half, ``_refresh_map_layers``, is what goes to a
    thread). Module-level so the traitlets observer handler can call it.
    """
    alerts_names_list = alert_filter_model.available_alerts_list

    if not alerts_names_list or len(alerts_names_list) == 0:
        cards["no_alerts"].show()
        for key in ("source", "min_area", "area_method", "sorting", "max_number"):
            cards[key].hide()
        return False

    alert_source_select.items = alerts_names_list
    alert_source_select.v_model = [alerts_names_list[0]]

    cards["no_alerts"].hide()
    for key in ("source", "min_area", "area_method", "sorting", "max_number"):
        cards[key].show()
    return True


def _refresh_map_layers(
    map_widget,
    alert_source_select,
    aoi_date_model,
    aux_model,
    alert_filter_model,
    tracked,
):
    """Rebuild ONLY this tile's layers on the shared map (AOI + alert/date/aux).

    The GEE half of the OLD tile's ``update_layout``. Every ``add_ee_layer`` here
    blocks while EE resolves a tile-map id, so this function is only ever called
    from inside ``asyncio.to_thread`` — never from a render, an effect body or an
    observer directly.

    ``map_widget`` is the SINGLE shared map, so we must NOT ``remove_all()`` (that
    wipes other tiles' layers + the basemap). ``tracked`` is a ``{"names": [...]}``
    holder of the layer names THIS tile last added; we remove exactly those before
    adding the new set, leaving every other tile's layers intact.
    """
    alerts_raster_dictionary = alert_filter_model.available_alerts_raster_list or {}
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


def _filtered_alerts_path(workspace, recipe_folder, alert_sources, start_date, end_date):
    """``(local_tif, remote_tif)`` for the downloaded filtered-alert raster.

    geedim writes and the vectorizer reads local files only, so the raster is
    always built in ``workspace`` scratch; it is *also* user data, so when a
    recipe is loaded it is uploaded into that recipe folder in the user's SEPAL
    workspace (``remote_tif``). Without a recipe there is nowhere to put it and
    ``remote_tif`` is ``None`` — the old ``module_dir`` fallback wrote it to the
    container's disk, where the user could never see it (inventory a28/a29).

    Filename encodes source(s) + date range via the shared ``naming`` helper, e.g.
    ``filtered_alerts_glad-l-radd_2026-01-01_2026-06-12.tif``.
    """
    name = alert_artifact_name(
        "filtered_alerts", alert_sources, start_date, end_date, "tif"
    )
    remote = str(PurePosixPath(recipe_folder) / name) if recipe_folder else None
    return str(workspace.scratch_file(name=name)), remote


def _loop_is_running() -> bool:
    """True when the calling thread has a running asyncio event loop.

    Same contract as ``gui.tile.aoi_tile._loop_is_running``: a
    ``prefer_threaded=False`` task is scheduled with a bare
    ``asyncio.create_task`` and raises without one, and an effect body must never
    raise (reacton masks the failure behind "you are not using a container").
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _guard_cancel(on_cancel, started_at):
    """Wrap a TaskButton cancel callback so an accidental double click can't cancel.

    Identical to (and documented at) ``gui.tile.aoi_tile._guard_cancel``: the
    TaskButton is a single start/cancel toggle whose cancel is never disabled, so
    the second press of a fast double click can land on the freshly toggled cancel
    state and kill the run the first press started. Cancels within
    ``_CANCEL_GRACE_SECONDS`` of the start are dropped; a deliberate cancel of a
    multi-minute analyze is always later than that. Returning early also leaves
    ``cancel_reason_ref`` untouched (the wrapped callback is what sets it).
    """

    def _on_cancel(*args, **kwargs):
        if time.monotonic() - (started_at.current or 0.0) < _CANCEL_GRACE_SECONDS:
            return
        on_cancel(*args, **kwargs)

    return _on_cancel


def _prepare_analyze_sync(request: AnalyzeRequest, workspace) -> AnalyzePlan:
    """Validate the snapshot and build the EE image + export params. Pure.

    Runs inside ``asyncio.to_thread``: ``check_alert_filter_inputs`` raises on bad
    input (surfaced through ``analyze_task.error``) and the ``create_*`` helpers
    build eeclient objects. Nothing here touches a model, a reactive or a widget.
    """
    (
        alert_source,
        user_min_alert_size,
        alert_area_selection,
        alert_sorting_method,
        user_max_number_alerts,
        user_selection_polygon,
    ) = check_alert_filter_inputs(
        _InputsShim(
            request.alert_sources,
            request.min_area,
            request.area_selection,
            request.sorting_method,
            request.max_alerts,
            request.drawn_geojson,
        )
    )

    alert_selection_dict_n = {
        cm.filter_tile.area_selection_method_label1: 1,
        cm.filter_tile.area_selection_method_label2: 2,
    }
    alert_sorting_method_dict_n = {
        cm.filter_tile.alert_sorting_method_label1: 1,
        cm.filter_tile.alert_sorting_method_label2: 2,
        cm.filter_tile.alert_sorting_method_label3: 3,
        cm.filter_tile.alert_sorting_method_label4: 4,
    }
    selections = AnalyzeSelections(
        selected_alert_sources=alert_source,
        alert_selection_area=alert_area_selection,
        alert_sorting_method=alert_sorting_method,
        min_area=float(user_min_alert_size),
        max_number_alerts=int(user_max_number_alerts),
        alert_selection_polygons=user_selection_polygon,
        alert_selection_area_n=alert_selection_dict_n.get(alert_area_selection, 0),
        alert_sorting_method_n=alert_sorting_method_dict_n.get(
            alert_sorting_method, 0
        ),
    )

    filtered_alert_raster = create_filtered_alert_raster(
        request.alerts_raster_dictionary,
        request.mask_layer,
        alert_source,
        alert_area_selection,
        user_selection_polygon,
    )

    (
        poly,
        _alerta_reducir,  # unused (legacy GEE band stack)
        pixel_size,
        min_size_pixels,
        _grid_size,  # unused (legacy GEE covering grid)
    ) = create_vector_download_params(
        request.feature_collection,
        user_selection_polygon,
        filtered_alert_raster,
        alert_source,
        user_min_alert_size,
        alert_area_selection,
    )

    # The downloaded filtered-alert raster is staged locally for the vectorizer
    # and published into the current recipe folder, named with the selected
    # source(s) + AOI date range (self-describing in the user's workspace).
    out_tif, remote_tif = _filtered_alerts_path(
        workspace,
        request.recipe_folder,
        alert_source,
        request.start_date,
        request.end_date,
    )
    return AnalyzePlan(
        selections=selections,
        filtered_alert_raster=filtered_alert_raster,
        region=poly,
        pixel_size=pixel_size,
        min_size_pixels=min_size_pixels,
        out_tif=out_tif,
        remote_tif=remote_tif,
    )


def _download_alert_raster(plan: AnalyzePlan) -> str:
    """Export the filtered alert raster to a local GeoTIFF. Pure, thread-only.

    First half of the OLD ``run_local_vectorize._work``. Blocking EE + network,
    so it only ever runs inside ``asyncio.to_thread``.

    Concurrent geedim exports are serialized inside ``download_ee_image`` itself
    (geedim has ONE process-wide asyncio runner). That matters here because
    cancelling Analyze abandons the coroutine but not this thread, so an
    immediately restarted run would otherwise collide with the download it just
    walked away from — as would the Analysis dialog's imagery/export threads.
    """
    # geedim exports over an ee.Geometry; the AOI / drawn poly is a
    # FeatureCollection, so pass its geometry.
    region = plan.region
    export_region = (
        region.geometry() if isinstance(region, ee.FeatureCollection) else region
    )
    # alert raster: band order [alert, date]; 0 = no-alert so unmask to 255.
    download_ee_image(
        plan.filtered_alert_raster.select(["alert", "date"]),
        plan.out_tif,
        region=export_region,
        scale=plan.pixel_size,
        crs="EPSG:4326",
        unmask_value=255,
        nodata_value=255,
    )
    return plan.out_tif


def _publish_alert_raster(plan: AnalyzePlan, workspace):
    """Upload the staged filtered-alert raster into the user's recipe folder.

    The raster is a deliverable the user asked for (it is named after the sources
    and dates they picked), so it goes to their SEPAL workspace — but geedim can
    only write locally, hence download-then-upload. No-op without a recipe folder.
    Returns the remote path written, or ``None``.
    """
    if not plan.remote_tif:
        return None
    return workspace.upload_from_scratch(plan.out_tif, plan.remote_tif)


def _vectorize_alert_tif(plan: AnalyzePlan):
    """Vectorize the downloaded GeoTIFF. Pure, CPU-heavy, thread-only."""
    return vectorize_alert_raster(
        plan.out_tif, min_cluster_size=plan.min_size_pixels, connectivity=8
    )


def _finish_analyze_sync(request: AnalyzeRequest, plan: AnalyzePlan, gdf):
    """Assemble the final outcome from the raw vectorize result. Pure.

    ``convert_to_geopandas`` builds the status/point/alert_sources columns that
    Overview + the analysis tile consume; ``custom_name`` labels the user-defined
    source (ten-thousands code slot) in the decoded ``alert_sources`` column.
    Nothing is published here — the completion effect owns that.
    """
    max_number_alerts = plan.selections.max_number_alerts
    return AnalyzeOutcome(
        selections=plan.selections,
        filtered_alert_raster=plan.filtered_alert_raster,
        alerts_total_bbs=gdf,
        alerts_bbs=gdf.head(max_number_alerts),
        alerts_gdf=convert_to_geopandas(gdf, custom_name=request.custom_alert_name),
        alert_count=len(gdf),
        # Carried from the click-time snapshot so the completion effect writes
        # the store into the folder THIS run was configured with, even if a
        # recipe was loaded while it was in flight.
        recipe_folder=request.recipe_folder,
    )


def _persist_alerts_gdf(alerts_gdf, recipe_folder, workspace) -> str | None:
    """Write the freshly vectorized gdf to the canonical parquet store.

    Returns ``None`` when the store was written (or when there is no recipe
    folder to write it into — smoke / pre-recipe runs), and the failure message
    otherwise. It does NOT raise: the analyze itself succeeded, and the alerts
    are already in memory, so a store failure must degrade the run's feedback,
    not throw the results away. The caller turns a returned message into an error
    toast — reporting a failed store write as a success is exactly the bug this
    return value exists to prevent.

    This is persisted STATE, not a scratch artifact: recipe load reads
    ``alert_db.parquet`` back (``recipe_helper.load_alert_store``, and the
    recipe-export zip in ``gui.widget.custom_sw``), so a run the user cancelled
    must never leave one behind — see the explicit cancellation re-check the task
    body does before calling this. Writing it at analyze time, not just on Save,
    means a reload before Save resumes without re-running EE.

    Blocking: a GeoParquet serialize plus an upload of the whole store. Callers
    must run it in a thread (``asyncio.to_thread``), never on the kernel loop.

    ``recipe_folder`` is a REMOTE path in the user's SEPAL workspace, so the join
    is posix and the write goes through ``workspace``.
    """
    if not recipe_folder:
        return None
    try:
        save_gdf_to_parquet(
            alerts_gdf,
            workspace,
            str(PurePosixPath(recipe_folder) / "alert_db.parquet"),
        )
    except Exception as exc:  # noqa: BLE001 — surfaced as an error toast
        logger.exception("Filter: failed to persist alert_db.parquet")
        return str(exc) or exc.__class__.__name__
    return None


def _analyze_feedback(outcome: AnalyzeOutcome) -> tuple[str, str]:
    """``(toast type, message)`` for a finished analyze.

    Split out of the completion effect so the "a failed alert-store write is not
    a success" rule is directly testable: the effect only routes the pair to the
    matching notifier method.
    """
    if outcome.persist_error:
        return "error", str(
            cm.filter_tile.analyze_store_failed.format(
                n=outcome.alert_count, error=outcome.persist_error
            )
        )
    return "success", str(cm.filter_tile.analyze_done.format(n=outcome.alert_count))


@solara.component
def AlertsFilterTile(
    aoi_date_model,
    aux_model,
    alert_filter_model,
    selected_alerts_model,
    analyzed_alerts_model,
    app_state,
    gee_interface,
    workspace,
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
    workspace : Workspace
        Session user-file adapter (built in ``Page()``); the vectorized alert
        store is written through it into the user's SEPAL workspace.
    map_ : SepalMap
        The single shared map; this tile attaches its draw/inspector controls and
        alert/AOI/aux layers to it.
    """
    # ------------------------------------------------------------------
    # Attach this tile's draw + inspector controls to the SHARED map (no own map),
    # and track the layer names this tile adds so a refresh removes only its own
    # layers (never remove_all on the shared map).
    # ------------------------------------------------------------------
    # This is a Solara component, so user feedback goes through the HOOK
    # (``use_notifications()``), never the ``notify()`` global: the hook binds
    # the notifier to the bus of the kernel that is rendering. The Notifier is
    # memoized on that bus, so the closure the analyze coroutine captures stays
    # valid for the life of the session.
    notifications = use_notifications()

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
    # map tile, and clear them when navigating to another right-panel tile, so they
    # don't bleed into AOI / Overview. Keyed on active_map_view (NOT
    # current_page_view) so the "analyze" -> Analysis dialog flow leaves these
    # layers drawn behind the popup; it re-runs on every nav between panel tiles:
    # on mount it clears (a no-op), and in the AOI-search flow (search populates
    # the model, then navigates here) it draws.
    # The form half of the refresh is plain widget mutation and stays inline; the
    # map half (a stack of blocking ``add_ee_layer`` calls) is handed to the async
    # layer task, which runs it in ``asyncio.to_thread``. ``layer_generation``
    # makes a superseded rebuild a no-op: cancelling a task abandons the coroutine
    # but not the thread it started, so a slow draw from a previous activation
    # could otherwise re-add layers after a later clear.
    active = app_state.active_map_view.value == "filter_alerts"
    layer_generation = solara.use_memo(lambda: {"value": 0}, dependencies=[])

    def _apply_filter_layers(request: FilterLayerRequest):
        if layer_generation["value"] != request.token:
            return
        if request.show:
            _refresh_map_layers(
                map_widget,
                alert_source_select,
                aoi_date_model,
                aux_model,
                alert_filter_model,
                tracked,
            )
        else:
            _clear_filter_layers(map_widget, tracked)

    async def _sync_filter_layers(request: FilterLayerRequest):
        await asyncio.to_thread(_apply_filter_layers, request)
        return request

    layer_task = solara.lab.use_task(
        _sync_filter_layers, dependencies=None, prefer_threaded=False, raise_error=False
    )

    def _schedule_layer_sync(show: bool):
        layer_generation["value"] += 1
        request = FilterLayerRequest(show=show, token=layer_generation["value"])
        if _loop_is_running():
            layer_task(request)
        else:
            # See _loop_is_running: without a loop on this thread the task would
            # raise out of the effect and take the render down, so fall back to
            # the pre-refactor inline behaviour.
            _apply_filter_layers(request)

    def _refresh_tile():
        """Form first (inline), then the map rebuild (task -> thread).

        Parity with the OLD single ``_refresh_layout``: with no alerts available
        it only collapses the form and leaves the map untouched.
        """
        if _refresh_form(alert_source_select, cards, alert_filter_model):
            _schedule_layer_sync(True)

    def _sync_active():
        if active:
            _refresh_tile()
        else:
            _schedule_layer_sync(False)

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
            # the active map tile. Otherwise (e.g. AOI's search populates the raster
            # list while the user is still on AOI) skip — the activation effect
            # above redraws from the model when the tile becomes active. Keyed on
            # active_map_view (NOT current_page_view) so a model
            # update arriving while a popup overlays this tile still refreshes the
            # layers underneath rather than being dropped as "inactive".
            if app_state.active_map_view.value != "filter_alerts":
                return
            # The AOI search now applies its results from a completion effect on
            # the kernel loop, so this handler fires there too — which is what
            # lets it schedule the layer task (a coroutine task needs a running
            # loop in the calling thread).
            _refresh_tile()

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
    async def _analyze(request: AnalyzeRequest) -> AnalyzeOutcome:
        """Stage the analyze run on the kernel loop; every stage in a thread.

        The tracker milestones are the only mutation in this coroutine — they run
        on the loop, between stages, and replace the reactive status line the tile
        used to render inline. Everything else is a pure worker; results reach the
        models only via ``_apply_analyze``.

        ``total_steps=4`` is the number of phases that actually exist here and
        already had a status message: join → download → vectorize → assemble.
        (The optional raster attribution between vectorize and assemble is a
        no-op unless configured and never had a phase message, so it rides along
        inside the "assembling" step rather than inflating the count.)
        """
        plan = None
        with notifications.track(
            cm.filter_tile.analyze_task_title, total_steps=4
        ) as task:
            try:
                # "joining" = unioning the selected sources into one raster.
                task.step(
                    cm.filter_tile.analyze_status_joining.format(
                        n=len(request.alert_sources or [])
                    )
                )
                plan = await asyncio.to_thread(
                    _prepare_analyze_sync, request, workspace
                )

                # Download + vectorize locally (replaces the OLD GEE reduceToVectors).
                task.step(cm.filter_tile.analyze_status_downloading)
                await asyncio.to_thread(_download_alert_raster, plan)
                await asyncio.to_thread(_publish_alert_raster, plan, workspace)

                task.step(cm.filter_tile.analyze_status_vectorizing)
                gdf = await asyncio.to_thread(_vectorize_alert_tif, plan)

                # Optional: attribute each alert with per-band stats from an extra
                # raster (GEE asset or local file). No-op when unconfigured. Fails
                # loud (the use_task surfaces the error) if configured but the
                # raster/band is bad — so we never publish alerts that are
                # silently missing the columns.
                gdf = await asyncio.to_thread(
                    attribute_alerts_from_config,
                    gdf,
                    request.attribute_raster,
                    plan.region,
                    workspace,
                )

                outcome = await asyncio.to_thread(
                    _finish_analyze_sync, request, plan, gdf
                )

                # Persisted state (alert_db.parquet). It is written HERE, in a
                # thread, and no longer from the completion effect: that effect
                # runs on the kernel loop, so every analyze used to block the UI
                # thread for a whole GeoParquet serialize + an upload of the
                # entire alert store.
                #
                # The property the completion effect gave us — a cancelled run
                # must never leave a loadable alert_db.parquet behind — is kept by
                # an explicit cancellation re-check: ``await asyncio.sleep(0)``
                # yields to the loop, so a cancel requested at any point up to
                # here raises CancelledError before anything is written. The check
                # goes BEFORE the call because cancelling a ``to_thread`` cannot
                # stop the thread once it has started.
                await asyncio.sleep(0)
                persist_error = await asyncio.to_thread(
                    _persist_alerts_gdf,
                    outcome.alerts_gdf,
                    outcome.recipe_folder,
                    workspace,
                )
                outcome = dataclasses.replace(outcome, persist_error=persist_error)

                task.step(
                    cm.filter_tile.analyze_status_found.format(n=outcome.alert_count)
                )
                return outcome
            except Exception as exc:
                # fail() BEFORE the error toast, and before the tracker's own
                # __exit__ runs: an explicitly failed tracker is already
                # "finished", so __exit__ will not publish a second (raw) error
                # toast on top of the one ``_apply_analyze`` publishes. The run
                # is recorded as FAILED in the task log (the pill only shows
                # RUNNING tasks, so it disappears rather than staying on
                # screen). asyncio.CancelledError is a BaseException and
                # deliberately not caught here — __exit__ marks it CANCELLED.
                task.fail(str(exc))
                raise
            finally:
                # The staged raster has been published (and vectorized) by now;
                # leaving it behind would keep a full copy of every analyze run
                # in the container's tmp for the life of the session.
                if plan is not None:
                    workspace.discard_scratch(plan.out_tif)

    analyze_task = solara.lab.use_task(
        _analyze, dependencies=None, prefer_threaded=False, raise_error=False
    )
    analyze_started_at = solara.use_ref(0.0)
    analyze_cancel_reason = solara.use_ref(None)

    def _start_analyze():
        # Synchronous pending guard: a render-time ``disabled=`` cannot stop the
        # second press of a double click (it is dispatched against the button as
        # it was before the re-render), but this can.
        if analyze_task.pending:
            return
        analyze_cancel_reason.current = None
        analyze_started_at.current = time.monotonic()
        logger.info(
            "Filter: analyze started for %s", alert_source_select.v_model
        )
        # Snapshot the whole form + the model inputs at click time, so the workers
        # can never observe a half-changed form or a model another tile is writing.
        analyze_task(
            AnalyzeRequest(
                alert_sources=alert_source_select.v_model,
                min_area=min_area_input.v_model,
                area_selection=alert_selection_method_select.v_model,
                sorting_method=alert_sorting_select.v_model,
                max_alerts=number_of_alerts.v_model,
                drawn_geojson=drawn_item.to_json(),
                alerts_raster_dictionary=alert_filter_model.available_alerts_raster_list,
                mask_layer=aux_model.mask_layer,
                feature_collection=aoi_date_model.feature_collection,
                start_date=aoi_date_model.start_date,
                end_date=aoi_date_model.end_date,
                recipe_folder=app_state.recipe_folder_path.value,
                attribute_raster=aux_model.attribute_raster,
                custom_alert_name=aux_model.custom_alert_name,
            )
        )

    def _apply_analyze():
        # The ONLY place analyze results reach the models. A cancelled run leaves
        # every model and the current page exactly as they were.
        if analyze_task.cancelled:
            logger.info("Filter: analyze cancelled — models and page unchanged")
            return
        if analyze_task.error:
            # ``analyze_task.exception`` is the raised exception; ``analyze_task.error``
            # is a bool, so str()-ing it would publish "True".
            logger.error("Filter: analyze failed", exc_info=analyze_task.exception)
            notifications.error(str(analyze_task.exception))
            return
        if not analyze_task.finished:
            return
        outcome = analyze_task.value
        if outcome is None:
            return

        # Save the user selections onto selected_alerts_model (parity). This now
        # runs on the kernel loop, so any traitlets `.observe` callbacks on
        # selected_alerts_model fire there too rather than on a worker thread.
        selections = outcome.selections
        selected_alerts_model.selected_alert_sources = selections.selected_alert_sources
        selected_alerts_model.alert_selection_area = selections.alert_selection_area
        selected_alerts_model.alert_sorting_method = selections.alert_sorting_method
        selected_alerts_model.min_area = selections.min_area
        selected_alerts_model.max_number_alerts = selections.max_number_alerts
        selected_alerts_model.alert_selection_polygons = (
            selections.alert_selection_polygons
        )
        selected_alerts_model.alert_selection_area_n = selections.alert_selection_area_n
        selected_alerts_model.alert_sorting_method_n = selections.alert_sorting_method_n
        selected_alerts_model.filtered_alert_raster = outcome.filtered_alert_raster

        # TODO(Recipe tile): the OLD tile also created the recipe directory
        # (create_recipe_directory) and saved recipe parameters
        # (save_recipe_parameters) here, plus built the before/after Planet
        # monthly-image dictionaries (create_planet_images_dictionary) onto
        # analyzed_alerts_model. Those pull in app_tile_model.recipe_folder_path
        # plumbing that is not wired into the Solara Page() yet, so they are
        # deferred to the Recipe tile task. The alerts_gdf publish below does not
        # need them.

        # Parity: publish the raw vectorize sets onto selected_alerts_model
        # (the OLD tile set these; the still-traitlets analysis tile reads them).
        selected_alerts_model.alerts_total_bbs = outcome.alerts_total_bbs
        selected_alerts_model.alerts_bbs = outcome.alerts_bbs
        selected_alerts_model.received_alerts = True

        # Publish the assembled alerts_gdf — this is what Overview + the analysis
        # tile consume.
        app_state.alerts_gdf.value = outcome.alerts_gdf
        logger.info("Filter: analyze finished — %s alerts", outcome.alert_count)
        # The alert store was written inside the task (off the kernel loop). If
        # that write failed the alerts still exist in memory and the page still
        # advances, but the user is told — a store failure was previously
        # swallowed and reported as a success.
        type_, message = _analyze_feedback(outcome)
        if type_ == "error":
            logger.error(
                "Filter: alert store write failed — %s", outcome.persist_error
            )
            notifications.error(message)
        else:
            notifications.success(message)

        # Jump to the analysis tile (as the OLD tile did). The shell's _NavBridge
        # translates this current_page_view write into the MapApp step change.
        app_state.current_page_view.value = "analysis_tile"

    solara.use_effect(
        _apply_analyze,
        [
            analyze_task.pending,
            analyze_task.finished,
            analyze_task.error,
            analyze_task.cancelled,
        ],
    )

    analyze_button_props = use_task_button(
        analyze_task, on_start=_start_analyze, cancel_reason_ref=analyze_cancel_reason
    )
    analyze_button_props["on_cancel"] = _guard_cancel(
        analyze_button_props["on_cancel"], analyze_started_at
    )

    # ------------------------------------------------------------------
    # Render layout: controls only (this widget IS the right-panel section
    # content). The alert/AOI/aux layers are drawn on the single shared map.
    # ------------------------------------------------------------------
    with solara.Column(style="padding: 0;"):
        with solara.Row(style="gap:4px;align-items:center;padding:0 12px;"):
            solara.Text(cm.filter_tile.description)
            InfoButton(cm.filter_tile.info_header, cm.filter_tile.info_md)
        solara.display(cards["no_alerts"])
        solara.display(cards["source"])
        solara.display(cards["min_area"])
        solara.display(cards["area_method"])
        solara.display(cards["sorting"])
        solara.display(cards["max_number"])
        with solara.Div(style="padding: 12px;"):
            TaskButtonComponent(
                label=cm.filter_tile.analyze_alerts_button,
                **analyze_button_props,
                block=True,
            )
        # No inline status/error elements here: progress is the notification
        # task pill (``notifications.track`` in ``_analyze``) and the outcome is
        # a toast from ``_apply_analyze``. The cards above are static form
        # guidance (labels/hints), not feedback, so they stay.


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
