"""build_export sub-component (Task 9.4 / B3): save attributes + gpkg/zip + Word report.

Architecture layer: view (raw-widget builder, no ``@solara.component``). Ports the
OLD ``AnalysisTile`` export card (``card06`` +
``label35``/``toolBarSaveExport``/``toolBarDownloads``):

* The **Save** button (``save_btn``) ran the OLD ``save_attributes_to_gdf`` —
  writing the user's confirmation / loss-driver / image-info / GEE-admin attributes
  + the drawn polygon area onto ``alerts_gdf`` and persisting ``alert_db.parquet``.
* The **Download alert data** button (``download_alert_data_btn``, disabled until a
  save happens) ran the OLD ``download_data`` — building the single-alert gpkg, the
  before/after-image GeoTIFF, the zip, and the Word report.
* The two ``sw.DownloadBtn`` (``files_dwn_btn`` / ``report_dwn_btn``) live in a
  ``toolBarDownloads`` that is hidden until a download completes, then get their
  URLs set to the zip / report and are revealed.

WHY A RAW WIDGET (not a Solara component + ``solara.display``): MapApp renders the
Analysis dialog with ``v-if``, so it DESTROYS the dialog DOM on close and REBUILDS it
on reopen. Content captured via ``solara.display`` lives inside an ``Output`` whose
view is NOT recreated on that rebuild, so it vanishes the 2nd time the dialog is
opened. A plain ipyvuetify widget tree is re-viewed from its model on reopen, so it
survives. ``build_export`` builds the card once and RETURNS it; the parent hands it
to MapApp as dialog content — same pattern as ``navigator`` / ``review_panel`` /
``edit_tools`` and ``recipe_tile.build_recipe_layout``.

Threading (FIXED by the task contract — same shape as ``edit_tools`` / ``recipe_tile``):

* **Save + Download each run button-triggered via ``spawn_in_context``** (both
  do blocking GEE ``.getInfo()`` / heavy report generation). They NEVER auto-run; the
  click handler toggles that button's ``.loading``/``.disabled`` and starts the
  worker; the worker runs the blocking work under ``notify_bridge.tracked_job``
  (inside a ``try/except/else/finally``) so the notification pill shows it
  running and the worker thread's app log lines become its milestones.
* **No widget mutation inside the blocking bodies.** The save body does the gdf
  mutation + GEE admin lookup + CSV write (data/IO only) and returns ``True``. The
  download body does the gpkg/image/zip/report (IO only) and RETURNS
  ``{"zip_path": ..., "report_path": ...}``. The OLD post-task apply step (enabling
  the download button, ``set_url(...)``, showing ``toolBarDownloads``) now runs at the
  END of the corresponding worker's tracked block with that returned value (so a
  failing apply is still a FAILED task); only the success toast follows the block. A failure is a FAILED task + one error toast, both published by
  ``tracked_job``; the worker's ``except`` only logs it. A refused Save (no
  alert selected, or ``SaveRefused``: the alerts or the active set changed) is a
  FAILED task + one warning toast. ipywidgets
  trait sets (and the ``alerts_gdf`` reactive rebind) from the worker thread are fine
  (recipe_tile / edit_tools do exactly this).
* The buttons + toolbars are **built once** (the builder runs once per build) so the
  imperative ``.show()/.hide()/.disabled``/``.set_url()`` calls port unchanged; the
  download buttons start in a hidden ``toolBarDownloads`` and the "Download alert data"
  button starts disabled (enabled by the save worker's success path).

Feedback goes through ``notify_bridge.tracked_job`` + ``current_notifier``, not the
``use_notifications()`` hook: this module builds raw ipyvuetify widgets and
publishes from daemon worker threads, so there is no component render to hold a
hook. ``spawn_in_context`` carries the kernel context ``current_notifier``
resolves the bus from. The download's before/after composite drives the task's
progress ring (geedim tile ticks via ``gee_progress.geedim_tile_progress``). There
is no inline ``sw.Alert`` any more.

Source-of-truth adaptation (Task 9): OLD read/wrote
``self.analyzed_alerts_model.alerts_gdf``; in the new architecture that GeoDataFrame
lives in ``app_state.alerts_gdf.value`` (the active set's, published by the shell's
set loader or by Integrate), and the alert
index is ``app_state.actual_alert_id.value``. ``save_attributes_to_gdf`` mutates that
gdf in place via ``.at[id, col] = ...`` (faithful to OLD), then stamps
``analysis.last_save_time``. Everything guards on the gdf being present
and the id in range, so it is smoke-safe (nothing runs at mount;
``dependencies=None``).

Props
-----
widgets : AnalysisWidgets
    Shared heavy widgets + scratch. Read here: ``confirmation_select`` /
    ``driver_combobox`` (stashed by AlertReviewPanel), ``actual_alert_grid``,
    ``actual_bb``.
app_state : AppState
    Navigation + alerts source of truth (``alerts_gdf`` / ``actual_alert_id``).
aux_model : SetContext
    ``custom_report_template`` (the project's) for the report generation.
analysis : AnalysisState
    ``defo_layer`` (drawn polygon), ``before_info`` / ``after_info``
    ([0]=source, [1]=milis, [3]=value/name, [4]=date-suffix),
    ``before_image`` / ``after_image`` (ee.Image) and ``last_save_time``
    (save stamp).
app_tile_model : SetContext
    ``recipe_folder_path`` (the active set's folder) for the output paths,
    ``pin`` (the active set, read once) for where a Save writes
    ``alert_db.parquet``, ``is_current``, which tells whether that set is
    still the active one when the Save writes, and ``record_review``, which
    keeps that set's reviewed count after a store save.

Every artifact this card produces — the alert store, the gpkg, the composite
GeoTIFF, the zip and the Word report — is written straight into the set folder
through a unique ``.part`` renamed into place (``local_files``); the
DownloadBtns get ``$HOME``-relative URLs (``local_files.home_relative``).
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import ee
import geopandas as gpd
import ipyvuetify as v
from pysepal import sepalwidgets as sw

from deforestation_alerts.errors import KeyedError
from deforestation_alerts.scripts import local_files
from deforestation_alerts.scripts.alert_store import save_gdf_to_parquet
from deforestation_alerts.scripts.gee_progress import geedim_tile_progress
from deforestation_alerts.scripts.mosaics_helper import calculate_total_area
from deforestation_alerts.scripts.report_builder import (
    add_files_to_zip,
    generate_deforestation_report_with_word_template,
)
from gui.i18n import t
from gui.scripts import loss_drivers as drivers
from gui.scripts import set_store
from gui.scripts.detached import render_detached
from gui.scripts.error_text import error_text
from gui.scripts.notify_bridge import (
    current_notifier,
    tile_progress_reporter,
    tracked_job,
)
from gui.scripts.solara_threads import spawn_in_context
from gui.store.project_writers import writer_name, writing
from gui.tile.analysis.paths import stage_alert_image
from gui.tile.analysis.review_panel import REVIEW_CHOICES
from gui.widget.report_template import ReportTemplateField

logger = logging.getLogger("SEPALUI.deforestation_alerts.analysis")

# Default Word report template (OLD used this literal when no custom template set).
# Resolve from the repo root (export.py -> analysis -> tile -> gui -> root):
# the CWD is not guaranteed writable or even stable on the platform.
_DEFAULT_REPORT_TEMPLATE = str(
    Path(__file__).resolve().parents[3] / "data" / "report_template.docx"
)


class ReportTemplateMissing(KeyedError, RuntimeError):
    """The project's report template is gone from disk: the download writes nothing."""

    key = "analysis_tile.export_labels.report_template_missing"

    def __init__(self, path: str):
        super().__init__(f"report template not found: {path}", path=path)


