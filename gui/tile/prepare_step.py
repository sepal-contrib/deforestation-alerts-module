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
nor points it at another project's job. ``_StopEvents`` also records each
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
rows a stopped job still holds no longer read Queued (``_StopEvents.stopped``):
they will not run.

Download again (a row's, or the footer menu's Download all again) and Delete
files ask first (``refresh_confirm``, ``delete_confirm``). Delete files drops
this period's raw and harmonized records of the source and deletes their
files; the source and the alert sets built from them stay.
"""

import asyncio
import logging
import threading
from typing import Optional

import solara
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine.coverage import UNCHECKED
from deforestation_alerts.engine.fingerprint import (
    AOI_KEY, Status, aoi_raster_inputs, harm_key, is_file_source, period_masks, raw_key, status,
)
from deforestation_alerts.engine.plan import plan_masks, plan_prepare
from deforestation_alerts.engine.why import source_reason
from deforestation_alerts.project import cleanup, paths
from deforestation_alerts.project.models import sets_using_source
from deforestation_alerts.project.review_copy import read_only
from gui.i18n import size_text, t, tn
from gui.scripts import aoi_io, source_layers
from gui.scripts.coverage_ui import CheckAgainButton, result_note, row_notes
from gui.scripts.error_text import error_text
from gui.scripts.inflight import InflightKeys
from gui.scripts.prepare_jobs import PrepareContext, progress_key, run_rows
from gui.scripts.prepare_view import row_status_label, source_details, source_label, source_state, source_subtitle
from gui.scripts.setup_guard import refuse_setup, update_setup
from gui.scripts.solara_threads import spawn_in_context
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

_DOWNLOADS = (Status.MISSING, Status.STALE)  # the runner downloads these (and anything forced)

SOURCE_ACTIONS = 4  # map, details, download again, delete


def source_columns() -> list:
    """Source | Status (spec "The sources table"): the size on disk is in the subtitle."""
    return [{"label": t("steps.prepare.col_source")},
            {"label": t("steps.prepare.col_status"), "width": f"{STATUS_PX}px"}]


def _inflight_key(project: str, period_id: str, source: str) -> str:
    return f"prep:{project}:{period_id}:{source}"


def mask_inflight_key(project: str, name: str) -> str:
    """Project-wide, not per period: a mask has one aligned file per project."""
    return f"prep:{project}:@mask:{name}"


def mine_keys(running, stops, project: str, period_id: str) -> set:
    """The claimed keys of the jobs started in this project and period, masks
    included: one Run all / Stop scope (spec §7.3). A mask job another period
    started is not in it: its Stop could not stop it."""
    return set(running) & stops.keys((project, period_id))


def mine_rows(p, period, mine) -> set:
    """The ``prepare_jobs.progress_key`` of every row (source or mask) whose
    in-flight key is in ``mine``: the rows this scope's jobs hold."""
    sources = {progress_key(p.name, period.id, "source", s) for s in p.sources
               if _inflight_key(p.name, period.id, s) in mine}
    masks = {progress_key(p.name, period.id, "mask", m) for m in p.masks if mask_inflight_key(p.name, m) in mine}
    return sources | masks


def scope_stages(stages: dict, project: str, period_id: str) -> dict:
    """The row stages of this project and period (a mask's key is project-wide)."""
    return {k: v for k, v in stages.items() if k[0] == project and k[1] in (period_id, None)}


def redownload_items(p, rows) -> list:
    """Download all again's rows (spec "Footer"): every source that is not
    blocked and has something to fetch (not a user file)."""
    return [("source", r.source) for r in rows if not r.blocked and not is_file_source(p.sources[r.source])]


def run_all_items(p, pdir, period) -> list:
    """Run all's rows (spec §7.3): the masks on in the period that need a run,
    then the runnable sources."""
    masks = [("mask", m.name) for m in plan_masks(p, pdir, period) if m.needs_run]
    sources = [("source", r.source) for r in plan_prepare(p, pdir, period) if r.needs_run]
    return masks + sources


