"""Earth Engine facts the steps need, fetched by one always-mounted leaf.

- GLAD-L's band list (``catalogue.meta_request("GLAD-L")``), once per session;
- the coverage of every source for the open project's AOI (spec §6.2): the
  three predefined entries once per session and AOI (their last days move
  daily), a custom entry whenever its record is missing or for another AOI;
- the ``aoi.geojson`` sidecar of an admin or asset AOI once the project has a
  GeoTIFF source or mask (spec §7.2).

Each is a ``use_task(prefer_threaded=False)`` (``await
gee_interface.get_info_async``) whose dependencies are values no worker
publish changes (the project name, the AOI digest, the names to check, where
the AOI geometry comes from, a retry counter): a worker's ``update_project``
re-renders this leaf on its own thread, where a task restart would find no
event loop (slice-1 NB1). Results are written with ``update_project`` from the
task, on the loop, only into the project and AOI they were fetched for. A
leaf, so nothing else re-renders when these reactives change.
"""

import asyncio
import json
import logging
import os
from datetime import date

import solara
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine import catalogue
from deforestation_alerts.engine.coverage import coverage_request, parse_coverage, record_for
from deforestation_alerts.engine.fingerprint import is_file_source
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import PREDEFINED_KEYS
from deforestation_alerts.project.review_copy import read_only
from gui.i18n import t
from gui.scripts import aoi_io
from gui.scripts.error_text import error_text

logger = logging.getLogger("SEPALUI.deforestation_alerts")


def coverage_needed(p, refreshed) -> tuple:
    """``(predefined keys, custom names)`` whose coverage the AOI still needs.

    A predefined entry once per session and AOI (``refreshed`` holds the
    ``(aoi_digest, key)`` pairs fetched this session), and again for a
    project without its record (another project with the same AOI was
    refreshed); a custom entry whenever its record is missing or stale.

    None for a review copy (spec §6.3): its sources are the creator's (their
    refs, possibly private Earth Engine assets, are never resolved) and its
    setup is never written, so neither a load nor "Check again" checks them.
    """
    if p is None or p.aoi is None or read_only(p):
        return (), ()
    digest = p.aoi.digest
    pre = tuple(k for k in PREDEFINED_KEYS if (digest, k) not in refreshed or record_for(p, k) is None)
    custom = tuple(sorted(n for n, s in p.sources.items() if s.kind == "custom" and record_for(p, n) is None))
    return pre, custom


def has_file_inputs(p) -> bool:
    return p is not None and (any(is_file_source(s) for s in p.sources.values())
                              or any(getattr(m, "origin", None) == "file" for m in p.masks.values()))


def sidecar_needed(p) -> bool:
    return p is not None and p.aoi is not None and p.aoi.geometry_file is None and has_file_inputs(p)


def aoi_origin(p, result):
    """Where the open AOI's geometry is read from: ``"result"`` (the picker's
    selection), ``"sidecar"`` (``aoi.geojson``) or None (not available yet).

    The AOI step publishes a changed AOI before it hands the new selection to
    ``aoi_result``, so for a moment that reactive still holds the previous
    AOI's. An Earth Engine selection names its AOI (``aoi_io.lazy_digest``)
    and is used only when it names this one; until the always-mounted AOI
    step hands it over (a pick, or the replay of a reopened project), the
    loader waits rather than parse that AOI's fetched sidecar (national AOIs
    run to megabytes) on the loop for a request the selection then restarts.
    A local AOI (drawn, uploaded, or an admin area fetched without Earth
    Engine) is not named by its selection, so the sidecar, replaced together
    with the AOI, is read instead.

    A review copy reads its AOI from the sidecar its package always carries,
    whatever the AOI kind (spec §5.3, §6.5): the evaluator's picker may never
    resolve an Earth Engine asset or admin code of the creator's.
    """
    if p is None or p.aoi is None:
        return None
    if read_only(p):
        return "sidecar" if p.aoi.geometry_file is not None else None
    if result is not None and aoi_io.lazy_digest(result) == p.aoi.digest:
        return "result"
    if p.aoi.geometry_file is None or is_ee_aoi(p.aoi):
        return None
    return "sidecar"


def is_ee_aoi(aoi) -> bool:
    """An Earth Engine AOI: one recorded by its admin code or asset id
    (``aoi_io.build_record`` takes ``aoi_io.lazy_digest`` for it)."""
    return str(aoi.digest).startswith(("admin:", "asset:"))


def _stage_sidecar(name, pdir, info):
    """Off the loop: the fetched AOI as ``aoi.geojson`` staged in ``pdir``."""
    logger.info("AOI sidecar of %s: %d features, %d bytes", name, len(info.get("features") or ()),
                len(json.dumps(info)))  # national AOIs approach Earth Engine's response limits
    return aoi_io.stage_sidecar(pdir, aoi_io.sidecar_gdf(info))


def _unlink_result(fut) -> None:
    if not fut.cancelled() and fut.exception() is None:
        fut.result().unlink(missing_ok=True)


