"""``gui.scripts.scene_loader``: when the project's scene index is fetched.

Two of these tests carry the slice's Review Focus items and are written to
fail loudly if the behaviour they name regresses:

* ``test_a_failed_tail_refresh_keeps_the_stale_index_and_reports_the_failure``
  (Focus #2) asserts the rows read from disk are *still published* after the
  refetch failed, and asserts it again after a further settle, so an
  implementation that published ``None`` on error, cleared the reactive before
  fetching, or let the exception escape the handler cannot pass it;
* ``test_every_stored_window_touches_the_coverage_it_merges_into`` wraps the
  real ``scene_cache.store_chunk`` and compares every window against the
  coverage on disk at the moment of the call, which is the invariant the disk
  layer's ``min``/``max`` coverage union depends on and deliberately does not
  assert itself.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import reacton
from pysepal.solara.notifications import NotificationProvider
from pysepal.solara.notifications.bus import get_current_bus

from deforestation_alerts.project import paths
from deforestation_alerts.project.models import AoiRecord, Period, Project
from deforestation_alerts.scripts import scene_index
from gui.i18n import t
from gui.scripts import scene_cache
from gui.scripts.error_text import ee_no_access
from gui.scripts import scene_loader as sl
from gui.store.state_manager import AppState

ROOT = Path(__file__).resolve().parents[1]
S2 = scene_index.SOURCE_S2
PLANET = scene_index.SOURCE_PLANET

RAW = {
    "scenes": {"source": [S2], "millis": [1775174400000], "cloud": [12.5], "tile": ["W"],
               "key": ["COPERNICUS/S2_HARMONIZED/A"]},
    "tiles": {"tile": ["W"], "gtype": ["Polygon"],
              "coords": [[[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]]},
}
#: one NICFI basemap, the mosaic of 2026-03
PLANET_RAW = {"key": ["projects/planet-nicfi/assets/basemaps/americas/2026-03"], "millis": [1772323200000]}
EMPTY_RAW = {"scenes": dict(scene_index.EMPTY_SCENE_ARRAYS),
             "tiles": {"tile": [], "coords": [], "gtype": []}}


@pytest.fixture(autouse=True)
def pinned_today(monkeypatch):
    """Hold the loader's clock still.

    Every window it computes is clamped at ``today``, so without this the
    literal dates below answer differently each day -- which is exactly how
    the brief's own illustrative tests went red the morning after they were
    written.
    """
    monkeypatch.setattr(sl, "_today", lambda: date(2026, 10, 2))


def _p(name="sl", digest="d1"):
    p = Project(name=name, created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest=digest,
                      bounds=(-55.1, -10.1, -55.0, -10.0), geometry_file=paths.AOI_SIDECAR)
    return p


def _with_period(p, start=date(2026, 5, 3), end=date(2026, 8, 23)):
    """The active period's alert window: what ``wanted_window`` pads."""
    pid = f"{start}_{end}"
    p.periods = {pid: Period(id=pid, start=start, end=end)}
    p.active_period = pid
    return p


def _covered(start, end):
    return scene_index.Coverage(start=start, end=end)


def _manifest(start, end, *, digest="d1", age_hours=0.0):
    return scene_index.SceneManifest(
        aoi_digest=digest, refreshed=datetime.now(timezone.utc) - timedelta(hours=age_hours),
        covered=_covered(start, end),
    )


# --------------------------------------------------------------- pure windows
def test_the_default_windows_are_the_ones_the_spec_asks_for():
    """Every component test below monkeypatches these, so without this one
    assertion the defaults could be changed to 1 and 3 and nothing would go
    red. They are the spec's numbers: two months around the alert window is
    what the timeline shows, six is what the background widener fills."""
    assert (sl.FOREGROUND_MONTHS, sl.BACKGROUND_MONTHS) == (2, 6)
    # and the chunk size of both paths: the spec's "one-month chunks" (§5.5),
    # which §9 makes the answer to a payload too large for one request
    assert sl.CHUNK_MONTHS == 1


def test_the_wanted_window_pads_the_active_period_by_whole_months():
    p = _with_period(_p())
    # The pad is whole months on the start; the end is padded and then clamped
    # at ``today``, which for this period is what both of these hit.
    assert sl.wanted_window(p, date(2026, 10, 2), 2) == (date(2026, 3, 3), date(2026, 10, 2))
    assert sl.wanted_window(p, date(2026, 10, 2), 6) == (date(2025, 11, 3), date(2026, 10, 2))


def test_an_unclamped_end_is_the_padded_one():
    p = _with_period(_p(), start=date(2025, 5, 3), end=date(2025, 8, 23))
    assert sl.wanted_window(p, date(2026, 10, 2), 2) == (date(2025, 3, 3), date(2025, 10, 23))


def test_the_wanted_window_never_runs_past_today():
    p = _with_period(_p(), start=date(2026, 8, 1), end=date(2026, 9, 28))
    assert sl.wanted_window(p, date(2026, 10, 2), 2) == (date(2026, 6, 1), date(2026, 10, 2))


def test_a_period_that_has_not_started_yet_asks_for_one_day_not_an_inverted_window():
    """``Coverage`` is ``start <= end`` by its contract and ``refresh_window``
    reads both ends: a padded start clamped past ``today`` must not invert."""
    p = _with_period(_p(), start=date(2027, 1, 1), end=date(2027, 2, 1))
    start, end = sl.wanted_window(p, date(2026, 10, 2), 2)
    assert start <= end == date(2026, 10, 2)


