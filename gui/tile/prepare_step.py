"""Download & harmonize step (spec §7.3).

Slice 1 simplifications of §7.3 (ruling #72): the table has one row per source
(masks join in slice 4) and no size column; one in-flight key per row covers
both of its artifacts; Run all is one job that runs its rows one at a time.

In-flight keys carry the project name (``prep:{project}:{period}:{source}``):
a job still finishing a row for a project the user left neither shows as
running in, nor blocks, the project now open, while reopening its own project
still finds its rows claimed until it ends.

Stop has the same scope: every job started in the open project and period
gets its own cancel Event, and Stop sets all of them, so a later start (even a
no-op click on a ready row) never takes Stop away from a Run all still going,
nor points it at another project's job.
"""

import logging
import threading

import solara
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine.fingerprint import Status, harm_key
from deforestation_alerts.engine.plan import plan_prepare
from deforestation_alerts.project import paths
from gui.i18n import t
from gui.scripts import aoi_io, catalogue_meta, raster_map
from gui.scripts.inflight import InflightKeys
from gui.scripts.prepare_jobs import PrepareContext, row_cells, run_rows
from gui.scripts.solara_threads import spawn_in_context
from gui.store import workflow_steps
from gui.widget.grid_form import GridForm
from gui.widget.product_table import ProductTable

logger = logging.getLogger("SEPALUI.deforestation_alerts")

_DOWNLOADS = (Status.MISSING, Status.STALE)  # the runner downloads these (and anything forced)


def _inflight_key(project: str, period_id: str, source: str) -> str:
    return f"prep:{project}:{period_id}:{source}"


class _StopEvents:
    """The cancel Events of the running jobs, by (project, period id).

    Clicks add and stop, and workers discard, so every access takes the lock.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._events: dict[tuple, set] = {}

    def add(self, scope: tuple) -> threading.Event:
        event = threading.Event()
        with self._lock:
            self._events.setdefault(scope, set()).add(event)
        return event

    def discard(self, scope: tuple, event: threading.Event) -> None:
        with self._lock:
            events = self._events.get(scope)
            if events is not None:
                events.discard(event)
                if not events:
                    del self._events[scope]

    def stop(self, scope: tuple) -> None:
        with self._lock:
            events = list(self._events.get(scope, ()))
        for event in events:
            event.set()


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
    row_errors = solara.use_reactive({})
    catalogue_meta.use_catalogue_meta(app_state, gee_interface)
    # A row's error belongs to the project and period it ran for.
    scope = (app_state.project_loaded_signal.value, p.name if p else None, p.active_period if p else None)
    solara.use_effect(lambda: row_errors.set({}), [scope])

    period = workflow_steps.active_period(p)
    if p is None or period is None or p.aoi is None:
        solara.Info(t("workflow.lock_prepare"))
        return
    pdir = paths.project_dir(p.name)
    rows = plan_prepare(p, pdir, period) if p.grid is not None else []
    by_source = {r.source: r for r in rows}

    def _start(sources, force=False):
        keys = [_inflight_key(p.name, period.id, s) for s in sources]
        if not sources or not inflight.claim(*keys):
            return
        # Drift note: only rows that download need Earth Engine (the metadata
        # and the AOI); a grid change re-harmonizes from the raw file on disk.
        downloads = [s for s in sources if force or by_source[s].raw in _DOWNLOADS]
        meta = app_state.catalogue_meta.value
        if any(p.sources[s].catalogue_key not in meta for s in downloads):
            inflight.release(*keys)
            notifications.info(t("steps.alerts.meta_loading"))
            return
        aoi = None  # a grid change re-harmonizes from the raw file: no Earth Engine
        if downloads:
            try:
                aoi = aoi_io.ee_aoi(app_state.aoi_result.value, pdir)  # builds ee objects, no request
            except Exception as exc:  # noqa: BLE001 - ValueError (no geometry), or a bad sidecar/ee object
                # Any failure here must give the claimed keys back, or the rows
                # read "running" for the rest of the session.
                inflight.release(*keys)
                logger.exception("could not build the Earth Engine AOI")
                notifications.error(str(exc) or exc.__class__.__name__)
                return
        scope = (p.name, period.id)
        cancel = stops.add(scope)
        ctx = PrepareContext(app_state=app_state, layers=layers, map_=map_, notifier=notifications,
                             name=p.name, pdir=pdir, period=period, meta=meta, aoi=aoi, cancel=cancel,
                             row_errors=row_errors)

        def _job():
            try:
                run_rows(ctx, sources, force=force)
            finally:
                stops.discard(scope, cancel)
                inflight.release(*keys)

        spawn_in_context(_job)

    def _toggle_map(source):
        art = p.artifacts.get(harm_key(period.id, source))
        if art is None:
            return
        key = f"src:{source}"
        path = paths.resolve(pdir, art.rel_path)
        layers.toggle(key, lambda: raster_map.add_raster_layer(map_, path, key=key, name=source),
                      content_id=art.content_id, notifier=notifications)

    mine = {k for k in running if k.startswith(_inflight_key(p.name, period.id, ""))}
    pending = [r.source for r in rows if r.needs_run]

    def _row(r):
        # A row in flight cannot be started again (the claim would refuse it
        # silently): both buttons say so, the download one with its spinner.
        busy = _inflight_key(p.name, period.id, r.source) in mine
        return {
            "key": r.source,
            "cells": row_cells(r, running=busy),
            "error": row_errors.value.get(r.source),
            "actions": [
                {"kind": "download", "disabled": busy, "loading": busy,
                 "on_click": (lambda s=r.source: _start([s]))},
                {"kind": "redownload", "disabled": busy, "on_click": (lambda s=r.source: _start([s], force=True))},
                {"kind": "map_toggle", "is_on": f"src:{r.source}" in on, "loading": f"src:{r.source}" in building,
                 "disabled": not r.ready, "on_click": (lambda s=r.source: _toggle_map(s))},
            ],
        }

    table_rows = [_row(r) for r in rows]

    with solara.Column(style="gap:16px;"):
        solara.Markdown(f"### {t('steps.prepare.title')}")
        solara.Text(t("steps.prepare.description"), classes=["text--secondary"])
        GridForm(app_state=app_state)  # remounts its fields per project and AOI (ruling #18)
        if p.grid is not None:
            ProductTable(
                title=t("steps.prepare.list_title"),
                columns=[{"label": t("steps.prepare.col_source")},
                         {"label": t("steps.prepare.col_download"), "width": "110px"},
                         {"label": t("steps.prepare.col_harmonize"), "width": "110px"}],
                rows=table_rows, empty_text=t("steps.prepare.empty"),
            )
            with solara.Row(style="gap:8px;"):
                solara.Button(t("steps.prepare.run_all", n=len(pending)), color="primary", small=True,
                              icon_name="mdi-play", disabled=not pending or bool(mine),
                              on_click=lambda: _start(pending))
                solara.Button(t("steps.prepare.stop"), small=True, outlined=True, disabled=not mine,
                              on_click=lambda: stops.stop((p.name, period.id)))
