"""Run Download & harmonize rows on a worker thread (spec §7.3).

One job runs its rows one at a time. Before each row it re-reads the live
project and stops when that row would no longer belong to the run it was
started as: the user stopped it, closed or switched the project, or changed
the period or the AOI (the Earth Engine AOI is fixed when the run starts, so a
later row would record fingerprints of inputs it did not use). Each row works
on a deep copy, and its artifacts are applied only while the project is still
the open one (``AppState.update_project``), so a late row never writes into
another project (review focus 5). Its files stay in its own project folder,
which is marked as being written for the whole run so it can't be deleted.

A row that finishes after its project was closed or switched has already
written (or replaced, or superseded) files in that project's folder, so its
artifacts are recorded in that project's own saved manifest instead, and the
run stops (the Integrate ruling #75, applied to rows). Nothing touches the
project open now, the map or the row errors.
"""

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import solara

from deforestation_alerts.engine.fingerprint import Status, harm_key
from deforestation_alerts.engine.plan import PrepareRow
from deforestation_alerts.engine.runner import (
    HarmonizeFailed,
    SourceJob,
    apply_artifacts,
    run_source_row,
)
from deforestation_alerts.project import io, paths
from deforestation_alerts.project.models import Period
from gui.i18n import t
from gui.scripts import raster_map
from gui.scripts.notify_bridge import (
    ERROR_TOAST_TIMEOUT,
    tile_progress_reporter,
    tracked_job,
)
from gui.store.project_writers import writing

logger = logging.getLogger("SEPALUI.deforestation_alerts")

_CELL_STATUS = {
    Status.CURRENT: "ready", Status.STALE: "stale", Status.MISSING: "missing", Status.NO_DATA: "no_data",
}


@dataclass(frozen=True)
class PrepareContext:
    app_state: Any
    layers: Any  # gui.scripts.map_layers.MapLayers
    map_: Any
    notifier: Any
    name: str  # the project the run belongs to
    pdir: Path
    period: Period
    meta: dict  # catalogue metadata by catalogue key; only rows that download need theirs
    aoi: Any  # ee.FeatureCollection; None only when it is unavailable and no row downloads
    cancel: threading.Event
    row_errors: Optional[solara.Reactive] = None  # {source: message}; None in tests


def _digest(p) -> Optional[str]:
    return p.aoi.digest if p is not None and p.aoi is not None else None


def _live(ctx: PrepareContext, aoi_digest: Optional[str]):
    """The live project while the run still belongs to it, else None."""
    live = ctx.app_state.project.value
    if live is None or live.name != ctx.name:
        return None
    if ctx.period.id not in live.periods or _digest(live) != aoi_digest:
        return None
    return live


def _set_error(ctx: PrepareContext, source: str, message: Optional[str]) -> None:
    if ctx.row_errors is None:
        return
    live = ctx.app_state.project.value
    if live is None or live.name != ctx.name or live.active_period != ctx.period.id:
        return  # the step now shows another project's (or period's) rows
    errors = dict(ctx.row_errors.value)
    if message is None:
        errors.pop(source, None)
    else:
        errors[source] = message
    ctx.row_errors.set(errors)


def _record_in_own_project(ctx: PrepareContext, arts: dict) -> None:
    """Record a late row's artifacts in its own project's saved manifest,
    under the project's manifest lock (``io.update_saved_project``).

    Called under the run's ``writing(ctx.name)``, once ``update_project`` has
    refused them because that project is no longer open. The row already
    wrote its files in that project's folder: without the record, a forced
    refresh would keep the old content_id over new bytes (spec §5.3), and a
    superseded file the manifest still points at would read as missing
    (spec §5.2). A failure is logged and never fails the run.
    """
    if not arts:
        return
    root = Path(ctx.pdir).parent

    def _apply(own):
        if ctx.period.id not in own.periods:
            return False  # the period was removed there: no orphan records
        apply_artifacts(own, arts)

    try:
        io.update_saved_project(ctx.name, _apply, root=root)
    except Exception:  # noqa: BLE001 - the row's files are written; only the record is lost
        logger.exception("could not record a late row in project %s", ctx.name)


def row_cells(row: PrepareRow, running: bool) -> list:
    def cell(s):
        return {"type": "status", "status": "running" if running else _CELL_STATUS[s]}

    return [{"value": row.source}, cell(row.raw), cell(row.harmonized)]


def run_rows(ctx: PrepareContext, sources: list, force: bool = False) -> None:
    aoi_digest = _digest(ctx.app_state.project.value)
    with writing(ctx.name), tracked_job(ctx.notifier, t("steps.prepare.job_title"),
                                        total_steps=len(sources)) as task:
        for source in sources:
            if ctx.cancel.is_set():
                return
            live = _live(ctx, aoi_digest)
            if live is None:
                return
            src = live.sources.get(source)
            if src is None:
                continue  # removed from the project meanwhile
            task.step(t("steps.prepare.job_row", source=source))
            job = SourceJob(project=live.model_copy(deep=True), pdir=ctx.pdir, period=ctx.period,
                            source=source, meta=ctx.meta.get(src.catalogue_key), aoi=ctx.aoi, force=force)
            try:
                arts = run_source_row(job, on_tiles=tile_progress_reporter(task))
            except Exception as exc:  # noqa: BLE001 - one failed row must not stop the others (spec §12)
                logger.exception("row %s failed", source)
                message = str(exc) or exc.__class__.__name__
                _set_error(ctx, source, message)
                if ctx.notifier is not None:
                    ctx.notifier.error(t("steps.prepare.row_failed", source=source, error=message),
                                       timeout=ERROR_TOAST_TIMEOUT)
                if isinstance(exc, HarmonizeFailed) and exc.artifacts:
                    # The download succeeded: record it, so the next run only
                    # re-harmonizes from the file on disk.
                    kept = exc.artifacts
                    if not ctx.app_state.update_project(ctx.name, lambda p, a=kept: apply_artifacts(p, a)):
                        _record_in_own_project(ctx, kept)  # the project was left mid-row
                        return
                continue
            _set_error(ctx, source, None)
            if not ctx.app_state.update_project(ctx.name, lambda p, a=arts: apply_artifacts(p, a)):
                _record_in_own_project(ctx, arts)  # the project was left mid-row
                return
            harm = arts.get(harm_key(ctx.period.id, source))
            key = f"src:{source}"
            if harm is not None and ctx.layers.is_on(key):
                # spec §6.2/§9.1: a source already on the map is redrawn from
                # its new harmonized file (``refresh`` rebuilds on a new content id)
                path = paths.resolve(ctx.pdir, harm.rel_path)
                ctx.layers.refresh(
                    key,
                    lambda pth=path, k=key, s=source: raster_map.add_raster_layer(ctx.map_, pth, key=k, name=s),
                    harm.content_id,
                )