def test_without_a_project_or_an_active_period_there_is_no_window():
    assert sl.wanted_window(None, date(2026, 10, 2), 2) is None
    assert sl.wanted_window(_p(), date(2026, 10, 2), 2) is None


def test_widest_unions_the_windows_and_ignores_the_missing_ones():
    a, b = (date(2026, 3, 1), date(2026, 6, 1)), (date(2026, 1, 1), date(2026, 4, 1))
    assert sl.widest(a, b) == (date(2026, 1, 1), date(2026, 6, 1))
    assert sl.widest(a, None) == a
    assert sl.widest(None, None) is None and sl.widest() is None


# ------------------------------------------------------------- contiguity (1)
def test_a_window_that_would_leave_a_gap_is_stretched_to_touch_the_coverage():
    """``scene_cache.store_chunk`` unions the window into ``Coverage`` with
    ``min``/``max`` and documents -- but does not enforce -- that the window
    touches what is already covered. Enforcement lives here, where a bad
    window can be recomputed instead of raising on a background thread."""
    covered = _covered(date(2026, 5, 1), date(2026, 5, 31))
    # June nobody fetched would otherwise be claimed by the union
    assert sl.contiguous((date(2026, 7, 1), date(2026, 7, 31)), covered) == (date(2026, 5, 31), date(2026, 7, 31))
    assert sl.contiguous((date(2026, 3, 1), date(2026, 3, 31)), covered) == (date(2026, 3, 1), date(2026, 5, 1))
    # already touching: an abutting and an overlapping window are left alone
    assert sl.contiguous((date(2026, 6, 1), date(2026, 6, 30)), covered) == (date(2026, 6, 1), date(2026, 6, 30))
    assert sl.contiguous((date(2026, 4, 30), date(2026, 5, 10)), covered) == (date(2026, 4, 30), date(2026, 5, 10))
    # abutting to the day is contiguous for an inclusive Coverage: Apr 30 and
    # May 1 leave nothing between them, so the window is not widened
    assert sl.contiguous((date(2026, 4, 1), date(2026, 4, 30)), covered) == (date(2026, 4, 1), date(2026, 4, 30))
    # no coverage yet: a rebuild defines the coverage, so there is nothing to abut
    assert sl.contiguous((date(2026, 7, 1), date(2026, 7, 31)), None) == (date(2026, 7, 1), date(2026, 7, 31))


def test_the_widening_step_stays_anchored_on_the_edge_of_the_coverage():
    """``refresh_window(..., "widen")`` hands back a whole missing side; the
    loader fetches it a month at a time, and the month it takes has to be the
    one *next to* the coverage. A step taken from the far end of the side
    would leave a hole the manifest then claims."""
    m = _manifest(date(2026, 3, 3), date(2026, 10, 2))
    # the earlier side: one month back from covered.start, not forward from the want
    assert sl.fetch_window(m, (date(2026, 1, 3), date(2026, 10, 2)), "widen",
                           step_months=1) == (date(2026, 2, 3), date(2026, 3, 3))
    # the later side: one month on from covered.end
    assert sl.fetch_window(m, (date(2026, 3, 3), date(2026, 12, 2)), "widen",
                           step_months=1) == (date(2026, 10, 2), date(2026, 11, 2))
    # a step wider than what is missing does not overshoot the wanted window
    assert sl.fetch_window(m, (date(2026, 2, 20), date(2026, 10, 2)), "widen",
                           step_months=1) == (date(2026, 2, 20), date(2026, 3, 3))
    # without a step the whole side is fetched at once (the foreground path)
    assert sl.fetch_window(m, (date(2026, 1, 3), date(2026, 10, 2)),
                           "widen") == (date(2026, 1, 3), date(2026, 3, 3))


def test_the_tail_and_rebuild_windows_come_straight_from_scene_query():
    m = _manifest(date(2026, 3, 3), date(2026, 10, 2), age_hours=30)
    want = (date(2026, 3, 3), date(2026, 10, 2))
    assert sl.fetch_window(m, want, "tail") == (date(2026, 9, 22), date(2026, 10, 2))
    assert sl.fetch_window(None, want, "rebuild") == want
    assert sl.fetch_window(m, want, "rebuild") == want


# ------------------------------------------------------------------- the leaf
def _render(state, gee):
    @reacton.component
    def Page():
        NotificationProvider()
        sl.SceneIndexLoader(app_state=state, gee_interface=gee)

    return reacton.render(Page(), handle_error=False)


async def _settle(predicate, ticks=300):
    for _ in range(ticks):
        await asyncio.sleep(0.01)
        if predicate():
            return True
    return False


def _toasts(kind):
    return [x.message for x in get_current_bus().toasts.value if x.type.value == kind]


@pytest.fixture()
def no_planet(monkeypatch):
    """Planet off: NICFI is licence-gated and every test but the two about it
    would otherwise have to answer a second request."""
    monkeypatch.setattr(sl, "_planet_chunk", lambda *a, **k: None)


@pytest.fixture()
def local_aoi(monkeypatch):
    monkeypatch.setattr(sl.aoi_io, "ee_aoi", lambda result, pdir: "AOI")


