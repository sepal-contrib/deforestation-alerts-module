"""build_export sub-component (Task 9.4 / B3): save attributes + gpkg/zip + Word report.

Architecture layer: view (raw-widget builder, no ``@solara.component``). Ports the
OLD ``AnalysisTile`` export card (``card06`` +
``label35``/``toolBarSaveExport``/``toolBarDownloads``) as the **body of the
collapsible Export section** (spec §5), which sits in the panel's sticky
footer (spec D6, ``footer``): the section owns the title, and the footer owns
the Save, so ``build_export`` builds neither.

* The OLD **Save** button (``save_btn``) ran ``save_attributes_to_gdf`` —
  writing the user's verdict / answers / image-info / GEE-admin attributes
  + the drawn polygon area onto ``alerts_gdf`` and persisting ``alert_db.parquet``.
  What a Save writes, and the store transaction, live in ``review_save``;
  the runner that drives it is ``make_save_runner`` (module level) and the button
  itself in the footer, which drives that runner; the runner's ``then``
  is what moves to the next alert, and only a save that wrote calls it.
  ``build_export`` returns the runner so there is ONE save for the panel.
* The section's **summary badge** (``set_badge``) says what the section can
  offer without opening it: nothing until a save of this alert lands, then a
  build, then the count of files that build left ready. An alert change takes
  it back to the first state, because the build button and the two download
  links belong to the alert they were made for.
* The dashed **Build alert files** tile (``download_alert_data_btn``, in
  ``toolbar_build``) runs the OLD ``download_data`` (``alert_files``) — building the single-alert
  gpkg, the before/after-image GeoTIFF, the zip, and the Word report. Until a
  save of this alert lands it is disabled and carries a muted "(save first)"
  hint (``save_first``).
* The S1 file tiles (``file_tile.FileTile``: ``files_dwn_btn`` / ``report_dwn_btn``)
  live in a ``toolbar_downloads`` that is hidden until a build completes, then
  get the zip / report (``set_file``) and are revealed. In that READY state the
  build tile is hidden and a small right-aligned **Rebuild** text button
  (``rebuild_btn``, in ``toolbar_rebuild``) runs the same build; anything that
  invalidates the files (a new save, a template change) brings the build tile
  back.
* The **Timelapse** tile (``gif_dwn_btn``, a ``FileTile``) is always shown and
  is its own export button (spec D9). Until a GIF of the alert exists it is a
  plain button (``set_action``) that animates a true alert's before/after
  pair with its neighbours: clickable only while the alert's row is Confirmed
  on the saved pair, its detail saying why not otherwise, rendered on a
  worker. Once the GIF exists the tile is its download link. A Confirmed
  alert's zip build makes the GIF too and puts it in the zip; the badge
  counts it among the files ready.

WHY A RAW WIDGET (not a Solara component + ``solara.display``): MapApp renders the
Analysis dialog with ``v-if``, so it DESTROYS the dialog DOM on close and REBUILDS it
on reopen. Content captured via ``solara.display`` lives inside an ``Output`` whose
view is NOT recreated on that rebuild, so it vanishes the 2nd time the dialog is
opened. A plain ipyvuetify widget tree is re-viewed from its model on reopen, so it
survives. ``build_export`` builds the card once and RETURNS it; the parent hands it
to MapApp as dialog content — same pattern as ``context`` / ``review_panel`` /
``edit_tools`` and ``recipe_tile.build_recipe_layout``.

Threading (FIXED by the task contract — same shape as ``edit_tools`` / ``recipe_tile``):

* **Save + Download each run button-triggered via ``spawn_in_context``** (both
  do blocking GEE ``.getInfo()`` / heavy report generation). They NEVER auto-run; the
  click handler toggles that button's ``.loading``/``.disabled`` and starts the
  worker through ``notify_bridge.run_button_job``, which runs the blocking work
  under ``tracked_job`` and always releases the button, so the notification pill shows it
  running and the worker thread's app log lines become its milestones.
* **No widget mutation inside the blocking bodies.** The save body does the gdf
  mutation + GEE admin lookup + CSV write (data/IO only) and returns ``True``. The
  download body does the gpkg/image/zip/report (IO only) and RETURNS
  ``{"zip_path": ..., "report_path": ...}``. The OLD post-task apply step (enabling
  the build tile, ``set_file(...)``, showing ``toolbar_downloads``) now runs at the
  END of the corresponding worker's tracked block with that returned value (so a
  failing apply is still a FAILED task); only the success toast follows the block. A failure is a FAILED task + one error toast, both published by
  ``tracked_job``; the worker's ``except`` only logs it. A refused Save (no
  alert selected, or ``SaveRefused``: the alerts or the active set changed) is a
  FAILED task + one warning toast. ipywidgets
  trait sets (and the ``alerts_gdf`` reactive rebind) from the worker thread are fine
  (recipe_tile / edit_tools do exactly this).
* The buttons + toolbars are **built once** (the builder runs once per build) so the
  imperative ``.show()/.hide()/.disabled``/``.set_file()`` calls port unchanged; the
  file tiles start in a hidden ``toolbar_downloads`` and the build tile starts
  disabled (enabled by the save worker's success path).

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
gdf in place via ``.at[id, col] = ...`` (faithful to OLD); the save runner
stamps ``analysis.last_save_time`` once the write landed, for the alert it
saved. Everything guards on the gdf being present
and the id in range, so it is smoke-safe (nothing runs at mount;
``dependencies=None``).

Props
-----
widgets : AnalysisWidgets
    Shared heavy widgets + scratch. Read here: ``actual_alert_grid``,
    ``actual_bb``. The reviewer's verdict and answers are NOT widgets: they
    live in ``analysis.verdict`` / ``analysis.answers`` and reach a Save
    through ``capture_alert``.
app_state : AppState
    Navigation + alerts source of truth (``alerts_gdf`` / ``actual_alert_id``).
aux_model : SetContext
    ``custom_report_template`` (the project's) for the report generation.
analysis : AnalysisState
    ``verdict`` (the reviewer's answer: one of the three stored statuses, or
    ``None``, which a Save refuses), ``defo_layer`` (drawn polygon),
    ``before_info`` / ``after_info`` ([0]=source, [1]=milis, [3]=value/name,
    [4]=date-suffix), ``before_image`` / ``after_image`` (ee.Image),
    ``last_save_time`` (save stamp) and ``dirty``, which a Save that wrote
    clears.
app_tile_model : SetContext
    ``recipe_folder_path`` (the active set's folder) for the output paths,
    ``pin`` (the active set, read once) for where a Save writes
    ``alert_db.parquet``, ``is_current``, which tells whether that set is
    still the active one when the Save writes, and ``record_review``, which
    keeps that set's reviewed count after a store save.

Every artifact this card produces — the alert store, the gpkg, the composite
GeoTIFF, the zip and the Word report — is written straight into the set folder
through a unique ``.part`` renamed into place (``local_files``); the
file tiles get URLs relative to the served root (``local_files``).
"""

