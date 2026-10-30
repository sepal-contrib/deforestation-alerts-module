"""Download & harmonize step (spec §7.3).

Slice 1 simplifications of §7.3 (ruling #72): the table has one row per source;
one in-flight key per row covers both of its artifacts; Run all is one job
that runs its rows one at a time.

Masks (slice 4): Run all first aligns the masks on in the period that need a
run, then runs the sources (``run_all_items``); a single source Run first runs
the period's stale masks (``items_for_source``). A mask has one aligned file
per project, so its in-flight key is project-wide (``mask_inflight_key``): two
periods never align it at once. A GeoTIFF mask, like a GeoTIFF source, needs
the AOI raster, so it waits for the AOI sidecar and claims ``aoi_key``.

A row explains its coverage (``build_rows``, spec §7.3): a source that is not
covered, has no data for the window or has no coverage record reads why under
its row and cannot be run or shown; a partly covered one keeps a warning line
per cause; a harmonized one reads what it found in its subtitle. Run all
skips the blocked rows (``PrepareRow.needs_run``). Only an Earth Engine download needs the
Earth Engine AOI (and only GLAD-L's its catalogue metadata); a GeoTIFF row
that must build the AOI raster first needs the AOI sidecar, and claims the
project's AOI-raster key (``aoi_key``) so two jobs never build it at once.

In-flight keys carry the project name (``prep:{project}:{period}:{source}``):
a job still finishing a row for a project the user left neither shows as
running in, nor blocks, the project now open, while reopening its own project
still finds its rows claimed until it ends.

Stop has the same scope: every job started in the open project and period
gets its own cancel Event, and Stop sets all of them, so a later start (even a
no-op click on a ready row) never takes Stop away from a Run all still going,
nor points it at another project's job. ``StopEvents`` also records each
job's keys: the keys of the jobs started in this project and period, mask keys
included, are the step's own (``mine_keys``); a mask another period's job is
aligning reads running in its row but is not in this period's Stop scope (this
Stop could not stop it). A click whose keys are already claimed (that mask, or
the AOI raster another job is building) says so (``claim_busy``).

The footer under the table (``BatchFooter``) runs every pending row, and
stops this scope's jobs while they run. Its counts come from the claimed rows
of the scope (``mine_rows``) and the stage each running row reports
(``row_stage``, ``scope_stages``): a claimed row with no stage is queued. Each
start adds its rows to the scope's ``batch`` total and each row end one to
its done count; the entry goes with the scope's last job.

Stop only sets the jobs' cancel Events, so it also marks the scope as
``stopping`` (a reactive): until the scope's last job ends, the footer reads
``Stopping after this row…`` while every job of the scope is stopped, and the
rows a stopped job still holds no longer read Queued (``StopEvents.stopped``):
they will not run.

The step is the Harmonize step (spec §2, ``stage="harmonize"``): it only
harmonizes (``mode="harmonize"``); a row whose raw file is not current waits
for its download in the Alerts step, which also holds Download again and
Download all again (``refresh_confirm``, ``redownload_items``, kept here for
it). Delete files asks first (``delete_confirm``): it drops this period's raw
and harmonized records of the source and deletes their files; the source and
the alert sets built from them stay.

The job state (in-flight keys, Stop events, row errors, progress, stages,
batch, stopping) and the start and Stop live in ``gui.scripts.prepare_scope``:
the shell creates one ``PrepareJobs`` per session and passes it in.
"""

import logging

import solara
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine.coverage import UNCHECKED
from deforestation_alerts.engine.fingerprint import (
    AOI_KEY, Status, aoi_raster_inputs, harm_key, is_file_source, period_masks, raw_key, status,
)
from deforestation_alerts.engine.plan import plan_masks, plan_prepare
from deforestation_alerts.engine.why import source_reason
from deforestation_alerts.project import cleanup, paths
from deforestation_alerts.project.models import period_sources, sets_using_source
from deforestation_alerts.project.review_copy import read_only
from gui.i18n import size_text, t, tn
from gui.scripts import source_layers
from gui.scripts.coverage_ui import CheckAgainButton, result_note, row_notes
from gui.scripts.prepare_jobs import progress_key
from gui.scripts.prepare_scope import aoi_key, inflight_key, mask_inflight_key, start_items, stop_scope  # noqa: F401 - aoi_key re-exported for the tests
from gui.scripts.prepare_view import row_status_label, source_details, source_label, source_state, source_subtitle
from gui.scripts.setup_guard import update_setup
from gui.scripts.source_layers import refresh_mask_legends
from gui.store import workflow_steps
from gui.store.project_writers import is_writing
from gui.tile.masks_card import MasksCard
from gui.widget.batch_footer import BatchFooter, footer_counts
from gui.widget.confirm_dialog import ConfirmDialog
from gui.widget.details_dialog import DetailsDialog
from gui.widget.grid_form import GridForm
from gui.widget.help import StepIntro
from gui.widget.product_table import STATUS_PX, ProductTable