def test_an_empty_project_fetches_the_index_and_caches_it(data_root, monkeypatch, no_planet, local_aoi):
    asked = []

    async def get_info_async(obj):
        asked.append(obj)
        return RAW

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: ("SCENES", s, e))
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)  # no widening to wait on
    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            # the whole window, not the first chunk of it: the index is
            # published as each chunk lands, so the settle has to wait for
            # the coverage to reach the far end
            assert await _settle(lambda: state.scene_manifest.value is not None
                                 and state.scene_manifest.value.covered.start == date(2026, 3, 3))
            assert len(state.scene_index.value.scenes) == 1
            assert state.scene_index_error.value is None
            assert state.scene_index_pending.value is False
            # The spec's payload mitigation (§5.2, §9, §11): the foreground
            # window is fetched a month at a time, anchored at the recent end
            # so the first chunk defines the coverage and every later one
            # abuts it walking backwards. Seven requests, each small, and a
            # failure part-way leaves the ones that landed on disk.
            assert asked == [("SCENES", date(2026, 9, 2), date(2026, 10, 2)),
                             ("SCENES", date(2026, 8, 2), date(2026, 9, 2)),
                             ("SCENES", date(2026, 7, 2), date(2026, 8, 2)),
                             ("SCENES", date(2026, 6, 2), date(2026, 7, 2)),
                             ("SCENES", date(2026, 5, 2), date(2026, 6, 2)),
                             ("SCENES", date(2026, 4, 2), date(2026, 5, 2)),
                             ("SCENES", date(2026, 3, 3), date(2026, 4, 2))]
            _, manifest = scene_cache.read_index(paths.project_dir("sl"))
            assert manifest is not None  # it went to disk, so the next session is free
            # and the manifest records them, in the order they landed
            assert [(c.start, c.end, c.ok) for c in manifest.chunks] == [
                (s, e, True) for _, s, e in asked]
            # A project that never had an index is not "rebuilding" one: that
            # word is reserved for a cache that was there and unreadable.
            assert _toasts("info") == [] and _toasts("error") == []
        finally:
            rc.close()

    asyncio.run(main())


def test_a_fresh_cached_index_is_served_without_any_request(data_root, monkeypatch, local_aoi):
    pdir = paths.project_dir("sl")
    scene_cache.store_chunk(
        pdir, scene_index.parse_scenes(RAW), (date(2026, 3, 1), date(2026, 11, 1)), "d1",
        now=datetime.now(timezone.utc),
    )
    asked = []

    async def get_info_async(obj):
        asked.append(obj)
        return RAW

    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)
    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: state.scene_index.value is not None)
            await asyncio.sleep(0.2)
            assert asked == []
        finally:
            rc.close()

    asyncio.run(main())


def test_a_failed_tail_refresh_keeps_the_stale_index_and_reports_the_failure(
        data_root, monkeypatch, local_aoi):
    """Review Focus #2: a 24h-stale index whose refetch fails must still answer.

    Losing a working timeline because a *refresh* of it failed is the worst
    outcome available here, and three plausible implementations produce it: one
    that publishes ``None`` from the error handler, one that clears the
    reactive before fetching, and one that lets the exception escape so
    ``use_task`` is left holding the state. The first two fail the
    ``scene_index.value is not None`` assertions below (the second fails them
    even before the error appears); the third never reaches ``_settle``'s
    predicate, so the first assertion fails on the timeout.
    """
    pdir = paths.project_dir("sl")
    scene_cache.store_chunk(
        pdir, scene_index.parse_scenes(RAW), (date(2026, 3, 1), date(2026, 11, 1)), "d1",
        now=datetime.now(timezone.utc) - timedelta(hours=30),
    )

    async def get_info_async(obj):
        raise RuntimeError("Earth Engine unavailable")

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: "SCENES")
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)
    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: state.scene_index_error.value is not None)
            assert state.scene_index.value is not None
            assert len(state.scene_index.value.scenes) == 1
            assert state.scene_index.value.scenes["key"].tolist() == ["COPERNICUS/S2_HARMONIZED/A"]
            assert state.scene_manifest.value.refreshed < datetime.now(timezone.utc) - timedelta(hours=24)
            assert state.scene_index_pending.value is False
            assert "Earth Engine unavailable" in state.scene_index_error.value
            assert any("Earth Engine unavailable" in m for m in _toasts("error"))
            # ... and it is still there a moment later: an implementation that
            # cleared the index *after* publishing the error would pass the
            # assertions above and fail these.
            await asyncio.sleep(0.25)
            assert state.scene_index.value is not None and len(state.scene_index.value.scenes) == 1
            assert state.scene_index_error.value is not None
        finally:
            rc.close()

    asyncio.run(main())


def test_the_retry_counter_refreshes_again_and_clears_the_error(data_root, monkeypatch, no_planet, local_aoi):
    """The timeline's "Try again" bumps ``scene_index_retry``."""
    pdir = paths.project_dir("sl")
    scene_cache.store_chunk(
        pdir, scene_index.parse_scenes(RAW), (date(2026, 3, 1), date(2026, 11, 1)), "d1",
        now=datetime.now(timezone.utc) - timedelta(hours=30),
    )
    calls = []

    async def get_info_async(obj):
        calls.append(obj)
        if len(calls) == 1:
            raise RuntimeError("Earth Engine unavailable")
        return RAW

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: "SCENES")
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)
    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: state.scene_index_error.value is not None)
            state.scene_index_retry.set(state.scene_index_retry.value + 1)
            assert await _settle(lambda: state.scene_index_error.value is None)
            assert len(calls) == 2
            assert state.scene_manifest.value.refreshed > datetime.now(timezone.utc) - timedelta(minutes=1)
        finally:
            rc.close()

    asyncio.run(main())


