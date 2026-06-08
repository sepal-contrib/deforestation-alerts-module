"""AlertEditTools sub-component (Task 9.3): draw-edit + CNN models.

Architecture layer: view. Ports the OLD ``AnalysisTile`` ``card04`` interaction as
ONE coherent unit, because the polygon draw/edit controls and the two CNN models
share the draw controls and the DL toolbars are only shown during edit mode.

Ported OLD handlers:

* Edit (``toolBarEdition``): ``start_edition_function`` / ``save_edition_function``
  / ``stop_edition_function`` / ``clear_edition_function`` — toggle the two
  ``CustomDrawControl``s (``draw_before`` / ``draw_after`` on ``widgets``) + the
  DL toolbars + the Alert-BB / Defo-Layer map-layer visibility, and persist the
  user-drawn polygon to ``analyzed_alerts_model.defo_dl_layer``.
* CNN (``toolBarDL1`` / ``toolBarDL2``): ``run_dl_model_1`` / ``run_dl_model_2``
  (download both images + ``cnn_worker.apply_dl_model`` with a FILE-PATH model arg),
  ``verify_model*_output`` + ``enable_dl*`` (show/hide the add/remove/msg buttons),
  ``add_model*_prediction`` / ``remove_model*_prediction`` (mutate
  ``draw_before.data`` from the prediction raster).
* ``add_defo_layer`` — observes ``analyzed_alerts_model.defo_dl_layer`` and adds a
  "Defo Layer" GeoData layer to both maps.

Design (FIXED by the task contract):

* Each CNN model runs as a button-triggered ``solara.lab.use_task(dependencies=
  None, prefer_threaded=True)`` (CPU/IO-bound; never auto-run). The task body
  builds the image name, downloads both images, pulls the HF model (idempotent /
  cached), and runs ``apply_dl_model`` -> returns the prediction ``.tif`` path. It
  mutates NO widget. This consolidates the OLD 4-thread / 4-queue worker machinery
  (file_queue1/2 + result_queue1/2 + the model-download worker startup) into 2
  use_tasks.
* The verify/enable step runs in a ``use_effect`` keyed on the task value (main
  thread), replacing the OLD ``model*_prediction_time`` traitlets observer.
* add/remove prediction are main-thread click handlers reading the stored
  prediction path.
* The edit + DL toolbars + buttons are memoized once (``use_memo([])``) so the OLD
  ``.show()/.hide()/.disabled`` imperative calls port unchanged (same rationale as
  the Filter tile's memoized widgets).
* ``add_defo_layer`` is a real ``analyzed_alerts_model.observe(..., "defo_dl_layer")``
  registered once via ``use_effect([])`` with teardown — a Solara use_effect keyed
  on a ``.value`` would not fire on a traitlets mutation.

Guards: everything no-ops safely with no alert loaded (smoke time): buttons render
but the CNN/edit click paths are not exercised, and the defo-layer observer is
registered but ``defo_dl_layer`` stays ``None``. EE / raster / file failures are
caught and surfaced on the ``alert_draw_alert`` (or logged) so a failure never
breaks the render.

Props
-----
widgets : AnalysisWidgets
    Shared heavy widgets; the edit buttons toggle ``draw_before`` / ``draw_after``
    visibility + read/write their ``.data`` / ``.to_json()``, and the CNN reads the
    selected images + grid scratch slots.
app_state : AppState
    Navigation + alerts source of truth (``actual_alert_id``).
analyzed_alerts_model : AlertAnalysisModel
    ``defo_dl_layer`` — the user-drawn polygon GeoDataFrame.
app_tile_model : AppTileModel
    ``recipe_folder_path`` for the prediction output paths.
"""

import logging

import ipyvuetify as v
import solara
from ipyleaflet import GeoData
from pysepal import color, sepalwidgets as sw

from deforestation_alerts.message import cm
from deforestation_alerts.scripts.cnn_worker import apply_dl_model
from deforestation_alerts.scripts.mosaics_helper import (
    convertir_formato3,
    download_both_images,
    filter_features_by_color,
    geojson_to_geodataframe,
    raster_to_gdf,
    simplify_and_extract_features,
    verify_raster,
)
from gui.tile.analysis.paths import alert_image_name

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


