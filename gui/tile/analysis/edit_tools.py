"""build_edit_tools sub-component (Task 9.3 / B2): draw-edit + CNN models.

Architecture layer: view (raw-widget builder, no ``@solara.component``). Ports
the OLD ``AnalysisTile`` ``card04`` interaction as ONE coherent unit, because the
polygon draw/edit controls and the two CNN models share the draw controls and the
DL toolbars are only shown during edit mode.

WHY A RAW WIDGET (not a Solara component + ``solara.display``): MapApp renders the
Analysis dialog with ``v-if``, so it DESTROYS the dialog DOM on close and REBUILDS
it on reopen. Content captured via ``solara.display`` lives inside an ``Output``
whose view is NOT recreated on that rebuild, so it vanishes the 2nd time the dialog
is opened. A plain ipyvuetify widget tree is re-viewed from its model on reopen, so
it survives. ``build_edit_tools`` builds the card once and RETURNS it; the parent
hands it to MapApp as dialog content — same pattern as ``navigator`` /
``review_panel`` and ``recipe_tile.build_recipe_layout``.

Ported OLD handlers:

* Edit (``toolBarEdition``): ``start_edition_function`` / ``save_edition_function``
  / ``stop_edition_function`` / ``clear_edition_function`` — toggle the two
  ``CustomDrawControl``s (``draw_before`` / ``draw_after`` on ``widgets``) + the
  DL toolbars + the Alert-BB / Defo-Layer map-layer visibility, and persist the
  user-drawn polygon to ``analysis.defo_layer``.
* CNN (``toolBarDL1`` / ``toolBarDL2``): ``run_dl_model_1`` / ``run_dl_model_2``
  (download both images + ``cnn_worker.apply_dl_model`` with a FILE-PATH model arg),
  ``verify_model*_output`` + ``enable_dl*`` (show/hide the add/remove/msg buttons),
  ``add_model*_prediction`` / ``remove_model*_prediction`` (mutate
  ``draw_before.data`` from the prediction raster).
* ``add_defo_layer`` — ``wire_defo_layer`` subscribes to ``analysis.defo_layer``
  and adds a "Defo Layer" GeoData layer to both maps.

Threading (FIXED by the task contract):

* Each CNN model runs button-triggered via ``spawn_in_context`` (CPU/IO-bound;
  never auto-run) — same pattern as ``recipe_tile``'s load/save workers. The worker
  builds the image name, downloads both images, pulls the HF model (idempotent /
  cached), runs ``apply_dl_model`` -> the prediction ``.tif`` path, and then (still on
  the worker thread, in the success path) runs the OLD ``verify_model*_output`` /
  ``enable_dl*`` apply step with that path: stores it in
  ``analysis.prediction_files`` and shows the add/remove buttons.
  The run button spins (``.loading``/``.disabled``) for the duration, the run is a
  tracked task in the notification pill (``notify_bridge.tracked_job``; the
  composite download drives its progress ring) and the outcome is a toast. Every
  run failure — download, inference or the verify/apply step — reaches the user as
  the friendly ``model_labels.dl_404`` text. ipywidgets trait
  sets from the worker thread are fine (recipe_tile does exactly this).
* add/remove prediction are main-thread click handlers reading the stored
  prediction path.
* The edit + DL toolbars + buttons are built once so the OLD ``.show()/.hide()/
  .disabled`` imperative calls port unchanged.
* ``wire_defo_layer`` is a ``defo_layer.subscribe`` registered once (it
  replaces the traitlets observer): it fires whenever a new layer is set.

Guards: everything no-ops safely with no alert loaded (smoke time): buttons render
but the CNN/edit click paths are not exercised, and the defo-layer observer is
registered but ``defo_layer`` stays ``None``. EE / raster / file failures are
caught and published as an error toast (or logged) so a failure never breaks render.

Feedback: the CNN run workers publish through ``notify_bridge.tracked_job`` +
``current_notifier``, not the ``use_notifications()`` hook: this module builds raw
ipyvuetify widgets and publishes from daemon worker threads, so there is no
component render to hold a hook. ``spawn_in_context`` carries the kernel context
``current_notifier`` resolves the bus from. ``tracked_job`` turns the worker
thread's app log lines into task milestones and, on a failure, marks the task
FAILED before it publishes the one error toast. The main-thread add-prediction
handler (no task) still uses the ``notify`` global for its error toast.

Props
-----
widgets : AnalysisWidgets
    Shared heavy widgets; the edit buttons toggle ``draw_before`` / ``draw_after``
    visibility + read/write their ``.data`` / ``.to_json()``, and the CNN reads the
    selected images + grid scratch slots.
app_state : AppState
    Navigation + alerts source of truth (``actual_alert_id``).
analysis : AnalysisState
    ``defo_layer`` (the user-drawn polygon GeoDataFrame), the chosen
    ``before/after_image`` + ``before/after_info`` the CNN downloads, and
    ``prediction_files`` (the CNN rasters by model number).
app_tile_model : SetContext
    ``recipe_folder_path`` — the active set's folder the before/after composite
    and the CNN's prediction raster are written to.
"""