def test_an_unreadable_index_is_rebuilt_and_the_user_is_told(data_root, monkeypatch, no_planet, local_aoi):
    """``read_index`` answers a corrupt or wrong-version cache as a cache miss
    and says nothing; the refetch it implies is announced from here."""
    pdir = paths.project_dir("sl")
    scene_cache.store_chunk(
        pdir, scene_index.parse_scenes(RAW), (date(2026, 3, 1), date(2026, 11, 1)), "d1",
        now=datetime.now(timezone.utc),
    )
    paths.scenes_path(pdir).write_bytes(b"not a parquet file at all")

    async def get_info_async(obj):
        return RAW

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: "SCENES")
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)
    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: state.scene_manifest.value is not None
                                 and state.scene_manifest.value.covered.start == date(2026, 3, 3))
            assert _toasts("info") == [t("imagery.index_rebuilding")]
            assert state.scene_index_error.value is None
        finally:
            rc.close()

    asyncio.run(main())


def test_a_widen_request_beyond_the_coverage_is_fetched_in_the_foreground(
        data_root, monkeypatch, no_planet, local_aoi):
    """The ``+1 month`` buttons publish a window. Inside the coverage it costs
    nothing; outside it the loader has to actually go and get the month, or the
    reviewer presses the button and watches an empty month appear."""
    pdir = paths.project_dir("sl")
    scene_cache.store_chunk(
        pdir, scene_index.parse_scenes(RAW), (date(2026, 3, 3), date(2026, 10, 2)), "d1",
        now=datetime.now(timezone.utc),
    )
    asked = []

    async def get_info_async(obj):
        asked.append(obj)
        return RAW

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: (s, e))
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)
    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: state.scene_index.value is not None)
            assert asked == []  # the cache already answers the default view
            state.scene_window_request.set((date(2026, 1, 3), date(2026, 10, 2)))
            assert await _settle(lambda: state.scene_manifest.value.covered.start == date(2026, 1, 3))
            # one side, anchored, and a month at a time
            assert asked == [(date(2026, 2, 3), date(2026, 3, 3)),
                             (date(2026, 1, 3), date(2026, 2, 3))]
        finally:
            rc.close()

    asyncio.run(main())


def test_an_earth_engine_aoi_waits_for_the_picker_instead_of_failing(
        data_root, monkeypatch, no_planet):
    """``AppState._install`` clears ``aoi_result``, and the AOI step republishes
    it a moment later. An asset or admin AOI has no sidecar to fall back on, so
    a loader that fetched in that gap would raise once and never retry."""
    calls = []

    async def get_info_async(obj):
        calls.append(obj)
        return RAW

    def ee_aoi(result, pdir):
        if result is None:
            raise RuntimeError("no AOI geometry available")
        return "AOI"

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: (s, e))
    monkeypatch.setattr(sl.aoi_io, "ee_aoi", ee_aoi)
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)
    state = AppState()
    p = _with_period(_p(digest="asset:users/x/aoi"))
    p.aoi.geometry_file = None
    state.load_project_state(p)

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            await asyncio.sleep(0.3)
            assert calls == [] and state.scene_index_error.value is None  # it waited
            state.aoi_result.set(SimpleNamespace(   # what aoi_io.lazy_digest reads
                method="ASSET", admin=None,
                spec=SimpleNamespace(asset_id="users/x/aoi", column=None, value=None)))
            assert await _settle(lambda: state.scene_index.value is not None)
            assert state.scene_index_error.value is None
        finally:
            rc.close()

    asyncio.run(main())


def test_the_widener_extends_the_coverage_a_month_at_a_time(data_root, monkeypatch, no_planet, local_aoi):
    windows = []

    async def get_info_async(obj):
        windows.append(obj)
        return RAW

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: (s, e))
    monkeypatch.setattr(sl, "FOREGROUND_MONTHS", 2)
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 4)
    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: state.scene_manifest.value is not None
                                 and state.scene_manifest.value.covered.start == date(2026, 1, 3))
            assert state.scene_manifest.value.covered.end == date(2026, 10, 2)  # clamped at today
            assert len(windows) > 1  # the foreground window, then the widening chunks
            assert all((e - s).days <= 32 for s, e in windows[1:])
            # Every chunk touches what was already covered, so the manifest's
            # min-start/max-end never spans a month nothing fetched.
            spans = sorted((s, e) for s, e in windows)
            for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
                assert next_start <= prev_end
        finally:
            rc.close()

    asyncio.run(main())


def test_every_stored_window_touches_the_coverage_it_merges_into(
        data_root, monkeypatch, no_planet, local_aoi):
    """Requirement 1, checked where it actually matters.

    ``store_chunk`` records coverage as min-of-starts to max-of-ends over a
    single ``Coverage`` interval, so a window that neither overlaps nor abuts
    what is on disk makes the manifest claim dates nothing ever fetched --
    months that then draw empty for ever, because ``needs_refresh`` answers
    ``""`` for them. The spy reads the coverage from disk at the moment of each
    call, so it catches a widening step taken from the far end of a missing
    side (the one plausible way this logic breaks) rather than trusting the
    loader's own bookkeeping.
    """
    seen = []
    real = scene_cache.store_chunk

    def spy(pdir, tables, window, digest, **kwargs):
        _, before = scene_cache.read_index(pdir)
        live = None if kwargs.get("replace") or before is None or before.aoi_digest != digest else before.covered
        seen.append((live, window))
        return real(pdir, tables, window, digest, **kwargs)

    async def get_info_async(obj):
        return RAW

    monkeypatch.setattr(sl.scene_cache, "store_chunk", spy)
    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: (s, e))
    monkeypatch.setattr(sl, "FOREGROUND_MONTHS", 2)
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 6)
    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: state.scene_manifest.value is not None
                                 and state.scene_manifest.value.covered.start == date(2025, 11, 3))
        finally:
            rc.close()

    asyncio.run(main())

    day = timedelta(days=1)
    gaps = [(covered, window) for covered, window in seen
            if covered is not None and (window[0] - covered.end > day or covered.start - window[1] > day)]
    assert gaps == [], f"windows that would make the manifest claim unfetched dates: {gaps}"
    # not vacuous: the foreground fetch plus the four monthly widening chunks
    assert len(seen) >= 4 and sum(1 for covered, _ in seen if covered is not None) >= 3


