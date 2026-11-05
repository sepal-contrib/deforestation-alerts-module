"""The prepare jobs' shared state, and their start (spec §7.3).

The shell creates one ``PrepareJobs`` per session and hands it to every step
that runs or shows prepare rows: a row has one in-flight key
(``inflight_key``) whichever step started it, so two jobs never run one row,
and ``JobScopes`` records which project, period and step started each job
(option C, user decision 2026-10-30): the Alerts step's downloads
(``mode="download"``) and the Harmonize step's harmonizing
(``mode="harmonize"``) are independent jobs (``mine_keys``). Never claimed
or released from a render or an effect (see gui/scripts/map_layers.py), only
from clicks and workers.
"""

import logging
import threading
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


class JobScopes:
    """Each running job's in-flight keys, by (project, period id, step).

    Clicks add and workers discard, so every access takes the lock.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[tuple, dict] = {}  # scope -> {job token: frozenset of its keys}

    def add(self, scope: tuple, keys=()) -> object:
        """Record a job's keys; returns the token ``discard`` takes."""
        token = object()
        with self._lock:
            self._jobs.setdefault(scope, {})[token] = frozenset(keys)
        return token

    def keys(self, scope: tuple) -> set:
        """The in-flight keys of the jobs started in ``scope``."""
        with self._lock:
            return set().union(*self._jobs.get(scope, {}).values())

    def discard(self, scope: tuple, token: object) -> None:
        with self._lock:
            jobs = self._jobs.get(scope)
            if jobs is not None:
                jobs.pop(token, None)
                if not jobs:
                    del self._jobs[scope]


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
        self.scopes = JobScopes()
        self.row_errors = solara.reactive({})  # {scope: {row: message}}, written through _ScopedErrors
        # {prepare_jobs.progress_key: percent} of the downloads running; keyed by
        # project and period, so a row of another scope never reads it
        self.row_progress = solara.reactive({})
        self.row_stage = solara.reactive({})  # {progress_key: "download"|"harmonize"|"align"} of the rows running
        self.errors_lock = threading.Lock()  # the jobs set row errors, percents and stages under it


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
    # ``items`` come from the render: a project or period switch since then can
    # name a row the live plan no longer has; drop it before any key is taken
    items = [it for it in items if it[1] in (by_mask if it[0] == "mask" else by_source)]
    # Only a harmonize (or a mask's alignment) builds the AOI raster: a
    # GeoTIFF source's download (Read file) reads the AOI bounds alone
    file_items = [it for it in items if _is_file(*it) and (mode == "harmonize" or it[0] == "mask")]
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
    # A source's Harmonize aligns the period's stale masks first. When another
    # job is aligning one of them, the source queues behind it (user decision
    # 2026-11-05): it claims its own rows now (they read Queued) and its masks
    # and AOI raster once that job gives them back. A waiting job then holds
    # only source keys, which nobody waits for, so two queued jobs never
    # wait on each other.
    later = []
    if any(kind == "source" for kind, _ in items) and any(
            kind == "mask" and _key(kind, name) in jobs.inflight for kind, name in items):
        later = [k for k in keys if k == aoi_key(p.name) or k.startswith(mask_inflight_key(p.name, ""))]
        keys = [k for k in keys if k not in later]
    step_scope = (p.name, period.id, mode)  # one scope per step (option C)
    scopes = jobs.scopes
    job = {}
    # The job joins its step's scope only once its claim succeeds, and
    # before the claim is published: the claim's re-render already counts
    # these keys as this step's (``mine_keys``), and a refused claim never
    # lists another job's keys as ours.
    if not jobs.inflight.claim(*keys, on_claim=lambda: job.update(token=scopes.add(step_scope, keys))):
        # A key is held by another job: the AOI raster being built, a row
        # still running.
        notifications.info(t("steps.prepare.claim_busy"))
        return
    token = job["token"]

    def _give_back():
        # Under the jobs' lock, so a start racing it sees the scope's last
        # job either before or after it is gone.
        with jobs.errors_lock:
            scopes.discard(step_scope, token)  # before the release, so its re-render reads both gone
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
                         name=p.name, pdir=pdir, period=period, meta=meta, aoi=aoi,
                         row_errors=_ScopedErrors(jobs.row_errors, scope), errors_lock=jobs.errors_lock,
                         row_progress=jobs.row_progress, row_stage=jobs.row_stage, downloads=frozenset(downloads),
                         mode=mode)

    def _job():
        try:
            if later:
                jobs.inflight.claim_when_free(*later)
                keys.extend(later)  # given back with the others
            run_rows(ctx, items, force=force)
        finally:
            _give_back()

    spawn_in_context(_job)