import logging

import ipyvuetify as v
from ipyleaflet import GeoData
from pysepal import color, sepalwidgets as sw
from pysepal.solara.notifications import notify

from deforestation_alerts import gdal_env
from deforestation_alerts.scripts.cnn_worker import apply_dl_model
from deforestation_alerts.scripts.gee_progress import geedim_tile_progress
from deforestation_alerts.scripts.mosaics_helper import (
    convertir_formato3,
    filter_features_by_color,
    geojson_to_geodataframe,
    raster_to_gdf,
    simplify_and_extract_features,
    verify_raster,
)
from gui.i18n import t
from gui.scripts.notify_bridge import (
    current_notifier,
    tile_progress_reporter,
    tracked_job,
)
from gui.scripts.solara_threads import spawn_in_context
from gui.store.project_writers import writer_name, writing
from gui.tile.analysis.paths import stage_alert_image

logger = logging.getLogger("SEPALUI.deforestation_alerts.analysis")

# OLD add_defo_layer / Defo Layer style.
_DEFO_LAYER_STYLE = {"color": "orange", "fillOpacity": "0"}

# HuggingFace model files (OLD pulled these in the worker startup); idempotent.
_MODEL1_REPO = "joseserafinig/forest_cover_change_cnn_m1"
_MODEL1_FILE = "Model1.h5"
_MODEL2_REPO = "joseserafinig/forest_cover_change_cnn_m2"
_MODEL2_FILE = "Model2.keras"

# CNN prediction probability cutoff (OLD passed 0.20 to verify_raster/raster_to_gdf)
# and polygon simplify tolerance in metres (OLD passed 15 to
# simplify_and_extract_features).
_PRED_THRESHOLD = 0.20
_SIMPLIFY_TOLERANCE = 15


class _EditionButton(v.Btn, sw.SepalWidget):
    """OLD ``EditionButtons``: small disabled-by-default icon button with .show/.hide."""

    def __init__(self, **kwargs):
        kwargs.setdefault("small", True)
        kwargs.setdefault("class_", "pa-1 ma-1")
        kwargs.setdefault("disabled", True)
        super().__init__(**kwargs)


