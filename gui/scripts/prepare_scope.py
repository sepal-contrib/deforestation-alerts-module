"""The prepare jobs' shared state, and their start and Stop (spec §7.3).

The shell creates one ``PrepareJobs`` per session and hands it to every step
that runs or shows prepare rows: a row has one in-flight key
(``inflight_key``) whichever step started it, and one Stop scope per project
and period. Never claimed or released from a render or an effect (see
gui/scripts/map_layers.py), only from clicks and workers.
"""

import asyncio
import logging
import threading
from typing import Optional

import solara

from deforestation_alerts.engine.fingerprint import AOI_KEY, Status, aoi_raster_inputs, is_file_source, status
from deforestation_alerts.engine.plan import plan_masks, plan_prepare
from deforestation_alerts.project import paths
from gui.i18n import t
from gui.scripts import aoi_io
from gui.scripts.error_text import error_text
from gui.scripts.inflight import InflightKeys
from gui.scripts.prepare_jobs import PrepareContext, run_rows
from gui.scripts.setup_guard import refuse_setup
from gui.scripts.solara_threads import spawn_in_context
from gui.store import workflow_steps

logger = logging.getLogger("SEPALUI.deforestation_alerts")

_DOWNLOADS = (Status.MISSING, Status.STALE)  # the runner downloads these (and anything forced)


def inflight_key(project: str, period_id: str, source: str) -> str:
    return f"prep:{project}:{period_id}:{source}"


def mask_inflight_key(project: str, name: str) -> str:
    """Project-wide, not per period: a mask has one aligned file per project."""
    return f"prep:{project}:@mask:{name}"


def aoi_key(project: str) -> str:
    """The in-flight key of the project's AOI raster (``masks/aoi.tif``),
    shared by every period: one AOI-raster build at a time."""
    return f"prep:{project}:@aoi"


class StopEvents:
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
    """One job's view of the row errors (``{scope: {row: message}}``): it
    reads and writes only its own scope's errors (the project and period it
    ran for), so an error recorded after the user moved on never lands on the
    same row of the period now showing."""

    def __init__(self, store, scope):
        self._store, self._scope = store, scope

    @property
    def value(self) -> dict:
        return self._store.value.get(self._scope, {})

    def set(self, errors: dict) -> None:
        self._store.set({**self._store.value, self._scope: errors})


class PrepareJobs:
    """The session's prepare job state, shared by the steps that show its rows."""

    def __init__(self):
        self.inflight = InflightKeys()
        self.stops = StopEvents()
        self.row_errors = solara.reactive({})  # {scope: {row: message}}, written through _ScopedErrors
        # {prepare_jobs.progress_key: percent} of the downloads running; keyed by
        # project and period, so a row of another scope never reads it
        self.row_progress = solara.reactive({})
        self.row_stage = solara.reactive({})  # {progress_key: "download"|"harmonize"|"align"} of the rows running
        self.batch = solara.reactive({})  # {(project, period id): (total, done)} of the scope's jobs, for the footer
        # {(project, period id): Stop clicks} of the scopes Stop reached, until their
        # last job ends: a count, so a second Stop (after a row started since the
        # first) is a new value and re-renders
        self.stopping = solara.reactive({})
        self.errors_lock = threading.Lock()  # the jobs set row errors, percents and stages under it


def _count(jobs: PrepareJobs, stop_scope, total=0, done=0):
    """Add to a scope's batch counts (a start's rows, a row's end); a worker
    calls it too, so under the jobs' lock."""
    with jobs.errors_lock:
        was_total, was_done = jobs.batch.value.get(stop_scope, (0, 0))
        jobs.batch.set({**jobs.batch.value, stop_scope: (was_total + total, was_done + done)})


def start_items(jobs: PrepareJobs, *, app_state, layers, map_, notifications, items: list, force: bool = False,
                mode: str = "download") -> None:
    """Start one tracked job for ``items`` (``("mask", name)`` /
    ``("source", name)``, in run order) in the open project and period."""
    if refuse_setup(app_state, notifications):  # a review copy downloads nothing (spec §6.3)
        return
    p = app_state.project.value
    period = workflow_steps.active_period(p)
    if period is None:  # the project closed between the render and the click
        return
    pdir = paths.project_dir(p.name)
    # a job writes its row errors under the scope it ran for (PrepareStep reads the showing one)
    scope = (app_state.project_loaded_signal.value, p.name, p.active_period)

    def _is_file(kind, name):
        return p.masks[name].origin == "file" if kind == "mask" else is_file_source(p.sources[name])

    def _key(kind, name):
        return mask_inflight_key(p.name, name) if kind == "mask" else inflight_key(p.name, period.id, name)

    by_source = {r.source: r for r in plan_prepare(p, pdir, period)}
    by_mask = {m.name: m for m in plan_masks(p, pdir, period)}
    file_items = [it for it in items if _is_file(*it)]
    needs_aoi_raster = bool(file_items) and status(p, pdir, AOI_KEY, aoi_raster_inputs(p)) is not Status.CURRENT
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
    stops, batch, stopping = jobs.stops, jobs.batch, jobs.stopping
    job = {}
    # The job joins this period's Stop scope only once its claim succeeds,
    # and before the claim is published: the claim's re-render already
    # counts these keys as this project's and period's (``mine_keys``),
    # and a refused claim never lists another job's keys as ours.
    if not jobs.inflight.claim(*keys, on_claim=lambda: job.update(cancel=stops.add(stop_scope, keys))):
        # A key is held by another job: a mask another period's job is
        # aligning, the AOI raster being built, a row still running.
        notifications.info(t("steps.prepare.claim_busy"))
        return
    cancel = job["cancel"]

    def _give_back():
        # Under the jobs' lock, so a Stop or a start racing it sees the
        # scope's last job either before or after it is gone.
        with jobs.errors_lock:
            stops.discard(stop_scope, cancel)  # before the release, so its re-render reads both gone
            if not stops.keys(stop_scope):  # the scope's last job: its batch and its Stop are over
                batch.set({k: v for k, v in batch.value.items() if k != stop_scope})
                if stop_scope in stopping.value:
                    stopping.set({k: n for k, n in stopping.value.items() if k != stop_scope})
        jobs.inflight.release(*keys)

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
                         row_errors=_ScopedErrors(jobs.row_errors, scope), errors_lock=jobs.errors_lock,
                         row_progress=jobs.row_progress, row_stage=jobs.row_stage, downloads=frozenset(downloads),
                         on_row_end=lambda: _count(jobs, stop_scope, done=1), mode=mode)

    def _job():
        try:
            run_rows(ctx, items, force=force)
        except asyncio.CancelledError:
            pass  # Stop: the task reads "cancelled"; not an error
        finally:
            _give_back()

    _count(jobs, stop_scope, total=len(items))
    spawn_in_context(_job)


def stop_scope(jobs: PrepareJobs, project: str, period_id: str) -> None:
    """Stop after the row in progress; the footer says so until the scope's last job ends."""
    scope = (project, period_id)
    jobs.stops.stop(scope)
    with jobs.errors_lock:  # ``_give_back`` drops the mark under it: never one left behind
        if jobs.stops.keys(scope):
            jobs.stopping.set({**jobs.stopping.value, scope: jobs.stopping.value.get(scope, 0) + 1})