def items_for_source(p, pdir, period, source) -> list:
    """A source row's Run: the period's stale masks first (its harmonize reads
    them all), then the source."""
    return [("mask", m.name) for m in plan_masks(p, pdir, period) if m.needs_run] + [("source", source)]


def aoi_key(project: str) -> str:
    """The in-flight key of the project's AOI raster (``masks/aoi.tif``),
    shared by every period: one AOI-raster build at a time."""
    return f"prep:{project}:@aoi"


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


def build_rows(p, pdir, period, rows, *, mine, errors, on, building, run, toggle, on_info, on_refresh, on_delete,
               stages=None, info_open=None, locked=False, progress=None, writing=False, masks_pend=False,
               stopped=frozenset()) -> list:
    """The ``ProductTable`` rows of the sources table (spec "The sources
    table", option B); pure.

    ``mine``: this project's and period's in-flight keys; ``errors``: the row
    errors by source; ``on``/``building``: the map keys shown and being built;
    ``run``/``toggle``/``on_info``/``on_refresh``/``on_delete``: the row's
    pill and actions, called with the source. ``stages``: the running job's
    stage (``download`` / ``harmonize``) by ``prepare_jobs.progress_key``: a
    row in flight without one waits for its turn (queued). ``info_open``: the
    source whose Details dialog is open. ``locked``: a review copy (spec
    §6.3): no pill, and nothing that writes. ``progress``: the downloads'
    percents, by ``progress_key``. ``writing``: a job writes into the
    project (``is_writing``): Delete files waits, saying why. ``masks_pend``:
    a mask on in the period needs a run: Download again waits for it, as
    Download all again does (a forced row runs no mask first). ``stopped``:
    the in-flight keys of the jobs Stop reached: their rows waiting for their
    turn will not run, so they read their state again, not Queued (the pill
    disabled until the job gives them back).

    One Status cell holds the row's state or the verb of its next run
    (``prepare_view.source_state``), and the name's subtitle says what the
    row holds or why it is out of date; only the warning and error notes
    stay as lines under the row. A blocked row (``PrepareRow.blocked``)
    cannot be run, shown or downloaded again; a row in flight cannot be
    started again (the claim would refuse it silently).

    A source already downloaded for this window offers no Run again (user
    decision 2026-10-15): it stays only while its harmonized file needs
    building, and comes back when the raw file goes out of date, as a new
    period or a change to the period's dates makes it. Download again stays: a
    live catalogue changes under the same window (spec §5.3).
    """
    out = []
    for r in rows:
        busy = _inflight_key(p.name, period.id, r.source) in mine
        pkey = progress_key(p.name, period.id, "source", r.source)
        stage = (stages or {}).get(pkey) if busy else None
        pct = (progress or {}).get(pkey) if busy else None
        queued = busy and stage is None and _inflight_key(p.name, period.id, r.source) not in stopped
        file_source = is_file_source(p.sources[r.source])
        raw_art, harm_art = _art(p, raw_key(period.id, r.source)), _art(p, harm_key(period.id, r.source))
        if r.file_missing:
            notes = [{"text": t("steps.prepare.file_missing", path=r.file_missing), "tone": "error"}]
        elif r.blocked:
            notes = []  # its coverage sentence is its subtitle
        else:
            notes = [n for n in row_notes(r.coverage, r.notes) if n["tone"] in ("warning", "error")]
        result = result_note(r.stats) if not r.blocked else None
        if result is not None and result["tone"] == "warning":  # every pixel masked out
            notes.append(result)
        reason = source_reason(p, period, r.source) if not (r.ready or r.blocked) else None
        subtitle, tone = source_subtitle(r, file_source=file_source, raw_art=raw_art, harm_art=harm_art,
                                         reason=reason, stage=stage, queued=queued, pct=pct)
        title = {"type": "title", "value": source_label(p.sources[r.source]), "subtitle": subtitle}
        if tone:
            title["subtitle_tone"] = tone
        state = source_state(r, file_source=file_source, stage=stage, queued=queued, pct=pct,
                             failed=r.source in errors, locked=locked, on_run=(lambda s=r.source: run(s)))
        if busy and state["type"] == "verb":  # a stopped job still holds the row
            state["disabled"] = True
        key = f"src:{r.source}"
        out.append({
            "key": r.source,
            "cells": [title, state],
            "notes": notes,
            "error": errors.get(r.source),
            "actions": [
                # ``ready`` keeps files built before a failed check (Integrate
                # uses them), but a blocked row is not shown (ruling T19 C)
                # (a layer already on can always be switched off)
                {"kind": "map_toggle", "is_on": key in on, "loading": key in building,
                 "disabled": (not r.ready or r.blocked) and key not in on, "on_click": (lambda s=r.source: toggle(s))},
                {"kind": "info", "is_on": info_open == r.source, "on_click": (lambda s=r.source: on_info(s))},
                {"kind": "refresh", "disabled": busy or locked or r.blocked or file_source or masks_pend,
                 "title": t("steps.prepare.action_nothing_to_fetch" if file_source
                            else "steps.prepare.redownload_all_masks" if masks_pend
                            else "steps.prepare.action_download_again"),
                 "on_click": (lambda s=r.source: on_refresh(s))},
                # the confirm re-checks: a job can start while it is open
                {"kind": "delete", "disabled": busy or writing or locked or (raw_art is None and harm_art is None),
                 "title": t("steps.prepare.delete_files_busy" if (busy or writing) and not locked
                            else "steps.prepare.action_delete_files"),
                 "on_click": (lambda s=r.source: on_delete(s))},
            ],
        })
    return out


