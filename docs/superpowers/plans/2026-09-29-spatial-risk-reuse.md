# Spatial-risk reuse: GEE download progress + pysepal job notifications — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port spatial_risk's hardened Earth Engine download (bounds-only export region, queued notice, tile-level progress) and its pysepal job-notification bridge (`tracked_job` + log→milestone forwarding) into the deforestation alerts app, and wire them into every download/job site.

**Architecture:** Two Solara-free modules are ported near-verbatim from spatial_risk: `deforestation_alerts/scripts/gee_progress.py` (geedim tqdm hook + queued-wait callback, thread-local) and `gui/scripts/notify_bridge.py` (thread-scoped log→`task.step` forwarding, `tracked_job`, a whole-percent tile-progress reporter). `download_ee_image` gains the bounds/clip rule and the queued notice. The Filter "Analyze" task and the raw-layout workers (recipe, overview export, analysis save/download, CNN) then use them, so every long job reports through pysepal's standard task pill with milestones and exactly one error toast.

**Tech Stack:** Python 3.11, pysepal 4.1.0 (stock — no fork features), solara 1.62.0, geedim 2.0.0, earthengine-api, pytest.

**Spec:** none (requirements agreed in conversation 2026-09-29; reference implementation = spatial_risk branch `feat/pysepal-v4`, worktree `/home/jserafini/Desktop/projects/spatial_risk/spatial-risk-module/.claude/worktrees/pysepal-v4`, referred to below as `$SR`).

## Global Constraints