def _run_cnn_model(
    model_repo,
    model_file,
    suffix,
    widgets,
    analysis,
    app_state,
    app_tile_model,
    *,
    task=None,
):
    """Body of the OLD ``run_dl_model_*`` (worker side), without widget mutation.

    Gets the before/after composite for the current alert (downloaded once
    into the active set's folder — see ``paths.stage_alert_image``), pulls the
    HF model (cached), runs ``apply_dl_model``, and returns the prediction
    ``.tif`` path. Runs on the daemon worker thread; the widget mutation
    (verify/enable, add/remove) happens separately, never inside this function.

    ``apply_dl_model`` writes the prediction raster next to the composite (same
    stem + ``_prediction<suffix>.tif``), so the composite and its prediction
    both stay in the set folder, on a first run and on a cache hit alike. The
    prediction path outlives this call: it is stashed in
    ``analysis.prediction_files`` and read again every time the user
    adds/removes the prediction overlay.

    ``task`` (the worker's ``TaskTracker``, optional) gets the composite
    download's geedim tile ticks as progress, and is handed back to
    indeterminate before the model pull + inference. A cache hit produces no
    ticks.
    """
    from huggingface_hub import hf_hub_download

    actual_alert_id = app_state.actual_alert_id.value

    on_tiles = tile_progress_reporter(task) if task is not None else None
    with geedim_tile_progress(on_tiles):
        image_local = stage_alert_image(
            app_tile_model.recipe_folder_path,
            actual_alert_id,
            analysis.before_info.value,
            analysis.after_info.value,
            analysis.before_image.value,
            analysis.after_image.value,
            widgets.actual_alert_grid,
        )
    if task is not None:
        task.set_progress(None)  # model pull + inference: back to indeterminate

    # The CNN weights are a container-local cache, not user data — but they must
    # land in the one directory this app has proven writable, not a hard-coded
    # ``/tmp`` (inventory c18).
    model_path = hf_hub_download(
        repo_id=model_repo, filename=model_file, local_dir=str(gdal_env.scratch_dir())
    )
    return apply_dl_model(str(image_local), model_path, suffix)


def wire_defo_layer(widgets, analysis):
    """Draw the reviewed polygon on both maps whenever a new one is set (the
    old ``.observe("defo_dl_layer")``). Returns the unsubscribe."""

    def _on_layer(gdf):
        if gdf is None:
            return
        try:
            layer = GeoData(geo_dataframe=gdf, name="Defo Layer", style=_DEFO_LAYER_STYLE)
            widgets.map_before.add_layer(layer)
            widgets.map_after.add_layer(layer)
        except Exception:
            logger.exception("Analysis: add_defo_layer failed")

    return analysis.defo_layer.subscribe(_on_layer)