def test_a_widen_window_pushed_off_the_coverage_edge_is_snapped_back(
        data_root, monkeypatch, no_planet, local_aoi):
    """Requirement 1 again, with the threat injected.

    The test above proves the windows this loader computes today are
    contiguous; this one proves the *guard* is what keeps them so. With
    ``scene_query.refresh_window`` returning a widen window pushed ten days
    away from the coverage edge -- the way another window source (a per-tile
    refetch, a "jump to this date") could get it wrong -- nothing that reaches
    ``store_chunk`` may still leave a gap. Neutralise ``contiguous`` and the
    pairs this spy records go non-contiguous and the assertion fails.
    """
    seen = []
    real_store = scene_cache.store_chunk
    real_refresh = sl.scene_query.refresh_window
    off = timedelta(days=10)

    def off_edge(manifest, want, mode):
        start, end = real_refresh(manifest, want, mode)
        if mode != "widen" or manifest is None:
            return (start, end)
        # push the near edge away from what is covered, in whichever
        # direction this side is being widened
        return (start + off, end) if start >= manifest.covered.end else (start, end - off)

    def spy(pdir, tables, window, digest, **kwargs):
        _, before = scene_cache.read_index(pdir)
        live = None if kwargs.get("replace") or before is None or before.aoi_digest != digest else before.covered
        seen.append((live, window))
        return real_store(pdir, tables, window, digest, **kwargs)

    async def get_info_async(obj):
        return RAW

    monkeypatch.setattr(sl.scene_query, "refresh_window", off_edge)
    monkeypatch.setattr(sl.scene_cache, "store_chunk", spy)
    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: (s, e))
    monkeypatch.setattr(sl, "FOREGROUND_MONTHS", 2)
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 6)
    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: state.scene_manifest.value is not None
                                 and state.scene_manifest.value.covered.start == date(2025, 11, 3))
        finally:
            rc.close()

    asyncio.run(main())

    day = timedelta(days=1)
    gaps = [(covered, window) for covered, window in seen
            if covered is not None and (window[0] - covered.end > day or covered.start - window[1] > day)]
    assert gaps == [], f"an off-edge window reached store_chunk unsnapped: {gaps}"
    # not vacuous: at least one widening chunk went through off_edge
    assert sum(1 for covered, _ in seen if covered is not None) >= 2


def test_the_pending_flag_is_raised_only_while_the_foreground_fetch_runs(
        data_root, monkeypatch, no_planet, local_aoi):
    """Tasks 10-12 render the first-load line from ``scene_index_pending``, and
    every other test here only ever asserts it ``False`` -- so an
    implementation that never raised it would pass them all. Each request waits
    on an event the test sets, so the flag is observed while the fetch is
    genuinely in flight, with no sleeping and no reliance on an interleaving
    happening to occur.

    The background buffer has no flag of its own (review m2: the one it used to
    write was read by nothing), so it is observed here through the chunks it
    asks for -- and the pending flag has to stay *down* while it runs, because
    the months the reviewer is looking at have already landed.
    """
    foreground, widening = asyncio.Event(), asyncio.Event()
    calls = []

    async def get_info_async(obj):
        calls.append(obj)
        if len(calls) == 1:
            await foreground.wait()  # the first foreground chunk
        elif obj[0] < date(2026, 3, 3):
            # The foreground window is fetched in month chunks, so the
            # background buffer cannot be told from it by a call count any
            # more: it is the chunks that start before the foreground's own
            # start. Those are the widener's.
            await widening.wait()
        return RAW

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: (s, e))
    monkeypatch.setattr(sl, "FOREGROUND_MONTHS", 2)
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 4)
    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: len(calls) == 1)
            assert state.scene_index_pending.value is True
            foreground.set()
            # The buffer's own chunks start before the foreground window does.
            assert await _settle(lambda: any(obj[0] < date(2026, 3, 3) for obj in calls))
            assert state.scene_index_pending.value is False  # the foreground is done
            widening.set()
            assert await _settle(lambda: state.scene_manifest.value is not None
                                 and state.scene_manifest.value.covered.start == date(2026, 1, 3))
            assert state.scene_index_pending.value is False
        finally:
            rc.close()

    asyncio.run(main())


def test_a_changed_aoi_rebuilds_the_index_rather_than_merging_into_it(
        data_root, monkeypatch, no_planet, local_aoi):
    pdir = paths.project_dir("sl")
    scene_cache.store_chunk(
        pdir, scene_index.parse_scenes(RAW), (date(2026, 3, 1), date(2026, 11, 1)), "OLD",
        now=datetime.now(timezone.utc),
    )

    async def get_info_async(obj):
        return EMPTY_RAW

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: "SCENES")
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)
    state = AppState()
    state.load_project_state(_with_period(_p(digest="d1")))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            # the first chunk already carries the new digest, so the settle
            # has to wait for the whole window to be covered
            assert await _settle(lambda: state.scene_manifest.value is not None
                                 and state.scene_manifest.value.aoi_digest == "d1"
                                 and state.scene_manifest.value.covered.start == date(2026, 3, 3))
            assert len(state.scene_index.value.scenes) == 0
            assert state.scene_manifest.value.covered == _covered(date(2026, 3, 3), date(2026, 10, 2))
        finally:
            rc.close()

    asyncio.run(main())


