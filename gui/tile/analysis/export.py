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
``analyzed_alerts_model.last_save_time``. Everything guards on the gdf being present
and the id in range, so it is smoke-safe (nothing runs at mount;
``dependencies=None``).

Props
-----
widgets : AnalysisWidgets
    Shared heavy widgets + scratch. Read here: ``confirmation_select`` /
    ``driver_combobox`` (stashed by AlertReviewPanel), ``selected_img_before_info_list``
    / ``selected_img_after_info_list`` ([0]=source, [1]=milis, [3]=value/name,
    [4]=date-suffix), ``selected_img_before`` / ``selected_img_after`` (ee.Image),
    ``actual_alert_grid``, ``actual_bb``.
app_state : AppState
    Navigation + alerts source of truth (``alerts_gdf`` / ``actual_alert_id``).
aux_model : SetContext
    ``custom_report_template`` (the project's) for the report generation.
analyzed_alerts_model : AlertAnalysisModel
    ``defo_dl_layer`` (drawn polygon) / ``last_save_time`` (save stamp).
app_tile_model : SetContext
    ``recipe_folder_path`` (the active set's folder) for the output paths,
    ``pin`` (the active set, read once) for where a Save writes
    ``alert_db.parquet``, ``is_current``, which tells whether that set is
    still the active one when the Save writes, and ``record_review``, which
    keeps that set's reviewed count after a store save.
workspace : Workspace
    The session user-file adapter. Every artifact this card produces — the alert
    store, the gpkg, the composite GeoTIFF, the zip and the Word report — is user
    data and goes through it; the DownloadBtns get ``home_relative`` URLs.
"""

import logging
from datetime import datetime
from pathlib import Path, PurePosixPath

import ee
import geopandas as gpd
import ipyvuetify as v
from pysepal import sepalwidgets as sw

from deforestation_alerts.message import cm
from deforestation_alerts.scripts.alert_store import save_gdf_to_parquet
from deforestation_alerts.scripts.gee_progress import geedim_tile_progress
from deforestation_alerts.scripts.mosaics_helper import calculate_total_area
from deforestation_alerts.scripts.report_builder import (
    add_files_to_zip,
    ensure_list,
    format_list,
    generate_deforestation_report_with_word_template,
)
from gui.scripts import set_store
from gui.scripts.notify_bridge import (
    current_notifier,
    tile_progress_reporter,
    tracked_job,
)
from gui.scripts.solara_threads import spawn_in_context
from gui.store.project_writers import writing
from gui.tile.analysis.paths import stage_alert_image

logger = logging.getLogger("SEPALUI.deforestation_alerts.analysis")

# Default Word report template (OLD used this literal when no custom template set).
# Resolve from the repo root (export.py -> analysis -> tile -> gui -> root):
# the CWD is not guaranteed writable or even stable on the platform.
_DEFAULT_REPORT_TEMPLATE = str(
    Path(__file__).resolve().parents[3] / "data" / "report_template.docx"
)


def _writer_name(app_state) -> str:
    """The open project's name, the key the Project dialog's delete guard
    checks (``project_writers.is_writing``); ``""`` with no project open."""
    p = app_state.project.value
    return p.name if p is not None else ""


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


class SaveRefused(Exception):
    """A Save that must not write: nothing went wrong, it has nowhere to go.
    The worker reports it as a warning, not as an error."""


def _refuse_unless_current(alertas_gdf, app_tile_model, target) -> None:
    """Raise ``SaveRefused`` unless the Save may still write for ``target``.

    ``alerts_gdf`` and the active set are separate reactives: Integrate records
    a new set before it publishes that set's alerts, so for a moment the alerts
    on hand are the previous set's. Saved, they would overwrite the new set's
    store. And the admin lookups block for seconds, during which an Integrate
    can activate another set, or the project can be switched: the alerts this
    Save edited then belong to a set that is no longer the open project's
    active set, so they are not written at all (Review Focus 5).
    """
    if target is None:
        return
    if not set_store.alerts_belong_to(alertas_gdf, target.alert_set):
        raise SaveRefused(str(cm.analysis_tile.export_labels.save_other_set))
    if not app_tile_model.is_current(target):
        raise SaveRefused(str(cm.analysis_tile.export_labels.save_set_changed))


def _save_attributes(widgets, app_state, analyzed_alerts_model, app_tile_model, workspace):
    """Body of the OLD ``save_attributes_to_gdf`` (data/IO only, no widgets).

    Mutates ``app_state.alerts_gdf.value`` in place via ``.at[id, col] = ...``
    (faithful to OLD), does the blocking GEE admin lookup, persists
    ``alert_db.parquet``, and stamps ``last_save_time``. Returns ``True`` on success.
    Guards on the gdf being present + the id in range (smoke-safe). Raises the OLD
    "confirmation==No but a polygon is drawn" guard, surfaced by the save effect,
    and ``SaveRefused`` for alerts of another set than the one it writes for.

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

    # Persist the alert DB (OLD save_alerts_to_gdf) where the save started.
    # Skip if there is no folder.
    _save_alerts_to_gdf(
        alertas_gdf, app_tile_model, workspace, folder=folder, target=target
    )

    analyzed_alerts_model.last_save_time = datetime.today().timestamp()
    return True


def _save_alerts_to_gdf(
    alertas_gdf, app_tile_model, workspace, *, folder=None, target=None
):
    """Persist the alerts gdf to the canonical GeoParquet store.

    Writes ``<folder>/alert_db.parquet`` (all geometry columns preserved)
    into the user's SEPAL workspace through ``workspace``; the folder is a
    REMOTE path, so the join is posix, not ``os.path``. Then records the
    set's reviewed count (``record_review``, when the model has one).

    ``folder`` and ``target`` are where the Save started (``_save_target``);
    without ``folder`` both are read now. Raises ``SaveRefused``, writing
    nothing, unless the alerts are ``target``'s set's and that set is still the
    open project's active set.
    Guard: skip if the folder is empty (smoke / no set yet).
    """
    if folder is None:
        folder, target = _save_target(app_tile_model)
    if not folder:
        return
    _refuse_unless_current(alertas_gdf, app_tile_model, target)
    save_gdf_to_parquet(
        alertas_gdf,
        workspace,
        str(PurePosixPath(folder) / "alert_db.parquet"),
    )
    record = getattr(app_tile_model, "record_review", None)
    if record is not None:
        # The count of the set this store belongs to (the Analysis badge).
        record(alertas_gdf, target)


def _download_data(
    widgets, app_state, aux_model, app_tile_model, workspace, *, task=None
):
    """Body of the OLD ``download_data`` (IO only, no widgets).

    Builds the single-alert gpkg, the before/after GeoTIFF (if missing), the zip,
    and the Word report. Returns ``{"zip_path", "report_path"}`` — REMOTE
    workspace paths for the download effect to wire onto the two DownloadBtns.
    Guards on the gdf/id.

    ``recipe_folder_path`` is a remote path, but GeoPandas, geedim, ``zipfile``
    and python-docx all only speak local files, so all four artifacts are built
    in one private ``workspace.scratch_dir()`` and uploaded (inventory rows
    a20-a25 + c14/c16). The cached-GeoTIFF probe is a remote ``exists``, and a
    cache hit is staged back down so the zip and the report can read it.
    Everything staged is released in the ``finally``.

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

    # Every artifact below is written into the recipe folder; without one there
    # is nowhere in the user's workspace to put them (smoke / pre-recipe runs).
    if not app_tile_model.recipe_folder_path:
        return None
    recipe_folder = PurePosixPath(app_tile_model.recipe_folder_path)

    # Single-row GeoDataFrame for the selected alert.
    selected_element = alertas_gdf.iloc[actual_alert_id]
    selected_gdf = gpd.GeoDataFrame(
        [selected_element], columns=alertas_gdf.columns
    )
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

    # One staging area for the artifacts built here; `download_to_scratch` makes
    # its own private dir per file, so those are tracked separately. Releasing
    # `gpkg_local` releases the whole `stage` directory it lives in.
    stage = workspace.scratch_dir()
    gpkg_remote = recipe_folder / f"alert_{actual_alert_id}.gpkg"
    gpkg_local = stage / gpkg_remote.name
    staged = [gpkg_local]
    try:
        selected_gdf.set_crs(epsg="4326", allow_override=True, inplace=True).to_file(
            str(gpkg_local), driver="GPKG"
        )
        workspace.upload_from_scratch(gpkg_local, str(gpkg_remote))

        # Before/after image GeoTIFF (downloaded only if not already in the recipe
        # folder). Built via the shared helper so it resolves to the same cached
        # file the CNN run (edit_tools) wrote.
        on_tiles = tile_progress_reporter(task) if task is not None else None
        with geedim_tile_progress(on_tiles):
            image_local, _ = stage_alert_image(
                workspace,
                str(recipe_folder),
                actual_alert_id,
                widgets.selected_img_before_info_list,
                widgets.selected_img_after_info_list,
                widgets.selected_img_before,
                widgets.selected_img_after,
                widgets.actual_alert_grid,
            )
        staged.append(image_local)
        if task is not None:
            task.set_progress(None)  # zip + report: back to indeterminate

        # Zip the image + gpkg.
        zip_remote = recipe_folder / f"alert_{actual_alert_id}.zip"
        zip_local = stage / zip_remote.name
        add_files_to_zip(str(zip_local), str(image_local), str(gpkg_local))
        workspace.upload_from_scratch(zip_local, str(zip_remote))

        # Word report. The DEFAULT template is a packaged asset (inventory b1) and
        # stays container-local; a CUSTOM one is a user file and is staged down.
        report_remote = recipe_folder / f"alert_{actual_alert_id}.docx"
        if aux_model.custom_report_template:
            report_template = workspace.download_to_scratch(
                aux_model.custom_report_template
            )
            staged.append(report_template)
        else:
            report_template = _DEFAULT_REPORT_TEMPLATE
        generate_deforestation_report_with_word_template(
            str(image_local),
            str(gpkg_local),
            str(report_template),
            str(report_remote),
            workspace,
        )

        return {"zip_path": str(zip_remote), "report_path": str(report_remote)}
    finally:
        for path in staged:
            workspace.discard_scratch(path)


def build_export(
    widgets,
    app_state,
    aux_model,
    analyzed_alerts_model,
    app_tile_model,
    workspace,
):
    """Build and return the export card (OLD ``card06``) with the save + download flow.

    ``workspace`` is the session ``Workspace`` built in ``Page()`` and threaded
    down like ``gee_interface``: the alert store it writes is a user file and
    must land in the user's SEPAL workspace, not the container's disk.
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
        # The zip/report live in the user's SEPAL workspace, so the buttons get
        # HOME-RELATIVE paths: ``set_url`` -> ``su.create_download_link`` builds
        # ``.../jupyter/files/<Path(path).relative_to(Path.home())>``, and in the
        # container ``Path.home()`` is the CONTAINER's home, so an absolute
        # workspace path degrades to a dead link (inventory a23).
        ns["files_dwn_btn"].set_url(path=workspace.home_relative(result["zip_path"]))
        ns["report_dwn_btn"].set_url(
            path=workspace.home_relative(result["report_path"])
        )
        ns["toolbar_downloads"].show()

    # ------------------------------------------------------------------
    # Save handler: button-triggered daemon thread (blocking GEE .getInfo() / CSV
    # write; never auto-run). The button spins for the duration; on success the
    # worker applies the OLD save effect, on failure tracked_job publishes the
    # error (incl. the OLD "confirmation==No but a polygon is drawn" guard) as a
    # toast and the worker logs it.
    # ------------------------------------------------------------------
    def _on_save(*_):
        ns["save_btn"].loading = True
        ns["save_btn"].disabled = True

        def _worker():
            # No ``total_steps`` — the save is a single opaque phase (GEE admin
            # lookups + the alert-store write), so the pill stays indeterminate.
            # On an exception tracked_job fails the task and publishes the one
            # error toast (the raw exception text), then re-raises into except.
            notifier = current_notifier()
            try:
                with tracked_job(
                    notifier, str(cm.analysis_tile.export_labels.save_task_title)
                ) as task:
                    task.step(str(cm.analysis_tile.export_labels.save_step))
                    # Writer tracking: register the open project while the
                    # set's alert store is written, so deleting the project
                    # refuses mid-write.
                    with writing(_writer_name(app_state)):
                        try:
                            saved = _save_attributes(
                                widgets,
                                app_state,
                                analyzed_alerts_model,
                                app_tile_model,
                                workspace,
                            )
                            refused = None
                        except SaveRefused as refusal:
                            saved, refused = False, str(refusal)
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
                        reason = refused or str(cm.analysis_tile.export_labels.save_no_alert)
                        task.fail(reason)
                        notifier.warning(reason)
                        logger.warning("Analysis: save refused — %s", reason)
                    else:
                        # Inside the tracked block: if the apply step raises, the
                        # run is a FAILED task + one error toast, not a green pill.
                        _apply_save()
            except Exception:  # noqa: BLE001 — tracked_job already toasted it
                logger.exception("Analysis: save attributes failed")
            else:
                if saved:
                    notifier.success(str(cm.analysis_tile.export_labels.save_done))
            finally:
                ns["save_btn"].loading = False
                ns["save_btn"].disabled = False

        spawn_in_context(_worker)

    # ------------------------------------------------------------------
    # Download handler: button-triggered daemon thread (heavy report generation /
    # IO). On success the worker applies the OLD download effect with the returned
    # zip/report paths; on failure tracked_job publishes the error as a toast and
    # the worker logs it.
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
                    notifier, str(cm.analysis_tile.export_labels.download_task_title)
                ) as task:
                    task.step(str(cm.analysis_tile.export_labels.download_step))
                    # Writer tracking: the report/zip generation writes into the
                    # set's folder — register the open project for the duration.
                    with writing(_writer_name(app_state)):
                        result = _download_data(
                            widgets,
                            app_state,
                            aux_model,
                            app_tile_model,
                            workspace,
                            task=task,
                        )
                    if result is None:
                        # `_download_data` refused: nothing was written and no
                        # download button will be revealed, so completing the
                        # task would show a green pill for a no-op the user
                        # cannot explain. A WARNING outcome: fail() BEFORE the
                        # toast, so the run is already recorded as FAILED (with
                        # its reason) in the task log when the message lands.
                        # (The two causes are re-derived here rather than
                        # returned, so `_download_data` keeps its "paths or
                        # nothing" contract.) Log last: a FAILED task takes no
                        # more milestones.
                        reason = str(
                            cm.analysis_tile.export_labels.download_no_recipe
                            if not app_tile_model.recipe_folder_path
                            else cm.analysis_tile.export_labels.download_no_alert
                        )
                        task.fail(reason)
                        notifier.warning(reason)
                        logger.warning("Analysis: download refused — %s", reason)
                    else:
                        # Inside the tracked block: ``workspace.home_relative``
                        # raises InvalidPathError for a path outside the SEPAL
                        # home, and that must be a FAILED task + one error toast,
                        # not a green pill with download buttons that never show.
                        _apply_download(result)
            except Exception:  # noqa: BLE001 — tracked_job already toasted it
                logger.exception("Analysis: download data failed")
            else:
                if result is not None:
                    notifier.success(
                        str(cm.analysis_tile.export_labels.download_done)
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

    # ------------------------------------------------------------------
    # Layout: OLD card06 = export title + toolBarSaveExport + toolBarDownloads
    # (hidden). The OLD inline error alert is gone: failures are error toasts.
    # ------------------------------------------------------------------
    card = v.Card(
        class_="pa-2 ma-2",
        hover=True,
        children=[
            v.CardTitle(
                class_="pa-1 ma-1",
                children=[cm.analysis_tile.export_labels.export_title],
            ),
            ns["toolbar_save_export"],
            ns["toolbar_downloads"],
        ],
    )
    return card