import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import ipyvuetify as v
from pysepal import sepalwidgets as sw

from deforestation_alerts.project.review_status import CONFIRMED
from gui.i18n import t, tn
from gui.scripts.error_text import error_text
from gui.scripts.notify_bridge import current_notifier, run_button_job
from gui.scripts.solara_threads import spawn_in_context
from gui.store.project_writers import writer_name, writing
from gui.tile.analysis import alert_files, review_save
from gui.tile.analysis import gif as gif_mod
from gui.tile.analysis.file_tile import FileTile
from gui.tile.analysis.labels import Labels
from gui.tile.analysis.paths import alert_gif_name
from gui.tile.analysis.review_save import SaveRefused
from gui.tile.analysis.state import capture_alert
from gui.widget.buttons import btn, set_label
from gui.widget.text_style import MUTED

logger = logging.getLogger("SEPALUI.deforestation_alerts.analysis")


def _save_attributes(widgets, app_state, analysis, app_tile_model, alert=None):
    """The save runner's body: ``review_save.save_review`` of ``alert``
    (``state.capture_alert``; taken here when not given)."""
    alert = alert if alert is not None else capture_alert(analysis, widgets, app_state)
    return review_save.save_review(alert, app_tile_model)


def _download_data(widgets, analysis, app_state, aux_model, app_tile_model, *, task=None, alert=None, gif=None):
    """The build's body: ``alert_files.build_alert_files`` of ``alert``
    (``state.capture_alert``; taken here when not given) into the set folder."""
    alert = alert if alert is not None else capture_alert(analysis, widgets, app_state)
    return alert_files.build_alert_files(alert, app_tile_model.recipe_folder_path, aux_model.custom_report_template,
                                         task=task, gif=gif)


