"""Run Download & harmonize rows on a worker thread (spec §7.3).

One job runs its rows one at a time: mask rows (``("mask", name)``: download
and align a project mask) and source rows (``("source", name)``), in the
order given, so Run all aligns the period's masks before the sources read
them. A keep mask on in the period that covers none of the AOI warns when its
alignment finishes. Before each row it re-reads the live
project and stops when that row would no longer belong to the run it was
started as: the user stopped it, closed or switched the project, or changed
the period, the AOI or the grid (the Earth Engine AOI is fixed when the run
starts, so a later row would record fingerprints of inputs it did not use).
Each row works on a deep copy, and its artifacts are applied only while the
project is still the open one (``AppState.update_project``), so a late row
never writes into another project (review focus 5). Its files stay in its own
project folder, which is marked as being written for the whole run so it
can't be deleted.

The row that was running when the period, the AOI or the grid changed is not
recorded either (the same rule as Integrate's, ``integrate_jobs.scope_changed``):
its new files that no record names are deleted, so an AOI change leaves no
old-AOI file behind, and the run stops. The AOI step and the grid form refuse
those edits while a job writes into the project; this is the backstop for an
edit made just before the job's mark was set.

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
message (``error_text``), any other error its own text.

A download says how far it has got (bug 2026-10-16): pysepal's pill draws no
progress bar, so each whole percent of the tiles goes into the task's title
and into ``row_progress``, which the row's Download cell reads. Both go back
when the download is done, and a milestone says the harmonize (alignment)
has begun.

Each row also says which stage it is in (``row_stage``): ``download`` while an
item of ``downloads`` fetches its tiles, then ``harmonize`` (a mask:
``align``); a row that downloads nothing starts at that second stage. The
stage goes when the row ends, and ``on_row_end`` is called for every row the
run reached, whether it succeeded, failed or was skipped: the step's footer
counts the running, queued and done rows from them.
"""

import asyncio
import contextlib
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import solara

from deforestation_alerts.engine.coverage import UNCHECKED
from deforestation_alerts.engine.fingerprint import Status, harm_key, mask_key
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
from deforestation_alerts.project.review_copy import refuse_if_read_only
from deforestation_alerts.project.cleanup import delete_orphans
from gui.i18n import t
from gui.scripts import source_layers
from gui.scripts.integrate_jobs import run_scope, scope_changed
from gui.scripts.coverage_ui import note_text, row_notes
from gui.scripts.error_text import error_text
from gui.scripts.notify_bridge import (
    ERROR_TOAST_TIMEOUT,
    tile_progress_reporter,
    tracked_job,
)
from gui.store.project_writers import writing

logger = logging.getLogger("SEPALUI.deforestation_alerts")

CELL_STATUS = {Status.CURRENT: "ready", Status.STALE: "stale", Status.MISSING: "missing"}


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
    row_progress: Optional[solara.Reactive] = None  # {progress_key: percent} of the downloads; None in tests
    row_stage: Optional[solara.Reactive] = None  # {progress_key: "download"|"harmonize"|"align"}; None in tests
    downloads: frozenset = frozenset()  # the items (``("source", name)`` / ``("mask", name)``) that download
    on_row_end: Optional[Callable[[], None]] = None  # called once each row the run reached is over


def progress_key(project: str, period_id: str, kind: str, name: str) -> tuple:
    """A row's key in ``row_progress``. A mask's is project-wide, like its
    in-flight key: one aligned file per project, whichever period runs it."""
    return (project, None, f"@mask:{name}") if kind == "mask" else (project, period_id, name)


def _live(ctx: PrepareContext, scope: tuple):
    """The live project while the run still belongs to it, else None."""
    live = ctx.app_state.project.value
    if live is None or live.name != ctx.name:
        return None
    if scope_changed(live, ctx.period.id, scope) is not None:
        return None
    return live


class _ScopeChanged(Exception):
    """Raised inside the ``update_project`` callback: nothing is recorded or
    published (``update_project`` publishes only when the callback returns)."""


