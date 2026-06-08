"""AlertExport sub-component (Task 9.4): save attributes + gpkg/zip + Word report.

Architecture layer: view. Ports the OLD ``AnalysisTile`` export card (``card06`` +
``label35``/``toolBarSaveExport``/``toolBarDownloads``):

* The **Save** button (``save_btn``) ran the OLD ``save_attributes_to_gdf`` —
  writing the user's confirmation / loss-driver / image-info / GEE-admin attributes
  + the drawn polygon area onto ``alerts_gdf`` and persisting ``alert_db.csv``.
* The **Download alert data** button (``download_alert_data_btn``, disabled until a
  save happens) ran the OLD ``download_data`` — building the single-alert gpkg, the
  before/after-image GeoTIFF, the zip, and the Word report.
* The two ``sw.DownloadBtn`` (``files_dwn_btn`` / ``report_dwn_btn``) live in a
  ``toolBarDownloads`` that is hidden until a download completes, then get their
  URLs set to the zip / report and are revealed.

Design (FIXED by the task contract — same shape as the rest of Task 9):

* **Save + Download are each a button-triggered**
  ``solara.lab.use_task(dependencies=None, prefer_threaded=True)`` (both do blocking
  GEE ``.getInfo()`` / heavy report generation). They NEVER auto-run; the buttons'
  ``loading`` reflects ``task.pending``.
* **No widget mutation inside the task bodies.** The save task does the gdf mutation
  + GEE admin lookup + CSV write (data/IO only) and returns ``True``. The download
  task does the gpkg/image/zip/report (IO only) and RETURNS
  ``{"zip_path": ..., "report_path": ...}``. All widget mutation — enabling the
  download button, ``set_url(...)``, showing ``toolBarDownloads``, surfacing errors —
  happens in ``use_effect``s keyed on the task value/state (main thread).
* The buttons + toolbars are **memoized once** (``use_memo([])``) so the imperative
  ``.show()/.hide()/.disabled``/``.set_url()`` calls port unchanged; the download
  buttons start in a hidden ``toolBarDownloads`` and the "Download alert data" button
  starts disabled (enabled by the save effect).
* Task errors are surfaced on a ``sw.Alert`` (like the edit-tools ``alert``) — e.g.
  the OLD "confirmation==No but a polygon is drawn" guard, or a save/report failure —
  keyed on ``[task.finished, task.error]`` so the effect fires on the ERROR
  transition too (the task value stays ``None`` on error).

Source-of-truth adaptation (Task 9): OLD read/wrote
``self.analyzed_alerts_model.alerts_gdf``; in the new architecture that GeoDataFrame
lives in ``app_state.alerts_gdf.value`` (published by the Filter tile), and the alert
index is ``app_state.actual_alert_id.value``. ``save_attributes_to_gdf`` mutates that
gdf in place via ``.at[id, col] = ...`` (faithful to OLD), then stamps
``analyzed_alerts_model.last_save_time``. Everything guards on the gdf being present
and the id in range, so it is smoke-safe (nothing runs at mount;
``dependencies=None``).

Props
-----
widgets : AnalysisWidgets
    Shared heavy widgets + scratch. Read here: ``confirmation_select`` /
    ``driver_combobox`` (stashed by AlertCnnPanel), ``selected_img_before_info_list``
    / ``selected_img_after_info_list`` ([0]=source, [1]=milis, [3]=value/name,
    [4]=date-suffix), ``selected_img_before`` / ``selected_img_after`` (ee.Image),
    ``actual_alert_grid``, ``actual_bb``.
app_state : AppState
    Navigation + alerts source of truth (``alerts_gdf`` / ``actual_alert_id``).
aux_model : AuxModel
    ``custom_report_template`` for the report generation.
analyzed_alerts_model : AlertAnalysisModel
    ``defo_dl_layer`` (drawn polygon) / ``last_save_time`` (save stamp).
app_tile_model : AppTileModel
    ``recipe_folder_path`` for the output paths + ``alert_db.csv``.
"""

import logging
import os
from datetime import datetime
from pathlib import Path

import ee
import geopandas as gpd
import ipyvuetify as v
import solara
from pysepal import sepalwidgets as sw

from deforestation_alerts.message import cm
from deforestation_alerts.scripts.mosaics_helper import (
    calculate_total_area,
    download_both_images,
)
from deforestation_alerts.scripts.report_builder import (
    add_files_to_zip,
    ensure_list,
    format_list,
    generate_deforestation_report_with_word_template,
)

logger = logging.getLogger("SEPALUI.deforestation_alerts.analysis")

# Default Word report template (OLD used this literal when no custom template set).
_DEFAULT_REPORT_TEMPLATE = "data/report_template.docx"