def build_edit_tools(
    widgets,
    app_state,
    analysis,
    app_tile_model,
):
    """Build and return the edit-tools + CNN card (OLD ``card04``).

    The before/after composite the CNN runs on is a file of the active set's
    folder (see ``paths.stage_alert_image``).
    """

    # ------------------------------------------------------------------
    # Widgets (created once so the OLD .show()/.hide()/.disabled imperative
    # toggles port unchanged). Bundled in a small namespace so the handlers below
    # can mutate them in place.
    # ------------------------------------------------------------------
    def _build_widgets():
        ns = {}

        # --- edit toolbar (start / save / stop) ---
        ns["start_btn"] = _EditionButton(
            color=color.primary,
            disabled=False,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-pen"])],
        )
        ns["clear_btn"] = _EditionButton(
            color=color.error,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-trash"])],
        )
        ns["save_btn"] = _EditionButton(
            color=color.primary,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-floppy-disk"])],
        )
        ns["stop_btn"] = _EditionButton(
            color=color.secondary,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-x"])],
        )

        tt_start = sw.Tooltip(
            ns["start_btn"],
            tooltip=t("analysis_tile.edition_labels.start_edit_button"),
            top=True, open_delay=100, close_delay=100,
        )
        tt_save = sw.Tooltip(
            ns["save_btn"],
            tooltip=t("analysis_tile.edition_labels.save_edit_button"),
            top=True, open_delay=100, close_delay=100,
        )
        tt_stop = sw.Tooltip(
            ns["stop_btn"],
            tooltip=t("analysis_tile.edition_labels.stop_edit_button"),
            top=True, open_delay=100, close_delay=100,
        )
        ns["toolbar_edition"] = v.Toolbar(
            class_="px-1 d-flex justify-space-around",
            children=[tt_start, tt_save, tt_stop],
        )

        # --- DL model 1 toolbar (run / add / remove / msg) ---
        ns["dl1_run"] = v.Btn(
            class_="pa-1 ma-1", color=color.secondary, rounded=True, small=True,
            children=["M 1"],
        )
        ns["dl1_add"] = _EditionButton(
            color=color.primary,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-plus"])],
        )
        ns["dl1_remove"] = _EditionButton(
            color=color.error,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-minus"])],
        )
        ns["dl1_msg"] = _EditionButton(
            color=color.error, disabled=False,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-triangle-exclamation"])],
        ).hide()
        tt_dl1 = sw.Tooltip(ns["dl1_run"], tooltip=t("analysis_tile.model_labels.dl_button1"),
                            top=True, open_delay=100, close_delay=100)
        tt_dl1_add = sw.Tooltip(ns["dl1_add"], tooltip=t("analysis_tile.model_labels.dl_button_add"),
                                top=True, open_delay=100, close_delay=100)
        tt_dl1_rem = sw.Tooltip(ns["dl1_remove"], tooltip=t("analysis_tile.model_labels.dl_button_remove"),
                                top=True, open_delay=100, close_delay=100)
        tt_dl1_msg = sw.Tooltip(ns["dl1_msg"], tooltip=t("analysis_tile.model_labels.dl_404"),
                                top=True, open_delay=100, close_delay=100)
        ns["toolbar_dl1"] = sw.Toolbar(
            children=[tt_dl1, tt_dl1_add, tt_dl1_rem, tt_dl1_msg]
        ).hide()

        # --- DL model 2 toolbar (run / add / remove / msg) ---
        ns["dl2_run"] = v.Btn(
            class_="pa-1 ma-1", color=color.secondary, rounded=True, small=True,
            children=["M 2"],
        )
        ns["dl2_add"] = _EditionButton(
            color=color.primary,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-plus"])],
        )
        ns["dl2_remove"] = _EditionButton(
            color=color.error,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-minus"])],
        )
        ns["dl2_msg"] = _EditionButton(
            color=color.error, disabled=False,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-triangle-exclamation"])],
        ).hide()
        tt_dl2 = sw.Tooltip(ns["dl2_run"], tooltip=t("analysis_tile.model_labels.dl_button2"),
                            top=True, open_delay=100, close_delay=100)
        tt_dl2_add = sw.Tooltip(ns["dl2_add"], tooltip=t("analysis_tile.model_labels.dl_button_add"),
                                top=True, open_delay=100, close_delay=100)
        tt_dl2_rem = sw.Tooltip(ns["dl2_remove"], tooltip=t("analysis_tile.model_labels.dl_button_remove"),
                                top=True, open_delay=100, close_delay=100)
        tt_dl2_msg = sw.Tooltip(ns["dl2_msg"], tooltip=t("analysis_tile.model_labels.dl_404"),
                                top=True, open_delay=100, close_delay=100)
        ns["toolbar_dl2"] = sw.Toolbar(
            children=[tt_dl2, tt_dl2_add, tt_dl2_rem, tt_dl2_msg]
        ).hide()

        return ns

    ns = _build_widgets()

    # ------------------------------------------------------------------
    # enable_dl1 / enable_dl2 (OLD) — show/hide the add/remove/msg buttons.
    # ------------------------------------------------------------------
    def _enable_dl1(state):
        if state:
            ns["dl1_msg"].hide()
            ns["dl1_add"].show()
            ns["dl1_remove"].show()
            ns["dl1_add"].disabled = False
        else:
            ns["dl1_msg"].show()
            ns["dl1_add"].hide()
            ns["dl1_remove"].hide()

    def _enable_dl2(state):
        if state:
            ns["dl2_msg"].hide()
            ns["dl2_add"].show()
            ns["dl2_remove"].show()
            ns["dl2_add"].disabled = False
        else:
            ns["dl2_msg"].show()
            ns["dl2_add"].hide()
            ns["dl2_remove"].hide()

    # ------------------------------------------------------------------
    # verify_model*_output (OLD) — applied at the END of the worker's success
    # path with the worker's computed prediction path (replaces the OLD
    # model*_prediction_time observer / the use_task-value effect).
    # ------------------------------------------------------------------
    # Both run INSIDE the worker's tracked job and simply raise on failure: a
    # verify failure then fails the task and publishes the one dl_404 error toast
    # through tracked_job, exactly like a failed download/inference.
    def _record_prediction(model, pred_path):
        # The two runs are separate workers and can finish together: replace
        # the dict in one locked read-and-set, never mutate it in place, so
        # neither run drops the other's path.
        files = analysis.prediction_files
        with files.lock:
            files.set({**files.value, model: pred_path})

    # A run belongs to the alert it started on (``token``, captured at the
    # click): one that ends after the user moved on must not record its
    # raster or enable "add" for the alert now on screen, whose review Save
    # would store the other alert's polygons. Returns whether it applied.
    def _apply_model(model, pred_path, token):
        if not analysis.is_current(token):
            return False
        valid = verify_raster(pred_path, _PRED_THRESHOLD)
        enable = _enable_dl1 if model == 1 else _enable_dl2

        def _apply():
            _record_prediction(model, pred_path)
            enable(valid)

        return analysis.apply_if_current(token, _apply)

    # ------------------------------------------------------------------
    # An alert change (``AnalysisState.reset_alert``) puts the card back as it
    # was built: out of edit mode, nothing drawn, the add/remove buttons
    # disabled. The drawn features and an enabled "add" belonged to the
    # previous alert.
    # ------------------------------------------------------------------
    def _reset_for_new_alert(_token=None):
        try:
            for n in (1, 2):
                ns[f"dl{n}_msg"].hide()
                ns[f"dl{n}_add"].show()
                ns[f"dl{n}_remove"].show()
                ns[f"dl{n}_add"].disabled = True
                ns[f"dl{n}_remove"].disabled = True
                ns[f"toolbar_dl{n}"].hide()
            for name in ("save_btn", "clear_btn", "stop_btn"):
                ns[name].disabled = True
            ns["start_btn"].disabled = False
            if widgets is not None:
                widgets.draw_before.clear()
                widgets.draw_after.clear()
        except Exception:
            logger.exception("Analysis: resetting the edit tools failed")

    # ------------------------------------------------------------------
    # add/remove prediction (OLD) — main-thread click handlers mutating
    # widgets.draw_before.data (jslink'd to draw_after; write both via
    # set_synced_draw_data since Python-originated writes propagate to
    # draw_after only through the explicit helper, never headlessly).
    # ------------------------------------------------------------------
    def _add_model_prediction(path, color_name, add_btn, remove_btn):
        if not path:
            return
        try:
            defo_gdf_layer = raster_to_gdf(path, "4326", _PRED_THRESHOLD)
            edit_layer = simplify_and_extract_features(
                defo_gdf_layer, "geometry", _SIMPLIFY_TOLERANCE
            )
            orig_features = widgets.draw_before.data
            new_features = convertir_formato3(edit_layer, color_name)
            widgets.draw_before.clear()
            widgets.draw_after.clear()
            widgets.set_synced_draw_data(orig_features + new_features)
            add_btn.disabled = True
            remove_btn.disabled = False
        except Exception:
            logger.exception("Analysis: add prediction failed")
            notify(t("analysis_tile.model_labels.dl_404"), type_="error")

    def _remove_model_prediction(color_name, add_btn, remove_btn):
        try:
            orig_features = widgets.draw_before.data
            widgets.draw_before.clear()
            widgets.draw_after.clear()
            widgets.set_synced_draw_data(filter_features_by_color(orig_features, color_name))
            add_btn.disabled = False
            remove_btn.disabled = True
        except Exception:
            logger.exception("Analysis: remove prediction failed")

    # ------------------------------------------------------------------
    # Edit handlers (start / save / stop / clear) — OLD *_edition_function.
    # ------------------------------------------------------------------
    def _start_edition(widget, event, data):
        widget.loading = True
        widget.disabled = True
        try:
            for m in (widgets.map_before, widgets.map_after):
                bb = m.find_layer("Alert BB", none_ok=True)
                if bb is not None:
                    bb.visible = False
                defo = m.find_layer("Defo Layer", none_ok=True)
                if defo is not None:
                    defo.visible = False

            widgets.draw_before.show()
            widgets.draw_after.show()
            defo_layer = analysis.defo_layer.value
            if defo_layer is not None:
                widgets.set_synced_draw_data(
                    convertir_formato3(
                        defo_layer.__geo_interface__["features"],
                        "blue",
                    )
                )

            ns["clear_btn"].disabled = False
            ns["save_btn"].disabled = False
            ns["stop_btn"].disabled = False
            ns["toolbar_dl1"].show()
            ns["toolbar_dl2"].show()
        except Exception:
            logger.exception("Analysis: start edition failed")
        finally:
            widget.loading = False

    def _save_edition(widget, event, data):
        widget.loading = True
        widget.disabled = True
        try:
            geometries = widgets.draw_before.to_json()["features"]
            if len(geometries) > 0:
                analysis.defo_layer.set(geojson_to_geodataframe(
                    widgets.draw_before.to_json()
                ).set_crs(epsg="4326", allow_override=True, inplace=True))
        except Exception:
            logger.exception("Analysis: save edition failed")
        finally:
            widget.loading = False
            widget.disabled = False

    def _stop_edition(widget, event, data):
        widget.loading = True
        widget.disabled = True
        try:
            widgets.draw_before.hide()
            widgets.draw_after.hide()

            for m in (widgets.map_before, widgets.map_after):
                bb = m.find_layer("Alert BB", none_ok=True)
                if bb is not None:
                    bb.visible = True
                defo = m.find_layer("Defo Layer", none_ok=True)
                if defo is not None:
                    defo.visible = True

            _enable_dl1(True)
            _enable_dl2(True)
            ns["toolbar_dl1"].hide()
            ns["toolbar_dl2"].hide()

            ns["dl1_add"].disabled = True
            ns["dl1_remove"].disabled = True
            ns["dl2_add"].disabled = True
            ns["dl2_remove"].disabled = True

            ns["save_btn"].disabled = True
            ns["clear_btn"].disabled = True
            ns["start_btn"].disabled = False
        except Exception:
            logger.exception("Analysis: stop edition failed")
        finally:
            widget.loading = False

    def _clear_edition(widget, event, data):
        widget.loading = True
        widget.disabled = True
        try:
            widgets.draw_before.clear()
            widgets.draw_after.clear()
        except Exception:
            logger.exception("Analysis: clear edition failed")
        finally:
            widget.loading = False
            widget.disabled = False

    # ------------------------------------------------------------------
    # CNN run handlers: button-triggered daemon threads (CPU/IO-bound; never
    # auto-run). The worker runs _run_cnn_model -> prediction path, then on the
    # success path applies the OLD verify/enable step with that path. The run
    # button spins for the duration; errors are published by tracked_job (FAILED
    # task + one dl_404 error toast), which also logs the raw traceback.
    # ------------------------------------------------------------------
    def _dl_404(_exc):
        # Every CNN run failure (composite download, HF model pull, inference,
        # verify) has always reached the user as this one friendly message; the
        # raw exception stays in the log.
        return t("analysis_tile.model_labels.dl_404")

    def _on_dl1_run(*_):
        ns["dl1_add"].disabled = True
        ns["dl1_remove"].disabled = True
        ns["dl1_run"].loading = True
        ns["dl1_run"].disabled = True
        token = analysis.alert_token.value  # the alert this run is for
        # Writer tracking (spec §12): the composite lands in the set's folder,
        # so the project is marked for the run and a delete is refused
        # meanwhile. The name is read now, at the click: the run keeps marking
        # the project it writes into after the user switched to another one.
        project = writer_name(app_state)

        def _worker():
            # No ``total_steps`` — the run is one download+inference phase; the
            # composite download drives the ring, the rest is indeterminate.
            notifier = current_notifier()
            try:
                with tracked_job(
                    notifier,
                    t("analysis_tile.model_labels.dl_task_title"),
                    error_format=_dl_404,
                ) as task:
                    task.step(t("analysis_tile.model_labels.dl_step_run"))
                    with writing(project):
                        pred_path = _run_cnn_model(
                            _MODEL1_REPO, _MODEL1_FILE, "_m1",
                            widgets, analysis, app_state, app_tile_model,
                            task=task,
                        )
                    applied = _apply_model(1, pred_path, token)
                    if not applied:
                        # Another alert is on screen: its result is dropped.
                        # A warning, recorded on the task before the toast.
                        reason = t("analysis_tile.model_labels.dl_stale")
                        task.fail(reason)
                        notifier.warning(reason)
            except Exception:  # noqa: BLE001 — tracked_job toasted and logged it (traceback included)
                pass
            else:
                if applied:
                    notifier.success(t("analysis_tile.model_labels.dl_done"))
            finally:
                ns["dl1_run"].loading = False
                ns["dl1_run"].disabled = False

        spawn_in_context(_worker)

    def _on_dl2_run(*_):
        ns["dl2_add"].disabled = True
        ns["dl2_remove"].disabled = True
        ns["dl2_run"].loading = True
        ns["dl2_run"].disabled = True
        token = analysis.alert_token.value  # the alert this run is for
        project = writer_name(app_state)  # as in _on_dl1_run

        def _worker():
            # Same tracked_job shape as _on_dl1_run above.
            notifier = current_notifier()
            try:
                with tracked_job(
                    notifier,
                    t("analysis_tile.model_labels.dl_task_title"),
                    error_format=_dl_404,
                ) as task:
                    task.step(t("analysis_tile.model_labels.dl_step_run"))
                    with writing(project):
                        pred_path = _run_cnn_model(
                            _MODEL2_REPO, _MODEL2_FILE, "_m2",
                            widgets, analysis, app_state, app_tile_model,
                            task=task,
                        )
                    applied = _apply_model(2, pred_path, token)
                    if not applied:
                        # Another alert is on screen: its result is dropped.
                        # A warning, recorded on the task before the toast.
                        reason = t("analysis_tile.model_labels.dl_stale")
                        task.fail(reason)
                        notifier.warning(reason)
            except Exception:  # noqa: BLE001 — tracked_job toasted and logged it (traceback included)
                pass
            else:
                if applied:
                    notifier.success(t("analysis_tile.model_labels.dl_done"))
            finally:
                ns["dl2_run"].loading = False
                ns["dl2_run"].disabled = False

        spawn_in_context(_worker)

    # ------------------------------------------------------------------
    # Wire all click handlers once (the widgets are built once per build).
    # ------------------------------------------------------------------
    def _register_handlers():
        ns["start_btn"].on_event("click", _start_edition)
        ns["save_btn"].on_event("click", _save_edition)
        ns["stop_btn"].on_event("click", _stop_edition)
        ns["clear_btn"].on_event("click", _clear_edition)

        def _on_dl1_add(*_):
            _add_model_prediction(analysis.prediction_files.value.get(1), "lime",
                                  ns["dl1_add"], ns["dl1_remove"])

        def _on_dl1_remove(*_):
            _remove_model_prediction("lime", ns["dl1_add"], ns["dl1_remove"])

        def _on_dl2_add(*_):
            _add_model_prediction(analysis.prediction_files.value.get(2), "purple",
                                  ns["dl2_add"], ns["dl2_remove"])

        def _on_dl2_remove(*_):
            _remove_model_prediction("purple", ns["dl2_add"], ns["dl2_remove"])

        ns["dl1_run"].on_event("click", _on_dl1_run)
        ns["dl1_add"].on_event("click", _on_dl1_add)
        ns["dl1_remove"].on_event("click", _on_dl1_remove)
        ns["dl2_run"].on_event("click", _on_dl2_run)
        ns["dl2_add"].on_event("click", _on_dl2_add)
        ns["dl2_remove"].on_event("click", _on_dl2_remove)

    _register_handlers()
    analysis.alert_token.subscribe(_reset_for_new_alert)

    # add_defo_layer (OLD): the "Defo Layer" lands on both maps whenever a new
    # reviewed polygon is set. Subscribed once (the layout is built once).
    wire_defo_layer(widgets, analysis)

    # ------------------------------------------------------------------
    # Layout: OLD card04 = edit title + toolBarEdition + toolBarDL1/2. The OLD
    # inline alert is gone: failures are error toasts (see _on_dl*_run).
    # ------------------------------------------------------------------
    card = v.Card(
        class_="pa-2 ma-2",
        hover=True,
        children=[
            v.CardTitle(
                class_="pa-1 ma-1",
                children=[t("analysis_tile.edition_labels.edit_title")],
            ),
            ns["toolbar_edition"],
            ns["toolbar_dl1"],
            ns["toolbar_dl2"],
        ],
    )
    return card