def _unnamed_files(p, arts: dict) -> list:
    """The rel_paths of ``arts`` that no record of ``p`` names: a refused
    row's files nothing will ever point at. A file a record still names (a
    rel_path shared with an earlier run of the row) stays: that record already
    reads out of date, its fingerprint naming the old AOI, grid or period."""
    named = {a.rel_path for a in p.artifacts.values()}
    return sorted({a.rel_path for a in arts.values() if a.rel_path and a.rel_path not in named})


def _delete_refused(ctx: PrepareContext, rel_paths: list) -> None:
    """Delete a refused row's files, then the folders that held only them.
    Called under the run's ``writing(ctx.name)``."""
    if rel_paths:
        delete_orphans(ctx.pdir, rel_paths)


def _record(ctx: PrepareContext, scope: tuple, arts: dict) -> bool:
    """Record a row's artifacts; False when the run must stop: its project
    was left (recorded in its own manifest instead) or its period, AOI or grid
    changed during the row (nothing recorded, its files deleted)."""
    refused = []

    def _apply(p):
        why = scope_changed(p, ctx.period.id, scope)
        if why is not None:
            refused.append((why, _unnamed_files(p, arts)))
            raise _ScopeChanged(why)
        apply_artifacts(p, arts)

    try:
        recorded = ctx.app_state.update_project(ctx.name, _apply)
    except _ScopeChanged:
        recorded = True  # the project is still open: nothing else to record into
    if not recorded:
        _record_in_own_project(ctx, scope, arts)  # the project was left mid-row
        return False
    if refused:
        why, files = refused[0]
        logger.warning("a row of project %s is not recorded: its %s changed during the run", ctx.name, why)
        _delete_refused(ctx, files)
        return False
    return True


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


def _record_in_own_project(ctx: PrepareContext, scope: tuple, arts: dict) -> None:
    """Record a late row's artifacts in its own project's saved manifest,
    under the project's manifest lock (``io.update_saved_project``).

    Called under the run's ``writing(ctx.name)``, once ``update_project`` has
    refused them because that project is no longer open. The row already
    wrote its files in that project's folder: without the record, a forced
    refresh would keep the old content_id over new bytes (spec §5.3), and a
    superseded file the manifest still points at would read as missing
    (spec §5.2). Refused, like the open-project record, when the saved
    project's period, AOI or grid is no longer the run's: the row's files no
    saved record names are then deleted. A failure is logged and never fails
    the run.
    """
    if not arts:
        return
    root = Path(ctx.pdir).parent
    refused = []

    def _apply(own):
        if scope_changed(own, ctx.period.id, scope) is not None:
            refused.extend(_unnamed_files(own, arts))
            return False  # a removed period would get orphan records; another AOI or grid is not this row's
        apply_artifacts(own, arts)

    try:
        io.update_saved_project(ctx.name, _apply, root=root)
    except Exception:  # noqa: BLE001 - the row's files are written; only the record is lost
        logger.exception("could not record a late row in project %s", ctx.name)
    _delete_refused(ctx, refused)


def _failure_text(exc: BaseException) -> str:
    """A row's failure for its error line and toast, translated where the
    engine says what went wrong by a coverage note or a message key."""
    if isinstance(exc, CoverageRefused) and exc.notes:
        return note_text(exc.notes[0])
    return error_text(exc)  # a HarmonizeFailed carries its cause's key and values


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
    if (getattr(exc, "values", None) or {}).get("name") == name:  # a HarmonizeFailed carries its cause's
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


def _publish(ctx: PrepareContext, store: Optional[solara.Reactive], key: tuple, value) -> None:
    """Publish (``None``: drop) a row's entry in ``store``, under the lock the
    step's jobs share: two jobs may report at once."""
    if store is None:
        return
    with ctx.errors_lock or contextlib.nullcontext():
        current = dict(store.value)
        if value is None:
            if key not in current:
                return
            current.pop(key)
        else:
            current[key] = value
        store.set(current)


def _set_progress(ctx: PrepareContext, key: tuple, pct: Optional[int]) -> None:
    """A row's download percent (``None``: drop it)."""
    _publish(ctx, ctx.row_progress, key, pct)


def _set_stage(ctx: PrepareContext, key: tuple, stage: Optional[str]) -> None:
    """A row's stage (``None``: drop it)."""
    _publish(ctx, ctx.row_stage, key, stage)