def _save_attributes(widgets, app_state, analyzed_alerts_model, app_tile_model):
    """Body of the OLD ``save_attributes_to_gdf`` (data/IO only, no widgets).

    Mutates ``app_state.alerts_gdf.value`` in place via ``.at[id, col] = ...``
    (faithful to OLD), does the blocking GEE admin lookup, persists
    ``alert_db.csv``, and stamps ``last_save_time``. Returns ``True`` on success.
    Guards on the gdf being present + the id in range (smoke-safe). Raises the OLD
    "confirmation==No but a polygon is drawn" guard, surfaced by the save effect.
    """
    alertas_gdf = app_state.alerts_gdf.value
    actual_alert_id = app_state.actual_alert_id.value

    # Guard: nothing to save without an alert set / valid id (smoke-safe).
    if alertas_gdf is None or actual_alert_id is None or actual_alert_id < 0:
        return False
    if actual_alert_id >= len(alertas_gdf):
        return False

    confirmation = widgets.confirmation_select.v_model

    # status (confirmation -> human label).
    status_dict = {
        cm.analysis_tile.questionarie.confirmation_yes: "Confirmed",
        cm.analysis_tile.questionarie.confirmation_no: "False Positive",
        cm.analysis_tile.questionarie.confirmation_revision: "Need revision",
    }
    alertas_gdf.at[actual_alert_id, "status"] = status_dict[confirmation]

    # description (loss-driver combobox).
    drivers_list = ensure_list(widgets.driver_combobox.v_model)
    alertas_gdf.at[actual_alert_id, "description"] = format_list(drivers_list)

    # before/after image name + info.
    alertas_gdf.at[actual_alert_id, "before_img"] = (
        widgets.selected_img_before_info_list[3]
    )
    alertas_gdf.at[actual_alert_id, "after_img"] = (
        widgets.selected_img_after_info_list[3]
    )
    alertas_gdf.at[actual_alert_id, "before_img_info"] = (
        widgets.selected_img_before_info_list[1]
    )
    alertas_gdf.at[actual_alert_id, "after_img_info"] = (
        widgets.selected_img_after_info_list[1]
    )

    # Administrative location attributes via GEE FAO/GAUL (OLD 1799-1828).
    adminL2 = ee.FeatureCollection("FAO/GAUL/2015/level2")
    selected_admin = adminL2.filterBounds(widgets.actual_bb.geometry())
    at1 = selected_admin.aggregate_array("ADM0_NAME").distinct()
    at2 = selected_admin.aggregate_array("ADM1_NAME").distinct()
    at3 = selected_admin.aggregate_array("ADM2_NAME").distinct()

    st1 = at1.iterate(
        lambda list_element, result: ee.String(result)
        .cat(list_element)
        .cat(ee.String(", ")),
        ee.String(""),
    )
    st2 = at2.iterate(
        lambda list_element, result: ee.String(result)
        .cat(list_element)
        .cat(ee.String(", ")),
        ee.String(""),
    )
    st3 = at3.iterate(
        lambda list_element, result: ee.String(result)
        .cat(list_element)
        .cat(ee.String(", ")),
        ee.String(""),
    )

    alertas_gdf.at[actual_alert_id, "admin1"] = st1.getInfo()[:-1]
    alertas_gdf.at[actual_alert_id, "admin2"] = st2.getInfo()[:-1]
    alertas_gdf.at[actual_alert_id, "admin3"] = st3.getInfo()[:-1]

    # OLD guard (1830): "False Positive" but a polygon was drawn => inconsistent.
    if (
        confirmation == cm.analysis_tile.questionarie.confirmation_no
        and analyzed_alerts_model.defo_dl_layer is not None
    ):
        raise Exception(cm.analysis_tile.questionarie.confirmation_q)

    # alert_polygon + area_ha from the drawn defo layer (if any).
    if analyzed_alerts_model.defo_dl_layer is None:
        alertas_gdf.at[actual_alert_id, "alert_polygon"] = None
        alertas_gdf.at[actual_alert_id, "area_ha"] = 0
    else:
        alertas_gdf.at[actual_alert_id, "alert_polygon"] = (
            analyzed_alerts_model.defo_dl_layer["geometry"].union_all()
        )
        alertas_gdf.at[actual_alert_id, "area_ha"] = calculate_total_area(
            analyzed_alerts_model.defo_dl_layer
        )

    # Persist the alert DB (OLD save_alerts_to_gdf). Skip if no recipe folder.
    _save_alerts_to_gdf(alertas_gdf, app_tile_model)

    analyzed_alerts_model.last_save_time = datetime.today().timestamp()
    return True