def report_template_path(template: Optional[str]) -> Path:
    """The ``.docx`` a report is built from: the project's, or the app's default."""
    if not template:
        return Path(_DEFAULT_REPORT_TEMPLATE)
    path = Path(template)
    if not path.is_file():
        raise ReportTemplateMissing(template)
    return path


def _save_target(app_tile_model):
    """Where a Save writes, read once: ``(folder, target)``.

    A ``SetContext`` pins its active set (``target``: that set and its
    project) and the folder is that set's. A recipe-era model (the old shell,
    test fakes) has only ``recipe_folder_path``, and no target. The folder is
    ``""`` when there is nowhere to write.
    """
    pin = getattr(app_tile_model, "pin", None)
    if pin is None:
        return app_tile_model.recipe_folder_path, None
    target = pin()
    return (str(target.folder), target) if target is not None else ("", None)


class SaveRefused(KeyedError):
    """A Save that must not write: nothing went wrong, it has nowhere to go.
    The worker reports it as a warning, not as an error. ``key`` names the
    reason; the worker renders it where it toasts (spec §11)."""


def _store_of(target) -> Path:
    """Where ``target``'s set keeps its alerts: the store this Save rewrites."""
    return Path(str(target.folder)) / "alert_db.parquet"


def _refuse_unless_current(alertas_gdf, app_tile_model, target) -> None:
    """Raise ``SaveRefused`` unless the Save may still write for ``target``.

    ``alerts_gdf`` and the active set are separate reactives: Integrate records
    a new set before it publishes that set's alerts, so for a moment the alerts
    on hand are the previous set's. Saved, they would overwrite the new set's
    store. And the admin lookups block for seconds, during which an Integrate
    can activate another set, or the project can be switched: the alerts this
    Save edited then belong to a set that is no longer the open project's
    active set, so they are not written at all (Review Focus 5).

    A re-run of the *same* set keeps its id, so those two checks pass while
    the run swaps a new store in under the alerts on hand. The alerts carry
    the generation of the store they were read from
    (``set_store.stamp_generation``): once the store on disk is another
    generation, writing them back would overwrite the new set, so the Save is
    refused (``save_store_changed``). Checked before the lookups, and again
    right before the file is moved into place (``_save_alerts_to_gdf``).
    """
    if target is None:
        return
    if not set_store.alerts_belong_to(alertas_gdf, target.alert_set):
        raise SaveRefused(key="analysis_tile.export_labels.save_other_set")
    if not app_tile_model.is_current(target):
        raise SaveRefused(key="analysis_tile.export_labels.save_set_changed")
    if set_store.generation_changed(alertas_gdf, _store_of(target)):
        raise SaveRefused(key="analysis_tile.export_labels.save_store_changed")