logger = logging.getLogger("SEPALUI.deforestation_alerts")

SOURCE_ACTIONS = 3  # the Harmonize step's: map, details, delete (Download again is the Alerts step's)


def source_columns() -> list:
    """Source | Status (spec "The sources table"): the size on disk is in the subtitle."""
    return [{"label": t("steps.prepare.col_source")},
            {"label": t("steps.prepare.col_status"), "width": f"{STATUS_PX}px"}]


def mine_keys(running, stops, project: str, period_id: str) -> set:
    """The claimed keys of the jobs started in this project and period, masks
    included: one Run all / Stop scope (spec §7.3). A mask job another period
    started is not in it: its Stop could not stop it."""
    return set(running) & stops.keys((project, period_id))


def mine_rows(p, period, mine) -> set:
    """The ``prepare_jobs.progress_key`` of every row (source or mask) whose
    in-flight key is in ``mine``: the rows this scope's jobs hold."""
    sources = {progress_key(p.name, period.id, "source", s) for s in period_sources(p, period)
               if inflight_key(p.name, period.id, s) in mine}
    masks = {progress_key(p.name, period.id, "mask", m) for m in p.masks if mask_inflight_key(p.name, m) in mine}
    return sources | masks


def scope_stages(stages: dict, project: str, period_id: str) -> dict:
    """The row stages of this project and period (a mask's key is project-wide)."""
    return {k: v for k, v in stages.items() if k[0] == project and k[1] in (period_id, None)}


def redownload_items(p, rows) -> list:
    """Download all again's rows (spec "Footer"): every source that is not
    blocked and has something to fetch (not a user file)."""
    return [("source", r.source) for r in rows if not r.blocked and not is_file_source(p.sources[r.source])]


def run_all_items(p, pdir, period, stage=None) -> list:
    """Run all's rows (spec §7.3): the masks on in the period that need a run,
    then the runnable sources; ``stage="harmonize"`` (spec §2): only the
    sources whose raw file is current, as a harmonize never downloads."""
    masks = [("mask", m.name) for m in plan_masks(p, pdir, period) if m.needs_run]
    sources = [("source", r.source) for r in plan_prepare(p, pdir, period)
               if r.needs_run and (stage != "harmonize" or r.raw is Status.CURRENT)]
    return masks + sources


def items_for_source(p, pdir, period, source) -> list:
    """A source row's Run: the period's stale masks first (its harmonize reads
    them all), then the source."""
    return [("mask", m.name) for m in plan_masks(p, pdir, period) if m.needs_run] + [("source", source)]


def _art(p, key):
    """The artifact under ``key``; a slice-1 ``no_data`` record reads as none (``status``)."""
    art = p.artifacts.get(key)
    return None if art is None or art.no_data else art


def refresh_confirm(p, period, source, redownload) -> tuple:
    """(title, message) of the Download again confirm of ``source``, or of
    Download all again's (``"*"``, its rows ``redownload``) (spec "Confirms")."""
    if source == "*":
        names = ", ".join(source_label(p.sources[name]) for _, name in redownload)
        return (tn("steps.prepare.redownload_all_title", len(redownload)),
                t("steps.prepare.redownload_all_message", names=names))
    start, end = period.search_window()
    window = t("steps.period.range", start=start.isoformat(), end=end.isoformat())
    raw_art = _art(p, raw_key(period.id, source))
    message = (t("steps.prepare.redownload_message_new", window=window) if raw_art is None
               else t("steps.prepare.redownload_message", window=window, date=raw_art.created_at.date().isoformat()))
    return t("steps.prepare.redownload_title", source=source_label(p.sources[source])), message