# ------------------------------------------------------------- Planet NICFI
def test_the_planet_basemaps_join_the_index_and_their_access_is_recorded(
        data_root, monkeypatch, local_aoi):
    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: "SCENES")
    monkeypatch.setattr(sl.scene_index, "planet_request", lambda cid, s, e: ("PLANET", cid))
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)

    async def get_info_async(obj):
        return PLANET_RAW if obj[0] == "PLANET" else RAW

    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: state.scene_index.value is not None)
            scenes = state.scene_index.value.scenes
            assert sorted(scenes["source"]) == [PLANET, S2]
            planet = state.scene_manifest.value.planet
            assert planet.region == "americas" and planet.access is True
        finally:
            rc.close()

    asyncio.run(main())


def test_no_planet_licence_costs_nothing_but_the_planet_rows(
        data_root, monkeypatch, local_aoi, caplog):
    """NICFI is licence-gated, so no access is an ordinary outcome: it is
    recorded for the toolbar, must not cost the Sentinel-2 rows -- and must
    stay quiet. An unlicensed project meets this on every refresh, so a
    traceback here would be noise, which is why only this answer takes the
    INFO path."""
    import ee

    refusal = ee.EEException(
        "ImageCollection.load: ImageCollection asset "
        "'projects/planet-nicfi/assets/basemaps/americas' not found "
        "(does not exist or caller does not have access).")
    assert ee_no_access(refusal) is True  # this is the branch under test

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: "SCENES")
    monkeypatch.setattr(sl.scene_index, "planet_request", lambda cid, s, e: ("PLANET", cid))
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)

    async def get_info_async(obj):
        if obj[0] == "PLANET":
            raise refusal
        return RAW

    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        with caplog.at_level(logging.INFO, logger="SEPALUI.deforestation_alerts.scene_loader"):
            box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
            try:
                assert await _settle(lambda: state.scene_index.value is not None)
                assert state.scene_index.value.scenes["source"].tolist() == [S2]
                assert state.scene_index_error.value is None  # not a failure of the index
                planet = state.scene_manifest.value.planet
                assert planet.region == "americas" and planet.access is False
            finally:
                rc.close()
        assert [r for r in caplog.records if "Planet" in r.getMessage()
                and r.levelno == logging.INFO]
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

    asyncio.run(main())


def test_an_earth_engine_failure_that_is_not_a_refusal_records_no_access_state(
        data_root, monkeypatch, local_aoi, caplog):
    """An expired token, a quota or a dropped connection is not a licence
    answer. Recording ``access=False`` for one would make a fixable fault look
    like a permanent licence state and -- as the INFO line reaches no handler
    in production -- leave nothing behind to find it by. The exception here is
    an ``ee.EEException`` whose text is *not* Earth Engine's refusal wording,
    so only ``ee_no_access``'s message test can tell it apart, and the
    assertion below pins that rather than assuming it.
    """
    import ee

    outage = ee.EEException("Request had invalid authentication credentials: access token expired.")
    assert ee_no_access(outage) is False

    async def get_info_async(obj):
        if obj[0] == "PLANET":
            raise outage
        return RAW

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: "SCENES")
    monkeypatch.setattr(sl.scene_index, "planet_request", lambda cid, s, e: ("PLANET", cid))
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)
    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        with caplog.at_level(logging.INFO, logger="SEPALUI.deforestation_alerts.scene_loader"):
            box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
            try:
                assert await _settle(lambda: state.scene_index.value is not None)
                assert state.scene_index.value.scenes["source"].tolist() == [S2]
                assert state.scene_index_error.value is None  # the index itself is fine
                # nothing recorded: the licence was never answered for
                assert state.scene_manifest.value.planet.region is None
                assert _toasts("error") == [] and _toasts("info") == []
            finally:
                rc.close()
        warnings = [r for r in caplog.records
                    if r.levelno >= logging.WARNING and "Planet" in r.getMessage()]
        # One per chunk, and the index is fetched a month at a time, so the
        # count is not what is being asserted: it is that every one of them
        # carries its traceback and the real reason.
        assert warnings and all(r.exc_info is not None for r in warnings)
        assert all("access token expired" in r.getMessage() for r in warnings)

    asyncio.run(main())


def test_a_planet_request_that_cannot_even_be_built_costs_nothing(data_root, monkeypatch, local_aoi):
    """The NICFI request is built on the loop, after the scene rows are already
    in hand. A failure there must not discard them -- and, unlike a request
    Earth Engine refused, it says nothing about the licence, so it must not
    record a no-access state either."""
    def boom(project, window):
        raise RuntimeError("Earth Engine client library not initialized")

    async def get_info_async(obj):
        return RAW

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: "SCENES")
    monkeypatch.setattr(sl, "_planet_chunk", boom)
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)
    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: state.scene_index.value is not None)
            assert state.scene_index.value.scenes["source"].tolist() == [S2]
            assert state.scene_index_error.value is None
            assert state.scene_manifest.value.planet.region is None
            assert state.scene_manifest.value.planet.access is False
        finally:
            rc.close()

    asyncio.run(main())


