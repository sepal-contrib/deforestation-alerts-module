"""Keeps ``app_state.scene_index`` in step with the active project.

Mounted once beside ``RemoteFactsLoader`` in the shell, not inside a step, so
it survives every navigation and a reviewer opening the Analysis step finds the
index already there.

Two windows. The *foreground* one -- the active period's alert window padded by
``FOREGROUND_MONTHS`` on each side -- is what the timeline shows by default and
what the loader fetches before anything else. The *background* one, padded by
``BACKGROUND_MONTHS``, is filled afterwards, so a reviewer pressing "one more
month" usually finds it already loaded.

Both are fetched in ``CHUNK_MONTHS``-month chunks through one loop, ``_fill``.
A single ``getInfo`` over a two-year project asks Earth Engine for the whole
span at once and has no partial result, so a payload too large would leave no
index at all and "Try again" would repeat the same oversized call (spec §5.5,
§9 and §11). Chunked, every chunk that landed is on disk and published, and
the manifest's ``chunks`` records which window did not -- so an empty month
reads as "never successfully asked" rather than "no images there".

The index on disk is published **before** any fetch is attempted. That is what
makes a stale index whose refresh fails degrade to "slightly old" instead of to
"empty": losing a working timeline because a refresh of it failed would be the
worst outcome available here (spec Review Focus #2).

Contiguity. ``scene_cache.store_chunk`` folds each fetched window into a single
``Coverage`` with a ``min``/``max`` union, which only stays honest while every
window overlaps or abuts what is already covered. The disk layer documents that
invariant and deliberately leaves it unenforced -- it runs on a background
thread, where a firing assert would kill the thread and drop the chunk -- so it
is enforced here, in the layer that computes windows and can widen a bad one
(``contiguous``).

This component reads only the project, the AOI selection, the requested window
and the retry counter during its render, never ``scene_index`` or
``scene_manifest``; the tasks publish those, so reading them in the render body
would subscribe the component to its own output and re-run the tasks for ever.
"""

import calendar
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import solara
import solara.lab
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.project import paths
from deforestation_alerts.scripts import scene_index, scene_query
from gui.i18n import t
from gui.scripts import aoi_io, remote_facts, scene_cache, vis_layers
from gui.scripts.error_text import ee_no_access, error_text
from gui.scripts.solara_threads import to_thread_in_context

#: Under ``SEPALUI.deforestation_alerts`` so a failure reaches the server log
#: (``notify_bridge.install_server_log_handler`` takes WARNING+ there). The
#: INFO lines below are emitted on the render loop, which registers no
#: notification tracker, so none of them becomes a notification pill.
logger = logging.getLogger("SEPALUI.deforestation_alerts.scene_loader")

FOREGROUND_MONTHS = 2
BACKGROUND_MONTHS = 6
#: How much of a multi-month window one request takes -- the chunk size of
#: both paths, foreground and background. The spec's number: §5.5 fills the
#: background buffer "in one-month chunks" and §9 answers "payload too large
#: for one request" with month-chunked fetching, which §11 calls part of the
#: design from the start. A month at a time means the timeline fills while the
#: reviewer works instead of in one long jump, and -- the reason the review
#: raised it -- a span too large for one ``getInfo`` leaves every chunk that
#: did land on disk instead of leaving no index at all.
CHUNK_MONTHS = 1

Window = tuple[date, date]


def _today() -> date:
    """``date.today()``, behind one name so a test can pin it.

    Every window below is clamped at today, so the loader's answers move at
    midnight; a test that spells its dates out needs that clock held still.
    """
    return date.today()


