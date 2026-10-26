"""build_export sub-component (Task 9.4 / B3): save attributes + gpkg/zip + Word report.

Architecture layer: view (raw-widget builder, no ``@solara.component``). Ports the
OLD ``AnalysisTile`` export card (``card06`` +
``label35``/``toolBarSaveExport``/``toolBarDownloads``) as the **body of the
collapsible Export section** (spec §5), which sits in the panel's dock
(``footer``, fixed to its bottom edge): the section owns the title, and the
footer owns the Save, so ``build_export`` builds neither.

* The OLD **Save** button (``save_btn``) ran ``save_attributes_to_gdf`` —
  writing the user's verdict / answers / image-info / GEE-admin attributes
  + the drawn polygon area onto ``alerts_gdf`` and persisting ``alert_db.parquet``.
  What a Save writes, and the store transaction, live in ``review_save``;
  the runner that drives it is ``make_save_runner`` (module level) and the button
  itself in the footer, which drives that runner; the runner's ``then``
  runs only after a save that wrote.
  ``build_export`` returns the runner so there is ONE save for the panel.
* The section's **summary badge** (``set_badge``) says what the section can
  offer without opening it: nothing until a saved review is on screen, then a
  build, then the count of files that build left ready. An alert change takes
  it back to the first state, because the build and the download links
  belong to the alert they were made for.
* The body is **one list** (user, 2026-10-26): the three file rows
  (``file_tile.FileTile``: zip, docx, GIF), disabled until a build gives them
  a file, and the build as the list's last row (``file_tile.BuildTile``):
  *Build files*, then *Rebuild files* once the files exist. It runs the OLD
  ``download_data`` (``alert_files``) — the single-alert gpkg, the
  before/after-image GeoTIFF, the zip, the Word report and, for a true alert
  on its saved pair, the Timelapse GIF (spec D9), which is also in the zip.
  The build waits for a saved review on screen with nothing unsaved over it;
  anything that invalidates the files (a new save, another pick, a template
  change) clears the links and offers the build again.

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
from types import SimpleNamespace

import ipyvuetify as v

from deforestation_alerts.project.review_status import CONFIRMED
from gui.i18n import t, tn
from gui.scripts.error_text import error_text
from gui.scripts.notify_bridge import current_notifier, run_button_job
from gui.scripts.solara_threads import spawn_in_context
from gui.store.project_writers import writer_name, writing
from gui.tile.analysis import alert_files, review_save
from gui.tile.analysis import gif as gif_mod
from gui.tile.analysis.file_tile import BuildTile, FileTile
from gui.tile.analysis.labels import Labels
from gui.tile.analysis.review_save import SaveRefused
from gui.tile.analysis.state import capture_alert

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
    """The review save, as a callable the footer's *Save* drives.

    ``run_save(button, then=None)``: spin ``button`` (optional), capture the
    alert on the UI thread, write the store on a worker, and run ``then`` only
    after a save that actually wrote **and only while the alert that was saved
    is still the one on screen** — a ``then`` of ``go_next`` (the old *Save &
    next*) shows why: a refusal
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
    registers the buttons that share the runner (the footer's *Save*), so
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
            # Said, not left to the state line alone: the reviewer pressed Save
            # and is about to move on, and the one thing on screen would be a
            # save they pressed that did not take everything.
            if any(outpaced):
                reason = t("analysis_tile.export_labels.save_outpaced")
                notifier.warning(reason)
                logger.info("Analysis: %s", reason)
            # Only now — a ``then`` that moves on must not move off an alert
            # whose review was refused. And only for the alert this save was
            # for: were ``then`` ``go_next``, a save that lands after the reviewer navigated
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

    ``SimpleNamespace(body, run_save, tiles, build_tile)``: ``body`` is what
    the collapsible Export section wraps -- ONE list (user, 2026-10-26): the
    three file rows and, as its last row, the build (``build_tile``, a
    ``file_tile.BuildTile``). The section owns the title, and the footer owns
    the Save, so neither is built here. The report template is picked in the
    Questionnaire step; a change of it still invalidates the files built
    here. ``run_save`` is the one save runner, handed to the footer so every
    save runs the same post-save step and lights up the build row.
    ``tiles`` are the three ``FileTile`` (zip, docx and the GIF).

    ``on_refused`` goes to ``make_save_runner``: what the panel does with a
    refused save once its warning is out.

    ``set_badge(text, color=None)`` is the section's badge setter (``None``
    before Task 10 wires one). It takes text that is ALREADY translated, so
    every message key the badge shows is resolved here.

    The alert store, the zip and the report it writes land in the active set's
    folder.

    ``labels`` (a ``Labels``) gets the section's label hook: a language switch
    rewrites the three file rows and the build row.
    """
    labels = labels if labels is not None else Labels()

    # ------------------------------------------------------------------
    # The list (user, 2026-10-26), created once so the imperative
    # .disabled / .set_file() calls stay put. Its states:
    #
    # * NO SAVED REVIEW ON SCREEN -- the three file rows are disabled, and so
    #   is the build row, which says what it waits for: a save of the alert,
    #   or of the changes made over its saved review;
    # * BUILDABLE -- the build row is live, *Build files*;
    # * BUILDING -- its spinner in place of the icon;
    # * READY -- the file rows are download links and the build row says
    #   *Rebuild files*, which runs the same build.
    #
    # The Timelapse is a row like the others: the build makes it for a true
    # alert on its saved pair (spec D9) and nothing else does any more; its
    # detail says why it was not made otherwise. ``_draw_state`` is what puts
    # the list in its state.
    # ------------------------------------------------------------------
    zip_tile = FileTile("zip", t("analysis_tile.export_labels.tile_zip"),
                        t("analysis_tile.export_labels.tile_zip_detail"))
    docx_tile = FileTile("docx", t("analysis_tile.export_labels.tile_docx"),
                         t("analysis_tile.export_labels.tile_docx_detail"))
    gif_tile = FileTile("gif", t("analysis_tile.export_labels.tile_gif"),
                        t("analysis_tile.export_labels.tile_gif_detail"))
    build_tile = BuildTile(disabled=True)

    # What this section can offer for the alert under review: whether a save
    # of it has landed this session and how many files the last build left
    # ready. Reset by every alert change. ``rev``: the revision of the inputs
    # of the build, which a worker compares with the one it started on.
    built = {"saved": False, "files": 0, "rev": 0, "gif": 0}
    # A build of this section is running: the build row holds its spinner,
    # whichever alert is on screen, until the worker releases it.
    building = {"on": False}

    def _row():
        return app_state.alerts_gdf.value, app_state.actual_alert_id.value

    def _has_saved_review():
        """A save of this alert landed, this session or before (its row has a
        status)."""
        return built["saved"] or alert_files.saved_status(*_row()) is not None

    def _review_on_screen(fresh=False):
        """Whether the build may run: the review on screen is a saved one,
        with nothing unsaved over it. A save this session, or a reopened
        alert on the pair its row was saved with (the rule the GIF already
        followed, spec D9). ``fresh``: an alert change is under way and the
        picks are still the previous alert's -- they say nothing about this
        row (two alerts can share an image)."""
        if analysis.dirty.value:
            return False
        if built["saved"]:
            return True
        if fresh:
            return False
        alerts, alert_id = _row()
        return (alert_files.saved_status(alerts, alert_id) is not None
                and alert_files.picks_are_saved(alerts, alert_id, analysis.before_info.value,
                                                analysis.after_info.value))

    def _invalidate_built_files(gif=True):
        """Stop offering what the last build left ready.

        The zip and the report are a snapshot: of the review as the save
        wrote it, of the imagery pair they were exported from and of the
        report template they were rendered with. Once any of those changes the
        files on disk are the previous answer, and the links plus the
        "N files ready" badge would hand the reviewer that answer for the
        state in front of them. Nothing is deleted -- the files stay in the
        set folder; they are simply not this review's any more, and the list
        goes back to offering the build.

        The links are cleared, not only disabled: ``set_file(None)`` points
        them at the sandbox file route's own "#" (nothing of this project's),
        disables them, drops their ``download`` attribute and their size.

        ``rev`` is the revision a build carries from its click
        (``_on_download``): one that started before the change cannot restore
        a readiness that is already stale. The caller redraws --
        ``_forget_previous_alert`` has more to change before it reads.

        The GIF is of the same review and the same pair (spec D9), but not of
        the report template: ``gif=False`` (a template change) leaves its link
        alone.
        """
        zip_tile.set_file(None)
        docx_tile.set_file(None)
        built["files"] = 0
        built["rev"] += 1
        if gif:
            gif_tile.set_file(None)
            built["gif"] = 0

    # ------------------------------------------------------------------
    # The list's state and the section's summary badge: what this section can
    # offer right now. Three badge states, in the order a reviewer meets
    # them -- nothing to build yet, a build is possible, the files are ready.
    # ------------------------------------------------------------------
    def _badge(text, color=None):
        if set_badge is not None:
            set_badge(text, color=color)

    def _draw_gif_detail():
        """The Timelapse row's line while it has no GIF: what the build will
        make, or why it will not (spec D9)."""
        if gif_tile.path is not None:
            gif_tile.set_detail(t("analysis_tile.export_labels.tile_gif_detail"))
            return
        alerts, alert_id = _row()
        status = alert_files.saved_status(alerts, alert_id)
        if alert_files.gif_allowed(alerts, alert_id, analysis.before_info.value, analysis.after_info.value):
            key = "analysis_tile.export_labels.tile_gif_detail"
        elif status is not None and status != CONFIRMED:
            key = "analysis_tile.export_labels.tile_gif_true_only"
        else:
            key = "analysis_tile.export_labels.gif_hint"
        gif_tile.set_detail(t(key))

    def _draw_state(fresh=False):
        can_build = _review_on_screen(fresh=fresh)
        first = not built["files"]
        if building["on"]:
            build_tile.disabled = True
            build_tile.set_face(t("analysis_tile.export_labels.building"),
                                t("analysis_tile.export_labels.building_detail"), "mdi-cogs")
        else:
            if can_build:
                detail = t("analysis_tile.export_labels.build_detail" if first
                           else "analysis_tile.export_labels.rebuild_detail")
            elif analysis.dirty.value and _has_saved_review():
                detail = t("analysis_tile.export_labels.save_changes_first")
            else:
                detail = t("analysis_tile.export_labels.save_first")
            build_tile.disabled = not can_build
            build_tile.set_face(t("analysis_tile.export_labels.build_tile" if first
                                  else "analysis_tile.export_labels.rebuild_tile"),
                                detail, "mdi-cogs" if first else "mdi-refresh")
        if not fresh:
            _draw_gif_detail()
        files = built["files"] + built["gif"]
        # Files first: a link on screen under "available after save" would
        # contradict it.
        if files:
            _badge(tn("analysis_tile.sections.export_files", files), color="primary")
        elif can_build:
            _badge(t("analysis_tile.sections.export_build"))
        else:
            _badge(t("analysis_tile.sections.export_after_save"))

    def _invalidate_and_draw(*_, gif=True):
        """``_invalidate_built_files`` + the redraw, for the subscribers whose
        only job is the invalidation."""
        try:
            _invalidate_built_files(gif=gif)
            _draw_state()
        except Exception:
            logger.exception("Analysis: invalidating the built files failed")

    def _label():
        """The list's words in the current language: the three file rows'
        own titles and details, then the state, which words the build row
        and the badge."""
        # Literal keys (not f-strings), so the catalogue test sees each one.
        for tile, title, detail in (
            (zip_tile, "analysis_tile.export_labels.tile_zip", "analysis_tile.export_labels.tile_zip_detail"),
            (docx_tile, "analysis_tile.export_labels.tile_docx", "analysis_tile.export_labels.tile_docx_detail"),
            (gif_tile, "analysis_tile.export_labels.tile_gif", "analysis_tile.export_labels.tile_gif_detail"),
        ):
            tile.set_title(t(title))
            tile.set_detail(t(detail))
        _draw_state()

    # ------------------------------------------------------------------
    # An alert change takes the list back to its empty state. What it can
    # offer belongs to ONE alert: the links still point at the previous
    # alert's files. Left alone they are a badge that counts files the
    # reviewer cannot get for the alert in front of them.
    #
    # Keyed on ``alert_token`` (bumped by EVERY ``reset_alert``), not on the
    # save stamp: the stamp is cleared to the value it already has when no
    # save landed on the previous alert, and a reactive set to an equal value
    # notifies nobody.
    # ------------------------------------------------------------------
    def _forget_previous_alert(*_):
        _invalidate_built_files()
        built["saved"] = False
        # ``fresh``: at the token bump the picks are still the previous
        # alert's while the row may already be the next one's, and comparing
        # the two could offer a build. ``reset_alert`` clears the picks right
        # after, and each pick that lands redraws.
        _draw_state(fresh=True)

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
    analysis.before_info.subscribe(_invalidate_and_draw)
    analysis.after_info.subscribe(_invalidate_and_draw)

    # Unsaved work over the saved review holds the build (the files would
    # describe a review that is not the one on screen); a save that clears it
    # gives it back.
    analysis.dirty.subscribe(lambda *_: _draw_state())

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
            _invalidate_and_draw(gif=False)

    app_state.project.subscribe(_on_project)

    # ------------------------------------------------------------------
    # Post-save apply (OLD save effect, success path) — offer the build + poke
    # the alerts_gdf reactive. Runs at the END of the save worker's tracked
    # block, only when the save wrote something, so a failure here still fails
    # the task.
    # ------------------------------------------------------------------
    def _apply_save():
        built["saved"] = True
        # The review just written is not the one the last build captured, so
        # its files are the previous answer: the links go, and the list offers
        # the build again (user, 2026-10-26: cleared, not kept).
        _invalidate_built_files()
        # Before the rebind, not at the end: the rebind below can raise (a gdf
        # that refuses to copy), and a badge still reading "available after
        # save" over a live build row would be a lie the error toast does not
        # explain. The row was written in place, so the Timelapse line already
        # reads the verdict this save wrote.
        _draw_state()
        # Poke the alerts_gdf reactive so dependents refresh. The save mutated
        # the gdf in place (.at[id, col]) which does NOT change the reactive's
        # object identity, so Overview (whose refresh effect keys on
        # app_state.alerts_gdf) would otherwise show stale review counts/markers
        # until the next reassign. Rebinding to a copy forces a new identity.
        gdf = app_state.alerts_gdf.value
        if gdf is not None:
            app_state.alerts_gdf.value = gdf.copy()

    # ------------------------------------------------------------------
    # Post-download apply (OLD download effect, success path) — point the file
    # rows at the files with the worker's computed paths. Runs at the END of
    # the download worker's tracked block (a failure here still fails the
    # task). The OLD error branch becomes tracked_job's error.
    # ------------------------------------------------------------------
    def _apply_download(result, rev):
        """Offer this build's files. Returns whether they were offered.

        ``rev`` is the revision the build started on: an input that changed
        meanwhile (a later save of this alert, another imagery pick, another
        template) has already invalidated what this build was for, and
        offering it now would restore a readiness that is stale -- links to
        files built from a review the reviewer has since replaced. The caller
        says so, like the stale-alert case.
        """
        if rev != built["rev"]:
            return False
        # The zip/report live in the set folder, so the rows link paths
        # relative to the served root: ``set_file`` -> ``local_files.home_relative``
        # -> ``custom_sw.DownloadBtn.set_url`` prefixes the download base. A path
        # outside the served root raises here, inside the tracked block: a
        # FAILED task.
        zip_tile.set_file(result["zip_path"])
        docx_tile.set_file(result["report_path"])
        # A Confirmed alert's build made its GIF too (spec D9).
        if result.get("gif_path"):
            gif_tile.set_file(str(result["gif_path"]))
            built["gif"] = 1
        # What the badge counts: the files this build actually produced, read
        # off the result rather than assumed to be two.
        built["files"] = len(
            [p for p in (result.get("zip_path"), result.get("report_path")) if p]
        )
        _draw_state()
        return True

    # ------------------------------------------------------------------
    # Save: the shared runner (``make_save_runner``), with this section's own
    # post-save apply step. The footer's *Save* drives it, so there is one
    # save flow for the panel.
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
    def _on_download(*_):
        # The build row spins (*Build files* and *Rebuild files* are its two
        # faces), and is held so the build cannot be started twice -- held in
        # Python too: a click queued while the row was disabled still lands.
        if building["on"]:
            return
        building["on"] = True
        build_tile.set_busy(True)
        _draw_state()
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
            building["on"] = False
            build_tile.set_busy(False)
            # The spinner always stops; whether the row comes back is the
            # state's call, for whichever alert is on screen now -- an
            # unconditional re-enable would leave a live build row under
            # "available after save" on the alert navigated to. Under the
            # alert lock (``token=None``: any alert), so an alert change
            # cannot land between the state's read and its write.
            analysis.apply_if_current(None, _draw_state)

        run_button_job(
            t("analysis_tile.export_labels.download_task_title"), _body,
            release=_release, on_success=_built,
            refused_log="Analysis: download refused", logger=logger,
            spawn=spawn_in_context, notifier=current_notifier,
        )

    # Wired once (the widgets are built once per session).
    build_tile.on_event("click", _on_download)

    # The list's words and the badge's opening state, once everything they
    # read exists. ``add`` runs the hook now (in place of a direct call) and
    # again on every language switch.
    labels.add(_label)

    # ------------------------------------------------------------------
    # Layout: the section BODY -- the one list. No title and no Save: the
    # collapsible section owns the first and the footer the second. The
    # rows are drawn as one bordered list by the screen's stylesheet
    # (``analysis_tile.SCREEN_CSS``: ``.defo-file-list``). The OLD inline
    # error alert is gone: failures are error toasts.
    # ------------------------------------------------------------------
    return SimpleNamespace(
        body=v.Html(tag="div", class_="defo-file-list", children=[zip_tile, docx_tile, gif_tile, build_tile]),
        run_save=run_save,
        tiles=[zip_tile, docx_tile, gif_tile],
        build_tile=build_tile,
    )