def review_status(confirmation) -> str:
    """The status a Save stores (spec §10): one of the three, never a label.

    Anything else is refused, an unhashable value (a list) included."""
    if not isinstance(confirmation, str) or confirmation not in {status for status, _ in REVIEW_CHOICES}:
        raise SaveRefused(key="analysis_tile.questionarie.confirmation_missing")
    return confirmation


def _save_attributes(widgets, app_state, analysis, app_tile_model):
    """Body of the OLD ``save_attributes_to_gdf`` (data/IO only, no widgets).

    Mutates ``app_state.alerts_gdf.value`` in place via ``.at[id, col] = ...``
    (faithful to OLD), does the blocking GEE admin lookup, persists
    ``alert_db.parquet``, and stamps ``last_save_time``. Returns ``True`` on success.
    Guards on the gdf being present + the id in range (smoke-safe). Raises
    ``SaveRefused`` for the OLD "False Positive but a polygon is drawn" guard
    (``false_positive_drawn``), a missing answer, and alerts of another set
    than the one it writes for.

    The store it writes is the active set's AS THE SAVE STARTS: the admin
    lookups below block for seconds, and the live ``SetContext`` (ruling #84)
    follows an Integrate that activates a new set, or a project switch,
    meanwhile. The write then happens only if that set is still the active
    one (``_save_alerts_to_gdf``); otherwise the Save is refused.
    """
    folder, target = _save_target(app_tile_model)
    alertas_gdf = app_state.alerts_gdf.value
    actual_alert_id = app_state.actual_alert_id.value

    # Guard: nothing to save without an alert set / valid id (smoke-safe).
    if alertas_gdf is None or actual_alert_id is None or actual_alert_id < 0:
        return False
    if actual_alert_id >= len(alertas_gdf):
        return False
    # Before any row changes or a lookup blocks; the store writer checks again.
    _refuse_unless_current(alertas_gdf, app_tile_model, target)
    status = review_status(widgets.confirmation_select.v_model)

    alertas_gdf.at[actual_alert_id, "status"] = status

    # description (loss-driver combobox): the drivers' codes, never labels
    # (``loss_drivers``); an export translates them.
    alertas_gdf.at[actual_alert_id, "description"] = drivers.encode(widgets.driver_combobox.v_model)

    # before/after image name + info. Guarded (fix round 1): both are None
    # until an image is actually picked for this alert (``AnalysisState``;
    # ``reset_alert`` clears them on every alert change), e.g. no Planet login
    # yet and no S2/Landsat chosen; unconditionally subscripting raised
    # "'NoneType' object is not subscriptable" and lost the whole review, the
    # same way the drawn-polygon case below is guarded on
    # ``defo_layer is None``.
    before_info = analysis.before_info.value
    after_info = analysis.after_info.value
    # Read with the images, before the lookups block: an alert change
    # meanwhile resets it (``reset_alert``), or the next alert's drawn polygon
    # replaces it, and neither belongs to the alert this Save writes.
    defo_layer = analysis.defo_layer.value
    if before_info is None:
        alertas_gdf.at[actual_alert_id, "before_img"] = None
        alertas_gdf.at[actual_alert_id, "before_img_info"] = None
    else:
        alertas_gdf.at[actual_alert_id, "before_img"] = before_info[3]
        alertas_gdf.at[actual_alert_id, "before_img_info"] = before_info[1]
    if after_info is None:
        alertas_gdf.at[actual_alert_id, "after_img"] = None
        alertas_gdf.at[actual_alert_id, "after_img_info"] = None
    else:
        alertas_gdf.at[actual_alert_id, "after_img"] = after_info[3]
        alertas_gdf.at[actual_alert_id, "after_img_info"] = after_info[1]

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
    # A refusal: the store is not written (the admin names above stay in memory).
    if (
        status == "False Positive"
        and defo_layer is not None
    ):
        raise SaveRefused(key="analysis_tile.questionarie.false_positive_drawn")

    # alert_polygon + area_ha from the drawn defo layer (if any). With nothing
    # drawn, area_ha keeps the alert's own area, which Integrate stored: the
    # store feeds the exports, the map tiles and the report.
    if defo_layer is None:
        alertas_gdf.at[actual_alert_id, "alert_polygon"] = None
    else:
        alertas_gdf.at[actual_alert_id, "alert_polygon"] = (
            defo_layer["geometry"].union_all()
        )
        alertas_gdf.at[actual_alert_id, "area_ha"] = calculate_total_area(defo_layer)

    # Persist the alert DB (OLD save_alerts_to_gdf) where the save started.
    # Skip if there is no folder.
    _save_alerts_to_gdf(alertas_gdf, app_tile_model, folder=folder, target=target)

    analysis.last_save_time.set(datetime.today().timestamp())
    return True