@solara.component
def RemoteFactsLoader(app_state, gee_interface):
    notifications = use_notifications()
    p = app_state.project.value
    name = p.name if p is not None else None
    digest = p.aoi.digest if p is not None and p.aoi is not None else None
    pre, custom = coverage_needed(p, app_state.coverage_refreshed.value)
    result = app_state.aoi_result.value
    origin = aoi_origin(p, result)
    retry = app_state.coverage_retry.value
    need_sidecar = sidecar_needed(p)

    # ---- GLAD-L's band list, once per session
    async def _meta():
        if p is None or "GLAD-L" in app_state.catalogue_meta.value:
            return
        info = await gee_interface.get_info_async(catalogue.meta_request("GLAD-L"))
        app_state.catalogue_meta.set({**app_state.catalogue_meta.value, "GLAD-L": info})

    # This task and the two below take plain dependencies: safe only because
    # they move on the event loop alone. Whether a project is open, its name,
    # its AOI digest and origin, the sources still needing coverage and the
    # retry change in handlers and loop tasks (the Project dialog, the AOI
    # and Alerts steps, the source modal, these tasks' own results), never in
    # a worker publish (tests/test_shell_thread_publish.py renders this
    # loader under the workers' publishes). Should a worker ever publish one of them, wrap
    # the list in ``use_loop_dependencies`` (gui/scripts/solara_threads.py),
    # or the task starts in the worker's render and raises "no running event
    # loop".
    meta_task = solara.lab.use_task(_meta, dependencies=[p is not None, retry], prefer_threaded=False,
                                    raise_error=False)

    # ---- coverage of the AOI
    async def _coverage():
        if origin is None or not (pre or custom):
            return
        live = app_state.project.value
        customs = [live.sources[n] for n in custom if n in live.sources]
        app_state.coverage_pending.set(True)
        try:
            pdir = paths.project_dir(name)
            aoi = aoi_io.ee_aoi(result, None) if origin == "result" else aoi_io.ee_aoi(None, pdir)
            raw = await gee_interface.get_info_async(coverage_request(aoi, pre, customs))
        finally:
            app_state.coverage_pending.set(False)
        records = parse_coverage(raw, aoi_digest=digest, today=date.today())

        def _apply(project):
            if project.aoi is None or project.aoi.digest != digest:
                return  # the AOI changed meanwhile: these shares are another AOI's
            project.coverage = {**project.coverage, **records}

        if app_state.update_project(name, _apply):
            app_state.coverage_refreshed.set(app_state.coverage_refreshed.value | {(digest, k) for k in pre})
            app_state.coverage_error.set(None)

    # Loop-only dependencies (see ``meta_task``).
    cov_task = solara.lab.use_task(_coverage, dependencies=[name, digest, pre, custom, origin, retry],
                                   prefer_threaded=False, raise_error=False)

    # ---- the AOI geometry of an Earth Engine AOI, for GeoTIFF inputs
    async def _sidecar():
        if not need_sidecar or origin != "result":
            return
        pdir = paths.project_dir(name)
        app_state.sidecar_pending.set(True)
        try:
            info = await gee_interface.get_info_async(aoi_io.sidecar_request(aoi_io.ee_aoi(result, None)))
            staging = asyncio.ensure_future(asyncio.to_thread(_stage_sidecar, name, pdir, info))
            try:
                staged = await asyncio.shield(staging)
            except asyncio.CancelledError:
                staging.add_done_callback(_unlink_result)  # restarted: the staged file is nobody's
                raise
        finally:
            app_state.sidecar_pending.set(False)

        def _apply(project):
            if project.aoi is None or project.aoi.digest != digest:
                return
            os.replace(staged, pdir / paths.AOI_SIDECAR)
            project.aoi = project.aoi.model_copy(update={"geometry_file": paths.AOI_SIDECAR})

        try:
            app_state.update_project(name, _apply)
        finally:
            staged.unlink(missing_ok=True)

    # ``retry``: "Check again" also fetches a sidecar whose fetch failed.
    # Loop-only dependencies (see ``meta_task``).
    side_task = solara.lab.use_task(_sidecar, dependencies=[name, digest, need_sidecar, origin, retry],
                                    prefer_threaded=False, raise_error=False)

    # A failed check belongs to the project and AOI it ran for (not a writer:
    # only a session reactive is set here, never the project).
    solara.use_effect(lambda: app_state.coverage_error.set(None), [name, digest])

    def _on_meta_error():
        if meta_task.error:
            notifications.error(t("steps.alerts.meta_failed", error=error_text(meta_task.exception)))

    def _on_cov_error():
        if cov_task.error:
            message = error_text(cov_task.exception)
            app_state.coverage_error.set(message)
            notifications.error(t("coverage.check_failed", error=message))

    def _on_side_error():
        if side_task.error:
            notifications.error(t("steps.prepare.sidecar_failed", error=error_text(side_task.exception)))

    solara.use_effect(_on_meta_error, [meta_task.pending, meta_task.finished, meta_task.error, meta_task.cancelled])
    solara.use_effect(_on_cov_error, [cov_task.pending, cov_task.finished, cov_task.error, cov_task.cancelled])
    solara.use_effect(_on_side_error, [side_task.pending, side_task.finished, side_task.error, side_task.cancelled])