def test_a_planet_answer_that_cannot_be_parsed_is_a_defect_not_a_licence_refusal(
        data_root, monkeypatch, local_aoi, caplog):
    """Earth Engine answered, so the licence is there; ``parse_planet`` failing
    on the answer is a bug on our side. Recording "no Planet access" for it
    would hide that bug behind a plausible-looking outcome and leave nothing in
    the server log, so this path records nothing and warns with the traceback.
    """
    def boom(raw):
        raise KeyError("millis")

    async def get_info_async(obj):
        return PLANET_RAW if obj[0] == "PLANET" else RAW

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: "SCENES")
    monkeypatch.setattr(sl.scene_index, "planet_request", lambda cid, s, e: ("PLANET", cid))
    monkeypatch.setattr(sl.scene_index, "parse_planet", boom)
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)
    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        with caplog.at_level(logging.INFO, logger="SEPALUI.deforestation_alerts.scene_loader"):
            box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
            try:
                assert await _settle(lambda: state.scene_index.value is not None)
                assert state.scene_index.value.scenes["source"].tolist() == [S2]
                assert state.scene_index_error.value is None  # the index itself is fine
                # nothing recorded: the answer said access exists, and this
                # failure says nothing about the licence either way
                assert state.scene_manifest.value.planet.region is None
                # the reviewer is told nothing: the months they asked for are
                # all there, and a defect of ours is not theirs to act on
                assert _toasts("error") == [] and _toasts("info") == []
            finally:
                rc.close()
        warnings = [r for r in caplog.records
                    if r.levelno >= logging.WARNING and "Planet" in r.getMessage()]
        # one per chunk; the traceback on each is the assertion
        assert warnings and all(r.exc_info is not None for r in warnings)

    asyncio.run(main())


def test_a_widening_chunk_does_not_erase_what_planet_already_answered(
        data_root, monkeypatch, no_planet, local_aoi):
    """``store_chunk`` keeps the manifest's ``PlanetState`` when a chunk passes
    ``planet=None``. A widening chunk asks NICFI nothing, so reporting a
    no-access state for it would switch the toolbar's Planet toggle off right
    after the first background month landed."""
    pdir = paths.project_dir("sl")
    scene_cache.store_chunk(
        pdir, scene_index.parse_scenes(RAW), (date(2026, 3, 3), date(2026, 10, 2)), "d1",
        now=datetime.now(timezone.utc),
        planet=scene_index.PlanetState(region="americas", access=True),
    )

    async def get_info_async(obj):
        return RAW

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: (s, e))
    monkeypatch.setattr(sl, "FOREGROUND_MONTHS", 2)
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 4)
    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: state.scene_manifest.value is not None
                                 and state.scene_manifest.value.covered.start == date(2026, 1, 3))
            planet = state.scene_manifest.value.planet
            assert planet.access is True and planet.region == "americas"
        finally:
            rc.close()

    asyncio.run(main())


# ----------------------------------------------------------------- the wiring
def test_opening_another_project_drops_the_previous_projects_index(data_root):
    """The index is per project and per AOI: a switch must not leave the
    previous project's granules answering the new project's alerts."""
    state = AppState()
    state.scene_index.set("TABLES")
    state.scene_manifest.set("MANIFEST")
    state.scene_index_error.set("boom")
    state.scene_window_request.set((date(2026, 1, 1), date(2026, 2, 1)))
    state.load_project_state(_with_period(_p(name="other")))
    assert state.scene_index.value is None and state.scene_manifest.value is None
    assert state.scene_index_error.value is None and state.scene_window_request.value is None


def test_the_shell_mounts_the_loader_beside_the_remote_facts_one():
    """App level, not inside a step: the index survives every navigation."""
    shell = (ROOT / "gui" / "solara_app.py").read_text()
    assert "from gui.scripts.scene_loader import SceneIndexLoader" in shell
    assert "SceneIndexLoader(app_state=app_state, gee_interface=gee_interface)" in shell
    assert shell.index("RemoteFactsLoader(app_state=") < shell.index("SceneIndexLoader(app_state=")


def test_a_reply_for_a_replaced_aoi_is_neither_stored_nor_published(
        data_root, monkeypatch, no_planet, local_aoi):
    """``_still_current`` guards the window cancellation cannot reach.

    A dependency change cancels ``_load`` at its next await -- but not every
    AOI change is a dependency change at the moment it happens: ``AoiRecord``
    is mutated on the open ``Project`` before anything republishes, and
    ``to_thread_in_context`` is not cancellable once entered. The two
    ``_still_current`` calls in ``_fetch`` straddle exactly that window, so
    this test does what cancellation cannot do for it -- it holds the Earth
    Engine reply, moves the digest under the in-flight fetch *without* a
    publish, and only then lets the reply land. ``store_chunk`` compares
    digests and would take the late chunk for the newer truth, so the stored
    digest is the assertion.
    """
    held = asyncio.Event()
    stored = []
    real = scene_cache.store_chunk

    def spy(pdir, tables, window, digest, **kwargs):
        stored.append(digest)
        return real(pdir, tables, window, digest, **kwargs)

    async def get_info_async(obj):
        await held.wait()
        return RAW

    monkeypatch.setattr(sl.scene_cache, "store_chunk", spy)
    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: "SCENES")
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)  # no widening to wait on
    state = AppState()
    state.load_project_state(_with_period(_p(digest="d1")))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: state.scene_index_pending.value is True)
            # the AOI is redrawn while the reply is in flight; mutating the
            # record publishes nothing, so nothing re-renders and nothing is
            # cancelled -- which is the whole point of the guard
            state.project.value.aoi.digest = "d2"
            held.set()
            assert await _settle(lambda: state.scene_index_pending.value is False)
            assert stored == [], f"the replaced AOI's index was written as {stored}"
            assert state.scene_index.value is None and state.scene_manifest.value is None
            _, on_disk = scene_cache.read_index(paths.project_dir("sl"))
            assert on_disk is None
            # a reply dropped because its AOI is gone is not a refresh failure
            assert state.scene_index_error.value is None and _toasts("error") == []
        finally:
            rc.close()

    asyncio.run(main())


