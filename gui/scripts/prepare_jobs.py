"""Run Download & harmonize rows on a worker thread (spec §7.3).

One job runs its rows one at a time: mask rows (``("mask", name)``: download
and align a project mask) and source rows (``("source", name)``), in the
order given, so Run all aligns the period's masks before the sources read
them. A keep mask on in the period that covers none of the AOI warns when its
alignment finishes. Before each row it re-reads the live
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

Stop ends the run before its next row by raising ``asyncio.CancelledError``
inside the tracked job, so the task pill reads "cancelled", not "completed"
(slice-1 Task 18 minor); the caller treats it as an ordinary end. A refused
or failed row's message is translated (``_failure_text``): a coverage refusal
reads its coverage note, an engine error that names a message key reads that
message.
"""

import asyncio
import contextlib
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import solara

from deforestation_alerts.engine.coverage import NO_DATA, NOT_COVERED, UNCHECKED
from deforestation_alerts.engine.fingerprint import Status, harm_key, mask_key
from deforestation_alerts.engine.plan import PrepareRow
from deforestation_alerts.engine.runner import (
    CoverageRefused,
    HarmonizeFailed,
    MaskJob,
    SourceJob,
    apply_artifacts,
    run_mask_row,
    run_source_row,
)
from deforestation_alerts.project import io, paths
from deforestation_alerts.project.models import Period
from gui.i18n import t
from gui.scripts import source_layers
from gui.scripts.coverage_ui import note_text, row_notes
from gui.scripts.notify_bridge import (
    ERROR_TOAST_TIMEOUT,
    tile_progress_reporter,
    tracked_job,
)
from gui.store.project_writers import writing

logger = logging.getLogger("SEPALUI.deforestation_alerts")

_CELL_STATUS = {Status.CURRENT: "ready", Status.STALE: "stale", Status.MISSING: "missing"}
_BLOCKED_STATUS = {NOT_COVERED: "not_covered", NO_DATA: "no_data", UNCHECKED: "unchecked"}


@dataclass(frozen=True)
class PrepareContext:
    app_state: Any
    layers: Any  # gui.scripts.map_layers.MapLayers
    map_: Any
    notifier: Any
    name: str  # the project the run belongs to
    pdir: Path
    period: Period
    meta: dict  # catalogue metadata by catalogue key; only a GLAD-L row that downloads needs it
    aoi: Any  # ee.FeatureCollection; None only when it is unavailable and no row downloads
    cancel: threading.Event
    row_errors: Optional[solara.Reactive] = None  # {source or "@mask:<name>": message}; None in tests
    errors_lock: Any = None  # threading.Lock shared by the step's jobs around ``row_errors``
    on_warning: Any = None  # called with a warning's text; None: ``notifier.warning`` (tests record it)


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
    # Two jobs of the step (a Run all and a single row) may finish rows at
    # once: the read-modify-write must not drop the other's error.
    with ctx.errors_lock or contextlib.nullcontext():
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


def row_cells(row: PrepareRow, running: bool, label=None) -> list:
    """The name and both stage cells; a row blocked by its coverage shows why
    (not covered, no data, not checked) in both."""
    def cell(s):
        if running:
            return {"type": "status", "status": "running"}
        if row.blocked and row.file_missing is None:
            return {"type": "status", "status": _BLOCKED_STATUS[row.coverage]}
        return {"type": "status", "status": _CELL_STATUS[s]}

    return [{"value": label or row.source}, cell(row.raw), cell(row.harmonized)]


def _failure_text(exc: BaseException) -> str:
    """A row's failure for its error line and toast, translated where the
    engine says what went wrong by a coverage note or a message key."""
    if isinstance(exc, CoverageRefused) and exc.notes:
        return note_text(exc.notes[0])
    if isinstance(exc, HarmonizeFailed) and exc.__cause__ is not None:
        return _failure_text(exc.__cause__)  # its message is the cause's
    key = getattr(exc, "key", None)  # AoiGeometryMissing, MaskNotReady, MaskFileMissing
    if isinstance(key, str):
        return t(key, **(getattr(exc, "values", None) or {}))
    return str(exc) or exc.__class__.__name__


def _failure_toast(source: str, exc: BaseException, message: str) -> str:
    """A refusal reads like its row ("Not covered: GLAD-S2 does not cover
    ..."), which already names the source; the rest get the source first."""
    if isinstance(exc, CoverageRefused) and exc.state != UNCHECKED and exc.notes:
        return row_notes(exc.state, exc.notes)[0]["text"]
    return t("steps.prepare.row_failed", source=source, error=message)


def _mask_failure_toast(name: str, exc: BaseException, message: str) -> str:
    """A mask row's failure says it is a mask ("Mask forest: ..."), unless its
    keyed refusal already names the mask (``MaskFileMissing``,
    ``MaskNativeUnknown``)."""
    cause = exc.__cause__ if isinstance(exc, HarmonizeFailed) and exc.__cause__ is not None else exc
    if (getattr(cause, "values", None) or {}).get("name") == name:
        return message
    return t("steps.prepare.mask_row_failed", mask=name, error=message)


def _items(rows) -> list:
    """``("mask", name)`` / ``("source", name)`` items; a bare string is a source."""
    return [r if isinstance(r, tuple) else ("source", r) for r in rows]