def _save_alerts_to_gdf(alertas_gdf, app_tile_model, *, folder=None, target=None):
    """Persist the alerts gdf to the canonical GeoParquet store.

    Writes ``<folder>/alert_db.parquet`` (all geometry columns preserved)
    through a unique ``.part`` renamed into place. Then records the set's
    reviewed count (``record_review``, when the model has one).

    ``folder`` and ``target`` are where the Save started (``_save_target``);
    without ``folder`` both are read now. Raises ``SaveRefused``, writing
    nothing, unless the alerts are ``target``'s set's, that set is still the
    open project's active set, and its store on disk is still the one the
    alerts came from; the last check runs again right before the serialized
    file is moved into place, so a store swapped in during the serialization
    is not overwritten either. After the write the alerts are stamped with
    the new store's generation, so the next Save of them lands.
    Guard: skip if the folder is empty (smoke / no set yet).
    """
    if folder is None:
        folder, target = _save_target(app_tile_model)
    if not folder:
        return
    _refuse_unless_current(alertas_gdf, app_tile_model, target)
    store = str(Path(folder) / "alert_db.parquet")
    save_gdf_to_parquet(
        alertas_gdf,
        store,
        guard=lambda: _refuse_unless_current(alertas_gdf, app_tile_model, target),
    )
    if target is not None:
        set_store.stamp_generation(alertas_gdf, store)
    record = getattr(app_tile_model, "record_review", None)
    if record is not None:
        # The count of the set this store belongs to (the Analysis badge).
        record(alertas_gdf, target)