def test_a_chunk_that_fails_part_way_leaves_the_chunks_that_landed(
        data_root, monkeypatch, no_planet, local_aoi):
    """Review I1: the spec's payload mitigation (§5.2, §9, §11).

    Before it, the foreground fetched its whole span in one ``getInfo`` and
    there was no partial result: a payload too large meant the index never
    built and "Try again" repeated the same oversized call. Now the months
    that did come back are on disk and published, the failure is still
    reported, and the manifest records which window did not land -- so an
    empty month reads as "never successfully asked" rather than "no images".
    """
    asked = []

    async def get_info_async(obj):
        asked.append(obj)
        if len(asked) == 3:
            raise RuntimeError("Request payload size exceeds the limit")
        return RAW

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: (s, e))
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)  # no widening to wait on
    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: state.scene_index_error.value is not None)
            # the two months that landed are published and on disk
            assert state.scene_manifest.value.covered == _covered(date(2026, 8, 2), date(2026, 10, 2))
            assert len(state.scene_index.value.scenes) == 1
            _, on_disk = scene_cache.read_index(paths.project_dir("sl"))
            assert on_disk.covered == state.scene_manifest.value.covered
            # the third chunk is recorded as the one that did not land
            assert [(c.start, c.end, c.ok) for c in on_disk.chunks] == [
                (date(2026, 9, 2), date(2026, 10, 2), True),
                (date(2026, 8, 2), date(2026, 9, 2), True),
                (date(2026, 7, 2), date(2026, 8, 2), False)]
            # and the reviewer is told, once
            assert _toasts("error") == [t("imagery.index_failed",
                                          error="Request payload size exceeds the limit")]
            assert state.scene_index_pending.value is False
        finally:
            rc.close()

    asyncio.run(main())


def test_a_drawing_failure_is_not_reported_as_a_failed_index_refresh(
        data_root, monkeypatch, no_planet, local_aoi, caplog):
    """Review m1. ``reactive.set`` runs its subscribers synchronously and
    ``timeline.render`` is subscribed to four of this component's reactives,
    so an exception raised while drawing used to come back up through
    ``set()`` into ``_load``'s ``except``: the reviewer was told the index
    refresh had failed when it had succeeded, "Try again" retried a fetch that
    was never the problem, and the publish after the failed one never ran, so
    ``scene_index`` and ``scene_manifest`` disagreed about what is on disk.
    """
    async def get_info_async(obj):
        return RAW

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: "SCENES")
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)
    state = AppState()
    state.load_project_state(_with_period(_p()))
    # a subscriber that cannot draw what it is given
    state.scene_index.subscribe(lambda _tables: (_ for _ in ()).throw(KeyError("Sentinel 3")))

    async def main():
        with caplog.at_level(logging.ERROR, logger="SEPALUI.deforestation_alerts.scene_loader"):
            box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
            try:
                assert await _settle(lambda: state.scene_manifest.value is not None
                                     and state.scene_index_pending.value is False)
                # the manifest is published even though the index publish before
                # it raised: the two no longer disagree about what is on disk
                assert state.scene_manifest.value.covered == _covered(date(2026, 3, 3),
                                                                      date(2026, 10, 2))
                # and it is NOT an index-refresh failure: no "Try again" state,
                # and the message names the drawing rather than the refresh
                assert state.scene_index_error.value is None
                # one message, not one per chunk: the bus collapses a toast
                # identical to the one already up
                assert _toasts("error") == [t("imagery.index_draw_failed", error="'Sentinel 3'")]
            finally:
                rc.close()
        drawn = [r for r in caplog.records if "could not be drawn" in r.getMessage()]
        assert drawn and all(r.exc_info is not None for r in drawn)

    asyncio.run(main())


def test_a_fetch_failure_is_still_reported_as_a_failed_index_refresh(
        data_root, monkeypatch, no_planet, local_aoi):
    """The other half of the pair from review m1: the two failure modes have
    to be distinguishable, so this is the one that really is a refresh
    failure and must keep saying so."""
    async def get_info_async(obj):
        raise RuntimeError("Earth Engine is unreachable")

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: "SCENES")
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)
    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: state.scene_index_error.value is not None)
            assert state.scene_index_error.value == "Earth Engine is unreachable"
            assert _toasts("error") == [t("imagery.index_failed", error="Earth Engine is unreachable")]
            assert state.scene_index_pending.value is False
        finally:
            rc.close()

    asyncio.run(main())


def test_waiting_for_the_aoi_clears_an_error_left_by_the_run_before(
        data_root, monkeypatch, no_planet):
    """Review m4. The ``origin is None`` branch is a run that did not fail --
    it is waiting for the AOI step to replay the selection -- so an error left
    behind by the run before it must not stay on screen labelling the index as
    old and offering a "Try again" with nothing yet to retry."""
    def ee_aoi(result, pdir):
        if result is None:
            raise RuntimeError("no AOI geometry available")
        return "AOI"

    async def get_info_async(obj):
        return RAW

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: (s, e))
    monkeypatch.setattr(sl.aoi_io, "ee_aoi", ee_aoi)
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)
    state = AppState()
    p = _with_period(_p(digest="asset:users/x/aoi"))
    p.aoi.geometry_file = None
    state.load_project_state(p)
    state.scene_index_error.set("Earth Engine is unreachable")  # the last run's failure

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: state.scene_index_error.value is None)
            assert state.scene_index.value is None  # still waiting, not fetched
        finally:
            rc.close()

    asyncio.run(main())