def delete_confirm(p, period, source) -> tuple:
    """(title, message, note) of the Delete files confirm (spec "Confirms"):
    the note names this period's alert sets built from the source, which
    will read out of date (user decision 2026-10-23: allowed, nothing
    cascades)."""
    size = size_text(sum(p.artifacts[k].size_bytes for k in cleanup.source_period_keys(p, period.id, source)))
    sets = [s for s in sets_using_source(p, source) if p.alert_sets[s].period_id == period.id]
    # a set is named by its sources: the period is this one
    names = ", ".join(" + ".join(p.alert_sets[s].sources) for s in sets)
    return (t("steps.prepare.delete_files_title", source=source_label(p.sources[source])),
            # a legacy record has no size: no empty "()"
            t("steps.prepare.delete_files_message", size=size) if size
            else t("steps.prepare.delete_files_message_nosize"),
            t("steps.prepare.delete_files_sets", names=names) if sets else None)


def build_rows(p, pdir, period, rows, *, mine, errors, on, building, run, toggle, on_info, on_delete,
               stages=None, info_open=None, locked=False, progress=None, writing=False,
               stopped=frozenset()) -> list:
    """The ``ProductTable`` rows of the Harmonize step's sources table (spec
    §2, option B); pure. A row whose raw file is not current reads
    ``steps.harmonize.download_first`` and its pill is disabled (Download
    again is the Alerts step's: ``alerts_step.alert_rows``).

    ``mine``: this project's and period's in-flight keys; ``errors``: the row
    errors by source; ``on``/``building``: the map keys shown and being built;
    ``run``/``toggle``/``on_info``/``on_delete``: the row's
    pill and actions, called with the source. ``stages``: the running job's
    stage (``download`` / ``harmonize``) by ``prepare_jobs.progress_key``: a
    row in flight without one waits for its turn (queued). ``info_open``: the
    source whose Details dialog is open. ``locked``: a review copy (spec
    §6.3): no pill, and nothing that writes. ``progress``: the downloads'
    percents, by ``progress_key``. ``writing``: a job writes into the
    project (``is_writing``): Delete files waits, saying why. ``stopped``:
    the in-flight keys of the jobs Stop reached: their rows waiting for their
    turn will not run, so they read their state again, not Queued (the pill
    disabled until the job gives them back).

    One Status cell holds the row's state or the verb of its next run
    (``prepare_view.source_state``), and the name's subtitle says what the
    row holds or why it is out of date; only the warning and error notes
    stay as lines under the row. A blocked row (``PrepareRow.blocked``)
    cannot be run, shown or downloaded again; a row in flight cannot be
    started again (the claim would refuse it silently).

    A source already harmonized offers no Run again (user decision
    2026-10-15): it comes back when an input goes out of date.
    """
    out = []
    for r in rows:
        busy = inflight_key(p.name, period.id, r.source) in mine
        pkey = progress_key(p.name, period.id, "source", r.source)
        running = (stages or {}).get(pkey) if busy else None  # the job's stage
        pct = (progress or {}).get(pkey) if busy else None
        queued = busy and running is None and inflight_key(p.name, period.id, r.source) not in stopped
        file_source = is_file_source(p.sources[r.source])
        raw_art, harm_art = _art(p, raw_key(period.id, r.source)), _art(p, harm_key(period.id, r.source))
        if r.file_missing:
            notes = [{"text": t("steps.prepare.file_missing", path=r.file_missing), "tone": "error"}]
        elif r.blocked:
            notes = []  # its coverage sentence is its subtitle
        else:
            notes = [n for n in row_notes(r.coverage, r.notes) if n["tone"] in ("warning", "error")]
            if r.raw is not Status.CURRENT:
                notes.insert(0, {"text": t("steps.harmonize.download_first"), "tone": "info"})
        result = result_note(r.stats) if not r.blocked else None
        if result is not None and result["tone"] == "warning":  # every pixel masked out
            notes.append(result)
        reason = source_reason(p, period, r.source) if not (r.ready or r.blocked) else None
        subtitle, tone = source_subtitle(r, file_source=file_source, raw_art=raw_art, harm_art=harm_art,
                                         reason=reason, stage=running, queued=queued, pct=pct)
        title = {"type": "title", "value": source_label(p.sources[r.source]), "subtitle": subtitle}
        if tone:
            title["subtitle_tone"] = tone
        state = source_state(r, file_source=file_source, stage=running, queued=queued, pct=pct,
                             failed=r.source in errors, locked=locked, on_run=(lambda s=r.source: run(s)),
                             harmonize_only=True)
        if busy and state["type"] == "verb":  # a stopped job still holds the row
            state["disabled"] = True
        key = f"src:{r.source}"
        actions = [
            # ``ready`` keeps files built before a failed check (Integrate
            # uses them), but a blocked row is not shown (ruling T19 C)
            # (a layer already on can always be switched off)
            {"kind": "map_toggle", "is_on": key in on, "loading": key in building,
             "disabled": (not r.ready or r.blocked) and key not in on, "on_click": (lambda s=r.source: toggle(s))},
            {"kind": "info", "is_on": info_open == r.source, "on_click": (lambda s=r.source: on_info(s))},
            # the confirm re-checks: a job can start while it is open
            {"kind": "delete", "disabled": busy or writing or locked or (raw_art is None and harm_art is None),
             "title": t("steps.prepare.delete_files_busy" if (busy or writing) and not locked
                        else "steps.prepare.action_delete_files"),
             "on_click": (lambda s=r.source: on_delete(s))},
        ]
        out.append({"key": r.source, "cells": [title, state], "notes": notes, "error": errors.get(r.source),
                    "actions": actions})
    return out