def shift_months(d: date, months: int) -> date:
    """``d`` moved by whole months, the day clamped to the target month's length."""
    total = (d.year * 12 + d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def wanted_window(project, today: date, months: int) -> Optional[Window]:
    """The active period's alert window padded by ``months`` on each side.

    Clamped at ``today``: there are no images of next week, and asking for them
    would make every index look stale for ever. A period that has not started
    yet would clamp to an inverted window, which ``Coverage`` forbids and
    ``refresh_window`` reads both ends of, so the start is pulled in with it.
    """
    if project is None or not project.active_period:
        return None
    period = project.periods.get(project.active_period)
    if period is None:
        return None
    end = min(today, shift_months(period.end, months))
    return (min(shift_months(period.start, -months), end), end)


def widest(*windows) -> Optional[Window]:
    """The smallest window containing every one given, ignoring ``None``s."""
    live = [w for w in windows if w is not None]
    if not live:
        return None
    return (min(w[0] for w in live), max(w[1] for w in live))


def contiguous(window: Window, covered) -> Window:
    """``window`` stretched, if it has to be, until it touches ``covered``.

    ``store_chunk`` has no record of disjoint intervals, only one ``Coverage``,
    so storing (Jul 1, Jul 31) onto an index covering [May 1, May 31] would
    claim June without ever having fetched it -- a hole that never heals,
    because ``needs_refresh`` then answers ``""`` for those dates and the 24h
    tail refresh only ever revisits the last ``TAIL_DAYS``. Stretching the
    window costs one larger request; raising here would drop the chunk on a
    background thread, and raising is not this layer's job anyway.
    """
    if covered is None:
        return window
    start, end = window
    day = timedelta(days=1)
    if start - covered.end > day:
        start = covered.end
    if covered.start - end > day:
        end = covered.start
    return (start, end)


def _one_step(covered, side: Window, months: int) -> Window:
    """``side`` cut to ``months`` months, keeping the end that touches ``covered``.

    ``refresh_window(..., "widen")`` returns the later side as
    ``(covered.end, want_end)`` and the earlier one as
    ``(want_start, covered.start)``, so the anchored end is whichever one is a
    coverage edge -- and the step has to be taken *from* it. A month taken from
    the far end of the side would leave exactly the hole ``contiguous`` exists
    to prevent.
    """
    if side[0] >= covered.end:  # the later side
        return (side[0], min(side[1], shift_months(side[0], months)))
    return (max(side[0], shift_months(side[1], -months)), side[1])  # the earlier side


def fetch_window(manifest, want: Window, mode: str, *, step_months: int = 0) -> Window:
    """The one contiguous range to request for ``mode``.

    ``step_months`` cuts the request down to that many months, taken from the
    end that is already anchored, and the caller loops until ``needs_refresh``
    goes quiet. 0 asks for the whole missing side in one request.

    A ``"rebuild"`` chunk is anchored at the *recent* end of ``want``. That
    end is where the scenes a reviewer is most often missing live, it is the
    end ``refresh_window`` already prefers for a widen, and taking the first
    chunk there means the coverage it defines grows backwards from it -- so
    every later round is an ordinary widen that abuts what is covered. A
    ``"tail"`` is never chunked: it spans ``TAIL_DAYS`` and is one request by
    construction.
    """
    window = scene_query.refresh_window(manifest, want, mode)
    if manifest is None or mode == "rebuild":
        if step_months:
            window = (max(window[0], shift_months(window[1], -step_months)), window[1])
        return window  # a rebuild defines the coverage: there is nothing to abut
    if step_months and mode == "widen":
        window = _one_step(manifest.covered, window, step_months)
    return contiguous(window, manifest.covered)


def _read(pdir):
    """Off the loop: ``(tables, manifest, had_files)`` for the cached index.

    ``read_index`` answers a corrupt, truncated or wrong-version index as a
    plain cache miss and says nothing to the user, which is right for a disk
    layer. ``had_files`` is what lets the loader tell that case ("rebuilding
    the image index") from a project that simply has no index yet, which
    deserves no message at all.
    """
    had_files = paths.scene_manifest_path(pdir).exists() and paths.scenes_path(pdir).exists()
    tables, manifest = scene_cache.read_index(pdir)
    return (tables, manifest, had_files)


def _planet_chunk(project, window: Window):
    """``(region, request)`` for ``window``, or ``None`` when the AOI has no
    region mapping. Separated so tests can switch Planet off in one line."""
    bounds = project.aoi.bounds if project is not None and project.aoi is not None else None
    if not bounds:
        return None
    region = vis_layers.nicfi_region(bounds)
    if region not in vis_layers.NICFI_COLLECTIONS:
        return None
    return (region, scene_index.planet_request(
        vis_layers.NICFI_COLLECTIONS[region], window[0], window[1]))


@solara.component
def SceneIndexLoader(app_state, gee_interface):
    notifications = use_notifications()
    p = app_state.project.value
    name = p.name if p is not None else None
    digest = p.aoi.digest if p is not None and p.aoi is not None else None
    today = _today()
    # The AOI geometry comes from the picker's selection or from the staged
    # sidecar, and for a moment after a project opens it is available from
    # neither: ``AppState._install`` clears ``aoi_result`` and the
    # always-mounted AOI step republishes it. ``remote_facts.aoi_origin`` is
    # the established answer -- None means "wait", and because ``origin`` is a
    # dependency the task re-runs by itself when the AOI step hands the
    # selection over.
    origin = remote_facts.aoi_origin(p, app_state.aoi_result.value)
    requested = app_state.scene_window_request.value
    fg = widest(wanted_window(p, today, FOREGROUND_MONTHS), requested)
    bg = widest(wanted_window(p, today, BACKGROUND_MONTHS), requested)
    retry = app_state.scene_index_retry.value

    def _still_current() -> bool:
        """The project and AOI this fetch was started for are still the open
        ones. ``use_task`` cancels at the next await, but the publish below
        follows one, so this guard is what keeps a slow reply from landing on
        the project that replaced it."""
        live = app_state.project.value
        return (live is not None and live.name == name
                and live.aoi is not None and live.aoi.digest == digest)

    async def _with_planet(tables, window: Window):
        """``(tables, planet)``: the NICFI basemaps of ``window`` added.

        Nothing here may cost the Sentinel-2 and Landsat rows already in hand.
        NICFI is licence-gated, so no access is an ordinary outcome, and it is
        *recorded* rather than only logged so the toolbar can disable the
        Planet toggle and say why (spec §5.4).
        """
        try:
            chunk = _planet_chunk(p, window)
        except Exception as exc:  # noqa: BLE001
            # Nothing in ``_planet_chunk`` is an expected failure -- "no NICFI
            # over this AOI" is a ``None`` return -- so an exception here is a
            # defect or an Earth Engine client that was never initialised. It
            # reaches the server log in full and claims nothing about the
            # licence, because it learnt nothing about it.
            logger.warning("Planet NICFI request for %s could not be built: %s",
                           name, error_text(exc), exc_info=True)
            return (tables, None)
        if chunk is None:
            # This chunk asked NICFI nothing, so it has learnt nothing:
            # ``store_chunk`` keeps what the manifest already recorded for
            # ``None``. Reporting "no access" here would switch the Planet
            # toggle off as soon as the first widening month landed.
            return (tables, None)
        try:
            raw = await gee_interface.get_info_async(chunk[1])
        except Exception as exc:  # noqa: BLE001 - no access reads as an EE error
            if not ee_no_access(exc):
                # Not Earth Engine's "not found / caller does not have
                # access", so not a licence answer: an expired token, a quota,
                # a network failure or a defect of ours. Recording "no Planet
                # access" for it would make a fixable fault look like a
                # permanent licence state, so this records nothing and the
                # reason goes to the server log in full. Same check and same
                # reading as the sibling NICFI validator in
                # ``gui.widget.vis_layer_modal``, so the two cannot disagree
                # about what counts as a refusal.
                logger.warning("Planet NICFI rows of %s not fetched: %s",
                               name, error_text(exc), exc_info=True)
                return (tables, None)
            # The licence gate. A project without a NICFI licence is refused
            # here, which is an ordinary outcome: it is recorded for the
            # toolbar (which disables the Planet toggle and says why) and
            # logged at INFO rather than warned about, so an unlicensed
            # project produces no traceback on every refresh.
            logger.info("no Planet NICFI rows for %s: %s", name, error_text(exc))
            return (tables, scene_index.PlanetState(region=chunk[0], access=False))
        try:
            rows = scene_index.parse_planet(raw)
        except Exception as exc:  # noqa: BLE001
            # Earth Engine answered and ``parse_planet`` could not read the
            # answer: a defect on our side, not a permission problem.
            # Recording "no Planet access" would hide a bug behind a
            # plausible-looking outcome, so this records nothing and the
            # traceback goes to the server log.
            logger.warning("Planet NICFI rows of %s could not be read: %s",
                           name, error_text(exc), exc_info=True)
            return (tables, None)
        return (
            scene_index.SceneTables(
                scenes=pd.concat([tables.scenes, rows], ignore_index=True), tiles=tables.tiles),
            scene_index.PlanetState(region=chunk[0], access=True),
        )

    async def _fetch(window: Window, mode: str):
        """One request, merged into the cache on disk, published.

        Returns the manifest that landed, or ``None`` when another project took
        over while the request was in flight.
        """
        pdir = paths.project_dir(name)
        aoi = (aoi_io.ee_aoi(app_state.aoi_result.value, None) if origin == "result"
               else aoi_io.ee_aoi(None, pdir))
        raw = await gee_interface.get_info_async(
            scene_index.scene_request(aoi, window[0], window[1]))
        tables, planet = await _with_planet(scene_index.parse_scenes(raw), window)

        # ``store_chunk`` takes the project's lock and re-reads the index
        # inside it, so no lock is held here and no pre-read snapshot is
        # passed in: the background widener and this call merge onto each
        # other rather than over each other.
        #
        # Checked here too, as late as it can be: ``to_thread_in_context`` is
        # not cancellable, so an AOI changed while a chunk is in flight would
        # have this ``store_chunk`` write the *old* AOI's rows after the
        # rebuild of the new one -- ``scene_cache`` compares digests and would
        # take this chunk for the newer truth. It self-heals (the publish
        # below is blocked, and the next ``read_index`` sees the mismatch and
        # rebuilds), but it costs a refetch, so the window is kept as narrow
        # as one check can make it.
        if not _still_current():
            return None
        merged, manifest = await to_thread_in_context(
            scene_cache.store_chunk, pdir, tables, window, digest, planet=planet,
            now=datetime.now(timezone.utc), replace=(mode == "rebuild"),
        )
        if not _still_current():
            return None
        app_state.scene_index.set(merged)
        app_state.scene_manifest.set(manifest)
        return manifest

    async def _fill(manifest, want: Window):
        """``want`` fetched in ``CHUNK_MONTHS``-month chunks, each stored as it lands.

        The payload mitigation of spec §5.2, §9 and §11 -- which §11 calls
        part of the design from the start, not a retrofit. One ``getInfo``
        over a two-year project asks Earth Engine for ~28 months of
        Sentinel-2 and Landsat metadata across the whole AOI, and it has no
        partial result: either it returns or the index never builds and "Try
        again" repeats the same oversized call. Chunked, every chunk that did
        land is on disk, published and visible, and the manifest records the
        window that failed.

        One loop for both callers, which is what the review asked for: the
        foreground walks it from the recent end of a rebuild, the background
        widener from the edge of the coverage, and ``fetch_window`` anchors
        both so every window abuts what is already covered.

        Returns the manifest that landed, or ``None`` when another project or
        AOI took over while a request was in flight.
        """
        # A bound, not a count: the loop ends on the first ``needs_refresh``
        # that is quiet. It exists so a manifest that somehow stops growing
        # cannot spin this coroutine for ever.
        for _ in range((want[1] - want[0]).days // 28 + 4):
            mode = scene_query.needs_refresh(manifest, want, digest=digest,
                                             now=datetime.now(timezone.utc))
            if not mode:
                return manifest
            window = fetch_window(manifest, want, mode, step_months=CHUNK_MONTHS)
            if window[1] <= window[0]:
                return manifest  # defensive: ``refresh_window`` returns no empty side
            try:
                # The manifest for the next round is the one this chunk
                # landed, never a snapshot taken before the await: the
                # widener and a reviewer's "one more month" both write while
                # this runs.
                landed = await _fetch(window, mode)
            except Exception:
                # Whatever landed before this chunk stays on disk and stays
                # published. The failed window is recorded so ``ok`` is a
                # fact rather than a constant, and so an empty month reads as
                # "never successfully asked" rather than "no images there".
                # The message belongs to the caller, which knows whether this
                # was the foreground or the buffer, so the exception goes on.
                await to_thread_in_context(
                    scene_cache.note_failed_chunk, paths.project_dir(name), window, digest,
                    now=datetime.now(timezone.utc))
                raise
            if landed is None:
                return None  # another project opened while this was in flight
            manifest = landed
        logger.info("scene index of %s: chunked fetch stopped at its bound", name)
        return manifest

    # ---- the index on disk, then the foreground window
    async def _load():
        if name is None or digest is None or fg is None:
            app_state.scene_index.set(None)
            app_state.scene_manifest.set(None)
            app_state.scene_index_error.set(None)
            return
        if origin is None:
            # No AOI geometry to ask about yet. An index of *this* AOI keeps
            # answering while the AOI step replays the selection; one left from
            # the AOI that was just replaced describes other tiles, so it goes.
            shown = app_state.scene_manifest.value
            if shown is not None and shown.aoi_digest != digest:
                app_state.scene_index.set(None)
                app_state.scene_manifest.set(None)
            return
        pdir = paths.project_dir(name)
        tables, manifest, had_files = await to_thread_in_context(_read, pdir)
        if tables is not None and manifest is not None and manifest.aoi_digest == digest:
            # Published before any fetch: a stale index answers while it
            # refreshes, and keeps answering if the refresh fails.
            app_state.scene_index.set(tables)
            app_state.scene_manifest.set(manifest)
        else:
            app_state.scene_index.set(None)
            app_state.scene_manifest.set(None)
            if manifest is None and had_files:
                # There were index files and ``read_index`` could not use them:
                # a corrupt, truncated or older-layout cache. It says nothing
                # to the user by design, so the refetch it implies is announced
                # from here, where the refetch is actually known about.
                logger.info("scene index of %s could not be read: rebuilding", name)
                notifications.info(t("imagery.index_rebuilding"))
            manifest = None

        now = datetime.now(timezone.utc)
        mode = scene_query.needs_refresh(manifest, fg, digest=digest, now=now)
        if not mode:
            app_state.scene_index_error.set(None)
            return
        app_state.scene_index_pending.set(True)
        try:
            await _fill(manifest, fg)
            app_state.scene_index_error.set(None)
        except Exception as exc:  # noqa: BLE001
            # Whatever was published above stays published: a refresh that
            # failed must not cost the reviewer the index they already had.
            # The message is both in state (the timeline labels the index as
            # old and offers "Try again", which bumps ``scene_index_retry``)
            # and in a toast, because this leaf runs wherever the reviewer is.
            message = error_text(exc)
            logger.warning("scene index of %s not refreshed: %s", name, message, exc_info=True)
            app_state.scene_index_error.set(message)
            notifications.error(t("imagery.index_failed", error=message))
        finally:
            # Known, deferred: a dependency change cancels this task at its
            # next await, and this ``finally`` can run *after* the replacement
            # run has already set the flag True -- so a spinner driven by it
            # can blink off mid-fetch. The fix is a generation counter (only
            # the newest run may lower the flag) and it belongs with the task
            # that renders the flag, not here.
            app_state.scene_index_pending.set(False)

    load = solara.lab.use_task(_load, dependencies=[name, digest, fg, origin, retry],
                               prefer_threaded=False, raise_error=False)

    # ---- the ±BACKGROUND_MONTHS buffer, one month at a time, once the
    # foreground window is actually covered
    async def _widen():
        if load.pending or name is None or digest is None or bg is None or origin is None:
            return
        if app_state.scene_index.value is None or app_state.scene_index_error.value is not None:
            return  # nothing to widen onto, or the foreground just failed
        manifest = app_state.scene_manifest.value
        if manifest is None:
            return
        now = datetime.now(timezone.utc)
        if fg is not None and scene_query.needs_refresh(manifest, fg, digest=digest, now=now):
            # ``load.pending`` is only observed once per render, so a widen can
            # start in the same tick as a foreground fetch of the same side and
            # ask Earth Engine for it twice. The months the reviewer is looking
            # at come first; the buffer waits for the next render.
            return
        if scene_query.needs_refresh(manifest, bg, digest=digest, now=now) != "widen":
            # Only a widen belongs to the buffer. A "tail" or a "rebuild" is
            # the foreground's work, and the guard above has already returned
            # for either of those.
            return
        app_state.scene_widening.set(True)
        try:
            await _fill(manifest, bg)
        except Exception as exc:  # noqa: BLE001
            # The foreground months still answer, so this is not reported to
            # the reviewer: the only visible effect is that "one more month"
            # has to fetch when they press it. It is logged at WARNING so the
            # server log carries the reason.
            logger.warning("scene index of %s not widened: %s", name, error_text(exc), exc_info=True)
        finally:
            app_state.scene_widening.set(False)

    # ``load.pending``/``load.finished`` are ``use_task`` status flags, not
    # worker publishes: they move once per ``_load`` run, so ``_widen`` is
    # re-run once per load rather than looping. ``app_state.scene_manifest``,
    # which ``_fetch`` does publish, is deliberately **not** a dependency.
    solara.lab.use_task(_widen, dependencies=[name, digest, fg, bg, origin, retry,
                                              load.pending, load.finished],
                        prefer_threaded=False, raise_error=False)