- Stock `pysepal==4.1.0`: `TaskTracker.set_progress(value)` has NO `detail` kwarg; never call it with one.
- App logger root name is `"SEPALUI.deforestation_alerts"` (every module logger lives under it).
- Run tests with `~/micromamba/envs/defo-alerts/bin/python -m pytest -q -p no:cacheprovider` from `deforestation-alerts-module/`. Baseline: 293 passed.
- Scripts under `deforestation_alerts/` and `gui/scripts/` must not import solara/ipyvuetify at module level (Solara-free helpers); lazy-import inside functions where needed.
- Any new user-visible string goes into ALL THREE catalogs: `deforestation_alerts/message/{en,es-ES,fr}/ui.json` (read through `cm.*`).
- `ERROR_TOAST_TIMEOUT = 10.0` seconds for job-failure toasts; successes keep pysepal's default.
- Exactly one error toast per failed job: call `task.fail(message)` BEFORE publishing the error toast (pysepal's `__exit__` only adds its own toast when the task is not already FAILED).
- `asyncio.CancelledError` is never reported as a failure (re-raise untouched; pysepal marks the task CANCELLED).
- Do not add `Co-Authored-By` or any Claude attribution to commit messages.
- Commit on the current branch `feat/pysepal-v4`; never push.

## Review Focus

1. **Heavy admin AOI (a FeatureCollection with a very large geometry):** the export region sent to geedim must be `geometry().bounds()`, and the clip must use the collection object itself — never `fc.geometry()` — or Earth Engine answers "Description length exceeds maximum". Pinned in Task 1 (`test_collection_region_clips_with_collection_and_exports_its_bounds`) and Task 3 (`test_download_alert_raster_passes_the_collection_not_its_geometry`).
2. **Two downloads at once (Analyze while an Analysis export runs):** the second waits, says "queued" once, and both succeed; a wedged first download still turns into a `TimeoutError` after 30 min. Pinned in Task 1 (`test_wait_notice_fires_only_for_the_download_that_queues`, `test_lock_timeout_still_raises`).
3. **A job fails:** exactly one error toast (10 s), task FAILED with the same message. Pinned in Task 2 (`test_failure_publishes_exactly_one_error_toast`) and Task 4 (per-site feedback tests).
4. **User cancels Analyze mid-download:** the abandoned download thread keeps ticking progress into a finished tracker — must be a silent no-op, never an exception. Pinned in Task 2 (`test_tile_reporter_after_task_finished_is_a_noop`).
5. **No NotificationProvider (pytest, scripts):** `tracked_job(None, ...)` and `current_notifier()` must fall back to a no-op notifier, never raise. Pinned in Task 2 (`test_no_provider_falls_back_to_noop`).

## Execution table

| Task | Title | Model |
|---|---|---|
| 1 | geedim progress hook + hardened `download_ee_image` | sonnet |
| 2 | `notify_bridge` + `to_thread_in_context` | sonnet |
| 3 | Filter Analyze: tile progress, queued notice, log milestones | sonnet |
| 4 | Raw-layout workers onto `tracked_job` (+ composite download progress) | opus |

---

### Task 1: geedim progress hook + hardened `download_ee_image`

**Files:**
- Create: `deforestation_alerts/scripts/gee_progress.py`
- Modify: `deforestation_alerts/scripts/raster_export.py` (whole file)
- Modify: `deforestation_alerts/scripts/attribute_source.py:52-67`
- Modify: `deforestation_alerts/scripts/mosaics_helper.py:204-232` (`download_both_images`)
- Test: `tests/test_gee_progress.py`, `tests/test_raster_export.py`, `tests/test_mosaics_download.py`

**Interfaces:**
- Produces:
  - `gee_progress.geedim_tile_progress(callback)` — context manager; `callback(done: int, total: int)` on this thread.
  - `gee_progress.geedim_download_wait(callback)` — context manager; `callback()` fired at most once per queued download on this thread.
  - `gee_progress.notify_download_wait() -> None`
  - `raster_export._export_bounds(region) -> bounds | None`
  - `raster_export.download_ee_image(...)` — same signature as today.

- [ ] **Step 1: Port the progress module.** Copy `$SR/spatialrisk/gee/progress.py` verbatim to `deforestation_alerts/scripts/gee_progress.py`. Change only the module docstring's cross-references: `gui/scripts/notify_bridge.py` stays, and "``download_ee_image``" refers to `deforestation_alerts/scripts/raster_export.py`.

- [ ] **Step 2: Port its tests.** Copy `$SR/tests/test_geedim_tile_progress.py` to `tests/test_gee_progress.py`, replacing every `spatialrisk.gee.progress` import with `deforestation_alerts.scripts.gee_progress`. Run: `~/micromamba/envs/defo-alerts/bin/python -m pytest -q -p no:cacheprovider tests/test_gee_progress.py` → Expected: 5 passed.

- [ ] **Step 3: Write the failing export tests** in `tests/test_raster_export.py`. Port `$SR/tests/test_ee_raster_export_region.py` (its fake geometry/collection/accessor classes and its three tests) and `$SR/tests/test_ee_raster_export_serialized.py` (both tests), replacing `spatialrisk.gee.ee_raster_export` with `deforestation_alerts.scripts.raster_export` and `spatialrisk.gee.progress` with `deforestation_alerts.scripts.gee_progress`. The SR module's lock is `_geedim_lock`; ours is `_DOWNLOAD_LOCK` — adjust any reference. Note that our `download_ee_image` clips only when `isinstance(region, (ee.Geometry, ee.FeatureCollection))`; the SR fakes are plain classes, so the region test must either monkeypatch `export_module.ee` the way the SR test's `fake_ee` fixture does (read it) or the implementation must use the SR rule `if region is not None` — use the SR rule (Step 5). Add these two tests:

```python
def test_lock_timeout_still_raises(tmp_path, monkeypatch):
    """A wedged download must still become a visible TimeoutError."""
    from unittest.mock import MagicMock

    import ee

    from deforestation_alerts.scripts import raster_export

    monkeypatch.setattr(raster_export, "_DOWNLOAD_LOCK_TIMEOUT_S", 0.05)
    assert raster_export._DOWNLOAD_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(TimeoutError):
            raster_export.download_ee_image(
                MagicMock(spec=ee.Image), str(tmp_path / "x.tif")
            )
    finally:
        raster_export._DOWNLOAD_LOCK.release()


def test_export_options_reach_geedim(tmp_path):
    """Options the signature accepts are forwarded, not silently dropped."""
    from unittest.mock import MagicMock

    import ee

    from deforestation_alerts.scripts.raster_export import download_ee_image

    image = MagicMock(spec=ee.Image)
    prepared = MagicMock()
    image.gd.prepareForExport.return_value = prepared
    download_ee_image(
        image,
        str(tmp_path / "x.tif"),
        crs="EPSG:3857",
        crs_transform=[1, 0, 0, 0, -1, 0],
        shape=(10, 20),
        scale_offset=True,
        max_tile_size=8,
        max_tile_dim=2000,
        num_threads=4,
    )
    prep = image.gd.prepareForExport.call_args.kwargs
    assert prep["crs_transform"] == [1, 0, 0, 0, -1, 0]
    assert prep["shape"] == (10, 20)
    assert prep["scale_offset"] is True
    geo = prepared.gd.toGeoTIFF.call_args.kwargs
    assert geo["max_tile_size"] == 8
    assert geo["max_tile_dim"] == 2000
    assert geo["max_requests"] == 4
```

Run: `... -m pytest -q -p no:cacheprovider tests/test_raster_export.py` → Expected: FAIL (bounds not applied, no wait notice, options not forwarded).

- [ ] **Step 4: Implement `raster_export.py`.** Keep the module docstring, `_DOWNLOAD_LOCK`, `_DOWNLOAD_LOCK_TIMEOUT_S` (and its comment) and the public signature. Add `import logging`, `logger = logging.getLogger("SEPALUI.deforestation_alerts.raster_export")`, and `from deforestation_alerts.scripts.gee_progress import notify_download_wait`. Add `_export_bounds` verbatim from `$SR/spatialrisk/gee/ee_raster_export.py:22-36`. Replace the body after the `isinstance(image, ee.Image)` check with:

```python
    # Clip with the region object as given (Geometry, Feature or
    # FeatureCollection) -- never ``region.geometry()``, see ``_export_bounds``.
    if unmask_value is not None:
        if region is not None:
            image = image.clip(region)
        image = image.unmask(unmask_value, sameFootprint=False)

    prepare_opts = {"crs_transform": crs_transform, "shape": shape}
    prepare_opts = {k: v for k, v in prepare_opts.items() if v is not None}
    tiff_opts = {
        "max_tile_size": max_tile_size,
        "max_tile_dim": max_tile_dim,
        "max_requests": num_threads,
    }
    tiff_opts = {k: v for k, v in tiff_opts.items() if v is not None}

    # One export at a time, process-wide (see the module docstring). A
    # download that has to wait says so once before blocking.
    if not _DOWNLOAD_LOCK.acquire(blocking=False):
        notify_download_wait()
        if not _DOWNLOAD_LOCK.acquire(timeout=_DOWNLOAD_LOCK_TIMEOUT_S):
            raise TimeoutError(
                f"Timed out after {_DOWNLOAD_LOCK_TIMEOUT_S}s waiting for another "
                f"Earth Engine export to finish; {filename} was not downloaded."
            )
    try:
        img = image.gd.prepareForExport(
            crs=crs,
            region=_export_bounds(region),
            scale=scale,
            resampling=resampling,
            dtype=dtype,
            scale_offset=scale_offset,
            **prepare_opts,
        )
        nodata = True if nodata_value is None else nodata_value
        img.gd.toGeoTIFF(
            file=filename, overwrite=overwrite, nodata=nodata, **tiff_opts, **kwargs
        )
    finally:
        _DOWNLOAD_LOCK.release()

    logger.info("Downloaded %s", filename)
```

Update the docstring: tiling is controlled by `max_tile_size` (MB) / `max_tile_dim` (px), `num_threads` maps to geedim's `max_requests`, and the export region is always the bounds of `region` (the footprint is applied by the clip).

- [ ] **Step 5: Stop unwrapping geometries at the callers.** In `attribute_source.materialize_attribute_raster`, delete the `region_geom = (...)` unwrap and its comment and pass `region=region`. (The Filter tile's `_download_alert_raster` is Task 3's.)

- [ ] **Step 6: Fix `download_both_images`.** Write `tests/test_mosaics_download.py`:

```python
import pytest

from deforestation_alerts.scripts import mosaics_helper


def test_unknown_source_raises_a_clear_error(monkeypatch):
    monkeypatch.setattr(mosaics_helper, "scaleS2v2", lambda img: img)
    with pytest.raises(ValueError, match="Unsupported imagery source"):
        mosaics_helper.download_both_images(
            object(), object(), "/tmp/x.tif", "Sentinel 2", "MODIS", region=None
        )


def test_downloads_even_when_the_target_exists(monkeypatch, tmp_path):
    """The caller (paths.stage_alert_image) owns caching; a scratch path that
    exists is never a valid cache hit, so the download always runs."""
    calls = []

    class _Img:
        def addBands(self, other):
            return self

    monkeypatch.setattr(mosaics_helper, "scaleS2v2", lambda img: _Img())
    import deforestation_alerts.scripts.raster_export as rx

    monkeypatch.setattr(rx, "download_ee_image", lambda *a, **k: calls.append(a[1]))
    target = tmp_path / "a.tif"
    target.write_bytes(b"stale")
    mosaics_helper.download_both_images(
        object(), object(), str(target), "Sentinel 2", "Landsat", region=None
    )
    assert calls == [str(target)]
```

Implement: replace the two `if/elif` blocks with a helper

```python
def _scale_for_source(image, source):
    if source in ("Sentinel 2", "Landsat"):
        return scaleS2v2(image)
    if source == "Planet NICFI":
        return scalePlanet(image)
    raise ValueError(f"Unsupported imagery source: {source!r}")
```

and delete the `os.path.exists(image_name)` branch so `download_ee_image(...)` always runs (same arguments as today). Remove the now-unused `import os` / `import ee` inside the function.

- [ ] **Step 7: Run the new tests and the suite.** `... -m pytest -q -p no:cacheprovider tests/test_gee_progress.py tests/test_raster_export.py tests/test_mosaics_download.py tests/test_vectorize.py tests/test_attribute_source.py` → all pass; then the full suite → 293 + new tests pass.

- [ ] **Step 8: Commit.**

```bash
git add deforestation_alerts/scripts/gee_progress.py deforestation_alerts/scripts/raster_export.py deforestation_alerts/scripts/attribute_source.py deforestation_alerts/scripts/mosaics_helper.py tests/test_gee_progress.py tests/test_raster_export.py tests/test_mosaics_download.py
git commit -m "feat(gee): bounds-only export region, queued notice and tile progress for downloads"
```

---

### Task 2: `notify_bridge` + `to_thread_in_context`

**Files:**
- Create: `gui/scripts/notify_bridge.py`
- Modify: `gui/scripts/solara_threads.py` (add `to_thread_in_context`, module docstring line)
- Modify: `gui/solara_app.py` (install the handler at module level, next to the httpx logger lines)
- Test: `tests/test_notify_bridge.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `notify_bridge.ERROR_TOAST_TIMEOUT: float = 10.0`
  - `notify_bridge.install_task_log_handler(level=logging.INFO) -> TaskStepLogHandler`
  - `notify_bridge.forward_logs_to(task)` — context manager registering `task` as the log-milestone target of the CURRENT thread (restores the previous one on exit).
  - `notify_bridge.tracked_job(notifier, title, total_steps=None, error_format=None)` — context manager yielding the `TaskTracker`.
  - `notify_bridge.current_notifier() -> Notifier | NoopNotifier` — a notifier bound to the current kernel's bus, or `NoopNotifier()` when no provider is mounted.
  - `notify_bridge.tile_progress_reporter(task) -> Callable[[int, int], None]` — `(done, total)` → `task.set_progress(done / total)`, throttled to whole-percent changes.
  - `solara_threads.to_thread_in_context(fn, *args, **kwargs)` — async; `asyncio.to_thread` that attaches the caller's kernel context to the pool thread.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_notify_bridge.py` by porting from `$SR/tests/test_notify_bridge.py`: the module docstring (reworded for this app), `_fresh()` (logger name `"SEPALUI.deforestation_alerts"`), `_milestones`, `_status`, and these tests: `test_job_log_lines_become_milestones_and_task_completes`, `test_debug_lines_are_not_forwarded`, `test_lines_from_unrelated_threads_are_not_forwarded`, `test_exception_marks_task_failed_with_error_toast_and_reraises`, `test_no_active_job_is_a_noop`, `test_install_is_idempotent`, `test_concurrent_jobs_keep_milestones_separate`, `test_to_thread_in_context_attaches_caller_context_to_pool_thread`, `test_to_thread_in_context_without_context_still_runs`, `test_nested_tracked_jobs_restore_the_outer_tracker`, `test_error_format_localizes_the_failure_toast_and_task`, `test_broken_error_format_does_not_mask_the_original_exception`, `test_failure_publishes_exactly_one_error_toast`, `test_cancellation_is_not_reported_as_a_failure`. Do NOT port the `layer_reporter` tests or `_needs_progress_detail` (no multi-layer downloads here; stock pysepal). Import `to_thread_in_context` from `gui.scripts.solara_threads`. Add:

```python
def test_forward_logs_to_registers_the_current_thread_only():
    logger, bus, notifier = _fresh()
    with notifier.track("fw") as task:
        with notify_bridge.forward_logs_to(task):
            logger.info("inside")
        logger.info("after")
    assert _milestones(bus, "fw") == ["inside"]


def test_tile_reporter_throttles_to_whole_percent():
    bus = NotificationBus()
    seen = []
    with Notifier(bus).track("tiles") as task:
        task.set_progress = seen.append  # spy
        report = notify_bridge.tile_progress_reporter(task)
        for done in range(1, 1001):
            report(done, 1000)
    assert len(seen) == 101  # one publish per whole percent, 0..100
    assert seen[-1] == 1.0


def test_tile_reporter_ignores_a_zero_total():
    bus = NotificationBus()
    seen = []
    with Notifier(bus).track("tiles0") as task:
        task.set_progress = seen.append
        notify_bridge.tile_progress_reporter(task)(0, 0)
    assert seen == []


def test_tile_reporter_after_task_finished_is_a_noop():
    """A cancelled Analyze abandons its download thread, which keeps ticking."""
    bus = NotificationBus()
    with Notifier(bus).track("done") as task:
        pass
    notify_bridge.tile_progress_reporter(task)(5, 10)  # must not raise


def test_no_provider_falls_back_to_noop():
    from pysepal.solara.notifications import NoopNotifier

    assert isinstance(notify_bridge.current_notifier(), NoopNotifier)
    with notify_bridge.tracked_job(None, "orphan") as task:
        task.step("still fine")
```

Run: `... -m pytest -q -p no:cacheprovider tests/test_notify_bridge.py` → Expected: FAIL (module missing).

- [ ] **Step 2: Implement `gui/scripts/notify_bridge.py`.** Port `$SR/gui/scripts/notify_bridge.py` with these changes:
  - Module docstring: say it bridges the `SEPALUI.deforestation_alerts` logger to pysepal TaskTrackers and replaces the deleted on-map LogConsole; drop every reference to `materialize_raw_layers` / `log_progress` / PR-1.
  - `_LOGGER_NAME = "SEPALUI.deforestation_alerts"`.
  - Delete `layer_progress_reporter` entirely.
  - Keep `ERROR_TOAST_TIMEOUT`, `_trackers`, `_install_lock`, `TaskStepLogHandler`, `install_task_log_handler`, `tracked_job` (verbatim logic).
  - Extract the thread registration out of `tracked_job` into:

```python
@contextmanager
def forward_logs_to(task):
    """Forward this thread's INFO+ app log records to ``task`` as milestones.

    For work whose tracker was opened elsewhere (e.g. a ``use_task`` coroutine
    that offloads a stage to a pool thread): enter it ON the worker thread.
    Nesting restores the previous tracker on exit.
    """
    install_task_log_handler()
    tid = threading.get_ident()
    previous = _trackers.get(tid)
    _trackers[tid] = task
    try:
        yield task
    finally:
        if previous is not None:
            _trackers[tid] = previous
        else:
            _trackers.pop(tid, None)
```

    and make `tracked_job` use `with forward_logs_to(task):` around its `yield` instead of duplicating the bookkeeping (behaviour identical, including the `CancelledError` re-raise and fail-before-toast order).
  - Add:

```python
def current_notifier():
    """A Notifier on the current kernel's bus, or a NoopNotifier without one.

    For raw-widget workers (no component render, so no ``use_notifications``
    hook): it resolves the bus exactly like pysepal's ``track_task``/``notify``
    globals do, but returns a notifier so ``tracked_job`` can publish its error
    toast with ``ERROR_TOAST_TIMEOUT``.
    """
    from pysepal.solara.notifications import NoopNotifier
    from pysepal.solara.notifications.bus import get_current_bus
    from pysepal.solara.notifications.notifier import Notifier

    bus = get_current_bus()
    return NoopNotifier() if bus is None else Notifier(bus)


def tile_progress_reporter(task):
    """Adapt geedim tile ticks ``(done, total)`` to ``task.set_progress``.

    Throttled to whole-percent changes so a many-tile export does not flood the
    bus. Safe after the task finished (pysepal's tracker ignores late calls).
    """
    last_pct = -1

    def on_tiles(done, total):
        nonlocal last_pct
        if not total:
            return
        pct = int(100 * done / total)
        if pct == last_pct:
            return
        last_pct = pct
        task.set_progress(done / total)

    return on_tiles
```

- [ ] **Step 3: Add `to_thread_in_context`** to `gui/scripts/solara_threads.py`, verbatim from `$SR/gui/scripts/solara_threads.py:27-50` (add `import asyncio` at the top), and add one line to this module's docstring: "``to_thread_in_context`` — the ``asyncio.to_thread`` counterpart of ``spawn_in_context`` for ``use_task`` bodies."

- [ ] **Step 4: Install the handler at app startup.** In `gui/solara_app.py`, import `from gui.scripts.notify_bridge import install_task_log_handler` and call `install_task_log_handler()` at module level right after the `logging.getLogger("httpcore")...` line, with the comment `# Forward INFO+ app log records of tracked jobs into pysepal's task pill (gui/scripts/notify_bridge.py).` Add to `tests/test_app_wiring.py`:

```python
def test_task_log_handler_is_installed_at_startup():
    src = _src("gui/solara_app.py")
    assert "install_task_log_handler()" in src
```

- [ ] **Step 5: Run.** `... -m pytest -q -p no:cacheprovider tests/test_notify_bridge.py tests/test_app_wiring.py` → pass; full suite → pass.

- [ ] **Step 6: Commit.**

```bash
git add gui/scripts/notify_bridge.py gui/scripts/solara_threads.py gui/solara_app.py tests/test_notify_bridge.py tests/test_app_wiring.py
git commit -m "feat(notifications): tracked_job and log-milestone bridge onto pysepal's task pill"
```

---

### Task 3: Filter Analyze — tile progress, queued notice, log milestones

**Files:**
- Modify: `gui/tile/alerts_filter_tile.py` (`_download_alert_raster` ~L590-618; the `_analyze` coroutine ~L930-1010; imports)
- Modify: `deforestation_alerts/message/{en,es-ES,fr}/ui.json` (one key under `filter_tile`)
- Test: `tests/test_filter_analyze_progress.py`

**Interfaces:**
- Consumes: `gee_progress.geedim_tile_progress`, `gee_progress.geedim_download_wait` (Task 1); `notify_bridge.forward_logs_to`, `notify_bridge.tile_progress_reporter`, `solara_threads.to_thread_in_context` (Task 2).
- Produces: `_download_alert_raster(plan, on_tiles=None, on_wait=None) -> str`.

- [ ] **Step 1: Write the failing tests** in `tests/test_filter_analyze_progress.py`:

```python
"""Filter Analyze download: region rule + progress/queued callbacks."""

import ee

from gui.tile import alerts_filter_tile as aft


class _Plan:
    def __init__(self, region, tmp_path):
        self.region = region
        self.out_tif = str(tmp_path / "a.tif")
        self.pixel_size = 10

        class _Raster:
            def select(self, bands):
                return self

        self.filtered_alert_raster = _Raster()


def test_download_alert_raster_passes_the_collection_not_its_geometry(
    monkeypatch, tmp_path
):
    seen = {}
    monkeypatch.setattr(
        aft, "download_ee_image", lambda img, out, **kw: seen.update(kw)
    )
    fc = ee.FeatureCollection([])
    aft._download_alert_raster(_Plan(fc, tmp_path))
    assert seen["region"] is fc


def test_download_alert_raster_routes_tiles_and_wait(monkeypatch, tmp_path):
    from deforestation_alerts.scripts import gee_progress

    def fake_download(img, out, **kw):
        gee_progress.notify_download_wait()

    monkeypatch.setattr(aft, "download_ee_image", fake_download)
    waits = []
    aft._download_alert_raster(
        _Plan(None, tmp_path), on_tiles=lambda d, t: None, on_wait=lambda: waits.append(1)
    )
    assert waits == [1]


def test_analyze_downloads_in_context_with_forwarded_logs():
    import inspect

    src = inspect.getsource(aft)
    body = src[src.index("async def _analyze("):src.index("def _start_analyze(")]
    assert "to_thread_in_context(" in body
    assert "forward_logs_to(" in src
    assert "tile_progress_reporter(" in body
    assert "analyze_status_queued" in body
```

If `ee.FeatureCollection([])` needs an initialized EE client in this env, replace it with a `MagicMock(spec=ee.FeatureCollection)`. Run → Expected: FAIL.

- [ ] **Step 2: Add the message key.** Under the same object that holds `analyze_status_downloading` in each `ui.json`, add `analyze_status_queued` right after it:
  - en: `"Waiting for another download to finish…"`
  - es-ES: `"Esperando a que termine otra descarga…"`
  - fr: `"En attente de la fin d'un autre téléchargement…"`

- [ ] **Step 3: Implement `_download_alert_raster`.** New signature `_download_alert_raster(plan, on_tiles=None, on_wait=None)`. Drop the geometry unwrap (and its comment) and pass `region=plan.region` (the collection itself; `download_ee_image` exports its bounds). Wrap the `download_ee_image(...)` call:

```python
    from deforestation_alerts.scripts.gee_progress import (
        geedim_download_wait,
        geedim_tile_progress,
    )

    with geedim_tile_progress(on_tiles), geedim_download_wait(on_wait):
        download_ee_image(...same arguments, region=plan.region...)
```

  (`geedim_tile_progress(None)` / `geedim_download_wait(None)` are valid: they register no callback.) Update the docstring: mention the callbacks run on the calling thread.

- [ ] **Step 4: Wire the coroutine.** In `_analyze`, replace

```python
                await asyncio.to_thread(_download_alert_raster, plan)
```

with

```python
                on_tiles = tile_progress_reporter(task)

                def _on_wait():
                    task.step(cm.filter_tile.analyze_status_queued)

                def _download_tracked():
                    # On the pool thread: its app log lines become milestones
                    # of this task, and geedim's tile ticks drive the ring.
                    with forward_logs_to(task):
                        return _download_alert_raster(
                            plan, on_tiles=on_tiles, on_wait=_on_wait
                        )

                await to_thread_in_context(_download_tracked)
                task.set_progress(None)
```

  Imports: `from gui.scripts.notify_bridge import forward_logs_to, tile_progress_reporter` and `from gui.scripts.solara_threads import to_thread_in_context`. Leave `total_steps=4` as is (the queued milestone is rare and a 5/4 display is acceptable; do not derive progress from step counts). Keep every other stage on `asyncio.to_thread` unchanged.

- [ ] **Step 5: Run.** `... -m pytest -q -p no:cacheprovider tests/test_filter_analyze_progress.py tests/test_filtered_alerts_raster.py tests/test_app_wiring.py tests/test_feedback_notifications.py tests/test_i18n.py` → pass; full suite → pass.

- [ ] **Step 6: Commit.**

```bash
git add gui/tile/alerts_filter_tile.py deforestation_alerts/message tests/test_filter_analyze_progress.py
git commit -m "feat(filter): tile progress, queued notice and log milestones for the Analyze download"
```

---

### Task 4: Raw-layout workers onto `tracked_job` (+ composite download progress)

**Files:**
- Modify: `gui/tile/recipe_tile.py` (load worker ~L300-360; save ~L400-416)
- Modify: `gui/tile/overview_tile.py` (`_bind_export` worker ~L110-143)
- Modify: `gui/tile/analysis/export.py` (save worker ~L450-501; download worker ~L505-558)
- Modify: `gui/tile/analysis/edit_tools.py` (M1/M2 workers ~L495-568)
- Modify: `tests/test_feedback_notifications.py` and any test that spies `track_task`/`notify` in these modules
- Test: `tests/test_feedback_notifications.py` (updated + new cases)

**Interfaces:**
- Consumes: `notify_bridge.tracked_job`, `notify_bridge.current_notifier`, `notify_bridge.tile_progress_reporter`, `ERROR_TOAST_TIMEOUT` (Task 2); `gee_progress.geedim_tile_progress` (Task 1).
- Produces: nothing new for other tasks.

These workers run on `spawn_in_context` daemon threads and today open a task imperatively with the pysepal globals: `task = track_task(title)`, `task.step(...)`, then `task.complete()` or `task.fail(str(e))` followed by `notify(str(e), type_="error")` (see the module docstrings of `export.py` / `edit_tools.py` / `overview_tile.py` / `recipe_tile.py`). Migrate each worker to:

```python
        def _worker():
            notifier = current_notifier()
            try:
                with tracked_job(notifier, title, total_steps=...) as task:
                    task.step(...)
                    ...work...
            except Exception:
                logger.exception("<what failed>")  # tracked_job already failed the task + toasted
                return
            ...success-only UI updates (enable buttons, set_url, notify success)...
```

Rules for this migration:
- The worker body runs inside `tracked_job` ON the worker thread (that is what makes its `SEPALUI.deforestation_alerts.*` log lines appear as milestones).
- Delete the manual `task.fail(...)` + `notify(..., type_="error")` pairs: `tracked_job` fails the task and publishes exactly one error toast (10 s). Where a site mapped every failure to a specific message (edit_tools maps download failures to `cm.analysis_tile.model_labels.dl_404`), keep that mapping through `error_format=lambda exc: str(cm.analysis_tile.model_labels.dl_404)` only for the exception types it used to map; otherwise use the raw `str(exc)`. Preserve the exact user-facing texts of today.
- Success toasts stay `notify(..., type_="success")` (or `notifier.success(...)`) with the same texts; keep any "warning" outcomes exactly as today.
- Keep the existing button disable/enable and `DownloadBtn.set_url` behaviour, including in the failure path (re-enable where today's code re-enables).
- Where the worker calls `stage_alert_image(...)` (export download + both CNN workers), wrap that call in `with geedim_tile_progress(tile_progress_reporter(task)):` followed by `task.set_progress(None)` so the composite download drives the ring. A cache hit (no download) simply produces no ticks.
- Do not change what runs on which thread; do not touch `spawn_in_context`.
- Update each module docstring paragraph that describes the old globals pattern to describe `tracked_job`.

- [ ] **Step 1: Update/extend the tests first.** In `tests/test_feedback_notifications.py`, replace assertions that pin the old imperative sequence (`track_task` then `task.fail` then `notify(error)`) with assertions against a real `NotificationBus`: patch `gui.scripts.notify_bridge.current_notifier` to return `Notifier(bus)` and assert, per migrated site: on success the task ends COMPLETED and the success toast text is unchanged; on failure the task ends FAILED and exactly ONE error toast exists with the same text as before (spy `Notifier.error` rather than counting `bus.toasts`, since pysepal's bus replaces ERROR toasts). Add one test asserting a worker's `logger.info(...)` line becomes a milestone of its task (pick the overview export worker). Add a source-scan test:

```python
def test_raw_layout_workers_use_tracked_job():
    for rel in (
        "gui/tile/recipe_tile.py",
        "gui/tile/overview_tile.py",
        "gui/tile/analysis/export.py",
        "gui/tile/analysis/edit_tools.py",
    ):
        src = _src(rel)
        assert "tracked_job(" in src, rel
        assert "track_task(" not in src, rel
```

(`_src` is this file's existing helper; if it is named differently, use the existing one.) Run → Expected: FAIL.

- [ ] **Step 2: Migrate `overview_tile.py`** (single worker) per the rules; run its tests (`tests/test_overview_export.py`, `tests/test_feedback_notifications.py -k overview`).

- [ ] **Step 3: Migrate `recipe_tile.py`** (load + save). Save currently only toasts (no task pill): wrap it in `tracked_job` too so it shows in the pill and task log. Run `tests/test_feedback_notifications.py -k recipe` plus recipe tests.

- [ ] **Step 4: Migrate `analysis/export.py`** (save + download; download wraps `stage_alert_image` with tile progress). Run `tests/test_analysis_download.py`, `tests/test_feedback_notifications.py -k export`.

- [ ] **Step 5: Migrate `analysis/edit_tools.py`** (M1 + M2; both wrap `stage_alert_image` with tile progress; keep the `dl_404` mapping via `error_format`). Run `tests/test_feedback_notifications.py -k "edit or cnn"`.

- [ ] **Step 6: Full suite.** `... -m pytest -q -p no:cacheprovider` → all pass.

- [ ] **Step 7: Commit.**

```bash
git add gui/tile/recipe_tile.py gui/tile/overview_tile.py gui/tile/analysis/export.py gui/tile/analysis/edit_tools.py tests/test_feedback_notifications.py
git commit -m "refactor(notifications): run raw-layout workers under tracked_job with download progress"
```

(Add any other test file you had to update to the `git add`.)