@solara.component
def PrepareStep(app_state, map_, gee_interface, layers, jobs, stage: str = "harmonize"):
    """``jobs``: the session's ``PrepareJobs``, created once by the shell.
    ``stage``: ``harmonize``, the only one (spec §2): the grid, the masks and
    the harmonize of each source the Alerts step downloaded."""
    notifications = use_notifications()
    p = app_state.project.value
    inflight, stops, row_errors, row_progress = jobs.inflight, jobs.stops, jobs.row_errors, jobs.row_progress
    running = inflight.value
    on = layers.on.value
    building = layers.inflight.value
    info_source, set_info_source = solara.use_state(None)  # the source whose Details dialog is open
    pending_delete, set_pending_delete = solara.use_state(None)  # the source whose files to delete
    # A row's error belongs to the project and period it ran for: a job writes
    # under its own scope, the table reads the showing scope's, and a change
    # of scope forgets them all.
    scope = (app_state.project_loaded_signal.value, p.name if p else None, p.active_period if p else None)
    solara.use_effect(lambda: row_errors.set({}), [scope])
    errors = row_errors.value.get(scope, {})

    period = workflow_steps.active_period(p)
    # Spec §7.3, §9.4: a mask switch (on, off, its mode) and a change of the
    # active period give every mask layer on the map the active period's
    # choice, without a rebuild. Keyed on the period and its choices only, so
    # a worker recording artifacts never runs it. An effect may call the
    # registry: MapLayers publishes outside its lock.
    choices = tuple(sorted((n, c.on, c.mode) for n, c in period.masks.items())) if period is not None else ()

    def _refresh_mask_legends():
        live = app_state.project.value
        if live is not None:
            refresh_mask_legends(layers, live)

    solara.use_effect(_refresh_mask_legends, [p.active_period if p is not None else None, choices])
    if p is None or period is None or p.aoi is None:
        solara.Info(t("workflow.lock_harmonize"))
        return
    pdir = paths.project_dir(p.name)
    rows = plan_prepare(p, pdir, period) if p.grid is not None else []
    by_source = {r.source: r for r in rows}

    def _aoi_raster_current():
        return status(p, pdir, AOI_KEY, aoi_raster_inputs(p)) is Status.CURRENT

    def _start(items, force=False):
        start_items(jobs, app_state=app_state, layers=layers, map_=map_, notifications=notifications,
                    items=items, force=force, mode=stage)

    def _toggle_map(source):
        # The Alerts step's pattern, not ``toggle``: a click behind a
        # superseded build of the key is queued, not dropped (slice-1
        # deferral), and a click on a layer still building is a no-op.
        key = f"src:{source}"
        if layers.is_on(key):
            if key not in layers.inflight.value:
                layers.hide(key)
            return
        art = p.artifacts.get(harm_key(period.id, source))
        if art is None:
            return
        path = paths.resolve(pdir, art.rel_path)
        build = source_layers.harmonized_builder(map_, path, key, source_layers.harmonized_name(source, period))
        layers.show(key, build, content_id=art.content_id, notifier=notifications,
                    legend=source_layers.harmonized_legend(key, source, period))

    mine = mine_keys(running, stops, p.name, period.id)
    stopped = stops.stopped((p.name, period.id))

    pending = run_all_items(p, pdir, period, stage=stage) if p.grid is not None else []
    stages = scope_stages(jobs.row_stage.value, p.name, period.id)
    counts = footer_counts(mine_rows(p, period, mine), stages, jobs.batch.value.get((p.name, period.id)))

    shown = info_source if info_source in by_source else None  # the dialog goes with its row

    def _toggle_info(source):
        set_info_source(None if source == shown else source)

    details = None
    if shown is not None:
        row = by_source[shown]
        shown_busy = inflight_key(p.name, period.id, shown) in mine
        shown_key = progress_key(p.name, period.id, "source", shown)
        shown_stage = stages.get(shown_key) if shown_busy else None
        lead = row_status_label(row, is_file_source(p.sources[shown]), stage=shown_stage,
                                queued=(shown_busy and shown_stage is None
                                        and inflight_key(p.name, period.id, shown) not in stopped),
                                pct=row_progress.value.get(shown_key) if shown_busy else None)
        details = source_details(p, period, shown, row, source_reason(p, period, shown), lead)

    deleting = pending_delete if pending_delete in by_source else None  # the confirm goes with its row

    def _delete_files():
        """This period's raw and harmonized files of the source and their
        records. The source stays, and the alert sets built from them are not
        touched: they read out of date (``set_status``)."""
        name = deleting
        set_pending_delete(None)
        # spec §5.1: like the other deletes, wait for the project's writers,
        # and for a job that claimed the row since the dialog opened
        if is_writing(p.name) or inflight_key(p.name, period.id, name) in inflight:
            notifications.warning(t("steps.prepare.delete_files_busy"))
            return
        layers.hide(f"src:{name}")  # its layer reads the harmonized file
        gone = []

        def _apply(live):
            gone.extend(cleanup.drop_artifacts(live, cleanup.source_period_keys(live, period.id, name)))

        if update_setup(app_state, p.name, _apply, notifications):
            cleanup.delete_files(pdir, gone)

    delete_title, delete_message, delete_note = delete_confirm(p, period, deleting) if deleting else ("", "", None)

    table_rows = build_rows(p, pdir, period, rows, mine=mine, errors=errors, on=on, building=building,
                            run=lambda s: _start(items_for_source(p, pdir, period, s)), toggle=_toggle_map,
                            on_info=_toggle_info, on_delete=set_pending_delete,
                            stages=stages, info_open=shown, locked=read_only(p), progress=row_progress.value,
                            writing=is_writing(p.name), stopped=stopped)

    with solara.Column(style="gap:16px;"):
        StepIntro(t("steps.harmonize.description"), t("steps.harmonize.info_header"), t("steps.harmonize.info_md"))
        GridForm(app_state=app_state)  # remounts its fields per project and AOI (ruling #18)
        if p.grid is not None:
            # between the grid card and the sources table (spec §7.3); a mask row
            # reads running whichever period's job claimed it, so it gets every key
            MasksCard(app_state=app_state, map_=map_, gee_interface=gee_interface, layers=layers, period=period,
                      rows=plan_masks(p, pdir, period), running=running, errors=errors,
                      on_run=lambda n: _start([("mask", n)]), on_rerun=lambda n: _start([("mask", n)], force=True),
                      progress=row_progress.value, stages=stages, stopped=stops.stopped())
            ProductTable(title=t("steps.prepare.list_title"), columns=source_columns(), rows=table_rows,
                         empty_text=t("steps.prepare.empty"))
            DetailsDialog(open=shown is not None, details=details, on_close=lambda: set_info_source(None))
            ConfirmDialog(open=deleting is not None, on_cancel=lambda: set_pending_delete(None),
                          on_confirm=_delete_files, title=delete_title, message=delete_message, note=delete_note,
                          confirm_label=t("common.delete"))
            BatchFooter(pending=len(pending), run_label=tn("steps.harmonize.run_all", len(pending)),
                        on_run=lambda: _start(pending), on_stop=lambda: stop_scope(jobs, p.name, period.id),
                        # Run stays off while the scope holds keys its rows no longer show (a mask removed)
                        menu=[], counts=counts, disabled=read_only(p) or bool(mine),
                        # every job of the scope is stopped (a row started since Stop brings Stop back)
                        stopping=(p.name, period.id) in jobs.stopping.value and bool(mine) and mine <= stopped)
            # Check again after a failed coverage check, or a failed AOI-sidecar
            # fetch that leaves the GeoTIFF rows (sources, masks on in the
            # period) waiting (the loader refetches it on the same retry)
            sidecar_failed = (p.aoi.geometry_file is None and not app_state.sidecar_pending.value
                              and (any(is_file_source(p.sources[r.source]) for r in rows)
                                   or any(p.masks[n].origin == "file" for n, _ in period_masks(p, period)))
                              and not _aoi_raster_current())
            CheckAgainButton(app_state=app_state,
                             show=sidecar_failed or (bool(app_state.coverage_error.value)
                                                     and any(r.coverage == UNCHECKED for r in rows)))