def _save_alerts_to_gdf(alertas_gdf, app_tile_model):
    """OLD ``save_alerts_to_gdf``: write ``alert_db.csv`` (geometry=bounding_box).

    Guard: skip if ``recipe_folder_path`` is empty (smoke / no recipe yet).
    """
    recipe_folder = app_tile_model.recipe_folder_path
    if not recipe_folder:
        return
    alertas_gdf.set_geometry("bounding_box", inplace=True)
    alert_db_name = recipe_folder + "/alert_db.csv"
    alertas_gdf.set_crs(epsg="4326", allow_override=True, inplace=True).to_csv(
        alert_db_name, index=False
    )


def _download_data(widgets, app_state, aux_model, app_tile_model):
    """Body of the OLD ``download_data`` (IO only, no widgets).

    Builds the single-alert gpkg, the before/after GeoTIFF (if missing), the zip,
    and the Word report. Returns ``{"zip_path", "report_path"}`` (absolute) for the
    download effect to wire onto the two DownloadBtns. Guards on the gdf/id.
    """
    alertas_gdf = app_state.alerts_gdf.value
    actual_alert_id = app_state.actual_alert_id.value

    if alertas_gdf is None or actual_alert_id is None or actual_alert_id < 0:
        return None
    if actual_alert_id >= len(alertas_gdf):
        return None

    recipe_folder = app_tile_model.recipe_folder_path

    # Single-row GeoDataFrame for the selected alert.
    selected_element = alertas_gdf.iloc[actual_alert_id]
    selected_gdf = gpd.GeoDataFrame(
        [selected_element], columns=alertas_gdf.columns
    )
    if not selected_gdf["alert_polygon"].isnull().all():
        selected_gdf.set_geometry("alert_polygon", inplace=True)
    else:
        selected_gdf.set_geometry("bounding_box", inplace=True)

    gpkg_name = recipe_folder + "/alert_" + str(actual_alert_id) + ".gpkg"
    selected_gdf.set_crs(epsg="4326", allow_override=True, inplace=True).to_file(
        gpkg_name, driver="GPKG"
    )

    # Before/after image GeoTIFF (download only if not already present).
    image_name = (
        recipe_folder
        + "/alert_"
        + str(actual_alert_id)
        + widgets.selected_img_before_info_list[4]
        + widgets.selected_img_after_info_list[4]
        + ".tif"
    )
    if not Path(image_name).exists():
        download_both_images(
            widgets.selected_img_before,
            widgets.selected_img_after,
            image_name,
            widgets.selected_img_before_info_list[0],
            widgets.selected_img_after_info_list[0],
            widgets.actual_alert_grid,
        )

    # Zip the image + gpkg.
    zipfile_name = recipe_folder + "/alert_" + str(actual_alert_id) + ".zip"
    add_files_to_zip(zipfile_name, image_name, gpkg_name)
    zip_path = os.path.abspath(zipfile_name)

    # Word report.
    output_report_path = recipe_folder + "/alert_" + str(actual_alert_id) + ".docx"
    if not aux_model.custom_report_template:
        report_template = _DEFAULT_REPORT_TEMPLATE
    else:
        report_template = aux_model.custom_report_template
    generate_deforestation_report_with_word_template(
        image_name, gpkg_name, report_template, output_report_path
    )
    report_path = os.path.abspath(output_report_path)

    return {"zip_path": zip_path, "report_path": report_path}