def _alert_selected(app_state) -> bool:
    """An alert set is loaded and the selected index points into it."""
    gdf = app_state.alerts_gdf.value
    alert_id = app_state.actual_alert_id.value
    return gdf is not None and alert_id is not None and 0 <= alert_id < len(gdf)


def _images_picked(widgets, analysis) -> bool:
    """A before and an after image are selected for the alert under review."""
    return analysis.before_info.value is not None and analysis.after_info.value is not None


def _download_data(widgets, analysis, app_state, aux_model, app_tile_model, *, task=None):
    """Body of the OLD ``download_data`` (IO only, no widgets).

    Builds the single-alert gpkg, the before/after GeoTIFF (if missing), the zip,
    and the Word report, all in the set folder. Returns ``{"zip_path",
    "report_path"}`` — the set-folder paths for the download effect to wire onto
    the two DownloadBtns. Guards on the gdf/id.

    The gpkg and the zip are built in a private scratch folder (GDAL may leave
    sidecars, zipfile writes a named file) and copied into the set folder; the
    composite is downloaded into the set folder once (``stage_alert_image``)
    and read where it is; the report is rendered from the scratch gpkg onto
    its set-folder path. The scratch folder is deleted when the block ends.

    ``task`` (the worker's ``TaskTracker``, optional) gets the composite
    download's geedim tile ticks as progress, and is handed back to
    indeterminate once the composite is staged. A cache hit produces no ticks.
    """
    alertas_gdf = app_state.alerts_gdf.value
    actual_alert_id = app_state.actual_alert_id.value

    if alertas_gdf is None or actual_alert_id is None or actual_alert_id < 0:
        return None
    if actual_alert_id >= len(alertas_gdf):
        return None

    # Every artifact below is written into the set folder; without one there
    # is nowhere to put them (smoke / no active set).
    if not app_tile_model.recipe_folder_path:
        return None
    folder = Path(app_tile_model.recipe_folder_path)

    # The composite needs a before and an after image (their info lists carry
    # the date suffix and the image id, see paths.alert_image_name), and both
    # are None until an image is picked for this alert, which nothing does by
    # itself. Refused here, before any file is written: the TypeError this
    # raised further down used to leave a partial alert_<id>.gpkg behind.
    if not _images_picked(widgets, analysis):
        return None
    # The picked images, read once: an alert change while the gpkg is written
    # resets them (``reset_alert``).
    picked = (analysis.before_info.value, analysis.after_info.value,
              analysis.before_image.value, analysis.after_image.value)

    # Before any file is written: a template gone from disk is a refusal, not
    # a half-written set of downloads.
    template = report_template_path(aux_model.custom_report_template)

    # Single-row GeoDataFrame for the selected alert.
    selected_element = alertas_gdf.iloc[actual_alert_id]
    # The loss drivers in the language of the download (stored as codes).
    selected_gdf = drivers.with_labels(gpd.GeoDataFrame(
        [selected_element], columns=alertas_gdf.columns
    ))
    active = (
        "alert_polygon"
        if not selected_gdf["alert_polygon"].isnull().all()
        else "bounding_box"
    )
    selected_gdf.set_geometry(active, inplace=True)
    # PRE-EXISTING BUG, fixed here because the migrated path is otherwise
    # untestable: ``alerts_gdf`` carries three geometry columns (polygon +
    # ``bounding_box`` + ``point``), and GPKG accepts exactly one — so
    # ``to_file`` raised "GeoDataFrame contains multiple geometry columns" on
    # EVERY download. Drop the inactive ones, the same way ``alert_export`` does
    # for its single-geometry formats; the report only reads attribute columns.
    selected_gdf = selected_gdf.drop(
        columns=[
            c
            for c in selected_gdf.columns
            if c != active and selected_gdf[c].dtype == "geometry"
        ]
    )

    gpkg_dest = folder / f"alert_{actual_alert_id}.gpkg"
    zip_dest = folder / f"alert_{actual_alert_id}.zip"
    report_dest = folder / f"alert_{actual_alert_id}.docx"
    with local_files.scratch_dir() as stage:
        gpkg_local = stage / gpkg_dest.name
        selected_gdf.set_crs(epsg="4326", allow_override=True, inplace=True).to_file(
            str(gpkg_local), driver="GPKG"
        )

        # Before/after image GeoTIFF (downloaded only if not already in the set
        # folder). Built via the shared helper so it resolves to the same file
        # the CNN run (edit_tools) wrote.
        on_tiles = tile_progress_reporter(task) if task is not None else None
        with geedim_tile_progress(on_tiles):
            image_local = stage_alert_image(
                folder,
                actual_alert_id,
                *picked,
                widgets.actual_alert_grid,
            )
        if task is not None:
            task.set_progress(None)  # zip + report: back to indeterminate

        # Only once the composite is there: a failed download must not leave
        # the alert's gpkg in the set folder.
        local_files.copy_into(gpkg_local, gpkg_dest)

        # Zip the image + gpkg.
        zip_local = stage / zip_dest.name
        add_files_to_zip(str(zip_local), str(image_local), str(gpkg_local))
        local_files.copy_into(zip_local, zip_dest)

        # Word report: the project's template (a file of the user's, read
        # where it is) or the packaged default, resolved above.
        generate_deforestation_report_with_word_template(
            str(image_local), str(gpkg_local), str(template), str(report_dest)
        )

    return {"zip_path": str(zip_dest), "report_path": str(report_dest)}