def _warn_if_covers_none(ctx: PrepareContext, name: str, arts: dict) -> None:
    """A keep mask on in the period whose aligned file has no on-pixel would
    remove every alert: say so when its alignment finishes (spec §7.3)."""
    art = arts.get(mask_key(name))
    if art is None or int(art.stats.get("on_pixels", 1)) != 0:
        return
    live = ctx.app_state.project.value
    period = live.periods.get(ctx.period.id) if live is not None else None
    choice = period.masks.get(name) if period is not None else None
    if choice is None or not choice.on or choice.mode != "keep":
        return
    message = t("steps.prepare.mask_covers_none", mask=name)
    if ctx.on_warning is not None:
        ctx.on_warning(message)
    elif ctx.notifier is not None:
        ctx.notifier.warning(message)


def _refresh_layer(ctx: PrepareContext, kind: str, name: str, arts: dict) -> None:
    """Redraw a row's layer already on the map from its new file (spec §6.2,
    §9.1; ``refresh`` rebuilds on a new content id): a source's harmonized
    file (``src:<source>``), a mask's aligned file (``mask:<name>``).
    ``refresh`` replaces the layer's legend too, so each passes its own
    (spec §9.4): a mask's names the live project's active-period choice. A
    source's layer is redrawn only while its run's period is still the active
    one (a mask's aligned file is project-wide, not per period)."""
    if kind == "mask":
        art, key = arts.get(mask_key(name)), f"mask:{name}"
        if art is None or not ctx.layers.is_on(key):
            return
        path = paths.resolve(ctx.pdir, art.rel_path)
        live = ctx.app_state.project.value
        ctx.layers.refresh(key, source_layers.mask_builder(ctx.map_, path, key, source_layers.mask_name(name)),
                           art.content_id, legend=source_layers.mask_layer_legend(live, name))
        return
    live = ctx.app_state.project.value
    if live is None or live.active_period != ctx.period.id:
        return  # the step now shows another period: its layer must not get this period's file
    art, key = arts.get(harm_key(ctx.period.id, name)), f"src:{name}"
    if art is not None and ctx.layers.is_on(key):
        path = paths.resolve(ctx.pdir, art.rel_path)
        build = source_layers.harmonized_builder(ctx.map_, path, key, source_layers.harmonized_name(name, ctx.period))
        ctx.layers.refresh(key, build, art.content_id,
                           legend=source_layers.harmonized_legend(key, name, ctx.period))


def run_rows(ctx: PrepareContext, rows: list, force: bool = False) -> None:
    """Run ``rows`` (``("mask", name)`` / ``("source", name)`` items, a bare
    string being a source) one at a time, in order: Run all puts the
    period's masks first (spec §7.3). Each row starts from a deep copy of the
    live project, so a row sees the masks aligned before it, and a mask
    switched while a row runs leaves that row recorded with the old choice
    (it then reads out of date: review focus 4). A mask row's error is keyed
    ``@mask:<name>`` in ``row_errors``."""
    aoi_digest = _digest(ctx.app_state.project.value)
    items = _items(rows)
    with writing(ctx.name), tracked_job(ctx.notifier, t("steps.prepare.job_title"),
                                        total_steps=len(items)) as task:
        for kind, name in items:
            if ctx.cancel.is_set():
                raise asyncio.CancelledError()  # pysepal marks the task CANCELLED
            live = _live(ctx, aoi_digest)
            if live is None:
                return
            if kind == "mask":
                if name not in live.masks:
                    continue  # removed from the project meanwhile
                task.step(t("steps.prepare.job_mask_row", mask=name))
                job = MaskJob(project=live.model_copy(deep=True), pdir=ctx.pdir, name=name, aoi=ctx.aoi,
                              force=force)
                run, error_key = run_mask_row, f"@mask:{name}"
            else:
                src = live.sources.get(name)
                if src is None:
                    continue  # removed from the project meanwhile
                task.step(t("steps.prepare.job_row", source=name))
                job = SourceJob(project=live.model_copy(deep=True), pdir=ctx.pdir, period=ctx.period,
                                source=name, aoi=ctx.aoi, force=force,
                                meta=ctx.meta.get("GLAD-L") if getattr(src, "catalogue_key", None) == "GLAD-L"
                                else None)
                run, error_key = run_source_row, name
            try:
                arts = run(job, on_tiles=tile_progress_reporter(task))
            except Exception as exc:  # noqa: BLE001 - one failed row must not stop the others (spec §12)
                logger.exception("row %s failed", error_key)
                message = _failure_text(exc)
                _set_error(ctx, error_key, message)
                if ctx.notifier is not None:
                    toast = (_mask_failure_toast if kind == "mask" else _failure_toast)(name, exc, message)
                    ctx.notifier.error(toast, timeout=ERROR_TOAST_TIMEOUT)
                if isinstance(exc, HarmonizeFailed) and exc.artifacts:
                    # The download (or the AOI raster) succeeded: record it, so
                    # the next run only re-harmonizes (re-aligns) from disk.
                    kept = exc.artifacts
                    if not ctx.app_state.update_project(ctx.name, lambda p, a=kept: apply_artifacts(p, a)):
                        _record_in_own_project(ctx, kept)  # the project was left mid-row
                        return
                continue
            _set_error(ctx, error_key, None)
            if not ctx.app_state.update_project(ctx.name, lambda p, a=arts: apply_artifacts(p, a)):
                _record_in_own_project(ctx, arts)  # the project was left mid-row
                return
            if kind == "mask":
                _warn_if_covers_none(ctx, name, arts)
            _refresh_layer(ctx, kind, name, arts)
