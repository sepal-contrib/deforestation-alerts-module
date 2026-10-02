"""Download & harmonize step (spec §7.3).

Slice 1 simplifications of §7.3 (ruling #72): the table has one row per source
(masks join in slice 4); one in-flight key per row covers both of its
artifacts; Run all is one job that runs its rows one at a time.

A row explains its coverage (``build_rows``, spec §7.3): a source that is not
covered, has no data for the window or has no coverage record reads why under
its row and cannot be run or shown; a partly covered one keeps a warning line
per cause; a harmonized one reads what it found. Run all skips the blocked
rows (``PrepareRow.needs_run``). Only an Earth Engine download needs the
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
nor points it at another project's job.
"""

import asyncio
import logging
import threading

import solara
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine import catalogue
from deforestation_alerts.engine.coverage import UNCHECKED
from deforestation_alerts.engine.fingerprint import (
    AOI_KEY, Status, aoi_raster_inputs, harm_key, is_file_source, raw_key, status,
)
from deforestation_alerts.engine.plan import plan_prepare
from deforestation_alerts.project import paths
from gui.i18n import t
from gui.scripts import aoi_io, source_layers
from gui.scripts.coverage_ui import result_note, row_notes
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


def aoi_key(project: str) -> str:
    """The in-flight key of the project's AOI raster (``masks/aoi.tif``),
    shared by every period: one AOI-raster build at a time."""
    return f"prep:{project}:@aoi"


def size_text(n: int) -> str:
    """A row's size on disk: "" for nothing yet, MB below 1 GB (never "0.0")."""
    if not n:
        return ""
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{max(n / 1e6, 0.1):.1f} MB"


def _label(p, source):
    src = p.sources[source]
    return t(catalogue.LABEL_KEYS[src.catalogue_key]) if src.kind == "predefined" else source