def _run_cnn_model(model_repo, model_file, suffix, widgets, app_state, app_tile_model):
    """Body of the OLD ``run_dl_model_*`` (worker side), without widget mutation.

    Builds the image name from the recipe folder + alert id + the two selected
    images' suffixes, downloads both images, pulls the HF model (cached), runs
    ``apply_dl_model``, and returns the prediction ``.tif`` path. Runs on the
    use_task worker thread, so it must not touch ipyvuetify widgets.
    """
    from huggingface_hub import hf_hub_download

    actual_alert_id = app_state.actual_alert_id.value
    recipe_folder = app_tile_model.recipe_folder_path

    before_info = widgets.selected_img_before_info_list
    after_info = widgets.selected_img_after_info_list

    image_name = alert_image_name(
        recipe_folder, actual_alert_id, before_info, after_info
    )
    source1 = before_info[0]
    source2 = after_info[0]

    download_both_images(
        widgets.selected_img_before,
        widgets.selected_img_after,
        image_name,
        source1,
        source2,
        widgets.actual_alert_grid,
    )

    model_path = hf_hub_download(
        repo_id=model_repo, filename=model_file, local_dir="/tmp"
    )
    return apply_dl_model(image_name, model_path, suffix)


@solara.component
def AlertEditTools(
    widgets,
    app_state,
    analyzed_alerts_model,
    app_tile_model,
):
    """Edit-tools + CNN card (OLD ``card04``)."""

    # ------------------------------------------------------------------
    # Memoized widgets (created once so the OLD .show()/.hide()/.disabled
    # imperative toggles port unchanged). Bundle them in a small namespace so the
    # handlers below can mutate them in place across renders.
    # ------------------------------------------------------------------
    def _build_widgets():
        ns = {}

        # error alert (OLD self.alert_draw_alert)
        ns["alert"] = sw.Alert().hide()

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
            tooltip=cm.analysis_tile.edition_labels.start_edit_button,
            top=True, open_delay=100, close_delay=100,
        )
        tt_save = sw.Tooltip(
            ns["save_btn"],
            tooltip=cm.analysis_tile.edition_labels.save_edit_button,
            top=True, open_delay=100, close_delay=100,
        )
        tt_stop = sw.Tooltip(
            ns["stop_btn"],
            tooltip=cm.analysis_tile.edition_labels.stop_edit_button,
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
        tt_dl1 = sw.Tooltip(ns["dl1_run"], tooltip=cm.analysis_tile.model_labels.dl_button1,
                            top=True, open_delay=100, close_delay=100)
        tt_dl1_add = sw.Tooltip(ns["dl1_add"], tooltip=cm.analysis_tile.model_labels.dl_button_add,
                                top=True, open_delay=100, close_delay=100)
        tt_dl1_rem = sw.Tooltip(ns["dl1_remove"], tooltip=cm.analysis_tile.model_labels.dl_button_remove,
                                top=True, open_delay=100, close_delay=100)
        tt_dl1_msg = sw.Tooltip(ns["dl1_msg"], tooltip=cm.analysis_tile.model_labels.dl_404,
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
        tt_dl2 = sw.Tooltip(ns["dl2_run"], tooltip=cm.analysis_tile.model_labels.dl_button2,
                            top=True, open_delay=100, close_delay=100)
        tt_dl2_add = sw.Tooltip(ns["dl2_add"], tooltip=cm.analysis_tile.model_labels.dl_button_add,
                                top=True, open_delay=100, close_delay=100)
        tt_dl2_rem = sw.Tooltip(ns["dl2_remove"], tooltip=cm.analysis_tile.model_labels.dl_button_remove,
                                top=True, open_delay=100, close_delay=100)
        tt_dl2_msg = sw.Tooltip(ns["dl2_msg"], tooltip=cm.analysis_tile.model_labels.dl_404,
                                top=True, open_delay=100, close_delay=100)
        ns["toolbar_dl2"] = sw.Toolbar(
            children=[tt_dl2, tt_dl2_add, tt_dl2_rem, tt_dl2_msg]
        ).hide()

        return ns

    ns = solara.use_memo(_build_widgets, dependencies=[])

    # Stored prediction paths (set by the verify effect, read by add/remove).
    pred1_path = solara.use_reactive(None)
    pred2_path = solara.use_reactive(None)

    # ------------------------------------------------------------------
    # CNN tasks: button-triggered (dependencies=None) so they never auto-run.
    # Each returns the prediction .tif path. Widget mutation happens in the verify
    # effect + add/remove handlers (main thread), never in the task body.
    # ------------------------------------------------------------------
    def _run_model1():
        return _run_cnn_model(
            _MODEL1_REPO, _MODEL1_FILE, "_m1", widgets, app_state, app_tile_model
        )

    def _run_model2():
        return _run_cnn_model(
            _MODEL2_REPO, _MODEL2_FILE, "_m2", widgets, app_state, app_tile_model
        )

    model1_task = solara.lab.use_task(
        _run_model1, dependencies=None, prefer_threaded=True, raise_error=False
    )
    model2_task = solara.lab.use_task(
        _run_model2, dependencies=None, prefer_threaded=True, raise_error=False
    )

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
    # verify_model*_output (OLD) — runs on the main thread keyed on the task value
    # (replaces the OLD model*_prediction_time observer).
    # ------------------------------------------------------------------
    def _verify_model1():
        if model1_task.finished and model1_task.value:
            try:
                pred1_path.value = model1_task.value
                analyzed_alerts_model.model1_prediction_file = model1_task.value
                _enable_dl1(verify_raster(model1_task.value, _PRED_THRESHOLD))
            except Exception:
                logger.exception("Analysis: model 1 verify failed")
                ns["alert"].add_msg(str(cm.analysis_tile.model_labels.dl_404), "error")
        elif model1_task.error:
            # The run task failed (e.g. clicking "M 1" before picking before/after
            # images leaves selected_img_*_info_list None). Surface it instead of
            # leaving the user with only a stopped spinner.
            logger.error(
                "Analysis: model 1 run failed", exc_info=model1_task.exception
            )
            ns["alert"].add_msg(str(cm.analysis_tile.model_labels.dl_404), "error")

    # Keyed on finished+error so the effect also fires on the ERROR transition
    # (task.value stays None on error, so keying on .value alone would miss it).
    solara.use_effect(
        _verify_model1, dependencies=[model1_task.finished, model1_task.error]
    )

    def _verify_model2():
        if model2_task.finished and model2_task.value:
            try:
                pred2_path.value = model2_task.value
                analyzed_alerts_model.model2_prediction_file = model2_task.value
                _enable_dl2(verify_raster(model2_task.value, _PRED_THRESHOLD))
            except Exception:
                logger.exception("Analysis: model 2 verify failed")
                ns["alert"].add_msg(str(cm.analysis_tile.model_labels.dl_404), "error")
        elif model2_task.error:
            logger.error(
                "Analysis: model 2 run failed", exc_info=model2_task.exception
            )
            ns["alert"].add_msg(str(cm.analysis_tile.model_labels.dl_404), "error")

    solara.use_effect(
        _verify_model2, dependencies=[model2_task.finished, model2_task.error]
    )

    # ------------------------------------------------------------------
    # add/remove prediction (OLD) — main-thread click handlers mutating
    # widgets.draw_before.data (which is .data-linked to draw_after).
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
            widgets.draw_before.data = orig_features + new_features
            add_btn.disabled = True
            remove_btn.disabled = False
        except Exception:
            logger.exception("Analysis: add prediction failed")
            ns["alert"].add_msg(str(cm.analysis_tile.model_labels.dl_404), "error")

    def _remove_model_prediction(color_name, add_btn, remove_btn):
        try:
            orig_features = widgets.draw_before.data
            widgets.draw_before.clear()
            widgets.draw_after.clear()
            widgets.draw_before.data = filter_features_by_color(orig_features, color_name)
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
            if analyzed_alerts_model.defo_dl_layer is not None:
                widgets.draw_after.data = convertir_formato3(
                    analyzed_alerts_model.defo_dl_layer.__geo_interface__["features"],
                    "blue",
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
                analyzed_alerts_model.defo_dl_layer = geojson_to_geodataframe(
                    widgets.draw_before.to_json()
                ).set_crs(epsg="4326", allow_override=True, inplace=True)
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
    # Wire all click handlers once (identities stable via use_memo([])).
    # ------------------------------------------------------------------
    def _register_handlers():
        ns["start_btn"].on_event("click", _start_edition)
        ns["save_btn"].on_event("click", _save_edition)
        ns["stop_btn"].on_event("click", _stop_edition)
        ns["clear_btn"].on_event("click", _clear_edition)

        def _on_dl1_run(*_):
            ns["dl1_add"].disabled = True
            ns["dl1_remove"].disabled = True
            model1_task()

        def _on_dl2_run(*_):
            ns["dl2_add"].disabled = True
            ns["dl2_remove"].disabled = True
            model2_task()

        def _on_dl1_add(*_):
            _add_model_prediction(pred1_path.value, "lime", ns["dl1_add"], ns["dl1_remove"])

        def _on_dl1_remove(*_):
            _remove_model_prediction("lime", ns["dl1_add"], ns["dl1_remove"])

        def _on_dl2_add(*_):
            _add_model_prediction(pred2_path.value, "purple", ns["dl2_add"], ns["dl2_remove"])

        def _on_dl2_remove(*_):
            _remove_model_prediction("purple", ns["dl2_add"], ns["dl2_remove"])

        ns["dl1_run"].on_event("click", _on_dl1_run)
        ns["dl1_add"].on_event("click", _on_dl1_add)
        ns["dl1_remove"].on_event("click", _on_dl1_remove)
        ns["dl2_run"].on_event("click", _on_dl2_run)
        ns["dl2_add"].on_event("click", _on_dl2_add)
        ns["dl2_remove"].on_event("click", _on_dl2_remove)

        def _teardown():
            for key, evt in (
                ("start_btn", "click"), ("save_btn", "click"),
                ("stop_btn", "click"), ("clear_btn", "click"),
                ("dl1_run", "click"), ("dl1_add", "click"), ("dl1_remove", "click"),
                ("dl2_run", "click"), ("dl2_add", "click"), ("dl2_remove", "click"),
            ):
                try:
                    ns[key].on_event(evt, None, remove=True)
                except Exception:
                    pass

        return _teardown

    solara.use_effect(_register_handlers, dependencies=[])

    # Reflect the running CNN tasks on the run buttons (OLD indeterminate_state).
    ns["dl1_run"].loading = model1_task.pending
    ns["dl2_run"].loading = model2_task.pending

    # ------------------------------------------------------------------
    # add_defo_layer (OLD) — registered as a real traitlets observer once (a
    # Solara use_effect keyed on a .value would not fire on a traitlets mutation).
    # Adds the "Defo Layer" GeoData to both maps whenever defo_dl_layer is set.
    # ------------------------------------------------------------------
    def _register_defo_observer():
        def handler(change):
            try:
                if analyzed_alerts_model.defo_dl_layer is not None:
                    geo_json_layer = GeoData(
                        geo_dataframe=analyzed_alerts_model.defo_dl_layer,
                        name="Defo Layer",
                        style=_DEFO_LAYER_STYLE,
                    )
                    widgets.map_before.add_layer(geo_json_layer)
                    widgets.map_after.add_layer(geo_json_layer)
            except Exception:
                logger.exception("Analysis: add_defo_layer failed")

        analyzed_alerts_model.observe(handler, "defo_dl_layer")
        return lambda: analyzed_alerts_model.unobserve(handler, "defo_dl_layer")

    solara.use_effect(_register_defo_observer, dependencies=[])

    # ------------------------------------------------------------------
    # Layout: OLD card04 = edit title + toolBarEdition + toolBarDL1/2 + alert.
    # ------------------------------------------------------------------
    card = solara.use_memo(
        lambda: v.Card(
            class_="pa-2 ma-2",
            hover=True,
            children=[
                v.CardTitle(
                    class_="pa-1 ma-1",
                    children=[cm.analysis_tile.edition_labels.edit_title],
                ),
                ns["toolbar_edition"],
                ns["toolbar_dl1"],
                ns["toolbar_dl2"],
                ns["alert"],
            ],
        ),
        dependencies=[],
    )
    solara.display(card)