def make_save_runner(widgets, app_state, analysis, app_tile_model, apply_save=None, on_refused=None):
    """The review save, as a callable the footer's two buttons drive.

    ``run_save(button, then=None)``: spin ``button`` (optional), capture the
    alert on the UI thread, write the store on a worker, and run ``then`` only
    after a save that actually wrote **and only while the alert that was saved
    is still the one on screen** — Save & next passes ``go_next``, so a refusal
    must leave the reviewer on the alert it refused (with the message that
    explains it), and a save that lands after the reviewer navigated by hand
    must not advance from where they went. ``then`` runs on the worker thread,
    before the button unspins, so a slow ``then`` keeps the button spinning.

    ``apply_save`` is the post-save UI step (the export section's build
    tile + the ``alerts_gdf`` rebind); the default rebinds alone, so the
    runner is usable before the export section is built.

    ``on_refused(refusal)`` runs on the worker for a ``SaveRefused``, after
    the warning toast that explains it: the panel opens the Questions section
    for a required answer left empty (spec D5). A hook that raises is logged
    and changes nothing else -- the refusal has already been reported.

    One save runs at a time, whichever button starts it: ``run_save.guard``
    registers the buttons that share the runner (the footer's two), so
    they are all held for the duration and none of them can start a second
    writer over the same store and row.
    """

    def _default_apply():
        gdf = app_state.alerts_gdf.value
        if gdf is not None:
            app_state.alerts_gdf.value = gdf.copy()

    apply = apply_save or _default_apply

    # One writer at a time: the buttons that share this runner, and whether a
    # save of theirs is in flight. Both halves matter -- the buttons so the
    # reviewer is not offered a second save, the flag because a click can
    # still arrive (another code path, a disabled button's queued event).
    guarded = []
    saving = {"on": False}

    def _guard(*buttons):
        """Register buttons that must be held while any save of this runner
        runs. Called once by the block that owns them (``footer``)."""
        guarded.extend(b for b in buttons if b not in guarded)

    def _hold(state):
        for button in guarded:
            button.disabled = state

    def _mark_saved(alert):
        """This alert's review is on disk: nothing unsaved left, and the
        save-state line shows when. Short on purpose: an alert change waits
        for it (``apply_if_current`` holds ``_alert_lock``).

        ``dirty`` is cleared only if the reviewer has not changed anything
        since the click: what was written is the review ``capture_alert``
        read then, so a later verdict or driver is still unsaved and saying
        otherwise loses it silently. The stamp is written either way -- a save
        of this alert did land -- and the unsaved line takes precedence over
        it in the footer's state line.
        """
        analysis.last_save_time.set(datetime.today().timestamp())
        if analysis.edit_rev.value == getattr(alert, "edit_rev", None):
            analysis.dirty.set(False)
            return True
        return False

    def run_save(button=None, then=None):
        if saving["on"]:
            # A save of this runner is already writing the store and the row:
            # a second one would race it, and the row values of whichever
            # finished last would win. The buttons are held below, so this is
            # the backstop, not the message.
            logger.info("Analysis: a save is already running, ignoring the click")
            return
        saving["on"] = True
        _hold(True)
        if button is not None:
            button.loading = True
            button.disabled = True
        # The alert saved and its values, read here on the UI thread: the
        # worker writes them whatever is shown by the time the lookups end.
        alert = capture_alert(analysis, widgets, app_state)
        token = alert.token  # the alert this save is for

        outpaced = []  # the reviewer changed this alert while the save ran
        refusal = {}  # the SaveRefused of a refused save, for ``on_refused``

        # No ``total_steps`` — the save is a single opaque phase (GEE admin
        # lookups + the alert-store write), so the pill stays indeterminate.
        def _body(task, notifier):
            task.step(t("analysis_tile.export_labels.save_step"))
            # Writer tracking: register the open project while the set's
            # alert store is written, so deleting the project refuses mid-write.
            with writing(writer_name(app_state)):
                try:
                    saved = _save_attributes(widgets, app_state, analysis, app_tile_model, alert=alert)
                except SaveRefused as exc:
                    refusal["exc"] = exc
                    return error_text(exc)
            if not saved:
                # A missing/invalid alert id: nothing was written and the
                # download button stays disabled, so completing the task would
                # record a DONE run for a no-op the user cannot explain.
                return t("analysis_tile.export_labels.save_no_alert")
            # Inside the tracked block: if the apply step raises, the run is a
            # FAILED task + one error toast, not a green pill.
            #
            # Only while the alert saved is still the one on screen:
            # ``apply_save`` is per-alert UI (the export section's build button
            # and its badge), and a save that lands after the reviewer moved on
            # would otherwise announce "ready to build" over an alert nobody
            # saved. The set-wide half — re-publishing ``alerts_gdf`` so
            # Overview's counts and markers see the write — is NOT per-alert and
            # still has to run: the store write landed either way. That half is
            # exactly ``_default_apply``.
            if not analysis.apply_if_current(token, apply):
                _default_apply()
            return None

        def _refused(_reason):
            # After the toast: the reviewer reads why, then sees where (a
            # required answer opens the Questions section).
            if on_refused is not None and "exc" in refusal:
                on_refused(refusal["exc"])

        def _saved(notifier):
            notifier.success(t("analysis_tile.export_labels.save_done"))
            # This alert's review is on disk: nothing unsaved left. Keyed on
            # the token for the same reason as the apply above: ``reset_alert``
            # has already cleared the flag for the alert now on screen, which
            # may carry edits of its own that this save did not write.
            #
            # The save stamp goes with it, and under the same guard: it is
            # per-alert UI (``reset_alert`` clears it so an alert nobody saved
            # shows no time at all), so a save that lands after the reviewer
            # navigated must not stamp the alert they went to as saved.
            analysis.apply_if_current(token, lambda: outpaced.append(not _mark_saved(alert)))
            # Said, not left to the state line alone: after *Save & next* the
            # reviewer is watching for the next alert, and the one thing on
            # screen would be a save they pressed that did not advance.
            if any(outpaced):
                reason = t("analysis_tile.export_labels.save_outpaced")
                notifier.warning(reason)
                logger.info("Analysis: %s", reason)
            # Only now — Save & next must not move off an alert whose review
            # was refused. And only for the alert this save was for: ``then``
            # is ``go_next``, so a save that lands after the reviewer navigated
            # by hand would advance from THERE and leave them one alert further
            # than where they went, with nothing to say why. ``is_current``,
            # not ``apply_if_current``: ``go_next`` republishes
            # ``actual_alert_id``, whose subscriber runs the whole per-alert
            # orchestration (``reset_alert`` included), and that must not run
            # under ``_alert_lock``. Guarded: a failing ``then`` is a log line,
            # not a lost success toast.
            #
            # And not off a change this save did not write: advancing would
            # leave the reviewer's later verdict or driver behind on an alert
            # they can no longer see is unsaved.
            if then is not None and analysis.is_current(token) and not any(outpaced):
                try:
                    then()
                except Exception:
                    logger.exception("Analysis: after-save step failed")

        def _release():
            # The runner is free again and every button it shares comes back,
            # whichever one started this save.
            saving["on"] = False
            _hold(False)
            if button is not None:
                button.loading = False
                button.disabled = False

        run_button_job(
            t("analysis_tile.export_labels.save_task_title"), _body,
            release=_release, on_success=_saved, on_refused=_refused,
            refused_log="Analysis: save refused", logger=logger,
            spawn=spawn_in_context, notifier=current_notifier,
        )

    run_save.guard = _guard
    return run_save