def _row_reporter(ctx: PrepareContext, task, title: str, key: tuple, done_step: str, then: str):
    """The row's ``on_tiles``: its percent in the task's title and on its row,
    until the download is done; then the title is back, ``done_step`` (the
    harmonize, or the alignment) is the pill's milestone and ``then`` the
    row's stage."""
    def on_pct(pct):
        if pct >= 100:
            _set_progress(ctx, key, 100)
            _set_stage(ctx, key, then)
            task.update(title)
            task.step(done_step)
            return
        _set_progress(ctx, key, pct)
        task.update(t("steps.prepare.job_progress", title=title, pct=pct))

    return tile_progress_reporter(task, on_pct=on_pct)


def run_rows(ctx: PrepareContext, rows: list, force: bool = False) -> None:
    """Run ``rows`` (``("mask", name)`` / ``("source", name)`` items, a bare
    string being a source) one at a time, in order: Run all puts the
    period's masks first (spec §7.3). Each row starts from a deep copy of the
    live project, so a row sees the masks aligned before it, and a mask
    switched while a row runs leaves that row recorded with the old choice
    (it then reads out of date: review focus 4). A mask row's error is keyed
    ``@mask:<name>`` in ``row_errors``. Raises ``ReadOnlyProject``, running
    nothing, in a review copy (spec §6.3)."""
    refuse_if_read_only(ctx.app_state.project.value)
    scope = run_scope(ctx.app_state.project.value)
    items = _items(rows)
    title = t("steps.prepare.job_title")
    with writing(ctx.name), tracked_job(ctx.notifier, title, total_steps=len(items)) as task:
        for kind, name in items:
            if ctx.cancel.is_set():
                raise asyncio.CancelledError()  # pysepal marks the task CANCELLED
            live = _live(ctx, scope)
            if live is None:
                return
            key = progress_key(ctx.name, ctx.period.id, kind, name)
            try:
                if not _run_item(ctx, task, title, scope, live, kind, name, key, force):
                    return
            finally:
                # the row is over, whichever way, and only now that it is recorded
                _set_stage(ctx, key, None)
                if ctx.on_row_end is not None:
                    ctx.on_row_end()


def _run_item(ctx: PrepareContext, task, title: str, scope: tuple, live, kind: str, name: str, key: tuple,
              force: bool) -> bool:
    """Run one row of ``run_rows``; False when the run must stop."""
    if kind == "mask":
        if name not in live.masks:
            return True  # removed from the project meanwhile
        task.step(t("steps.prepare.job_mask_row", mask=name))
        job = MaskJob(project=live.model_copy(deep=True), pdir=ctx.pdir, name=name, aoi=ctx.aoi,
                      force=force)
        run, error_key = run_mask_row, f"@mask:{name}"
        done_step, then = t("steps.prepare.job_align_row", mask=name), "align"
    else:
        src = live.sources.get(name)
        if src is None:
            return True  # removed from the project meanwhile
        task.step(t("steps.prepare.job_row", source=name))
        job = SourceJob(project=live.model_copy(deep=True), pdir=ctx.pdir, period=ctx.period,
                        source=name, aoi=ctx.aoi, force=force,
                        meta=ctx.meta.get("GLAD-L") if getattr(src, "catalogue_key", None) == "GLAD-L"
                        else None)
        run, error_key = run_source_row, name
        done_step, then = t("steps.prepare.job_harmonize_row", source=name), "harmonize"
    _set_stage(ctx, key, "download" if (kind, name) in ctx.downloads else then)
    try:
        arts = run(job, on_tiles=_row_reporter(ctx, task, title, key, done_step, then))
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
            return _record(ctx, scope, exc.artifacts)
        return True
    finally:
        # the row's download is over, whichever way: its percent goes with it
        _set_progress(ctx, key, None)
        task.update(title)
    _set_error(ctx, error_key, None)
    if not _record(ctx, scope, arts):
        return False
    if kind == "mask":
        _warn_if_covers_none(ctx, name, arts)
    _refresh_layer(ctx, kind, name, arts)
    return True