def build_rows(p, pdir, period, rows, *, mine, errors, on, building, run, rerun, toggle) -> list:
    """The ``ProductTable`` rows of the sources table (spec §7.3); pure.

    ``mine``: this project's and period's in-flight keys; ``errors``: the row
    errors by source; ``on``/``building``: the map keys shown and being built;
    ``run``/``rerun``/``toggle``: the row actions, called with the source.
    A blocked row (``PrepareRow.blocked``) cannot be run or shown; a row in
    flight cannot be started again (the claim would refuse it silently), so
    both run buttons say so, the download one with its spinner.
    """
    out = []
    for r in rows:
        busy = _inflight_key(p.name, period.id, r.source) in mine
        if r.file_missing:
            notes = [{"text": t("steps.prepare.file_missing", path=r.file_missing), "tone": "error"}]
        else:
            notes = row_notes(r.coverage, r.notes)
        result = result_note(r.stats) if not r.blocked else None
        if result is not None:
            notes.append(result)
        size = sum(a.size_bytes for k in (raw_key(period.id, r.source), harm_key(period.id, r.source))
                   if (a := p.artifacts.get(k)) is not None)
        key = f"src:{r.source}"
        out.append({
            "key": r.source,
            "cells": row_cells(r, running=busy, label=_label(p, r.source))
            + [{"value": size_text(size), "muted": True}],
            "notes": notes,
            "error": errors.get(r.source),
            "actions": [
                {"kind": "download", "disabled": busy or r.blocked, "loading": busy,
                 "on_click": (lambda s=r.source: run(s))},
                {"kind": "redownload", "disabled": busy or r.blocked, "on_click": (lambda s=r.source: rerun(s))},
                # ``ready`` keeps files built before a failed check (Integrate
                # uses them), but a blocked row is not shown (ruling T19 C)
                {"kind": "map_toggle", "is_on": key in on, "loading": key in building,
                 "disabled": not r.ready or r.blocked, "on_click": (lambda s=r.source: toggle(s))},
            ],
        })
    return out


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
    errors_lock = solara.use_memo(threading.Lock, [])  # the step's jobs set row errors under it
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
        file_rows = [s for s in sources if is_file_source(p.sources[s])]
        if file_rows and status(p, pdir, AOI_KEY, aoi_raster_inputs(p)) is not Status.CURRENT:
            if p.aoi.geometry_file is None:
                notifications.info(t("steps.prepare.aoi_geometry_missing"))
                return
            keys.append(aoi_key(p.name))  # one AOI-raster build at a time
        if not sources or not inflight.claim(*keys):
            return
        # Drift note: only rows that download from Earth Engine need it (the
        # metadata and the AOI); a grid change re-harmonizes from the raw file
        # on disk, and a GeoTIFF row is normalized locally from the user's file.
        downloads = [s for s in sources
                     if not is_file_source(p.sources[s]) and (force or by_source[s].raw in _DOWNLOADS)]
        meta = app_state.catalogue_meta.value
        if any(getattr(p.sources[s], "catalogue_key", None) == "GLAD-L" for s in downloads) and "GLAD-L" not in meta:
            inflight.release(*keys)
            notifications.info(t("steps.alerts.meta_loading"))
            return
        aoi = None  # local rows (a grid change, GeoTIFF sources) need no Earth Engine
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
        stop_scope = (p.name, period.id)
        cancel = stops.add(stop_scope)
        ctx = PrepareContext(app_state=app_state, layers=layers, map_=map_, notifier=notifications,
                             name=p.name, pdir=pdir, period=period, meta=meta, aoi=aoi, cancel=cancel,
                             row_errors=row_errors, errors_lock=errors_lock)

        def _job():
            try:
                run_rows(ctx, sources, force=force)
            except asyncio.CancelledError:
                pass  # Stop: the task reads "cancelled"; not an error
            finally:
                stops.discard(stop_scope, cancel)
                inflight.release(*keys)

        spawn_in_context(_job)

    def _toggle_map(source):
        art = p.artifacts.get(harm_key(period.id, source))
        if art is None:
            return
        key = f"src:{source}"
        path = paths.resolve(pdir, art.rel_path)
        layers.toggle(key, source_layers.harmonized_builder(map_, path, key, source),
                      content_id=art.content_id, notifier=notifications)

    mine = {k for k in running if k.startswith(_inflight_key(p.name, period.id, ""))}
    pending = [r.source for r in rows if r.needs_run]

    table_rows = build_rows(p, pdir, period, rows, mine=mine, errors=row_errors.value, on=on, building=building,
                            run=lambda s: _start([s]), rerun=lambda s: _start([s], force=True), toggle=_toggle_map)

    with solara.Column(style="gap:16px;"):
        solara.Markdown(f"### {t('steps.prepare.title')}")
        solara.Text(t("steps.prepare.description"), classes=["text--secondary"])
        GridForm(app_state=app_state)  # remounts its fields per project and AOI (ruling #18)
        if p.grid is not None:
            ProductTable(
                title=t("steps.prepare.list_title"),
                columns=[{"label": t("steps.prepare.col_source")},
                         {"label": t("steps.prepare.col_download"), "width": "110px"},
                         {"label": t("steps.prepare.col_harmonize"), "width": "110px"},
                         {"label": t("steps.prepare.col_size"), "width": "80px"}],
                rows=table_rows, empty_text=t("steps.prepare.empty"),
            )
            with solara.Row(style="gap:8px;"):
                solara.Button(t("steps.prepare.run_all", n=len(pending)), color="primary", small=True,
                              icon_name="mdi-play", disabled=not pending or bool(mine),
                              on_click=lambda: _start(pending))
                solara.Button(t("steps.prepare.stop"), small=True, outlined=True, disabled=not mine,
                              on_click=lambda: stops.stop((p.name, period.id)))
            if app_state.coverage_error.value and any(r.coverage == UNCHECKED for r in rows):
                solara.Button(t("coverage.check_again"), small=True, outlined=True, icon_name="mdi-refresh",
                              disabled=app_state.coverage_pending.value,
                              on_click=lambda: app_state.coverage_retry.set(app_state.coverage_retry.value + 1))