def build_export(
    widgets,
    app_state,
    aux_model,
    analysis,
    app_tile_model,
):
    """Build and return the export card (OLD ``card06``) with the save + download flow.

    The alert store, the zip and the report it writes land in the active set's
    folder.
    """

    # ------------------------------------------------------------------
    # Buttons + toolbars (created once so the imperative
    # .show()/.hide()/.disabled/.set_url() calls port unchanged). The download
    # buttons start in a hidden toolBarDownloads; the "Download alert data" button
    # starts disabled (enabled by the save worker's success path).
    # ------------------------------------------------------------------
    def _build_widgets():
        ns = {}

        ns["save_btn"] = sw.Btn(
            msg=t("analysis_tile.export_labels.save_btn"),
            color="primary",
            outlined=True,
            class_="pa-1 ma-1",
        )
        ns["download_alert_data_btn"] = sw.Btn(
            msg=t("analysis_tile.export_labels.download_alert_data_btn"),
            color="primary",
            outlined=True,
            disabled=True,
            class_="pa-1 ma-1",
        )
        ns["files_dwn_btn"] = sw.DownloadBtn(
            class_="pa-1 ma-1",
            text=t("analysis_tile.export_labels.files_dwn_btn"),
        )
        ns["report_dwn_btn"] = sw.DownloadBtn(
            class_="pa-1 ma-1",
            text=t("analysis_tile.export_labels.report_dwn_btn"),
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

    ns = _build_widgets()

    # ------------------------------------------------------------------
    # Post-save apply (OLD save effect, success path) — enable the download button
    # + poke the alerts_gdf reactive. Runs at the END of the save worker's tracked
    # block, only when the save wrote something (main thread previously; trait
    # sets / reactive rebind from the worker thread are fine), so a failure here
    # still fails the task. The OLD error branch becomes tracked_job's error.
    # ------------------------------------------------------------------
    def _apply_save():
        ns["download_alert_data_btn"].disabled = False
        # Poke the alerts_gdf reactive so dependents refresh. The save mutated
        # the gdf in place (.at[id, col]) which does NOT change the reactive's
        # object identity, so Overview (whose refresh effect keys on
        # app_state.alerts_gdf) would otherwise show stale review counts/markers
        # until the next reassign. Rebinding to a copy forces a new identity.
        gdf = app_state.alerts_gdf.value
        if gdf is not None:
            app_state.alerts_gdf.value = gdf.copy()

    # ------------------------------------------------------------------
    # Post-download apply (OLD download effect, success path) — set the two
    # DownloadBtn URLs + reveal the toolbar with the worker's computed paths. Runs
    # at the END of the download worker's tracked block (a failure here still
    # fails the task). The OLD error branch becomes tracked_job's error.
    # ------------------------------------------------------------------
    def _apply_download(result):
        # The zip/report live in the set folder, so the buttons get
        # $HOME-relative paths: ``set_url`` -> ``su.create_download_link`` builds
        # ``.../jupyter/files/<path relative to $HOME>``.
        ns["files_dwn_btn"].set_url(path=local_files.home_relative(result["zip_path"]))
        ns["report_dwn_btn"].set_url(
            path=local_files.home_relative(result["report_path"])
        )
        ns["toolbar_downloads"].show()

    # ------------------------------------------------------------------
    # Save handler: button-triggered daemon thread (blocking GEE .getInfo() / CSV
    # write; never auto-run). The button spins for the duration; on success the
    # worker applies the OLD save effect, on failure tracked_job publishes the
    # error as a toast and logs its traceback; a ``SaveRefused`` (incl. the OLD
    # "False Positive but a polygon is drawn" guard) is a warning.
    # ------------------------------------------------------------------
    def _on_save(*_):
        ns["save_btn"].loading = True
        ns["save_btn"].disabled = True

        def _worker():
            # No ``total_steps`` — the save is a single opaque phase (GEE admin
            # lookups + the alert-store write), so the pill stays indeterminate.
            # On an exception tracked_job fails the task and publishes the one
            # error toast (``error_text``), then re-raises into except.
            notifier = current_notifier()
            try:
                with tracked_job(
                    notifier, t("analysis_tile.export_labels.save_task_title")
                ) as task:
                    task.step(t("analysis_tile.export_labels.save_step"))
                    # Writer tracking: register the open project while the
                    # set's alert store is written, so deleting the project
                    # refuses mid-write.
                    with writing(writer_name(app_state)):
                        try:
                            saved = _save_attributes(
                                widgets,
                                app_state,
                                analysis,
                                app_tile_model,
                            )
                            refused = None
                        except SaveRefused as refusal:
                            saved, refused = False, error_text(refusal)
                    if not saved:
                        # `_save_attributes` refused: nothing was written and the
                        # download button stays disabled, so completing the task
                        # would record a DONE run for a no-op the user cannot
                        # explain. A WARNING outcome, not an error: fail() BEFORE
                        # the toast, so the run is already recorded as FAILED
                        # with its reason when the message lands. (Returning
                        # False means a missing/invalid alert id, which is what
                        # the default message says; SaveRefused carries its
                        # own.) The log line comes last: once the task is FAILED
                        # it is not echoed as a milestone.
                        reason = refused or t("analysis_tile.export_labels.save_no_alert")
                        task.fail(reason)
                        notifier.warning(reason)
                        logger.warning("Analysis: save refused — %s", reason)
                    else:
                        # Inside the tracked block: if the apply step raises, the
                        # run is a FAILED task + one error toast, not a green pill.
                        _apply_save()
            except Exception:  # noqa: BLE001 — tracked_job toasted and logged it (traceback included)
                pass
            else:
                if saved:
                    notifier.success(t("analysis_tile.export_labels.save_done"))
            finally:
                ns["save_btn"].loading = False
                ns["save_btn"].disabled = False

        spawn_in_context(_worker)

    # ------------------------------------------------------------------
    # Download handler: button-triggered daemon thread (heavy report generation /
    # IO). On success the worker applies the OLD download effect with the returned
    # zip/report paths; on failure tracked_job publishes the error as a toast and
    # logs its traceback.
    # ------------------------------------------------------------------
    def _on_download(*_):
        ns["download_alert_data_btn"].loading = True
        ns["download_alert_data_btn"].disabled = True

        def _worker():
            # Same tracked_job shape as _on_save above; the report/zip build is
            # one opaque long phase, but the composite download inside it drives
            # the progress ring (``task=`` below).
            notifier = current_notifier()
            try:
                with tracked_job(
                    notifier,
                    t("analysis_tile.export_labels.download_task_title"),
                ) as task:
                    task.step(t("analysis_tile.export_labels.download_step"))
                    # Writer tracking: the report/zip generation writes into the
                    # set's folder — register the open project for the duration.
                    with writing(writer_name(app_state)):
                        result = _download_data(
                            widgets,
                            analysis,
                            app_state,
                            aux_model,
                            app_tile_model,
                            task=task,
                        )
                    if result is None:
                        # `_download_data` refused: nothing was written and no
                        # download button will be revealed, so completing the
                        # task would show a green pill for a no-op the user
                        # cannot explain. A WARNING outcome: fail() BEFORE the
                        # toast, so the run is already recorded as FAILED (with
                        # its reason) in the task log when the message lands.
                        # (The three causes are re-derived here rather than
                        # returned, so `_download_data` keeps its "paths or
                        # nothing" contract.) Log last: a FAILED task takes no
                        # more milestones.
                        if not app_tile_model.recipe_folder_path:
                            reason = t("analysis_tile.export_labels.download_no_recipe")
                        elif not _alert_selected(app_state):
                            reason = t("analysis_tile.export_labels.download_no_alert")
                        else:
                            reason = t("analysis_tile.export_labels.download_no_images")
                        task.fail(reason)
                        notifier.warning(reason)
                        logger.warning("Analysis: download refused — %s", reason)
                    else:
                        # Inside the tracked block: ``local_files.home_relative``
                        # raises ValueError for a path outside $HOME, and that
                        # must be a FAILED task + one error toast,
                        # not a green pill with download buttons that never show.
                        _apply_download(result)
            except Exception:  # noqa: BLE001 — tracked_job toasted and logged it (traceback included)
                pass
            else:
                if result is not None:
                    notifier.success(
                        t("analysis_tile.export_labels.download_done")
                    )
            finally:
                ns["download_alert_data_btn"].loading = False
                ns["download_alert_data_btn"].disabled = False

        spawn_in_context(_worker)

    # ------------------------------------------------------------------
    # Wire click handlers once (the widgets are built once per build). The buttons
    # start their workers; loading state is toggled inside the handlers.
    # ------------------------------------------------------------------
    def _register_handlers():
        ns["save_btn"].on_event("click", _on_save)
        ns["download_alert_data_btn"].on_event("click", _on_download)

    _register_handlers()

    # The project's report template (spec §10), a Solara field in the raw card.
    template_field = render_detached(lambda: ReportTemplateField(app_state=app_state),
                                     "report template", notify=False)[0]

    # ------------------------------------------------------------------
    # Layout: OLD card06 = export title + (new) report template field +
    # toolBarSaveExport + toolBarDownloads
    # (hidden). The OLD inline error alert is gone: failures are error toasts.
    # ------------------------------------------------------------------
    card = v.Card(
        class_="pa-2 ma-2",
        hover=True,
        children=[
            v.CardTitle(
                class_="pa-1 ma-1",
                children=[t("analysis_tile.export_labels.export_title")],
            ),
            template_field,
            ns["toolbar_save_export"],
            ns["toolbar_downloads"],
        ],
    )
    return card