@solara.component
def AlertExport(
    widgets,
    app_state,
    aux_model,
    analyzed_alerts_model,
    app_tile_model,
):
    """Renders the export card (OLD ``card06``) with the save + download flow."""

    # ------------------------------------------------------------------
    # Memoized buttons + toolbars (created once so the imperative
    # .show()/.hide()/.disabled/.set_url() calls port unchanged). The download
    # buttons start in a hidden toolBarDownloads; the "Download alert data" button
    # starts disabled (enabled by the save effect).
    # ------------------------------------------------------------------
    def _build_widgets():
        ns = {}
        ns["alert"] = sw.Alert().hide()

        ns["save_btn"] = sw.Btn(
            msg=cm.analysis_tile.export_labels.save_btn,
            color="primary",
            outlined=True,
            class_="pa-1 ma-1",
        )
        ns["download_alert_data_btn"] = sw.Btn(
            msg=cm.analysis_tile.export_labels.download_alert_data_btn,
            color="primary",
            outlined=True,
            disabled=True,
            class_="pa-1 ma-1",
        )
        ns["files_dwn_btn"] = sw.DownloadBtn(
            class_="pa-1 ma-1",
            text=cm.analysis_tile.export_labels.files_dwn_btn,
        )
        ns["report_dwn_btn"] = sw.DownloadBtn(
            class_="pa-1 ma-1",
            text=cm.analysis_tile.export_labels.report_dwn_btn,
        )

        ns["toolbar_save_export"] = sw.Toolbar(
            class_="pa-1 ma-1 d-flex justify-space-between",
            children=[ns["save_btn"], ns["download_alert_data_btn"]],
        )
        ns["toolbar_downloads"] = sw.Toolbar(
            class_="pa-1 ma-1 d-flex justify-space-between",
            children=[ns["files_dwn_btn"], ns["report_dwn_btn"]],
        ).hide()

        return ns

    ns = solara.use_memo(_build_widgets, dependencies=[])

    # ------------------------------------------------------------------
    # Save task: button-triggered (dependencies=None) so it never auto-runs. Does
    # gdf mutation + GEE admin lookup + CSV write only; returns True. Widget
    # mutation (enable download btn, surface errors) is in the effect below.
    # ------------------------------------------------------------------
    def _run_save():
        return _save_attributes(
            widgets, app_state, analyzed_alerts_model, app_tile_model
        )

    save_task = solara.lab.use_task(
        _run_save, dependencies=None, prefer_threaded=True, raise_error=False
    )

    # ------------------------------------------------------------------
    # Download task: button-triggered. Builds gpkg/image/zip/report (IO only) and
    # RETURNS the zip/report paths; the effect sets the DownloadBtn URLs + reveals
    # toolBarDownloads (main thread).
    # ------------------------------------------------------------------
    def _run_download():
        return _download_data(widgets, app_state, aux_model, app_tile_model)

    download_task = solara.lab.use_task(
        _run_download, dependencies=None, prefer_threaded=True, raise_error=False
    )

    # ------------------------------------------------------------------
    # Post-save effect: enable the download button on success, surface errors.
    # Keyed on finished+error so it fires on the ERROR transition too (the task
    # value stays None on error, so keying on .value alone would miss it).
    # ------------------------------------------------------------------
    def _on_save_done():
        if save_task.finished and save_task.value:
            ns["alert"].hide()
            ns["download_alert_data_btn"].disabled = False
            # Poke the alerts_gdf reactive so dependents refresh. The save mutated
            # the gdf in place (.at[id, col]) which does NOT change the reactive's
            # object identity, so Overview (whose refresh effect keys on
            # app_state.alerts_gdf) would otherwise show stale review counts/markers
            # until the next reassign. Rebinding to a copy forces a new identity.
            gdf = app_state.alerts_gdf.value
            if gdf is not None:
                app_state.alerts_gdf.value = gdf.copy()
        elif save_task.error:
            logger.error("Analysis: save attributes failed", exc_info=save_task.exception)
            ns["alert"].add_msg(str(save_task.exception), "error")

    solara.use_effect(
        _on_save_done, dependencies=[save_task.finished, save_task.error]
    )

    # ------------------------------------------------------------------
    # Post-download effect: set the two DownloadBtn URLs + reveal the toolbar on
    # success, surface errors.
    # ------------------------------------------------------------------
    def _on_download_done():
        if download_task.finished and download_task.value:
            result = download_task.value
            ns["alert"].hide()
            ns["files_dwn_btn"].set_url(path=result["zip_path"])
            ns["report_dwn_btn"].set_url(path=result["report_path"])
            ns["toolbar_downloads"].show()
        elif download_task.error:
            logger.error(
                "Analysis: download data failed", exc_info=download_task.exception
            )
            ns["alert"].add_msg(str(download_task.exception), "error")

    solara.use_effect(
        _on_download_done,
        dependencies=[download_task.finished, download_task.error],
    )

    # ------------------------------------------------------------------
    # Wire click handlers once (identities stable via use_memo([])). The buttons
    # trigger the tasks; loading state is reflected below.
    # ------------------------------------------------------------------
    def _register_handlers():
        def _on_save(*_):
            save_task()

        def _on_download(*_):
            download_task()

        ns["save_btn"].on_event("click", _on_save)
        ns["download_alert_data_btn"].on_event("click", _on_download)

        def _teardown():
            for key in ("save_btn", "download_alert_data_btn"):
                try:
                    ns[key].on_event("click", None, remove=True)
                except Exception:
                    pass

        return _teardown

    solara.use_effect(_register_handlers, dependencies=[])

    # Reflect the running tasks on their buttons (OLD widget.loading toggles).
    ns["save_btn"].loading = save_task.pending
    ns["download_alert_data_btn"].loading = download_task.pending

    # ------------------------------------------------------------------
    # Layout: OLD card06 = export title + toolBarSaveExport + toolBarDownloads
    # (hidden) + the error alert.
    # ------------------------------------------------------------------
    card = solara.use_memo(
        lambda: v.Card(
            class_="pa-2 ma-2",
            hover=True,
            children=[
                v.CardTitle(
                    class_="pa-1 ma-1",
                    children=[cm.analysis_tile.export_labels.export_title],
                ),
                ns["toolbar_save_export"],
                ns["toolbar_downloads"],
                ns["alert"],
            ],
        ),
        dependencies=[],
    )
    solara.display(card)