class _StopEvents:
    """The cancel Events of the running jobs, and each job's in-flight keys,
    by (project, period id).

    Clicks add and stop, and workers discard, so every access takes the lock.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._events: dict[tuple, dict] = {}  # scope -> {Event: frozenset of its keys}

    def add(self, scope: tuple, keys=()) -> threading.Event:
        event = threading.Event()
        with self._lock:
            self._events.setdefault(scope, {})[event] = frozenset(keys)
        return event

    def keys(self, scope: tuple) -> set:
        """The in-flight keys of the jobs started in ``scope``."""
        with self._lock:
            return set().union(*self._events.get(scope, {}).values())

    def discard(self, scope: tuple, event: threading.Event) -> None:
        with self._lock:
            events = self._events.get(scope)
            if events is not None:
                events.pop(event, None)
                if not events:
                    del self._events[scope]

    def stop(self, scope: tuple) -> None:
        with self._lock:
            events = list(self._events.get(scope, ()))
        for event in events:
            event.set()

    def stopped(self, scope: Optional[tuple] = None) -> set:
        """The in-flight keys of the jobs Stop reached, in ``scope`` (None: every scope)."""
        with self._lock:
            scopes = [self._events.get(scope, {})] if scope is not None else list(self._events.values())
            return {key for events in scopes for event, keys in events.items() if event.is_set() for key in keys}


class _ScopedErrors:
    """One job's view of the step's row errors (``{scope: {row: message}}``):
    it reads and writes only its own scope's errors (the project and period
    it ran for), so an error recorded after the user moved on never lands on
    the same row of the period now showing."""

    def __init__(self, store, scope):
        self._store, self._scope = store, scope

    @property
    def value(self) -> dict:
        return self._store.value.get(self._scope, {})

    def set(self, errors: dict) -> None:
        self._store.set({**self._store.value, self._scope: errors})


@solara.component
def PrepareStep(app_state, map_, gee_interface, layers):
    notifications = use_notifications()
    p = app_state.project.value
    # Session-wide, like the step: never claimed or released from a render or
    # an effect (see gui/scripts/map_layers.py), only from clicks and workers.
    inflight = solara.use_memo(InflightKeys, [])
    running = inflight.value
    on = layers.on.value
    building = layers.inflight.value
    stops = solara.use_memo(_StopEvents, [])  # session-wide, like ``inflight``
    row_errors = solara.use_reactive({})  # {scope: {row: message}}, written through _ScopedErrors
    errors_lock = solara.use_memo(threading.Lock, [])  # the step's jobs set row errors, percents and stages under it
    # {prepare_jobs.progress_key: percent} of the downloads running; keyed by
    # project and period, so a row of another scope never reads it
    row_progress = solara.use_reactive({})
    info_source, set_info_source = solara.use_state(None)  # the source whose Details dialog is open
    pending_refresh, set_pending_refresh = solara.use_state(None)  # a source to download again, "*" for all
    pending_delete, set_pending_delete = solara.use_state(None)  # the source whose files to delete
    row_stage = solara.use_reactive({})  # {progress_key: "download"|"harmonize"|"align"} of the rows running
    batch = solara.use_reactive({})  # {(project, period id): (total, done)} of the scope's jobs, for the footer
    # {(project, period id): Stop clicks} of the scopes Stop reached, until their
    # last job ends: a count, so a second Stop (after a row started since the
    # first) is a new value and re-renders
    stopping = solara.use_reactive({})
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
        solara.Info(t("workflow.lock_prepare"))
        return
    pdir = paths.project_dir(p.name)
    rows = plan_prepare(p, pdir, period) if p.grid is not None else []
    by_source = {r.source: r for r in rows}

    def _aoi_raster_current():
        return status(p, pdir, AOI_KEY, aoi_raster_inputs(p)) is Status.CURRENT

    def _is_file(kind, name):
        return p.masks[name].origin == "file" if kind == "mask" else is_file_source(p.sources[name])

    def _key(kind, name):
        return mask_inflight_key(p.name, name) if kind == "mask" else _inflight_key(p.name, period.id, name)

    def _start(items, force=False):
        """Start one tracked job for ``items`` (``("mask", name)`` /
        ``("source", name)``, in run order)."""
        if refuse_setup(app_state, notifications):  # a review copy downloads nothing (spec §6.3)
            return
        by_mask = {m.name: m for m in plan_masks(p, pdir, period)}
        file_items = [it for it in items if _is_file(*it)]
        needs_aoi_raster = bool(file_items) and not _aoi_raster_current()
        if needs_aoi_raster and p.aoi.geometry_file is None:
            # The GeoTIFF rows (sources and masks) wait for the AOI sidecar;
            # the others still run (spec §12: one row must not stop the others).
            notifications.info(t("steps.prepare.aoi_geometry_missing" if app_state.sidecar_pending.value
                                 else "steps.prepare.aoi_geometry_failed"))
            items = [it for it in items if it not in file_items]
            needs_aoi_raster = False
        if not items:
            return
        keys = [_key(*it) for it in items]
        if needs_aoi_raster:
            keys.append(aoi_key(p.name))  # one AOI-raster build at a time
        stop_scope = (p.name, period.id)
        job = {}
        # The job joins this period's Stop scope only once its claim succeeds,
        # and before the claim is published: the claim's re-render already
        # counts these keys as this project's and period's (``mine_keys``),
        # and a refused claim never lists another job's keys as ours.
        if not inflight.claim(*keys, on_claim=lambda: job.update(cancel=stops.add(stop_scope, keys))):
            # A key is held by another job: a mask another period's job is
            # aligning, the AOI raster being built, a row still running.
            notifications.info(t("steps.prepare.claim_busy"))
            return
        cancel = job["cancel"]

        def _give_back():
            # Under the step's lock, so a Stop or a start racing it sees the
            # scope's last job either before or after it is gone.
            with errors_lock:
                stops.discard(stop_scope, cancel)  # before the release, so its re-render reads both gone
                if not stops.keys(stop_scope):  # the scope's last job: its batch and its Stop are over
                    batch.set({k: v for k, v in batch.value.items() if k != stop_scope})
                    if stop_scope in stopping.value:
                        stopping.set({k: n for k, n in stopping.value.items() if k != stop_scope})
            inflight.release(*keys)

        # Drift note: only rows that download from Earth Engine need it (the
        # metadata and the AOI); a grid change re-harmonizes (re-aligns) from
        # the raw file on disk, and a GeoTIFF row is read locally from the
        # user's file.
        raw = {it: (by_mask[it[1]].raw if it[0] == "mask" else by_source[it[1]].raw) for it in items}
        downloads = [it for it in items if not _is_file(*it) and (force or raw[it] in _DOWNLOADS)]
        meta = app_state.catalogue_meta.value
        if (any(kind == "source" and getattr(p.sources[name], "catalogue_key", None) == "GLAD-L"
                for kind, name in downloads) and "GLAD-L" not in meta):
            _give_back()
            notifications.info(t("steps.alerts.meta_loading"))
            return
        aoi = None  # local rows (a grid change, GeoTIFF sources and masks) need no Earth Engine
        if downloads:
            try:
                aoi = aoi_io.ee_aoi(app_state.aoi_result.value, pdir)  # builds ee objects, no request
            except Exception as exc:  # noqa: BLE001 - ValueError (no geometry), or a bad sidecar/ee object
                # Any failure here must give the claimed keys back, or the rows
                # read "running" for the rest of the session.
                _give_back()
                logger.exception("could not build the Earth Engine AOI")
                notifications.error(error_text(exc))
                return
        ctx = PrepareContext(app_state=app_state, layers=layers, map_=map_, notifier=notifications,
                             name=p.name, pdir=pdir, period=period, meta=meta, aoi=aoi, cancel=cancel,
                             row_errors=_ScopedErrors(row_errors, scope), errors_lock=errors_lock,
                             row_progress=row_progress, row_stage=row_stage, downloads=frozenset(downloads),
                             on_row_end=lambda: _count(stop_scope, done=1))

        def _job():
            try:
                run_rows(ctx, items, force=force)
            except asyncio.CancelledError:
                pass  # Stop: the task reads "cancelled"; not an error
            finally:
                _give_back()

        _count(stop_scope, total=len(items))
        spawn_in_context(_job)

    def _count(stop_scope, total=0, done=0):
        """Add to a scope's batch counts (a start's rows, a row's end); a
        worker calls it too, so under the step's lock."""
        with errors_lock:
            was_total, was_done = batch.value.get(stop_scope, (0, 0))
            batch.set({**batch.value, stop_scope: (was_total + total, was_done + done)})

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

    def _stop():
        """Stop after the row in progress; the footer says so until the scope's last job ends."""
        stop_scope = (p.name, period.id)
        stops.stop(stop_scope)
        with errors_lock:  # ``_give_back`` drops the mark under it: never one left behind
            if stops.keys(stop_scope):
                stopping.set({**stopping.value, stop_scope: stopping.value.get(stop_scope, 0) + 1})
    pending = run_all_items(p, pdir, period) if p.grid is not None else []
    stages = scope_stages(row_stage.value, p.name, period.id)
    counts = footer_counts(mine_rows(p, period, mine), stages, batch.value.get((p.name, period.id)))
    redownload = redownload_items(p, rows)

    def _redownload_all():
        """Force a fresh download and harmonize of every catalogue and Earth
        Engine source that is not blocked (the footer menu's, once its
        confirm is accepted)."""
        _start(redownload, force=True)

    masks_pend = any(kind == "mask" for kind, _ in pending)
    menu = [{"label": tn("steps.prepare.redownload_all", len(redownload)),
             "on_click": lambda: set_pending_refresh("*"),  # asks first
             "disabled": masks_pend or read_only(p),
             "title": t("steps.prepare.redownload_all_masks") if masks_pend else ""}] if redownload else []

    shown = info_source if info_source in by_source else None  # the dialog goes with its row

    def _toggle_info(source):
        set_info_source(None if source == shown else source)

    details = None
    if shown is not None:
        row = by_source[shown]
        shown_busy = _inflight_key(p.name, period.id, shown) in mine
        shown_key = progress_key(p.name, period.id, "source", shown)
        shown_stage = stages.get(shown_key) if shown_busy else None
        lead = row_status_label(row, is_file_source(p.sources[shown]), stage=shown_stage,
                                queued=(shown_busy and shown_stage is None
                                        and _inflight_key(p.name, period.id, shown) not in stopped),
                                pct=row_progress.value.get(shown_key) if shown_busy else None)
        details = source_details(p, period, shown, row, source_reason(p, period, shown), lead)

    # A confirm goes with its row, Download all again's with its menu item.
    refreshing = pending_refresh if pending_refresh in by_source or (pending_refresh == "*" and redownload) else None
    deleting = pending_delete if pending_delete in by_source else None

    def _confirm_refresh():
        set_pending_refresh(None)
        if refreshing == "*":
            _redownload_all()
        else:
            _start([("source", refreshing)], force=True)  # no masks first: only the download is asked for

    def _delete_files():
        """This period's raw and harmonized files of the source and their
        records. The source stays, and the alert sets built from them are not
        touched: they read out of date (``set_status``)."""
        name = deleting
        set_pending_delete(None)
        # spec §5.1: like the other deletes, wait for the project's writers,
        # and for a job that claimed the row since the dialog opened
        if is_writing(p.name) or _inflight_key(p.name, period.id, name) in inflight:
            notifications.warning(t("steps.prepare.delete_files_busy"))
            return
        layers.hide(f"src:{name}")  # its layer reads the harmonized file
        gone = []

        def _apply(live):
            gone.extend(cleanup.drop_artifacts(live, cleanup.source_period_keys(live, period.id, name)))

        if update_setup(app_state, p.name, _apply, notifications):
            cleanup.delete_files(pdir, gone)

    refresh_title, refresh_message = refresh_confirm(p, period, refreshing, redownload) if refreshing else ("", "")
    delete_title, delete_message, delete_note = delete_confirm(p, period, deleting) if deleting else ("", "", None)

    table_rows = build_rows(p, pdir, period, rows, mine=mine, errors=errors, on=on, building=building,
                            run=lambda s: _start(items_for_source(p, pdir, period, s)), toggle=_toggle_map,
                            on_info=_toggle_info, on_refresh=set_pending_refresh, on_delete=set_pending_delete,
                            stages=stages, info_open=shown, locked=read_only(p), progress=row_progress.value,
                            writing=is_writing(p.name), masks_pend=masks_pend, stopped=stopped)

    with solara.Column(style="gap:16px;"):
        StepIntro(t("steps.prepare.description"), t("steps.prepare.info_header"), t("steps.prepare.info_md"))
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
            ConfirmDialog(open=refreshing is not None, on_cancel=lambda: set_pending_refresh(None),
                          on_confirm=_confirm_refresh, title=refresh_title, message=refresh_message,
                          confirm_label=t("steps.prepare.redownload_label"), confirm_color="primary")
            ConfirmDialog(open=deleting is not None, on_cancel=lambda: set_pending_delete(None),
                          on_confirm=_delete_files, title=delete_title, message=delete_message, note=delete_note,
                          confirm_label=t("common.delete"))
            BatchFooter(pending=len(pending), run_label=tn("steps.prepare.run_all", len(pending)),
                        on_run=lambda: _start(pending), on_stop=_stop,
                        # Run stays off while the scope holds keys its rows no longer show (a mask removed)
                        menu=menu, counts=counts, disabled=read_only(p) or bool(mine),
                        # every job of the scope is stopped (a row started since Stop brings Stop back)
                        stopping=(p.name, period.id) in stopping.value and bool(mine) and mine <= stopped)
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