def build_export(
    widgets,
    app_state,
    aux_model,
    analysis,
    app_tile_model,
    set_badge=None,
    on_refused=None,
    *,
    labels=None,
):
    """Build and return the Export section's body and the review save runner.

    ``SimpleNamespace(body, run_save, tiles)``: ``body`` is what the
    collapsible Export section wraps (the dashed build tile and the hidden
    file tiles, spec D6) — the section owns the title, and the footer owns
    the Save, so neither is built here. The report template is picked in the
    Questionnaire step now; a change of it still invalidates the files built
    here. ``run_save`` is the one save runner, handed to the footer so both
    save paths run the same post-save step and light up the build tile below.
    ``tiles`` are the three ``FileTile`` (zip, docx and the GIF placeholder).

    ``on_refused`` goes to ``make_save_runner``: what the panel does with a
    refused save once its warning is out.

    ``set_badge(text, color=None)`` is the section's badge setter (``None``
    before Task 10 wires one). It takes text that is ALREADY translated, so
    every message key the badge shows is resolved here.

    The alert store, the zip and the report it writes land in the active set's
    folder.

    ``labels`` (a ``Labels``) gets the section's label hook: a language switch
    rewrites the build tile, *Rebuild* and the three file tiles' words, and the
    GIF tile's action text (a GIF still rendering included).
    """
    labels = labels if labels is not None else Labels()

    # ------------------------------------------------------------------
    # The S1 layout (spec D6, the mockup's three states), created once so the
    # imperative .show()/.hide()/.disabled/.set_file() calls port unchanged:
    #
    # * BEFORE A SAVE -- the dashed build tile, disabled, "(save first)";
    # * SAVED -- the build tile, enabled, without the hint;
    # * READY -- the build tile gives way to the zip and report tiles and a
    #   small *Rebuild* under them, which runs the same build.
    #
    # The GIF tile is shown, disabled, in every state. ``_show_badge_for_state``
    # is what puts the body in its state. The ns keys of the build tile and
    # the two file tiles are the OLD buttons' names, so the logic around them
    # reads as it did.
    # ------------------------------------------------------------------
    def _build_widgets():
        ns = {}

        ns["save_first"] = v.Html(
            tag="span", class_="font-weight-regular ml-1", style_=MUTED,
            children=[f"({t('analysis_tile.export_labels.save_first')})"],
        )
        ns["download_alert_data_btn"] = v.Btn(
            outlined=True, block=True, color="primary", disabled=True, class_="defo-build-tile",
            style_="border-style:dashed;height:44px;text-transform:none;letter-spacing:normal;margin-bottom:6px;",
            children=[v.Icon(left=True, small=True, children=["mdi-cogs"]), "", ns["save_first"]],
        )
        # A minor link under the tiles, as the workflow's *Edit split*.
        ns["rebuild_btn"] = btn("tertiary", icon="mdi-refresh", color="primary", small=False, x_small=True,
                                class_="defo-rebuild")
        ns["files_dwn_btn"] = FileTile("zip", t("analysis_tile.export_labels.tile_zip"),
                                       t("analysis_tile.export_labels.tile_zip_detail"))
        ns["report_dwn_btn"] = FileTile("docx", t("analysis_tile.export_labels.tile_docx"),
                                        t("analysis_tile.export_labels.tile_docx_detail"))
        ns["gif_dwn_btn"] = FileTile("gif", t("analysis_tile.export_labels.tile_gif"),
                                     t("analysis_tile.export_labels.tile_gif_detail"))
        ns["toolbar_build"] = sw.Html(tag="div", children=[ns["download_alert_data_btn"]])
        ns["toolbar_downloads"] = sw.Html(tag="div", children=[
            ns["files_dwn_btn"], ns["report_dwn_btn"]]).hide()
        ns["toolbar_rebuild"] = sw.Html(tag="div", class_="d-flex justify-end",
                                        children=[ns["rebuild_btn"]]).hide()
        # No file yet: the tile is the Export GIF button, locked until the gate
        # (``_refresh_gif_gate``) opens it.
        ns["gif_dwn_btn"].set_action(False, t("analysis_tile.export_labels.gif_hint"))

        return ns

    ns = _build_widgets()

    # What this section can offer for the alert under review: whether a save
    # of it has landed (so a build is possible) and how many files the last
    # build left ready. Kept here rather than read back off the build button's
    # ``disabled``, which is ALSO true while a build runs (the click handler
    # spins it) and would make the badge say "available after save" over a
    # build in progress. Reset by every alert change. ``rev`` / ``gif_rev``:
    # the revisions of the inputs of the zip build and of the GIF, which a
    # worker compares with the one it started on (a template change bumps
    # ``rev`` alone: the GIF is not made from it).
    built = {"saved": False, "files": 0, "rev": 0, "gif": 0, "gif_rev": 0}
    # A GIF of this section is rendering: the gate holds the button disabled.
    gif_running = {"on": False}
    # The key of the GIF tile's action text, so a switch can reword a tile
    # held by a running GIF (the gate skips it then). The tile starts locked.
    gif_action = {"key": "analysis_tile.export_labels.gif_hint"}

    def _set_build_enabled(flag):
        """The build tile and *Rebuild* are one action (two faces of it, per
        S1 state), so they are enabled and disabled together."""
        ns["download_alert_data_btn"].disabled = not flag
        ns["rebuild_btn"].disabled = not flag

    def _invalidate_built_files(gif=True):
        """Stop offering what the last build left ready.

        The zip and the report are a snapshot: of the review as the save
        wrote it, of the imagery pair they were exported from and of the
        report template they were rendered with. Once any of those changes the
        files on disk are the previous answer, and the two links plus the
        "N files ready" badge would hand the reviewer that answer for the
        state in front of them. Nothing is deleted -- the files stay in the
        set folder; they are simply not this review's any more, and the
        section goes back to offering the rebuild.

        The links are cleared, not only hidden: ``set_file(None)`` points both
        at the sandbox file route's own "#" (nothing of this project's),
        disables them, drops their ``download`` attribute and their size, so
        anything that reveals the toolbar again cannot resurrect them.

        ``rev`` is the revision a build carries from its click
        (``_on_download``): one that started before the change cannot restore
        a readiness that is already stale. The caller refreshes the badge --
        ``_forget_previous_alert`` has more to change before it reads.

        The GIF is of the same review and the same pair (spec D9), but not of
        the report template: ``gif=False`` (a template change) leaves its link
        and its own revision, ``gif_rev``, alone, so a GIF still rendering
        lands.
        """
        ns["files_dwn_btn"].set_file(None)
        ns["report_dwn_btn"].set_file(None)
        ns["toolbar_downloads"].hide()
        built["files"] = 0
        built["rev"] += 1
        if gif:
            ns["gif_dwn_btn"].set_file(None)
            # Back to the locked button; the gate reopens it if it may.
            gif_action["key"] = "analysis_tile.export_labels.gif_hint"
            ns["gif_dwn_btn"].set_action(False, t(gif_action["key"]))
            built["gif"] = 0
            built["gif_rev"] += 1

    # ------------------------------------------------------------------
    # The section's summary badge: what this section can offer right now,
    # without opening it. Three states, in the order a reviewer meets them —
    # nothing to build yet, a build is possible, the files are ready.
    # ------------------------------------------------------------------
    def _badge(text, color=None):
        if set_badge is not None:
            set_badge(text, color=color)

    def _show_badge_for_state():
        # ``viz`` is sw.SepalWidget's shown flag (what .show()/.hide() write);
        # a plain widget has none, and then the toolbar counts as shown.
        revealed = getattr(ns["toolbar_downloads"], "viz", True)
        built_files = built["files"] if revealed else 0
        files = built_files + built["gif"]
        # The body's S1 state (see ``_build_widgets``): the hint until a save
        # of this alert lands, and the build tile or -- once a build has left
        # files -- the small *Rebuild* under them.
        ns["save_first"].style_ = MUTED if not built["saved"] else "display: none;"
        if built_files:
            ns["toolbar_build"].hide()
            ns["toolbar_rebuild"].show()
        else:
            ns["toolbar_rebuild"].hide()
            ns["toolbar_build"].show()
        # Files first: the GIF can be offered without a save in this session
        # (its gate reads the row, spec D9), and a live link under "available
        # after save" would contradict it.
        if files:
            _badge(tn("analysis_tile.sections.export_files", files), color="primary")
        elif built["saved"]:
            _badge(t("analysis_tile.sections.export_build"))
        else:
            _badge(t("analysis_tile.sections.export_after_save"))

    def _invalidate_and_badge(*_, gif=True):
        """``_invalidate_built_files`` + the badge, for the subscribers whose
        only job is the invalidation."""
        try:
            _invalidate_built_files(gif=gif)
            _show_badge_for_state()
        except Exception:
            logger.exception("Analysis: invalidating the built files failed")

    def _refresh_gif_gate(*_):
        """The Timelapse tile makes a GIF for a true alert (spec D9): the row's stored
        status is Confirmed and the pair on screen (both sides picked) is the
        pair the row was saved with -- another pick disables it, picking the
        saved pair back enables it. The row, not this session's saves: a true
        alert reopened on its saved pair offers it at once. Held disabled
        while a GIF of this section renders; a tile that already links its
        GIF is left a link (anything that invalidates it clears it first)."""
        if gif_running["on"] or ns["gif_dwn_btn"].path is not None:
            return
        alerts, alert_id = app_state.alerts_gdf.value, app_state.actual_alert_id.value
        allowed = alert_files.gif_allowed(alerts, alert_id, analysis.before_info.value, analysis.after_info.value)
        gif_action["key"] = ("analysis_tile.export_labels.tile_gif_make" if allowed
                             else "analysis_tile.export_labels.gif_hint")
        ns["gif_dwn_btn"].set_action(allowed, t(gif_action["key"]))

    def _label():
        """The section's words in the current language. Text only, plus the
        two audited callbacks (the gate and the badge), whose other writes are
        derived from state a switch does not change."""
        ns["save_first"].children = [f"({t('analysis_tile.export_labels.save_first')})"]
        build = ns["download_alert_data_btn"]
        build.children = [build.children[0], t("analysis_tile.export_labels.build_tile"), ns["save_first"]]
        set_label(ns["rebuild_btn"], t("analysis_tile.export_labels.rebuild_tile"))
        # Literal keys (not f-strings), so the catalogue test sees each one.
        for name, title, detail in (
            ("files_dwn_btn", "analysis_tile.export_labels.tile_zip", "analysis_tile.export_labels.tile_zip_detail"),
            ("report_dwn_btn", "analysis_tile.export_labels.tile_docx",
             "analysis_tile.export_labels.tile_docx_detail"),
            ("gif_dwn_btn", "analysis_tile.export_labels.tile_gif", "analysis_tile.export_labels.tile_gif_detail"),
        ):
            ns[name].set_title(t(title))
            ns[name].set_detail(t(detail))
        # A running GIF: the gate below returns at once, so the held tile's
        # words are rewritten here, and nothing else on it (a no-op on a
        # tile that links its GIF: ``set_file`` cleared its action).
        ns["gif_dwn_btn"].relabel_action(t(gif_action["key"]))
        _refresh_gif_gate()
        _show_badge_for_state()

    # ------------------------------------------------------------------
    # An alert change takes the section back to its empty state. What it can
    # offer belongs to ONE alert: the build button was enabled by a save of
    # the previous alert, and the two download links still point at that
    # alert's zip and report. Left alone they are a badge that counts files
    # the reviewer cannot get for the alert in front of them — the same leak
    # as the save stamp that used to outlive its alert.
    #
    # Keyed on ``alert_token`` (bumped by EVERY ``reset_alert``), not on the
    # save stamp: the stamp is cleared to the value it already has when no
    # save landed on the previous alert, and a reactive set to an equal value
    # notifies nobody.
    # ------------------------------------------------------------------
    def _forget_previous_alert(*_):
        _set_build_enabled(False)
        # Off, without asking the gate: at the token bump the picks are still
        # the previous alert's while the row may already be the next one's,
        # and comparing the two could enable it. ``reset_alert`` clears the
        # picks right after, and each pick that lands runs the gate.
        _invalidate_built_files()  # the GIF tile goes back to locked with it
        built["saved"] = False
        _show_badge_for_state()

    # Registered once (the builder runs once per session); the unsubscriber is
    # deliberately discarded, as everywhere else under analysis/.
    analysis.alert_token.subscribe(_forget_previous_alert)

    # The other two inputs of a build, invalidated for the alert ON screen.
    #
    # The imagery pair: the composite in the zip and the figures in the report
    # are of the pair they were exported from, so another pick (which is also
    # an unsaved change to the review, ``state.imagery_changed``) leaves them
    # describing imagery the reviewer is no longer looking at. Both infos,
    # because either side alone changes the composite.
    analysis.before_info.subscribe(lambda *_: (_invalidate_and_badge(), _refresh_gif_gate()))
    analysis.after_info.subscribe(lambda *_: (_invalidate_and_badge(), _refresh_gif_gate()))

    # The report template: the reviewer's own ``.docx``, picked in the
    # Questionnaire step. It is a project value, so what the picker writes arrives as a
    # publish of ``app_state.project``; only a CHANGE of the template
    # invalidates, or every autosave of the project would drop the links.
    # The zip and the report only: the GIF is not made from the template.
    template_seen = {"value": aux_model.custom_report_template}

    def _on_project(*_):
        value = aux_model.custom_report_template
        if value != template_seen["value"]:
            template_seen["value"] = value
            _invalidate_and_badge(gif=False)

    app_state.project.subscribe(_on_project)

    # ------------------------------------------------------------------
    # Post-save apply (OLD save effect, success path) — enable the download button
    # + poke the alerts_gdf reactive. Runs at the END of the save worker's tracked
    # block, only when the save wrote something (main thread previously; trait
    # sets / reactive rebind from the worker thread are fine), so a failure here
    # still fails the task. The OLD error branch becomes tracked_job's error.
    # ------------------------------------------------------------------
    def _apply_save():
        _set_build_enabled(True)
        built["saved"] = True
        # The review just written is not the one the last build captured, so
        # its zip and report are the previous answer: the links go, and the
        # section offers the rebuild that was enabled a line above.
        _invalidate_built_files()
        # Right after the enable, not at the end: the rebind below can raise
        # (a gdf that refuses to copy), and a badge still reading "available
        # after save" over an enabled build button would be a lie the error
        # toast does not explain.
        _show_badge_for_state()
        # Export GIF follows the verdict this save wrote: on for Confirmed,
        # off again for a later Need revision / False Positive (spec D9).
        _refresh_gif_gate()
        # Poke the alerts_gdf reactive so dependents refresh. The save mutated
        # the gdf in place (.at[id, col]) which does NOT change the reactive's
        # object identity, so Overview (whose refresh effect keys on
        # app_state.alerts_gdf) would otherwise show stale review counts/markers
        # until the next reassign. Rebinding to a copy forces a new identity.
        gdf = app_state.alerts_gdf.value
        if gdf is not None:
            app_state.alerts_gdf.value = gdf.copy()

    # ------------------------------------------------------------------
    # Post-download apply (OLD download effect, success path) — point the two
    # file tiles at the files + reveal them with the worker's computed paths. Runs
    # at the END of the download worker's tracked block (a failure here still
    # fails the task). The OLD error branch becomes tracked_job's error.
    # ------------------------------------------------------------------
    def _apply_download(result, rev):
        """Offer this build's files. Returns whether they were offered.

        ``rev`` is the revision the build started on: an input that changed
        meanwhile (a later save of this alert, another imagery pick, another
        template) has already invalidated what this build was for, and
        revealing it now would restore a readiness that is stale -- links to
        files built from a review the reviewer has since replaced. The caller
        says so, like the stale-alert case.
        """
        if rev != built["rev"]:
            return False
        # The zip/report live in the set folder, so the tiles link paths
        # relative to the served root: ``set_file`` -> ``local_files.home_relative``
        # -> ``custom_sw.DownloadBtn.set_url`` prefixes the download base. A path
        # outside the served root raises here, inside the tracked block: a
        # FAILED task.
        ns["files_dwn_btn"].set_file(result["zip_path"])
        ns["report_dwn_btn"].set_file(result["report_path"])
        # A Confirmed alert's zip build made its GIF too (spec D9).
        if result.get("gif_path"):
            _offer_gif(result["gif_path"])
        ns["toolbar_downloads"].show()
        # What the badge counts: the files this build actually produced, read
        # off the result rather than assumed to be two.
        built["files"] = len(
            [p for p in (result.get("zip_path"), result.get("report_path")) if p]
        )
        _show_badge_for_state()
        return True

    def _offer_gif(path):
        """Point the GIF link at ``path`` and reveal its row (spec D9)."""
        ns["gif_dwn_btn"].set_file(str(path))
        built["gif"] = 1
        _show_badge_for_state()

    def _apply_gif(path, rev):
        """Offer this GIF, unless what it was made from has changed since
        (``rev``: the ``gif_rev`` at its click)."""
        if rev != built["gif_rev"]:
            return False
        _offer_gif(path)
        return True

    # ------------------------------------------------------------------
    # Save: the shared runner (``make_save_runner``), with this section's own
    # post-save apply step. The footer's "Save" and "Save & next" drive the
    # SAME runner, so there is one save flow for both buttons.
    # ------------------------------------------------------------------
    run_save = make_save_runner(
        widgets, app_state, analysis, app_tile_model, apply_save=_apply_save,
        on_refused=on_refused,
    )

    # ------------------------------------------------------------------
    # Download handler: button-triggered daemon thread (heavy report generation /
    # IO). On success the worker applies the OLD download effect with the returned
    # zip/report paths; on failure tracked_job publishes the error as a toast and
    # logs its traceback.
    # ------------------------------------------------------------------
    def _on_download(clicked, *_):
        # ``clicked`` is the build tile or *Rebuild* (both run this): it spins,
        # and both are held so the build cannot be started twice.
        clicked.loading = True
        _set_build_enabled(False)
        alert = capture_alert(analysis, widgets, app_state)  # as in _on_save
        token = alert.token  # the alert this build is for
        rev = built["rev"]  # ...and the inputs it is built from
        # A true alert's zip carries its GIF (spec D9), captured with the rest
        # of the alert at the click -- by the button's rule: Confirmed, on the
        # saved pair. A Confirmed alert whose pair on screen is not the saved
        # one gets no GIF, and is told so after the build, like nothing to
        # animate.
        gif_job, gif_skipped = None, False
        # A capture that fails for any other reason costs the GIF too, never
        # the build: escaping here would leave the build button spinning with
        # no worker started.
        if not alert_files.gif_allowed(alert.alerts, alert.alert_id, alert.before_info, alert.after_info):
            gif_skipped = alert_files.saved_status(alert.alerts, alert.alert_id) == CONFIRMED
        else:
            try:
                gif_job = gif_mod.capture_gif(app_state, widgets, analysis, alert)
            except gif_mod.GifRefused:
                gif_skipped = True
            except Exception:  # noqa: BLE001 - logged; the zip is built without the GIF
                logger.exception("Analysis: the GIF for the zip could not be captured")
                gif_skipped = True

        result = {}  # the built paths, for the GIF warning after the task

        # The report/zip build is one opaque long phase, but the composite
        # download inside it drives the progress ring (``task=`` below).
        def _body(task, notifier):
            task.step(t("analysis_tile.export_labels.download_step"))
            # Writer tracking: the report/zip generation writes into the set's
            # folder — register the open project for the duration.
            with writing(writer_name(app_state)):
                built_paths = _download_data(
                    widgets, analysis, app_state, aux_model, app_tile_model,
                    task=task, alert=alert, gif=gif_job,
                )
            if built_paths is None:
                # Refused: nothing was written and no download button will be
                # revealed, so completing the task would show a green pill for
                # a no-op the user cannot explain. (The three causes are
                # re-derived here rather than returned, so ``_download_data``
                # keeps its "paths or nothing" contract.)
                if not app_tile_model.recipe_folder_path:
                    return t("analysis_tile.export_labels.download_no_recipe")
                if not alert_files.alert_selected(alert):
                    return t("analysis_tile.export_labels.download_no_alert")
                return t("analysis_tile.export_labels.download_no_images")
            result.update(built_paths)
            # Inside the tracked block: ``local_files.home_relative`` raises
            # ValueError for a path outside $HOME, and that must be a FAILED
            # task + one error toast, not a green pill with download buttons
            # that never show.
            #
            # And only while the alert built for is still on screen. The build
            # is slow (an imagery export plus the Word report), so the reviewer
            # can navigate while it runs, and this apply is itself what REVEALS
            # the toolbar: a stale one would hand the reviewer the previous
            # alert's GeoPackage and report under a badge counting them as the
            # current alert's.
            offered = {"ok": False}
            analysis.apply_if_current(token, lambda: offered.update(ok=_apply_download(built_paths, rev)))
            if not offered["ok"]:
                # Said, not swallowed: the files exist (they were written into
                # the set's folder) but nothing on screen offers them, and the
                # reviewer waited for them -- because they moved on, or because
                # what this build was for has changed since.
                return t("analysis_tile.export_labels.download_stale")
            return None

        def _built(notifier):
            notifier.success(t("analysis_tile.export_labels.download_done"))
            if gif_skipped or result.get("gif_warning"):
                notifier.warning(t("analysis_tile.export_labels.gif_zip_skipped"))

        def _release():
            clicked.loading = False
            # The spinner always stops; the button only comes back for the
            # alert this build was for. An unconditional re-enable would undo
            # ``_forget_previous_alert``'s disable and leave a clickable build
            # button under "available after save". Through
            # ``apply_if_current``: a bare ``is_current`` check followed by the
            # write is two statements outside ``_alert_lock``, and a
            # ``reset_alert`` landing between them re-enables the button anyway.
            analysis.apply_if_current(token, lambda: _set_build_enabled(True))

        run_button_job(
            t("analysis_tile.export_labels.download_task_title"), _body,
            release=_release, on_success=_built,
            refused_log="Analysis: download refused", logger=logger,
            spawn=spawn_in_context, notifier=current_notifier,
        )

    # ------------------------------------------------------------------
    # Export GIF (spec D9): the job is captured at the click (UI thread), the
    # frames render on a worker under ``tracked_job``, and the link is offered
    # only while the alert and the inputs it was made from are still current.
    # ------------------------------------------------------------------
    def _on_gif(*_):
        # The tile is also the GIF's download link: a click on it then is the
        # browser's download, not a request for another GIF.
        if gif_running["on"] or ns["gif_dwn_btn"].path is not None or ns["gif_dwn_btn"].disabled:
            return
        alert = capture_alert(analysis, widgets, app_state)
        token, rev = alert.token, built["gif_rev"]
        folder = app_tile_model.recipe_folder_path
        # ``crashed``: a capture that failed for any other reason. Raised again
        # inside the worker's ``tracked_job``, so the click is a FAILED task
        # with one error toast and its traceback in the log, and the button
        # is released -- never a click that silently does nothing.
        crashed = None
        try:
            if not folder:
                raise gif_mod.GifRefused(key="analysis_tile.export_labels.download_no_recipe")
            job, refused = gif_mod.capture_gif(app_state, widgets, analysis, alert), None
        except gif_mod.GifRefused as exc:
            job, refused = None, exc
        except Exception as exc:  # noqa: BLE001 - reported by the worker's tracked_job
            job, refused, crashed = None, None, exc
        dest = None if job is None else Path(
            alert_gif_name(str(folder), alert.alert_id, alert.before_info, alert.after_info))
        gif_running["on"] = True
        ns["gif_dwn_btn"].loading = True
        ns["gif_dwn_btn"].disabled = True

        def _body(task, notifier):
            if crashed is not None:
                raise crashed
            if refused is not None:
                return error_text(refused)
            task.step(t("analysis_tile.export_labels.gif_step", count=len(job.frames)))
            # Writer tracking: the GIF is written into the set's folder.
            with writing(writer_name(app_state)):
                try:
                    path = gif_mod.build_gif(job, dest)
                except gif_mod.GifRefused as exc:
                    return error_text(exc)
            # Only while the alert it was made for is on screen, and nothing it
            # was made from has changed since (``rev``).
            offered = {"ok": False}
            analysis.apply_if_current(token, lambda: offered.update(ok=_apply_gif(path, rev)))
            return None if offered["ok"] else t("analysis_tile.export_labels.gif_stale")

        def _release():
            ns["gif_dwn_btn"].loading = False
            gif_running["on"] = False
            # The gate decides, for whichever alert is on screen now: it reads
            # that alert's row (its stored status and saved pair) and the picks
            # on screen, so the button comes back for a true alert still on its
            # saved pair, stays off for one whose row is not Confirmed or whose
            # pair has changed, and is not left held for one saved Confirmed
            # while this GIF rendered (the gate skipped it then). Under the
            # alert lock (``token=None``: any alert), so an alert change cannot
            # land between the gate's read and its write.
            analysis.apply_if_current(None, _refresh_gif_gate)

        run_button_job(
            t("analysis_tile.export_labels.gif_task_title"), _body,
            release=_release,
            on_success=lambda notifier: notifier.success(t("analysis_tile.export_labels.gif_done")),
            refused_log="Analysis: GIF not offered", logger=logger,
            spawn=spawn_in_context, notifier=current_notifier,
        )

    # ------------------------------------------------------------------
    # Wire click handlers once (the widgets are built once per build). The buttons
    # start their workers; loading state is toggled inside the handlers.
    # ------------------------------------------------------------------
    def _register_handlers():
        ns["download_alert_data_btn"].on_event("click", _on_download)
        ns["rebuild_btn"].on_event("click", _on_download)
        ns["gif_dwn_btn"].on_event("click", _on_gif)

    _register_handlers()

    # The section's words and the badge's opening state, once everything they
    # read exists: a build is not possible until a save of this alert has
    # landed. ``add`` runs the hook now (in place of a direct badge call) and
    # again on every language switch.
    labels.add(_label)

    # ------------------------------------------------------------------
    # Layout: the section BODY — the build tile's row and the (hidden) file
    # tiles. No title and no Save: the collapsible section owns the first and
    # the footer the second. The report template is the Questionnaire step's.
    # The OLD inline error alert is gone: failures are error toasts.
    # ------------------------------------------------------------------
    return SimpleNamespace(
        body=v.Html(tag="div", children=[ns["toolbar_build"], ns["toolbar_downloads"],
                                         ns["gif_dwn_btn"], ns["toolbar_rebuild"]]),
        run_save=run_save,
        tiles=[ns["files_dwn_btn"], ns["report_dwn_btn"], ns["gif_dwn_btn"]],
    )
