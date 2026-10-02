# Shared Image Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two per-pane image selectors with one shared, date-accurate timeline fed by a project-level Earth Engine scene index, so moving between alerts costs no Earth Engine round trip.

**Architecture:** One Earth Engine request per project fetches granule metadata for the whole AOI as parallel scalar arrays and caches it under `<project_dir>/imagery/`; per-alert selection is a local pandas/numpy filter over that cache. The UI is one raw-ipyvuetify strip spanning both map panes: a before lane above a piecewise date axis and an after lane below, with each collection drawn as coloured stripes in its own band, and months collapsible to a block carrying its image count.

**Tech Stack:** Python 3, Earth Engine, pandas, geopandas, shapely, pyarrow, pydantic, Solara reactives, raw ipyvuetify, pytest.

**Spec:** [`docs/superpowers/specs/2026-10-02-shared-image-timeline-design.md`](../specs/2026-10-02-shared-image-timeline-design.md)

**Worktree:** run every command from `/home/jserafini/Desktop/projects/deforestation_alerts/slices2-6-wt`, on branch `feat/alert-monitoring-slices2-6`. Never `cd` to the original repository root. Never use bare `git stash` / `git stash pop` — the stash stack is shared across worktrees.

## Task Model Assignments

| # | Task | Model | Why |
|---|---|---|---|
| 1 | Index paths and manifest model | sonnet | Mechanical paths plus a pydantic model with an existing convention to copy |
| 2 | Staleness and refresh-window decision | sonnet | Small pure function, fully specified by a table of cases |
| 3 | Parse the scene payload | sonnet | Pure array→DataFrame transform, modelled on `parse_coverage` |
| 4 | Earth Engine request builders | **opus** | The server-side graph must avoid any per-scene `getInfo`, and the true-footprint-vs-bbox distinction is subtle and load-bearing |
| 5 | Per-alert scene query | sonnet | Pure pandas/numpy with an exact worked example in the tests |
| 6 | Piecewise month layout and auto-collapse | sonnet | Pure arithmetic, exhaustively enumerated cases |
| 7 | Default before/after pick | sonnet | Pure rule with its cases enumerated |
| 8 | Cache read, atomic write, chunk merge | sonnet | Follows `alert_store.save_gdf_to_parquet` closely |
| 9 | `SceneIndexLoader` and background widening | **opus** | Solara task-dependency traps, thread-vs-loop rules, and concurrent append to one parquet |
| 10 | The timeline widget | **opus** | Raw-widget pattern, the `v-if` rebuild trap, dense absolute-positioned geometry, i18n |
| 11 | Keyboard navigation | **opus** | ipyvue event wiring needs `module.exports`; focus management across two lanes |
| 12 | Integration, image rebuild, retirement, test rewrites | **opus** | Touches eight files at once and inverts an existing test invariant |

Never use the `fable` model for any task; where this table says opus, run opus at effort `xhigh`.

## Global Constraints

Every task's requirements implicitly include all of these.

- **No traitlets.** State lives in the pydantic `Project` document and Solara reactives. No `HasTraits`, no pysepal model classes, under `gui/tile/analysis/`. `tests/test_analysis_state.py::test_nothing_under_analysis_imports_traitlets_or_pysepal_model` enforces this.
- **Raw-widget builders only.** Builders are plain functions returning `v.Card` / `v.Container` / `v.Html`, never `@solara.component`. MapApp renders step content with `v-if`, which destroys and rebuilds the DOM on close and reopen; `solara.display` / `render_fixed` content goes blank on reopen, while a raw ipyvuetify tree is re-viewed from its model and survives.
- **Reactive `.subscribe()`, not `use_effect`.** Builders register once, because the layout is built once per session.
- **Source strings are exactly** `"Sentinel 2"`, `"Landsat"`, `"Planet NICFI"`. `mosaics_helper._scale_for_source` and `before_info[0]` switch on them. The display label `"Sentinel-2"` is a UI string only and never reaches a data structure.
- **The `before_info` / `after_info` five-element contract is preserved:** `[source, millis, cloud_str, value, date_suffix]` where `cloud_str` is formatted `"%.2f"`, `value` is `f"{source} {YYYY-MM-DD} "` (trailing space included), and `date_suffix` is `strftime("_%d%m%y")`. `paths.alert_image_name`, `export.py` and `edit_tools.py` depend on it.
- **Every user-visible string goes through `gui.i18n.t()`**, with keys added to all three locales — `deforestation_alerts/message/{en,es-ES,fr}/ui.json`. `tests/test_i18n.py` iterates `LOCALES = ["en", "es-ES", "fr"]` and fails on a key missing from any of them.
- **Test tiers.** The default run is offline. A test that is not marked `ee` and reaches Earth Engine **fails** via the `_no_live_ee` guard in `tests/conftest.py`. Earth Engine tests live in `tests/test_*_ee.py`, take the `gee` fixture, and are skipped unless `DEFO_ALERTS_EE_TESTS=1`. `scene_query.py` must never import `ee`.
- **Blocking Earth Engine work goes off the UI thread** via `gui.scripts.solara_threads.spawn_in_context`, with feedback through `notify_bridge.tracked_job` and `notify_bridge.current_notifier()` — never the `use_notifications()` hook from a worker.
- **Atomic file writes** through `local_files.write_into(dest)`, which yields a `.part` sibling and `os.replace`s it on success.
- **Add no dependencies.** `pandas>=2.2`, `geopandas>=1.0`, `shapely>=2.0` and `pyarrow` are already declared in `sepal_environment.yml`. A test asserts every imported package is declared.
- **Fixed numbers from the spec:** default view is the alert window **±2 months**; the background buffer is **±6 months**; staleness is **24 hours**; the tail refetch starts at `covered.end - 10 days`; a collapsed month block is **66px** wide; lane bands are **Sentinel-2 17px, Landsat 14px, Planet 10px** inside a **45px** lane with a **27px** axis between the lanes; stripes are **2.5px** wide, **4px** when selected, with an **11px** hit area.
- **Commit messages carry no `Co-Authored-By` trailer.**

## Review Focus

Five conditions the spec implies that no task's happy path exercises, most likely to bite first. Each has its test added to the task that owns the code.

1. **A scene acquired exactly on `alert_date_min` or `alert_date_max`.** The spec says "at or before" and "at or after", so those boundary scenes are legal picks, not in-window ones. An exclusive comparison silently drops the single best candidate. → Task 7.
2. **A stale index whose tail refetch fails.** The cache must keep serving the old rows, labelled with their age, not be emptied or rebuilt. → Task 9.
3. **An alert whose bbox overlaps no indexed tile** — an AOI edge, or a gap between a period and the index coverage. The timeline must report "no images in this window", not raise or render an empty strip with no explanation. → Task 5.
4. **The background widener appending while a foreground widen-by-a-month writes.** Two writers on one parquet must not interleave into a corrupt or half-merged file. → Task 9.
5. **Two granules of one date where only one overlaps the alert.** The per-alert cloud mean must average only the overlapping granules, or the figure stops matching the `aggregate_mean` it replaces. → Task 5.

## File Structure

**Create**

| File | Responsibility |
|---|---|
| `deforestation_alerts/scripts/scene_index.py` | `SceneManifest` and friends; `scene_request`, `planet_request`; `parse_scenes`, `parse_planet`. Imports `ee`. |
| `deforestation_alerts/scripts/scene_query.py` | `overlapping_tiles`, `scenes_for`, `source_counts`, `month_counts`, `month_segments`, `x_for`, `auto_collapsed`, `default_pick`, `needs_refresh`, `refresh_window`. **Never imports `ee`.** |
| `gui/scripts/scene_cache.py` | parquet and manifest IO, the `scene_index` reactive, `SceneIndexLoader`, the background widener. |
| `gui/tile/analysis/timeline.py` | `build_timeline`: the strip widget, its subscriptions, and its click and key handlers. |
| `tests/test_scene_index.py` | offline parser tests. |
| `tests/test_scene_query.py` | offline rule tests. |
| `tests/test_scene_index_ee.py` | live-tier request-builder tests. |
| `tests/test_timeline_ee.py` | live-tier image-rebuild test: the Landsat TOA fallback. |
| `tests/test_scene_cache.py` | offline IO, staleness and merge tests. |

**Modify**

| File | Change |
|---|---|
| `deforestation_alerts/project/paths.py` | `imagery_dir`, `scenes_path`, `tiles_path`, `scene_manifest_path` |
| `gui/tile/analysis/widgets.py` | drop `imgSelection1/2`, `_build_image_selector`, `imgInfo1/2`; hold the strip |
| `gui/tile/analysis/analysis_tile.py:446` | the map-height CSS |
| `gui/tile/analysis/analysis_tile.py:475-515` | the two columns and the new full-width strip row |
| `gui/tile/analysis/analysis_tile.py:270-280` | the `imagery_ctx` payload |
| `gui/tile/analysis/imagery.py` | reduced to `image_slider_map_callback`, rebuilt from `keys` |
| `gui/tile/analysis/state.py` | remove `before_planet` / `after_planet` |
| `gui/solara_app.py:755` | mount `SceneIndexLoader` |
| `gui/widget/custom_sw.py:158-194` | delete `CustomSlideGroup` |
| `deforestation_alerts/scripts/imagery_service.py` | delete both `getIndividual*` functions |
| `deforestation_alerts/scripts/mosaics_helper.py:46` | delete `get_sentinel2_dates` |
| `deforestation_alerts/message/{en,es-ES,fr}/ui.json` | the strip's keys |
| `tests/test_analysis_state.py` | rewrite the three imagery token tests |
| `tests/test_set_context.py` | drop the `imgSelection` reference |

---

### Task 1: Index paths and the manifest model

**Files:**
- Modify: `deforestation_alerts/project/paths.py`
- Create: `deforestation_alerts/scripts/scene_index.py`
- Test: `tests/test_scene_index.py`

**Interfaces:**
- Consumes: `paths.project_dir`, already present.
- Produces: `paths.imagery_dir(pdir)`, `paths.scenes_path(pdir)`, `paths.tiles_path(pdir)`, `paths.scene_manifest_path(pdir)`; `scene_index.SOURCE_S2 = "Sentinel 2"`, `SOURCE_LANDSAT = "Landsat"`, `SOURCE_PLANET = "Planet NICFI"`, `SOURCE_CODE = {SOURCE_S2: "s", SOURCE_LANDSAT: "l", SOURCE_PLANET: "p"}`, `row_id(source, d) -> str`, `Coverage(BaseModel)` with `start: date` / `end: date`, `PlanetState(BaseModel)` with `region: str | None = None` / `access: bool = False`, `SceneManifest(BaseModel)` with `version: int = 1`, `aoi_digest: str`, `refreshed: datetime`, `covered: Coverage`, `counts: dict[str, int]`, `planet: PlanetState`, and `INDEX_VERSION = 1`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scene_index.py
from datetime import date, datetime, timezone

import pytest

from deforestation_alerts.project import paths
from deforestation_alerts.scripts import scene_index


def test_imagery_paths_sit_under_the_project_dir(tmp_path):
    pdir = tmp_path / "amazonia"
    assert paths.imagery_dir(pdir) == pdir / "imagery"
    assert paths.scenes_path(pdir) == pdir / "imagery" / "scenes.parquet"
    assert paths.tiles_path(pdir) == pdir / "imagery" / "tiles.parquet"
    assert paths.scene_manifest_path(pdir) == pdir / "imagery" / "index.json"


def test_row_id_is_stable_and_distinguishes_the_sources():
    d = date(2026, 8, 27)
    assert scene_index.row_id(scene_index.SOURCE_S2, d) == "s20260827"
    assert scene_index.row_id(scene_index.SOURCE_LANDSAT, d) == "l20260827"
    assert scene_index.row_id(scene_index.SOURCE_PLANET, d) == "p20260827"


def test_manifest_round_trips_through_json():
    m = scene_index.SceneManifest(
        aoi_digest="abc123",
        refreshed=datetime(2026, 10, 2, 9, 30, tzinfo=timezone.utc),
        covered=scene_index.Coverage(start=date(2026, 3, 3), end=date(2026, 10, 23)),
        counts={scene_index.SOURCE_S2: 47, scene_index.SOURCE_LANDSAT: 29},
    )
    again = scene_index.SceneManifest.model_validate_json(m.model_dump_json())
    assert again == m
    assert again.version == scene_index.INDEX_VERSION


def test_source_strings_are_the_ones_the_scalers_switch_on():
    from deforestation_alerts.scripts import mosaics_helper

    src = (mosaics_helper.__file__ and open(mosaics_helper.__file__).read())
    for name in (scene_index.SOURCE_S2, scene_index.SOURCE_LANDSAT, scene_index.SOURCE_PLANET):
        assert f'"{name}"' in src
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_scene_index.py -v`
Expected: FAIL — `AttributeError: module 'deforestation_alerts.project.paths' has no attribute 'imagery_dir'`, and `ModuleNotFoundError: deforestation_alerts.scripts.scene_index`.

- [ ] **Step 3: Add the paths**

Append to `deforestation_alerts/project/paths.py`, after `aoi_raster_path`:

```python
def imagery_dir(pdir: Path) -> Path:
    """``<pdir>/imagery``: the project-wide scene index, shared by every period and set."""
    return Path(pdir) / "imagery"


def scenes_path(pdir: Path) -> Path:
    return imagery_dir(pdir) / "scenes.parquet"


def tiles_path(pdir: Path) -> Path:
    return imagery_dir(pdir) / "tiles.parquet"


def scene_manifest_path(pdir: Path) -> Path:
    return imagery_dir(pdir) / "index.json"
```

- [ ] **Step 4: Write the module**

```python
# deforestation_alerts/scripts/scene_index.py
"""The project-level scene index: one Earth Engine request per project returns
the granule metadata of the whole AOI, and every alert is then answered from
the cached tables without a round trip (see
``docs/superpowers/specs/2026-10-02-shared-image-timeline-design.md``).

This module is the Earth-Engine-facing half: the request builders and the
array-to-DataFrame parsers. Every rule that can be expressed over plain data
lives in ``scene_query``, which never imports ``ee``, so the offline test tier
can exercise it.
"""

from datetime import date, datetime
from typing import Dict

from pydantic import BaseModel, Field

INDEX_VERSION = 1

# Exactly the strings ``mosaics_helper._scale_for_source`` and ``before_info[0]``
# switch on. The UI label "Sentinel-2" is a display string and never lands here.
SOURCE_S2 = "Sentinel 2"
SOURCE_LANDSAT = "Landsat"
SOURCE_PLANET = "Planet NICFI"

SOURCE_CODE = {SOURCE_S2: "s", SOURCE_LANDSAT: "l", SOURCE_PLANET: "p"}


def row_id(source: str, d: date) -> str:
    """The stable id of one timeline row: its source and its acquisition date.

    A row is one date of one collection, however many granules it mosaics, so
    the id carries no granule key. Selections are held as these ids.
    """
    return f"{SOURCE_CODE[source]}{d:%Y%m%d}"


class Coverage(BaseModel):
    """The contiguous date range the index holds, inclusive at both ends."""

    start: date
    end: date


class PlanetState(BaseModel):
    """Whether the NICFI basemaps answered, and for which region.

    NICFI is licence-gated, so no access is an ordinary outcome rather than an
    error. It is recorded instead of logged because the toolbar has to tell a
    reviewer *why* the Planet band is empty — a collection they have no access
    to reads differently from one with no images this month.
    """

    region: Optional[str] = None
    access: bool = False


class SceneManifest(BaseModel):
    """What is in ``scenes.parquet``, and how old it is."""

    version: int = INDEX_VERSION
    aoi_digest: str
    refreshed: datetime
    covered: Coverage
    counts: Dict[str, int] = Field(default_factory=dict)
    planet: PlanetState = Field(default_factory=PlanetState)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_scene_index.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 6: Commit**

```bash
git add deforestation_alerts/project/paths.py deforestation_alerts/scripts/scene_index.py tests/test_scene_index.py
git commit -m "feat(imagery): project-level scene index paths and manifest model"
```

---

### Task 2: Staleness and the refresh window

**Files:**
- Create: `deforestation_alerts/scripts/scene_query.py`
- Test: `tests/test_scene_query.py`

**Interfaces:**
- Consumes: `scene_index.SceneManifest`, `Coverage`, `INDEX_VERSION`.
- Produces: `scene_query.TTL_HOURS = 24`, `TAIL_DAYS = 10`, `needs_refresh(manifest, want, *, digest, now) -> str` returning one of `""`, `"tail"`, `"widen"`, `"rebuild"`, and `refresh_window(manifest, want, mode) -> tuple[date, date]`.

`scene_query.py` must not import `ee`, now or in any later task.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scene_query.py
from datetime import date, datetime, timedelta, timezone

import pytest

from deforestation_alerts.scripts import scene_index, scene_query

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
WANT = (date(2026, 3, 3), date(2026, 10, 23))


def _manifest(start, end, *, age_hours=1.0, digest="abc", version=scene_index.INDEX_VERSION):
    return scene_index.SceneManifest(
        version=version,
        aoi_digest=digest,
        refreshed=NOW - timedelta(hours=age_hours),
        covered=scene_index.Coverage(start=start, end=end),
        counts={},
    )


def test_no_manifest_means_rebuild():
    assert scene_query.needs_refresh(None, WANT, digest="abc", now=NOW) == "rebuild"


def test_a_different_aoi_means_rebuild():
    m = _manifest(*WANT, digest="other")
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == "rebuild"


def test_an_older_index_version_means_rebuild():
    m = _manifest(*WANT, version=scene_index.INDEX_VERSION - 1)
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == "rebuild"


def test_a_fresh_covering_index_needs_nothing():
    m = _manifest(date(2026, 1, 1), date(2026, 12, 31))
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == ""


def test_a_window_past_the_covered_end_is_a_widen_not_a_rebuild():
    m = _manifest(date(2026, 1, 1), date(2026, 9, 1))
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == "widen"


def test_coverage_beats_age_when_both_apply():
    m = _manifest(date(2026, 1, 1), date(2026, 9, 1), age_hours=40)
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == "widen"


def test_a_covering_index_older_than_the_ttl_needs_its_tail():
    m = _manifest(date(2026, 1, 1), date(2026, 12, 31), age_hours=24.5)
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == "tail"


def test_exactly_at_the_ttl_is_not_yet_stale():
    m = _manifest(date(2026, 1, 1), date(2026, 12, 31), age_hours=24.0)
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == ""


def test_a_rebuild_fetches_the_whole_wanted_window():
    assert scene_query.refresh_window(None, WANT, "rebuild") == WANT


def test_a_tail_refetch_starts_ten_days_before_the_covered_end():
    m = _manifest(date(2026, 1, 1), date(2026, 9, 20))
    assert scene_query.refresh_window(m, WANT, "tail") == (date(2026, 9, 10), date(2026, 10, 23))


def test_a_widen_takes_the_later_side_first():
    m = _manifest(date(2026, 5, 1), date(2026, 9, 1))
    assert scene_query.refresh_window(m, WANT, "widen") == (date(2026, 9, 1), date(2026, 10, 23))


def test_a_widen_takes_the_earlier_side_once_the_later_one_is_covered():
    m = _manifest(date(2026, 5, 1), date(2026, 11, 1))
    assert scene_query.refresh_window(m, WANT, "widen") == (date(2026, 3, 3), date(2026, 5, 1))


def test_nothing_to_fetch_raises_rather_than_returning_an_empty_range():
    m = _manifest(date(2026, 1, 1), date(2026, 12, 31))
    with pytest.raises(ValueError):
        scene_query.refresh_window(m, WANT, "")


def test_scene_query_never_imports_earth_engine():
    src = open(scene_query.__file__).read()
    assert "import ee" not in src and "from ee" not in src
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_scene_query.py -v`
Expected: FAIL — `ModuleNotFoundError: deforestation_alerts.scripts.scene_query`.

- [ ] **Step 3: Write the module**

```python
# deforestation_alerts/scripts/scene_query.py
"""Everything the shared image timeline decides over plain data: which scenes
an alert can see, where they land on a piecewise date axis, which pair is
picked by default, and whether the cached index still answers.

This module must never import ``ee``. The offline test tier fails any
unmarked test that reaches Earth Engine (``tests/conftest.py``), so keeping
the rules here is what makes them testable without a live session.
"""

from datetime import date, timedelta
from typing import Optional, Tuple

from deforestation_alerts.scripts.scene_index import INDEX_VERSION, SOURCE_CODE, row_id

TTL_HOURS = 24
TAIL_DAYS = 10

Window = Tuple[date, date]


def needs_refresh(manifest, want: Window, *, digest: str, now) -> str:
    """What the cached index needs before it can answer ``want``.

    ``"rebuild"`` — there is no usable index: none on disk, one written by an
    older layout, or one built for a different AOI, whose tile footprints and
    granule set do not describe this project.
    ``"widen"`` — the index is for this AOI but does not reach across ``want``.
    ``"tail"`` — the index covers ``want`` but is older than ``TTL_HOURS``, so
    recently published scenes may be missing from its end.
    ``""`` — it answers as it is.

    Coverage outranks age: a widen that reaches the wanted end refetches that
    end anyway, so reporting ``"tail"`` there would cost a second request for
    nothing.
    """
    if manifest is None or manifest.version != INDEX_VERSION or manifest.aoi_digest != digest:
        return "rebuild"
    if want[0] < manifest.covered.start or want[1] > manifest.covered.end:
        return "widen"
    if (now - manifest.refreshed).total_seconds() > TTL_HOURS * 3600:
        return "tail"
    return ""


def refresh_window(manifest, want: Window, mode: str) -> Window:
    """The one contiguous range to fetch for ``mode``.

    A widen whose wanted window overhangs *both* sides of the coverage is
    served in two calls, the later side first: images published after the
    index was built are what a reviewer is most often missing. The caller
    loops until ``needs_refresh`` goes quiet.
    """
    if mode == "rebuild":
        return want
    if manifest is None:
        raise ValueError(f"mode {mode!r} needs a manifest")
    covered = manifest.covered
    if mode == "tail":
        return (covered.end - timedelta(days=TAIL_DAYS), max(want[1], covered.end))
    if mode == "widen":
        if want[1] > covered.end:
            return (covered.end, want[1])
        if want[0] < covered.start:
            return (want[0], covered.start)
        raise ValueError("nothing to widen: the coverage already spans the wanted window")
    raise ValueError(f"nothing to fetch for mode {mode!r}")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_scene_query.py -v`
Expected: PASS, 14 tests.

- [ ] **Step 5: Commit**

```bash
git add deforestation_alerts/scripts/scene_query.py tests/test_scene_query.py
git commit -m "feat(imagery): index staleness and refresh-window rules"
```

---

### Task 3: Parse the scene payload

**Files:**
- Modify: `deforestation_alerts/scripts/scene_index.py`
- Test: `tests/test_scene_index.py`

**Interfaces:**
- Consumes: `SOURCE_S2`, `SOURCE_LANDSAT`, `SOURCE_PLANET`.
- Produces: `scene_index.SceneTables` — a `NamedTuple` with `scenes: pandas.DataFrame` (columns `source` str, `date` `datetime64[ns]`, `cloud` float, `tile` str, `key` str) and `tiles: geopandas.GeoDataFrame` (columns `tile` str, `geometry`, CRS EPSG:4326); `scene_index.SCENE_COLUMNS`, `scene_index.WGS84`, `scene_index.EMPTY_SCENE_ARRAYS`; `parse_scenes(raw) -> SceneTables`; `parse_planet(raw) -> pandas.DataFrame`; `empty_tables() -> SceneTables`.

The payload is **parallel scalar arrays**, not a list of records, because one `ee.Dictionary` of `aggregate_array` results is what makes the whole fetch a single `getInfo`. Parsing therefore has to assert the arrays are the same length: a mismatch means a server-side graph bug that would otherwise mis-pair a cloud figure with the wrong date.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_scene_index.py
import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

RAW = {
    "scenes": {
        "source": ["Sentinel 2", "Sentinel 2", "Landsat"],
        "millis": [1775088000000, 1775088000000, 1775260800000],  # 2 Apr, 2 Apr, 4 Apr 2026 UTC
        "cloud": [12.5, 40.0, 3.25],
        "tile": ["21LYG", "21LYH", "LC08_227068"],
        "key": ["COPERNICUS/S2_HARMONIZED/A", "COPERNICUS/S2_HARMONIZED/B", "LC82270682026100LGN00"],
    },
    "tiles": {
        "tile": ["21LYG", "21LYH", "LC08_227068"],
        "gtype": ["Polygon", "Polygon", "Polygon"],
        "coords": [
            [[[-55.2, -10.2], [-55.0, -10.2], [-55.0, -10.0], [-55.2, -10.0], [-55.2, -10.2]]],
            [[[-55.0, -10.2], [-54.8, -10.2], [-54.8, -10.0], [-55.0, -10.0], [-55.0, -10.2]]],
            [[[-55.4, -10.4], [-54.6, -10.4], [-54.6, -9.8], [-55.4, -9.8], [-55.4, -10.4]]],
        ],
    },
}


def test_parse_scenes_pairs_each_array_into_rows():
    tables = scene_index.parse_scenes(RAW)
    assert list(tables.scenes.columns) == list(scene_index.SCENE_COLUMNS)
    assert len(tables.scenes) == 3
    row = tables.scenes.iloc[0]
    assert row["source"] == scene_index.SOURCE_S2
    assert row["date"] == pd.Timestamp("2026-04-02")
    assert row["cloud"] == pytest.approx(12.5)
    assert row["tile"] == "21LYG"
    assert row["key"] == "COPERNICUS/S2_HARMONIZED/A"


def test_parse_scenes_builds_tile_footprints_in_wgs84():
    tables = scene_index.parse_scenes(RAW)
    assert isinstance(tables.tiles, gpd.GeoDataFrame)
    assert tables.tiles.crs.to_epsg() == 4326
    assert len(tables.tiles) == 3
    got = tables.tiles.set_index("tile").loc["21LYG", "geometry"]
    assert got.equals(Polygon(RAW["tiles"]["coords"][0][0]))


def test_parse_scenes_handles_a_multipolygon_footprint():
    # A Sentinel-2 granule footprint is occasionally a MultiPolygon (a sliver
    # across the antimeridian, or a split orbit edge), so the parser reads the
    # geometry type the request reports instead of assuming Polygon.
    raw = {
        "scenes": {k: v[:1] for k, v in RAW["scenes"].items()},
        "tiles": {
            "tile": ["21LYG"],
            "gtype": ["MultiPolygon"],
            "coords": [[
                [[[-55.2, -10.2], [-55.1, -10.2], [-55.1, -10.1], [-55.2, -10.1], [-55.2, -10.2]]],
                [[[-55.0, -10.2], [-54.9, -10.2], [-54.9, -10.1], [-55.0, -10.1], [-55.0, -10.2]]],
            ]],
        },
    }
    geom = scene_index.parse_scenes(raw).tiles.iloc[0]["geometry"]
    assert geom.geom_type == "MultiPolygon"
    assert len(geom.geoms) == 2


def test_parse_scenes_rejects_arrays_of_different_lengths():
    bad = {"scenes": {**RAW["scenes"], "cloud": [1.0]}, "tiles": RAW["tiles"]}
    with pytest.raises(ValueError, match="length"):
        scene_index.parse_scenes(bad)


def test_parse_scenes_of_an_empty_aoi_window_keeps_the_columns():
    empty = {"scenes": {k: [] for k in RAW["scenes"]}, "tiles": {"tile": [], "coords": [], "gtype": []}}
    tables = scene_index.parse_scenes(empty)
    assert len(tables.scenes) == 0
    assert list(tables.scenes.columns) == list(scene_index.SCENE_COLUMNS)
    assert tables.tiles.crs.to_epsg() == 4326


def test_parse_planet_rows_carry_no_footprint_and_no_cloud():
    raw = {"key": ["projects/planet-nicfi/assets/basemaps/sr/2026-04"], "millis": [1775001600000]}  # 1 Apr 2026
    df = scene_index.parse_planet(raw)
    assert list(df.columns) == list(scene_index.SCENE_COLUMNS)
    assert df.iloc[0]["source"] == scene_index.SOURCE_PLANET
    assert df.iloc[0]["tile"] == ""
    assert pd.isna(df.iloc[0]["cloud"])


def test_empty_tables_are_usable_without_a_fetch():
    tables = scene_index.empty_tables()
    assert len(tables.scenes) == 0 and len(tables.tiles) == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_scene_index.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'parse_scenes'`.

- [ ] **Step 3: Implement the parsers**

Add to `deforestation_alerts/scripts/scene_index.py`:

```python
from typing import Any, Dict, List, Mapping, NamedTuple

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

SCENE_COLUMNS = ("source", "date", "cloud", "tile", "key")
WGS84 = "EPSG:4326"


class SceneTables(NamedTuple):
    """The cached index: one row per granule, plus each tile's footprint once.

    The footprints are kept apart because a granule's footprint repeats on
    every date it was acquired — an AOI of 30 Sentinel-2 granules over six
    months is thousands of scene rows but still 30 polygons.
    """

    scenes: pd.DataFrame
    tiles: gpd.GeoDataFrame


def _checked(arrays: Mapping[str, List[Any]], names) -> int:
    lengths = {name: len(arrays.get(name) or ()) for name in names}
    if len(set(lengths.values())) > 1:
        raise ValueError(f"scene arrays of different length: {lengths}")
    return next(iter(lengths.values()), 0)


def _scene_frame(arrays: Mapping[str, List[Any]]) -> pd.DataFrame:
    _checked(arrays, ("source", "millis", "cloud", "tile", "key"))
    df = pd.DataFrame(
        {
            "source": pd.Series(arrays["source"], dtype="string").astype(object),
            # normalize() drops the time of day: the index is keyed by
            # acquisition *date*, which is what the timeline and the saved
            # provenance show. Two granules of one pass share that date.
            "date": pd.to_datetime(pd.Series(arrays["millis"], dtype="float64"), unit="ms", utc=True)
            .dt.tz_localize(None)
            .dt.normalize(),
            "cloud": pd.Series(arrays["cloud"], dtype="float64"),
            "tile": pd.Series(arrays["tile"], dtype="string").astype(object),
            "key": pd.Series(arrays["key"], dtype="string").astype(object),
        },
        columns=list(SCENE_COLUMNS),
    )
    return df.astype({"source": object, "tile": object, "key": object})


def _tile_frame(arrays: Mapping[str, List[Any]]) -> gpd.GeoDataFrame:
    _checked(arrays, ("tile", "coords", "gtype"))
    # The footprints come from ``img.geometry()``, never from its bounds — see
    # the spec: a bbox here would turn the exact-footprint test into a second
    # bbox test and let Landsat over-selection through. The geometry type
    # travels with the coordinates because a granule footprint is usually a
    # Polygon but not always.
    geoms = [
        shape({"type": gtype, "coordinates": coords})
        for gtype, coords in zip(arrays.get("gtype") or (), arrays.get("coords") or ())
    ]
    return gpd.GeoDataFrame(
        {"tile": list(arrays.get("tile") or ())}, geometry=gpd.GeoSeries(geoms, crs=WGS84), crs=WGS84
    )


def parse_scenes(raw: Mapping[str, Any]) -> SceneTables:
    """The one request's reply, as tables."""
    return SceneTables(scenes=_scene_frame(raw.get("scenes") or {}), tiles=_tile_frame(raw.get("tiles") or {}))


def parse_planet(raw: Mapping[str, Any]) -> pd.DataFrame:
    """Planet NICFI monthly basemaps as scene rows.

    A basemap is one cloud-minimized mosaic of a whole month over the whole
    AOI, so it has no granule footprint to test and no cloud figure to
    compare: ``tile`` is empty, which ``scene_query`` reads as "covers every
    alert", and ``cloud`` is NaN, which the cloud filter never hides.
    """
    keys = list(raw.get("key") or ())
    return _scene_frame(
        {
            "source": [SOURCE_PLANET] * len(keys),
            "millis": list(raw.get("millis") or ()),
            "cloud": [float("nan")] * len(keys),
            "tile": [""] * len(keys),
            "key": keys,
        }
    )


EMPTY_SCENE_ARRAYS = {"source": [], "millis": [], "cloud": [], "tile": [], "key": []}


def empty_tables() -> SceneTables:
    """An index with the right columns and no rows, so the timeline can render
    its "no images" state before any fetch has run."""
    return SceneTables(
        scenes=_scene_frame(dict(EMPTY_SCENE_ARRAYS)),
        tiles=_tile_frame({"tile": [], "coords": [], "gtype": []}),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_scene_index.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add deforestation_alerts/scripts/scene_index.py tests/test_scene_index.py
git commit -m "feat(imagery): parse the scene payload into scene and tile tables"
```

---

### Task 4: The Earth Engine request builders

**Files:**
- Modify: `deforestation_alerts/scripts/scene_index.py`
- Test: `tests/test_scene_index_ee.py`

**Interfaces:**
- Consumes: `SOURCE_S2`, `SOURCE_LANDSAT`, `parse_scenes`, `parse_planet`.
- Produces: `scene_index.scene_request(aoi, start, end) -> ee.Dictionary` whose `getInfo()` is the `raw` of `parse_scenes`, and `scene_index.planet_request(collection_id, start, end) -> ee.Dictionary` whose `getInfo()` is the `raw` of `parse_planet`.

**Why this task is opus.** Two things here are easy to get quietly wrong.

First, **no per-scene `getInfo`.** The code being replaced calls `.getInfo()` once per date inside a Python loop — that is the whole reason a new alert takes seconds. Every value this builder needs must be reachable through `aggregate_array` on a server-side `FeatureCollection`, so the one `ee.Dictionary` resolves in one round trip however many scenes it holds.

Second, **the feature's geometry must be the granule's true footprint**, `ee.Feature(img.geometry(), {...})`, not `img.geometry().bounds()`. `distinct("tile")` keeps whichever feature it meets first for each tile and that feature's geometry becomes the footprint in `tiles.parquet`. If it were a bbox, the exact-footprint test in Task 5 would be a second bounding-box test, and the Landsat over-selection it exists to catch — a scene whose bbox covers the alert while its slanted footprint misses it — would survive both filters.

Third, **the date window is applied once, to the source collections only.** `filterDate`'s end is exclusive, so an inclusive `end` has to ask for the day after; and the merged `FeatureCollection` must *not* be filtered by date again, because `_row` builds features carrying `millis` rather than `system:time_start` and `FeatureCollection.filterDate` reads only the latter — a second filter there returns an empty collection, which parses into an empty index with no error anywhere. `_window` is the one place either rule is expressed. Note also that `str()` on an Earth Engine object renders its graph, not a date string, so date arguments are built as `ee.Date` objects and passed as objects.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scene_index_ee.py
"""Tier 2: the one scene-index request against real Earth Engine. Skips without credentials."""

from datetime import date

import pandas as pd
import pytest

from deforestation_alerts.scripts import scene_index

START, END = date(2025, 6, 1), date(2025, 8, 31)


@pytest.fixture(scope="module")
def tables(tiny_aoi):
    return scene_index.parse_scenes(scene_index.scene_request(tiny_aoi, START, END).getInfo())


def test_a_scene_acquired_on_the_last_day_of_the_window_is_included(tiny_aoi, gee):
    """``filterDate``'s end is exclusive, so an inclusive ``end`` has to ask for
    the day after. A one-day window is the cheapest way to catch a regression:
    it returns nothing at all if the off-by-one comes back."""
    import ee

    day = ee.ImageCollection(scene_index.S2_COLLECTION).filterBounds(tiny_aoi).filterDate(
        START.isoformat(), END.isoformat()
    ).first().date().format("YYYY-MM-dd").getInfo()
    one = date.fromisoformat(day)
    tables = scene_index.parse_scenes(scene_index.scene_request(tiny_aoi, one, one).getInfo())
    assert len(tables.scenes) > 0
    assert (tables.scenes["date"] == pd.Timestamp(one)).all()


def test_the_whole_index_arrives_in_one_round_trip(tables):
    assert len(tables.scenes) > 0
    assert set(tables.scenes["source"]) <= {scene_index.SOURCE_S2, scene_index.SOURCE_LANDSAT}
    assert tables.scenes["date"].between(pd.Timestamp(START), pd.Timestamp(END)).all()
    assert tables.scenes["cloud"].between(0, 90).all()  # the < 90 prefilter imagery_service applies today
    assert (tables.scenes["key"] != "").all()


def test_every_scene_row_names_a_tile_that_the_tiles_table_defines(tables):
    assert set(tables.scenes["tile"]) <= set(tables.tiles["tile"])
    assert tables.tiles["tile"].is_unique


def test_the_tile_footprints_are_real_footprints_not_bounding_boxes(tables):
    # A Sentinel-2 granule is a square, a Landsat scene a slanted
    # parallelogram: its footprint must be strictly smaller than its envelope.
    ls = tables.tiles.merge(
        tables.scenes[tables.scenes["source"] == scene_index.SOURCE_LANDSAT][["tile"]].drop_duplicates(),
        on="tile",
    )
    if ls.empty:
        pytest.skip("no Landsat scene over the test AOI in this window")
    worst = (ls.geometry.area / ls.geometry.envelope.area).min()
    assert worst < 0.95


def test_the_sentinel2_keys_are_loadable_asset_ids(tables, gee):
    import ee

    key = tables.scenes[tables.scenes["source"] == scene_index.SOURCE_S2]["key"].iloc[0]
    assert ee.Image(key).bandNames().size().getInfo() > 0


def test_the_landsat_keys_resolve_in_both_the_l2_and_toa_collections(tables, gee):
    import ee

    hit = tables.scenes[tables.scenes["source"] == scene_index.SOURCE_LANDSAT]
    if hit.empty:
        pytest.skip("no Landsat scene over the test AOI in this window")
    key = hit["key"].iloc[0]
    for cid in ("LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2",
                "LANDSAT/LC08/C02/T1_TOA", "LANDSAT/LC09/C02/T1_TOA"):
        sizes = ee.ImageCollection(cid).filter(ee.Filter.eq("LANDSAT_SCENE_ID", key)).size()
        if sizes.getInfo():
            break
    else:
        pytest.fail(f"{key} is in none of the Landsat collections the rebuild searches")


def test_planet_months_come_back_as_one_row_each(gee, tiny_aoi):
    import ee

    from gui.scripts import vis_layers

    cid = vis_layers.NICFI_COLLECTIONS["americas"]
    try:
        ee.ImageCollection(cid).limit(1).size().getInfo()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no NICFI access: {exc}")
    df = scene_index.parse_planet(scene_index.planet_request(cid, START, END).getInfo())
    assert len(df) == 3  # June, July, August
    assert (df["source"] == scene_index.SOURCE_PLANET).all()
    assert df["date"].dt.day.eq(1).all()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `DEFO_ALERTS_EE_TESTS=1 python -m pytest tests/test_scene_index_ee.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'scene_request'`. (If Earth Engine is unauthenticated the whole file skips instead; in that case run it once with credentials before the task is considered done, and say so in the commit if it could not be run.)

- [ ] **Step 3: Implement the builders**

Add to `deforestation_alerts/scripts/scene_index.py`:

```python
S2_COLLECTION = "COPERNICUS/S2_HARMONIZED"
LANDSAT_COLLECTIONS = ("LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2")

#: The property every row's ``key`` comes from, per source. Sentinel-2 and
#: Planet keys are asset ids, so the chosen image is rebuilt with
#: ``ee.Image(key)``. A Landsat key is its ``LANDSAT_SCENE_ID``, because the
#: rebuild has to find the *same* scene in both the surface-reflectance and
#: the TOA collection to pan-sharpen it, and those are two different assets.
KEY_PROPERTY = {SOURCE_S2: "system:id", SOURCE_LANDSAT: "LANDSAT_SCENE_ID", SOURCE_PLANET: "system:id"}


def _window(start: date, end: date):
    """``[start, end]`` as the half-open pair ``filterDate`` actually wants.

    ``filterDate``'s end is **exclusive**, so an inclusive window has to ask
    for the day after. Both ends are ``ee.Date`` objects: ``str()`` on an
    Earth Engine object renders a *graph*, not a date string, so passing
    ``str(ee.Date(...).advance(...))`` would hand the filter nonsense.
    """
    import ee

    return (ee.Date(start.isoformat()), ee.Date(end.isoformat()).advance(1, "day"))


def _row(img, source, cloud_prop, tile):
    """One scene as a feature whose geometry is the granule's true footprint.

    The footprint, not its bounding box: ``distinct("tile")`` carries *this*
    geometry into the tiles table, and the exact-overlap test downstream is
    only worth running if it is not a second bounding-box test.
    """
    import ee

    img = ee.Image(img)
    return ee.Feature(
        img.geometry(),
        {
            "source": source,
            "millis": img.date().millis(),
            "cloud": ee.Number(img.get(cloud_prop)),
            "tile": tile,
            "key": img.get(KEY_PROPERTY[source]),
        },
    )


def _s2_rows(aoi, start, end):
    import ee

    d0, d1 = _window(start, end)
    coll = (ee.ImageCollection(S2_COLLECTION).filterBounds(aoi).filterDate(d0, d1)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 90)))
    return coll.map(lambda img: _row(img, SOURCE_S2, "CLOUDY_PIXEL_PERCENTAGE",
                                    ee.String(ee.Image(img).get("MGRS_TILE"))))


def _landsat_rows(aoi, start, end):
    import ee

    coll = ee.ImageCollection(LANDSAT_COLLECTIONS[0])
    for cid in LANDSAT_COLLECTIONS[1:]:
        coll = coll.merge(ee.ImageCollection(cid))
    d0, d1 = _window(start, end)
    coll = coll.filterBounds(aoi).filterDate(d0, d1).filter(ee.Filter.lt("CLOUD_COVER", 90))

    # WRS-2 path/row, zero-padded, as the tile key. Scenes of one path/row
    # have the same nominal footprint, so the first one met represents the
    # rest; real footprints drift by a few hundred metres between passes, so
    # the overlap test is approximate for an alert sitting on a scene edge.
    def row(img):
        img = ee.Image(img)
        tile = (ee.Number(img.get("WRS_PATH")).format("%03d")
                .cat("_").cat(ee.Number(img.get("WRS_ROW")).format("%03d")))
        return _row(img, SOURCE_LANDSAT, "CLOUD_COVER", tile)

    return coll.map(row)


def scene_request(aoi, start: date, end: date):
    """Every Sentinel-2 granule and Landsat scene over ``aoi`` in one request.

    The reply is a dictionary of parallel scalar arrays rather than a list of
    records, because ``aggregate_array`` over a server-side collection is what
    lets one ``getInfo()`` resolve the whole index. ``end`` is inclusive: it is
    the last date a reviewer asked to see, which ``_window`` turns into the
    half-open range ``filterDate`` wants.

    The date window is applied **only** to the two source collections. Filtering
    the merged ``FeatureCollection`` again would return nothing: ``_row`` builds
    features carrying ``millis``, not ``system:time_start``, and that is the one
    property ``FeatureCollection.filterDate`` reads.

    The ``CLOUDY_PIXEL_PERCENTAGE`` / ``CLOUD_COVER`` < 90 prefilters are the
    ones ``imagery_service`` applies today, kept so the candidate set does not
    change (spec §5.1). The slider's top stop therefore means "everything the
    index holds", not literally 100%.
    """
    import ee

    rows = ee.FeatureCollection(_s2_rows(aoi, start, end).merge(_landsat_rows(aoi, start, end)))
    tiles = rows.distinct("tile").map(
        lambda f: ee.Feature(f).set({
            "coords": ee.Feature(f).geometry().coordinates(),
            "gtype": ee.Feature(f).geometry().type(),
        })
    )
    return ee.Dictionary({
        "scenes": ee.Dictionary({
            "source": rows.aggregate_array("source"),
            "millis": rows.aggregate_array("millis"),
            "cloud": rows.aggregate_array("cloud"),
            "tile": rows.aggregate_array("tile"),
            "key": rows.aggregate_array("key"),
        }),
        "tiles": ee.Dictionary({
            "tile": tiles.aggregate_array("tile"),
            "coords": tiles.aggregate_array("coords"),
            "gtype": tiles.aggregate_array("gtype"),
        }),
    })


def planet_request(collection_id: str, start: date, end: date):
    """The NICFI monthly basemaps overlapping ``[start, end]``.

    ``collection_id`` is passed in rather than looked up: the region mapping
    lives in ``gui.scripts.vis_layers.NICFI_COLLECTIONS`` and this module must
    not import from ``gui``. A basemap is already an AOI-wide mosaic, so there
    is nothing to filter by bounds and no footprint to record.
    """
    import ee

    d0, d1 = _window(start.replace(day=1), end)
    coll = ee.ImageCollection(collection_id).filterDate(d0, d1)
    return ee.Dictionary({
        "key": coll.aggregate_array("system:id"),
        # The basemap's own start date, i.e. the first of its month.
        "millis": coll.aggregate_array("system:time_start"),
    })
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `DEFO_ALERTS_EE_TESTS=1 python -m pytest tests/test_scene_index_ee.py -v`
Expected: PASS (or `skip` on the two Planet/Landsat-dependent tests if the AOI window holds none).

- [ ] **Step 5: Verify the offline tier still refuses live calls**

Run: `python -m pytest tests/test_scene_index.py tests/test_scene_query.py tests/test_scene_index_ee.py -v`
Expected: the `_ee` file reports `skipped`, the other two PASS. A `failed` in the `_ee` file here means a module-level Earth Engine call slipped in.

- [ ] **Step 6: Commit**

```bash
git add deforestation_alerts/scripts/scene_index.py tests/test_scene_index_ee.py
git commit -m "feat(imagery): one Earth Engine request for the project's scene index"
```

---

### Task 5: The per-alert scene query

**Files:**
- Modify: `deforestation_alerts/scripts/scene_query.py`
- Test: `tests/test_scene_query.py`

**Interfaces:**
- Consumes: `scene_index.SceneTables`, `SOURCE_*`, `row_id`.
- Produces: `scene_query.SELECTION_COLUMNS = ("id", "source", "date", "cloud", "n", "hidden")`, `overlapping_tiles(tiles, bbox) -> set[str]`, `scenes_for(tables, bbox, start, end, *, max_cloud=None) -> pandas.DataFrame`, `source_counts(sel) -> dict[str, tuple[int, int]]`, `month_counts(sel) -> dict[tuple[int, int], int]`.

`bbox` is a shapely geometry in EPSG:4326 — the alert's bounding box as `imagery_ctx` already carries it.

One row of the result is **one date of one collection**: that is what the reviewer picks, and what the rebuild mosaics. `n` says how many granules it mosaics, and `cloud` is the mean over exactly those granules — which is why Review Focus #5 matters: averaging a granule that does not touch the alert would report a cloud figure for land the reviewer is not looking at, and would no longer match the `aggregate_mean` this replaces.

A row over the cloud limit is kept with `hidden=True` rather than dropped, so the timeline can draw it faded. The reviewer can see that an image exists before deciding to raise the limit.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_scene_query.py
import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon, box

from deforestation_alerts.scripts.scene_index import SceneTables

S2, LS, PL = scene_index.SOURCE_S2, scene_index.SOURCE_LANDSAT, scene_index.SOURCE_PLANET

#  west tile [0,0]-[2,2]   east tile [2,0]-[4,2]   wide tile [-1,-1]-[5,3]
TILES = gpd.GeoDataFrame(
    {"tile": ["W", "E", "WIDE", "SLANT"]},
    geometry=gpd.GeoSeries(
        [
            box(0, 0, 2, 2),
            box(2, 0, 4, 2),
            box(-1, -1, 5, 3),
            # a parallelogram whose envelope covers (1, 1) but which does not
            Polygon([(3, -1), (5, -1), (2, 3), (0, 3)]),
        ],
        crs="EPSG:4326",
    ),
    crs="EPSG:4326",
)


def _scenes(rows):
    return pd.DataFrame(rows, columns=list(scene_index.SCENE_COLUMNS)).astype({"date": "datetime64[ns]"})


def _tables(rows):
    return SceneTables(scenes=_scenes(rows), tiles=TILES)


ALERT = box(0.5, 0.5, 1.5, 1.5)  # inside W only


def test_only_the_tiles_that_overlap_the_alert_contribute():
    tables = _tables([
        (S2, pd.Timestamp("2026-05-10"), 10.0, "W", "kw"),
        (S2, pd.Timestamp("2026-05-10"), 90.0, "E", "ke"),
    ])
    sel = scene_query.scenes_for(tables, ALERT, date(2026, 5, 1), date(2026, 5, 31))
    assert len(sel) == 1
    assert sel.iloc[0]["n"] == 1
    assert sel.iloc[0]["cloud"] == pytest.approx(10.0)  # not 50.0: E never touches the alert


def test_overlapping_granules_of_one_date_become_one_row_with_their_mean_cloud():
    tables = _tables([
        (S2, pd.Timestamp("2026-05-10"), 10.0, "W", "kw"),
        (S2, pd.Timestamp("2026-05-10"), 30.0, "WIDE", "kd"),
    ])
    sel = scene_query.scenes_for(tables, ALERT, date(2026, 5, 1), date(2026, 5, 31))
    assert len(sel) == 1 and sel.iloc[0]["n"] == 2
    assert sel.iloc[0]["cloud"] == pytest.approx(20.0)
    assert sel.iloc[0]["id"] == scene_index.row_id(S2, date(2026, 5, 10))


def test_a_footprint_whose_envelope_covers_the_alert_is_still_excluded():
    tables = _tables([(LS, pd.Timestamp("2026-05-12"), 1.0, "SLANT", "ks")])
    assert TILES.set_index("tile").loc["SLANT", "geometry"].envelope.intersects(ALERT)
    sel = scene_query.scenes_for(tables, ALERT, date(2026, 5, 1), date(2026, 5, 31))
    assert len(sel) == 0


def test_two_collections_on_one_date_stay_two_rows():
    tables = _tables([
        (S2, pd.Timestamp("2026-05-10"), 10.0, "W", "kw"),
        (LS, pd.Timestamp("2026-05-10"), 20.0, "WIDE", "kd"),
    ])
    sel = scene_query.scenes_for(tables, ALERT, date(2026, 5, 1), date(2026, 5, 31))
    assert sorted(sel["source"]) == [LS, S2]


def test_a_planet_row_has_no_tile_and_covers_every_alert():
    tables = _tables([(PL, pd.Timestamp("2026-05-01"), float("nan"), "", "kp")])
    sel = scene_query.scenes_for(tables, box(40, 40, 41, 41), date(2026, 5, 1), date(2026, 5, 31))
    assert len(sel) == 1 and sel.iloc[0]["source"] == PL


def test_the_date_window_is_inclusive_at_both_ends():
    tables = _tables([
        (S2, pd.Timestamp("2026-05-01"), 1.0, "W", "a"),
        (S2, pd.Timestamp("2026-05-31"), 1.0, "W", "b"),
        (S2, pd.Timestamp("2026-06-01"), 1.0, "W", "c"),
    ])
    sel = scene_query.scenes_for(tables, ALERT, date(2026, 5, 1), date(2026, 5, 31))
    assert list(sel["date"]) == [pd.Timestamp("2026-05-01"), pd.Timestamp("2026-05-31")]


def test_a_row_over_the_cloud_limit_is_kept_and_marked_hidden():
    tables = _tables([
        (S2, pd.Timestamp("2026-05-10"), 12.0, "W", "a"),
        (S2, pd.Timestamp("2026-05-20"), 72.0, "W", "b"),
    ])
    sel = scene_query.scenes_for(tables, ALERT, date(2026, 5, 1), date(2026, 5, 31), max_cloud=40.0)
    assert list(sel["hidden"]) == [False, True]


def test_a_planet_row_is_never_hidden_by_the_cloud_limit():
    tables = _tables([(PL, pd.Timestamp("2026-05-01"), float("nan"), "", "kp")])
    sel = scene_query.scenes_for(tables, ALERT, date(2026, 5, 1), date(2026, 5, 31), max_cloud=0.0)
    assert list(sel["hidden"]) == [False]


def test_an_alert_outside_every_tile_returns_an_empty_frame_with_the_columns():
    # Review Focus #3: an alert on the AOI edge, or in a gap in the index.
    tables = _tables([(S2, pd.Timestamp("2026-05-10"), 10.0, "W", "kw")])
    sel = scene_query.scenes_for(tables, box(80, 80, 81, 81), date(2026, 5, 1), date(2026, 5, 31))
    assert len(sel) == 0
    assert list(sel.columns) == list(scene_query.SELECTION_COLUMNS)
    assert scene_query.source_counts(sel) == {S2: (0, 0), LS: (0, 0), PL: (0, 0)}
    assert scene_query.month_counts(sel) == {}


def test_an_empty_index_returns_an_empty_frame_with_the_columns():
    sel = scene_query.scenes_for(scene_index.empty_tables(), ALERT, date(2026, 5, 1), date(2026, 5, 31))
    assert len(sel) == 0 and list(sel.columns) == list(scene_query.SELECTION_COLUMNS)


def test_source_counts_split_visible_from_hidden():
    tables = _tables([
        (S2, pd.Timestamp("2026-05-10"), 12.0, "W", "a"),
        (S2, pd.Timestamp("2026-05-20"), 72.0, "W", "b"),
        (LS, pd.Timestamp("2026-05-21"), 5.0, "WIDE", "c"),
    ])
    sel = scene_query.scenes_for(tables, ALERT, date(2026, 5, 1), date(2026, 5, 31), max_cloud=40.0)
    assert scene_query.source_counts(sel) == {S2: (1, 1), LS: (1, 0), PL: (0, 0)}


def test_month_counts_count_only_the_visible_rows():
    tables = _tables([
        (S2, pd.Timestamp("2026-05-10"), 12.0, "W", "a"),
        (S2, pd.Timestamp("2026-05-20"), 72.0, "W", "b"),
        (S2, pd.Timestamp("2026-06-04"), 3.0, "W", "c"),
    ])
    sel = scene_query.scenes_for(tables, ALERT, date(2026, 5, 1), date(2026, 6, 30), max_cloud=40.0)
    assert scene_query.month_counts(sel) == {(2026, 5): 1, (2026, 6): 1}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_scene_query.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'scenes_for'`.

- [ ] **Step 3: Implement the query**

Add to `deforestation_alerts/scripts/scene_query.py`:

```python
import pandas as pd

SELECTION_COLUMNS = ("id", "source", "date", "cloud", "n", "hidden")

_EMPTY = pd.DataFrame(
    {
        "id": pd.Series(dtype=object),
        "source": pd.Series(dtype=object),
        "date": pd.Series(dtype="datetime64[ns]"),
        "cloud": pd.Series(dtype="float64"),
        "n": pd.Series(dtype="int64"),
        "hidden": pd.Series(dtype=bool),
    },
    columns=list(SELECTION_COLUMNS),
)


def overlapping_tiles(tiles, bbox) -> set:
    """The tiles whose true footprint meets ``bbox``.

    Two steps, and both are needed. The spatial index answers in bounding
    boxes, which is fast and admits too much; ``intersects`` on just those
    candidates then settles it against the real footprint. Dropping the second
    step would let a Landsat scene whose slanted footprint misses the alert
    look like coverage.
    """
    if tiles is None or len(tiles) == 0:
        return set()
    candidates = tiles.iloc[list(tiles.sindex.query(bbox, predicate="intersects"))]
    exact = candidates[candidates.geometry.intersects(bbox)]
    return set(exact["tile"])


def scenes_for(tables, bbox, start: date, end: date, *, max_cloud: Optional[float] = None) -> pd.DataFrame:
    """The timeline's rows for one alert: one row per (collection, date).

    ``start`` and ``end`` are both inclusive — they are the dates a reviewer
    asked to see, not a half-open interval.

    ``cloud`` is the mean over exactly the granules that overlap the alert,
    which reproduces the ``aggregate_mean`` of the per-alert request this
    replaces: that request's ``filterBounds`` chose *which* granules to average
    and ``CLOUDY_PIXEL_PERCENTAGE`` was always a whole-granule statistic, so
    averaging the same subset locally gives the same number.

    Rows over ``max_cloud`` are returned with ``hidden=True`` instead of being
    dropped, so the timeline can show the reviewer that an image exists there.
    """
    scenes = tables.scenes
    if scenes is None or len(scenes) == 0:
        return _EMPTY.copy()

    keep = set(overlapping_tiles(tables.tiles, bbox))
    # An empty tile means an AOI-wide mosaic (a Planet basemap): there is no
    # footprint to test, so it covers every alert.
    in_area = scenes["tile"].isin(keep) | (scenes["tile"] == "")
    in_window = scenes["date"].between(pd.Timestamp(start), pd.Timestamp(end))
    rows = scenes[in_area & in_window]
    if len(rows) == 0:
        return _EMPTY.copy()

    grouped = (
        rows.groupby(["source", "date"], as_index=False, sort=True)
        .agg(cloud=("cloud", "mean"), n=("key", "size"))
    )
    grouped["id"] = [row_id(src, d) for src, d in zip(grouped["source"], grouped["date"].dt.date)]
    # NaN cloud (a Planet basemap) is never over the limit: it has no figure
    # to compare, and hiding it would make the collection look unavailable.
    grouped["hidden"] = (
        False if max_cloud is None else grouped["cloud"].gt(float(max_cloud)).fillna(False)
    )
    grouped["hidden"] = grouped["hidden"].astype(bool)
    out = grouped.sort_values(["date", "source"], kind="stable").reset_index(drop=True)
    return out[list(SELECTION_COLUMNS)].astype({"n": "int64"})


def source_counts(sel) -> dict:
    """``{source: (visible, hidden)}`` for the collection toggles, every source
    present even at zero so the toolbar shows a count rather than nothing."""
    out = {name: (0, 0) for name in SOURCE_CODE}
    for name, part in sel.groupby("source", sort=False):
        hidden = int(part["hidden"].sum())
        out[name] = (len(part) - hidden, hidden)
    return out


def month_counts(sel) -> dict:
    """``{(year, month): visible rows}``, the number a collapsed month shows.

    Visible only: with a cloud limit set, a collapsed month claiming images the
    reviewer cannot reach on expanding it would be a lie.
    """
    shown = sel[~sel["hidden"]]
    if len(shown) == 0:
        return {}
    keys = zip(shown["date"].dt.year, shown["date"].dt.month)
    counts: dict = {}
    for key in keys:
        counts[key] = counts.get(key, 0) + 1
    return counts
```

`scene_query` imports `scene_index` at module level, which is safe and intended: `scene_index` imports `ee` only inside its functions, so taking its constants costs no Earth Engine dependency and the "never imports ee" test still passes.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_scene_query.py -v`
Expected: PASS, 26 tests.

- [ ] **Step 5: Commit**

```bash
git add deforestation_alerts/scripts/scene_query.py tests/test_scene_query.py
git commit -m "feat(imagery): answer one alert's timeline from the cached index"
```

---

### Task 6: The piecewise month axis and auto-collapse

**Files:**
- Modify: `deforestation_alerts/scripts/scene_query.py`
- Test: `tests/test_scene_query.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `scene_query.COLLAPSED_PX = 66.0`, `MonthSegment` (a `NamedTuple` with `year, month, start, end, days, x, w, ppd, collapsed`), `months_between(start, end) -> list[tuple[int, int]]`, `month_segments(start, end, collapsed, track_px, *, collapsed_px=COLLAPSED_PX) -> dict[tuple[int, int], MonthSegment]`, `x_for(segments, d, *, end=False) -> float`, `auto_collapsed(start, end, a0, a1) -> set[tuple[int, int]]`.

The axis is piecewise because collapsing is what makes a long window fit: an expanded month gets a share of the track proportional to its days, so dates inside it keep their true spacing, while a collapsed month is a fixed 66px block whatever its length. Every expanded month shares one pixels-per-day, so February is narrower than March — which is the point of putting dates on a date axis at all.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_scene_query.py
def test_months_between_spans_the_end_month_inclusively():
    assert scene_query.months_between(date(2026, 11, 20), date(2027, 2, 3)) == [
        (2026, 11), (2026, 12), (2027, 1), (2027, 2)
    ]


def test_months_between_a_single_month():
    assert scene_query.months_between(date(2026, 5, 3), date(2026, 5, 20)) == [(2026, 5)]


def test_the_first_and_last_segments_are_clipped_to_the_view():
    segs = scene_query.month_segments(date(2026, 3, 10), date(2026, 5, 20), set(), 1000.0)
    assert segs[(2026, 3)].start == date(2026, 3, 10) and segs[(2026, 3)].days == 22
    assert segs[(2026, 4)].days == 30
    assert segs[(2026, 5)].end == date(2026, 5, 20) and segs[(2026, 5)].days == 20


def test_expanded_months_share_one_pixels_per_day_and_fill_the_track():
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 5, 31), set(), 920.0)
    ppds = {round(s.ppd, 9) for s in segs.values()}
    assert len(ppds) == 1
    assert segs[(2026, 3)].x == pytest.approx(0.0)
    assert sum(s.w for s in segs.values()) == pytest.approx(920.0)
    # 31 + 30 + 31 days: March is wider than April, which is the whole point
    assert segs[(2026, 3)].w > segs[(2026, 4)].w


def test_a_collapsed_month_is_a_fixed_block_and_the_rest_reflow():
    collapsed = {(2026, 4)}
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 5, 31), collapsed, 920.0)
    assert segs[(2026, 4)].collapsed and segs[(2026, 4)].w == pytest.approx(66.0)
    assert segs[(2026, 4)].ppd == 0.0
    assert sum(s.w for s in segs.values()) == pytest.approx(920.0)
    assert segs[(2026, 5)].x == pytest.approx(segs[(2026, 4)].x + 66.0)


def test_every_month_collapsed_leaves_no_division_by_zero():
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 5, 31), {(2026, 3), (2026, 4), (2026, 5)}, 920.0)
    assert [s.w for s in segs.values()] == [66.0, 66.0, 66.0]
    assert all(s.ppd == 0.0 for s in segs.values())


def test_a_track_too_narrow_for_its_collapsed_blocks_does_not_go_negative():
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 5, 31), {(2026, 3), (2026, 4)}, 100.0)
    assert segs[(2026, 5)].ppd == 0.0 and segs[(2026, 5)].w == 0.0


def test_x_for_places_a_date_by_its_offset_inside_its_expanded_month():
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 4, 30), set(), 610.0)
    ppd = segs[(2026, 3)].ppd
    assert scene_query.x_for(segs, date(2026, 3, 1)) == pytest.approx(0.0)
    assert scene_query.x_for(segs, date(2026, 3, 11)) == pytest.approx(10 * ppd)


def test_x_for_collapses_every_date_of_a_collapsed_month_onto_its_block():
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 4, 30), {(2026, 3)}, 610.0)
    left = scene_query.x_for(segs, date(2026, 3, 4))
    assert left == pytest.approx(scene_query.x_for(segs, date(2026, 3, 29)))
    assert scene_query.x_for(segs, date(2026, 3, 29), end=True) == pytest.approx(left + 66.0)


def test_x_for_clamps_a_date_outside_the_view():
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 4, 30), set(), 610.0)
    assert scene_query.x_for(segs, date(2025, 1, 1)) == pytest.approx(0.0)
    assert scene_query.x_for(segs, date(2027, 1, 1)) == pytest.approx(610.0)


def test_auto_collapse_takes_the_months_wholly_inside_the_alert_window():
    got = scene_query.auto_collapsed(date(2026, 3, 3), date(2026, 10, 23), date(2026, 5, 3), date(2026, 8, 23))
    assert got == {(2026, 6), (2026, 7)}


def test_the_boundary_months_of_the_window_stay_expanded():
    # May holds the before candidates and August the after ones: collapsing
    # them would hide exactly the dates the reviewer has to choose between.
    got = scene_query.auto_collapsed(date(2026, 3, 3), date(2026, 10, 23), date(2026, 5, 3), date(2026, 8, 23))
    assert (2026, 5) not in got and (2026, 8) not in got


def test_a_short_alert_window_collapses_nothing():
    got = scene_query.auto_collapsed(date(2026, 3, 3), date(2026, 7, 23), date(2026, 5, 3), date(2026, 5, 20))
    assert got == set()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_scene_query.py -k "month or x_for or collapse" -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'months_between'`.

- [ ] **Step 3: Implement the axis**

Add to `deforestation_alerts/scripts/scene_query.py`:

```python
import calendar
from typing import Dict, Iterable, List, NamedTuple, Set

COLLAPSED_PX = 66.0


class MonthSegment(NamedTuple):
    """One month's slice of the track.

    ``days`` is the number of days of this month that the view shows, so the
    first and last months of a view are short. ``ppd`` is pixels per day,
    zero for a collapsed month, where every date lands on the block.
    """

    year: int
    month: int
    start: date
    end: date
    days: int
    x: float
    w: float
    ppd: float
    collapsed: bool


def months_between(start: date, end: date) -> List[tuple]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def month_segments(
    start: date, end: date, collapsed: Iterable, track_px: float, *, collapsed_px: float = COLLAPSED_PX
) -> Dict[tuple, MonthSegment]:
    """Lay the months of ``[start, end]`` across ``track_px`` pixels.

    Collapsed months take ``collapsed_px`` each; the expanded ones divide
    what is left in proportion to the days they show, all at one
    pixels-per-day, so a date's position still means its date.
    """
    collapsed = set(collapsed)
    keys = months_between(start, end)
    spans = {}
    for (y, m) in keys:
        first = date(y, m, 1)
        last = date(y, m, calendar.monthrange(y, m)[1])
        s = max(first, start)
        e = min(last, end)
        spans[(y, m)] = (s, e, (e - s).days + 1)

    n_col = sum(1 for k in keys if k in collapsed)
    exp_days = sum(spans[k][2] for k in keys if k not in collapsed)
    # A track too narrow for its blocks would otherwise give a negative
    # pixels-per-day and draw the later months off to the left.
    free = max(0.0, track_px - collapsed_px * n_col)
    ppd = free / exp_days if exp_days else 0.0

    segs, x = {}, 0.0
    for key in keys:
        s, e, days = spans[key]
        is_col = key in collapsed
        w = collapsed_px if is_col else days * ppd
        segs[key] = MonthSegment(key[0], key[1], s, e, days, x, w, 0.0 if is_col else ppd, is_col)
        x += w
    return segs


def x_for(segments: Dict[tuple, MonthSegment], d: date, *, end: bool = False) -> float:
    """The pixel offset of ``d`` on the track laid out by ``month_segments``.

    ``end=True`` asks for the right edge of what ``d`` occupies, which is what
    the alert-window band needs. A date outside the view clamps to the nearest
    edge rather than raising: a saved selection from an earlier, wider view is
    a normal thing to meet.
    """
    if not segments:
        return 0.0
    keys = sorted(segments)
    first, last = segments[keys[0]], segments[keys[-1]]
    if d < first.start:
        return 0.0
    if d > last.end:
        return last.x + last.w
    seg = segments[(d.year, d.month)]
    if seg.collapsed:
        return seg.x + (seg.w if end else 0.0)
    return seg.x + ((d - seg.start).days + (1 if end else 0)) * seg.ppd


def auto_collapsed(start: date, end: date, a0: date, a1: date) -> Set[tuple]:
    """The months to collapse when an alert first opens: those lying wholly
    inside the alert window.

    Inside the window every image shows partial clearing, so none of it is a
    legal before or after; those months are context, not choices, and
    collapsing them is what lets a GLAD window of several months fit beside
    the two margins the reviewer actually picks from. The window's own
    boundary months stay expanded, because they hold the nearest candidates.
    """
    out = set()
    for key in months_between(start, end):
        y, m = key
        s = max(date(y, m, 1), start)
        e = min(date(y, m, calendar.monthrange(y, m)[1]), end)
        if a0 <= s and e <= a1:
            out.add(key)
    return out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_scene_query.py -v`
Expected: PASS, 39 tests.

- [ ] **Step 5: Commit**

```bash
git add deforestation_alerts/scripts/scene_query.py tests/test_scene_query.py
git commit -m "feat(imagery): piecewise month axis with collapsible months"
```

---

### Task 7: The default before/after pick

**Files:**
- Modify: `deforestation_alerts/scripts/scene_query.py`
- Test: `tests/test_scene_query.py`

**Interfaces:**
- Consumes: `scenes_for`'s result, `scene_index.row_id`.
- Produces: `scene_query.default_pick(sel, a0, a1) -> tuple[Optional[str], Optional[str]]`, returning row ids.

The old code enforced correctness by only *offering* out-of-window images. That cannot hold here: a GLAD window can run for months, and a reviewer sometimes has to take an in-window image because it is the only cloud-free one there is. So in-window rows stay selectable and the guarantee moves into this function — nothing inside the window is ever picked *for* the reviewer.

**The boundary is inclusive** (Review Focus #1). A scene acquired on `alert_date_min` is the last look before the window opened, and one acquired on `alert_date_max` the first after it closed. These are often the best pair available, so an exclusive comparison here would silently drop the single most useful candidate on each side.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_scene_query.py
A0, A1 = date(2026, 5, 3), date(2026, 8, 23)


def _sel(rows, max_cloud=40.0):
    tables = _tables([(src, pd.Timestamp(d), cloud, "W", f"k{i}")
                      for i, (src, d, cloud) in enumerate(rows)])
    return scene_query.scenes_for(tables, ALERT, date(2026, 3, 1), date(2026, 10, 31), max_cloud=max_cloud)


def test_the_pick_is_the_nearest_clear_image_on_each_side():
    sel = _sel([
        (S2, "2026-04-02", 5.0),
        (S2, "2026-04-24", 8.0),   # nearest before
        (S2, "2026-06-10", 2.0),   # inside the window
        (S2, "2026-08-27", 3.0),   # nearest after
        (S2, "2026-09-30", 1.0),
    ])
    assert scene_query.default_pick(sel, A0, A1) == (
        scene_index.row_id(S2, date(2026, 4, 24)),
        scene_index.row_id(S2, date(2026, 8, 27)),
    )


def test_nothing_inside_the_alert_window_is_ever_picked():
    sel = _sel([(S2, "2026-06-10", 1.0), (S2, "2026-07-01", 1.0)])
    assert scene_query.default_pick(sel, A0, A1) == (None, None)


def test_a_scene_on_the_window_start_date_is_a_legal_before():
    sel = _sel([(S2, "2026-04-01", 5.0), (S2, "2026-05-03", 5.0)])
    assert scene_query.default_pick(sel, A0, A1)[0] == scene_index.row_id(S2, date(2026, 5, 3))


def test_a_scene_on_the_window_end_date_is_a_legal_after():
    sel = _sel([(S2, "2026-08-23", 5.0), (S2, "2026-09-15", 5.0)])
    assert scene_query.default_pick(sel, A0, A1)[1] == scene_index.row_id(S2, date(2026, 8, 23))


def test_a_side_whose_every_candidate_is_over_the_limit_is_left_unselected():
    # The spec's rule (§3): *before* is the latest scene **with cloud at or
    # under the limit**. With none, that side stays empty and the readout
    # points at the slider — auto-picking an 88%-cloud image instead would
    # make the one message that tells the reviewer to raise the limit
    # unreachable, and would look like a considered choice.
    sel = _sel([(S2, "2026-04-24", 88.0), (S2, "2026-04-02", 90.0), (S2, "2026-08-27", 4.0)])
    before, after = scene_query.default_pick(sel, A0, A1)
    assert before is None
    assert after == scene_index.row_id(S2, date(2026, 8, 27))


def test_raising_the_limit_makes_that_side_pick_again():
    rows = [(S2, "2026-04-24", 88.0), (S2, "2026-04-02", 90.0), (S2, "2026-08-27", 4.0)]
    assert scene_query.default_pick(_sel(rows, max_cloud=89.0), A0, A1)[0] == scene_index.row_id(
        S2, date(2026, 4, 24)
    )


def test_a_clear_scene_further_out_beats_a_cloudy_nearer_one():
    sel = _sel([(S2, "2026-04-28", 88.0), (S2, "2026-04-02", 6.0), (S2, "2026-08-27", 4.0)])
    assert scene_query.default_pick(sel, A0, A1)[0] == scene_index.row_id(S2, date(2026, 4, 2))


def test_planet_is_never_auto_picked():
    # A NICFI basemap is a whole month's mosaic: it cannot witness a date, so
    # it is a reviewer's deliberate choice, never a default.
    sel = _sel([(PL, "2026-04-01", float("nan")), (S2, "2026-04-02", 6.0), (S2, "2026-08-27", 4.0)])
    before, _ = scene_query.default_pick(sel, A0, A1)
    assert before == scene_index.row_id(S2, date(2026, 4, 2))


def test_only_planet_available_picks_nothing():
    sel = _sel([(PL, "2026-04-01", float("nan")), (PL, "2026-09-01", float("nan"))])
    assert scene_query.default_pick(sel, A0, A1) == (None, None)


def test_an_empty_selection_picks_nothing():
    assert scene_query.default_pick(_EMPTY_SEL(), A0, A1) == (None, None)


def _EMPTY_SEL():
    return scene_query.scenes_for(scene_index.empty_tables(), ALERT, date(2026, 3, 1), date(2026, 10, 31))


def test_one_side_available_still_picks_that_side():
    sel = _sel([(S2, "2026-04-02", 6.0)])
    assert scene_query.default_pick(sel, A0, A1) == (scene_index.row_id(S2, date(2026, 4, 2)), None)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_scene_query.py -k default_pick -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'default_pick'`.

- [ ] **Step 3: Implement the pick**

Add to `deforestation_alerts/scripts/scene_query.py`:

```python
from deforestation_alerts.scripts.scene_index import SOURCE_PLANET  # add to the existing import line


def default_pick(sel, a0: date, a1: date) -> tuple:
    """The pair to mark when an alert opens: ``(before_id, after_id)``.

    Nearest first, among the rows the cloud limit does not hide. A side whose
    every candidate is over the limit is left unselected: that is the spec's
    rule (§3), and it is what makes the readout's "raise the limit" guidance
    reachable. The rows are still drawn, faded, so the reviewer can see that
    images exist before deciding.

    Nothing inside ``[a0, a1]`` is ever picked, because an image taken while
    the alert was accumulating shows partial clearing and would understate the
    change. In-window rows stay *selectable* — a long window may leave no
    other choice — but the reviewer has to ask for one.

    Both bounds are inclusive: a scene acquired on ``a0`` is the last look
    before the window opened, and one acquired on ``a1`` the first after it
    closed.
    """
    if sel is None or len(sel) == 0:
        return (None, None)
    usable = sel[sel["source"] != SOURCE_PLANET]
    before_all = usable[usable["date"] <= pd.Timestamp(a0)]
    after_all = usable[usable["date"] >= pd.Timestamp(a1)]

    def _nearest(part, newest: bool):
        if len(part) == 0:
            return None
        pool = part[~part["hidden"]]
        if len(pool) == 0:
            return None
        row = pool.loc[pool["date"].idxmax() if newest else pool["date"].idxmin()]
        return str(row["id"])

    return (_nearest(before_all, newest=True), _nearest(after_all, newest=False))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_scene_query.py -v`
Expected: PASS, 50 tests.

- [ ] **Step 5: Commit**

```bash
git add deforestation_alerts/scripts/scene_query.py tests/test_scene_query.py
git commit -m "feat(imagery): default before/after pick outside the alert window"
```

---

### Task 8: Cache read, atomic write, chunk merge

**Files:**
- Create: `gui/scripts/scene_cache.py`
- Test: `tests/test_scene_cache.py`

**Interfaces:**
- Consumes: `paths.scenes_path/tiles_path/scene_manifest_path`, `scene_index.SceneTables/SceneManifest/Coverage/empty_tables`, `local_files.write_into`.
- Produces: `scene_cache.read_index(pdir) -> tuple[Optional[SceneTables], Optional[SceneManifest]]`, `write_index(pdir, tables, manifest) -> None`, `merge_tables(old, new) -> SceneTables`, `store_chunk(pdir, new_tables, window, digest, *, now, planet=None, replace=False) -> tuple[SceneTables, SceneManifest]`.

**Review Focus #4 lives here.** The background widener and a reviewer's "one more month" button can both be writing `scenes.parquet`. `store_chunk` therefore takes a per-project-directory lock, re-reads from disk inside it, merges onto *that*, and writes atomically — so the loser of a race adds to the winner's rows instead of overwriting them. A corrupt or truncated file is impossible because `local_files.write_into` writes a `.part` sibling and `os.replace`s it, which is the same guarantee `alert_store.save_gdf_to_parquet` relies on.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scene_cache.py
import threading
from datetime import date, datetime, timedelta, timezone

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from deforestation_alerts.project import paths
from deforestation_alerts.scripts import local_files, scene_index
from gui.scripts import scene_cache

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
S2, LS = scene_index.SOURCE_S2, scene_index.SOURCE_LANDSAT


def _tables(rows, tiles=("W",)):
    scenes = pd.DataFrame(rows, columns=list(scene_index.SCENE_COLUMNS)).astype({"date": "datetime64[ns]"})
    tdf = gpd.GeoDataFrame(
        {"tile": list(tiles)},
        geometry=gpd.GeoSeries([box(0, 0, 2, 2)] * len(tiles), crs=scene_index.WGS84),
        crs=scene_index.WGS84,
    )
    return scene_index.SceneTables(scenes=scenes, tiles=tdf)


CHUNK = _tables([(S2, pd.Timestamp("2026-05-10"), 10.0, "W", "k1"),
                 (LS, pd.Timestamp("2026-05-12"), 3.0, "W", "k2")])


def test_reading_a_project_with_no_index_yields_nothing():
    assert scene_cache.read_index(paths.project_dir("absent")) == (None, None)


def test_the_planet_access_state_survives_a_chunk_that_did_not_ask(data_root):
    """The toolbar disables the Planet toggle from ``manifest.planet.access``.
    A background widening chunk fetches no NICFI, so it must not overwrite what
    the first build learned."""
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(
        pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW,
        planet=scene_index.PlanetState(region="americas", access=True),
    )
    later = _tables([(S2, pd.Timestamp("2026-06-04"), 8.0, "W", "k3")])
    _, manifest = scene_cache.store_chunk(pdir, later, (date(2026, 6, 1), date(2026, 6, 30)), "d1", now=NOW)
    assert manifest.planet.access is True and manifest.planet.region == "americas"

    # A rebuild for a different AOI starts over: another region, another answer.
    _, manifest = scene_cache.store_chunk(
        pdir, later, (date(2026, 6, 1), date(2026, 6, 30)), "d2", now=NOW
    )
    assert manifest.planet.access is False and manifest.planet.region is None


def test_a_stored_chunk_round_trips_through_disk(data_root):
    pdir = paths.project_dir("p")
    tables, manifest = scene_cache.store_chunk(
        pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW
    )
    assert manifest.covered == scene_index.Coverage(start=date(2026, 5, 1), end=date(2026, 5, 31))
    assert manifest.aoi_digest == "d1" and manifest.refreshed == NOW
    assert manifest.counts == {S2: 1, LS: 1}

    back, back_manifest = scene_cache.read_index(pdir)
    assert back_manifest == manifest
    assert len(back.scenes) == 2 and set(back.scenes["key"]) == {"k1", "k2"}
    assert back.tiles.crs.to_epsg() == 4326
    assert back.scenes["date"].dtype == tables.scenes["date"].dtype


def test_a_second_chunk_widens_the_coverage_and_adds_its_rows(data_root):
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    later = _tables([(S2, pd.Timestamp("2026-06-04"), 8.0, "W", "k3")], tiles=("W", "E"))
    tables, manifest = scene_cache.store_chunk(
        pdir, later, (date(2026, 5, 31), date(2026, 6, 30)), "d1", now=NOW + timedelta(hours=1)
    )
    assert manifest.covered == scene_index.Coverage(start=date(2026, 5, 1), end=date(2026, 6, 30))
    assert len(tables.scenes) == 3
    assert set(tables.tiles["tile"]) == {"W", "E"}  # the new tile joined, the old one stayed


def test_a_refetched_scene_replaces_its_row_rather_than_duplicating_it(data_root):
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    corrected = _tables([(S2, pd.Timestamp("2026-05-10"), 44.0, "W", "k1")])
    tables, _ = scene_cache.store_chunk(
        pdir, corrected, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW
    )
    assert len(tables.scenes) == 2
    assert tables.scenes.set_index("key").loc["k1", "cloud"] == pytest.approx(44.0)


def test_replace_drops_the_old_rows(data_root):
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    fresh = _tables([(S2, pd.Timestamp("2026-07-01"), 1.0, "W", "k9")])
    tables, manifest = scene_cache.store_chunk(
        pdir, fresh, (date(2026, 7, 1), date(2026, 7, 31)), "d2", now=NOW, replace=True
    )
    assert list(tables.scenes["key"]) == ["k9"]
    assert manifest.aoi_digest == "d2"
    assert manifest.covered == scene_index.Coverage(start=date(2026, 7, 1), end=date(2026, 7, 31))


def test_an_index_of_another_aoi_is_not_merged_into(data_root):
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    fresh = _tables([(S2, pd.Timestamp("2026-05-10"), 1.0, "W", "k9")])
    tables, manifest = scene_cache.store_chunk(
        pdir, fresh, (date(2026, 5, 1), date(2026, 5, 31)), "d2", now=NOW
    )
    assert list(tables.scenes["key"]) == ["k9"] and manifest.aoi_digest == "d2"


def test_a_failed_write_leaves_the_previous_index_in_place(data_root, monkeypatch):
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    bad = _tables([(S2, pd.Timestamp("2026-06-04"), 8.0, "W", "k3")])

    boom = {"n": 0}
    real = scene_cache._write_manifest

    def explode(*a, **k):
        boom["n"] += 1
        raise OSError("disk full")

    monkeypatch.setattr(scene_cache, "_write_manifest", explode)
    with pytest.raises(OSError):
        scene_cache.store_chunk(pdir, bad, (date(2026, 6, 1), date(2026, 6, 30)), "d1", now=NOW)
    monkeypatch.setattr(scene_cache, "_write_manifest", real)

    tables, manifest = scene_cache.read_index(pdir)
    assert manifest.covered.end == date(2026, 5, 31)
    assert not list(paths.imagery_dir(pdir).glob("*.part"))
    # The index is still a consistent generation, not a mix that reads as
    # scenes whose tiles are missing: every scene row names a known tile.
    assert set(tables.scenes["tile"]) <= set(tables.tiles["tile"])


def test_a_failed_scene_write_still_leaves_every_scene_row_findable(data_root, monkeypatch):
    """The three files are written tiles-first for this reason: a crash between
    two replacements must not leave scene rows naming tiles that are not there."""
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    new_tile = _tables([(S2, pd.Timestamp("2026-06-04"), 8.0, "E", "k3")], tiles=("E",))

    real = local_files.write_into

    def explode(path, *a, **k):
        if path == paths.scenes_path(pdir):
            raise OSError("disk full")
        return real(path, *a, **k)

    monkeypatch.setattr(scene_cache.local_files, "write_into", explode)
    with pytest.raises(OSError):
        scene_cache.store_chunk(pdir, new_tile, (date(2026, 6, 1), date(2026, 6, 30)), "d1", now=NOW)
    monkeypatch.setattr(scene_cache.local_files, "write_into", real)

    tables, manifest = scene_cache.read_index(pdir)
    assert set(tables.scenes["key"]) == {"k1", "k2"}          # the old scenes
    assert set(tables.scenes["tile"]) <= set(tables.tiles["tile"])  # still findable
    assert manifest.covered.end == date(2026, 5, 31)


def test_two_threads_storing_chunks_both_land(data_root):
    """Review Focus #4: the background widener and a foreground widen-by-a-month
    write the same parquet. The loser must add to the winner's rows."""
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    start = threading.Barrier(2)

    def add(month, key):
        start.wait()
        scene_cache.store_chunk(
            pdir,
            _tables([(S2, pd.Timestamp(f"2026-{month:02d}-04"), 8.0, "W", key)]),
            (date(2026, month, 1), date(2026, month, 28)),
            "d1",
            now=NOW,
        )

    threads = [threading.Thread(target=add, args=(6, "k6")), threading.Thread(target=add, args=(4, "k4"))]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    tables, manifest = scene_cache.read_index(pdir)
    assert set(tables.scenes["key"]) == {"k1", "k2", "k4", "k6"}
    # min of the starts, max of the ends: 1 Apr from the April chunk, 28 Jun
    # from the June one — the windows above stop on the 28th.
    assert manifest.covered == scene_index.Coverage(start=date(2026, 4, 1), end=date(2026, 6, 28))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_scene_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: gui.scripts.scene_cache`.

- [ ] **Step 3: Implement the cache**

```python
# gui/scripts/scene_cache.py
"""The scene index on disk: ``<project_dir>/imagery/``.

Three files, written together and read together: ``scenes.parquet`` (one row
per granule), ``tiles.parquet`` (each tile's footprint once) and
``index.json`` (what is in them and how old it is).

One index serves the whole project — every period and every set — because the
granules over an AOI do not depend on which alerts were run over them. A
reviewer moving between alerts, periods or sets pays no Earth Engine round
trip at all.
"""

import json
import logging
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import geopandas as gpd
import pandas as pd

from deforestation_alerts.project import paths
from deforestation_alerts.scripts import local_files
from deforestation_alerts.scripts.scene_index import (
    SCENE_COLUMNS, WGS84, Coverage, PlanetState, SceneManifest, SceneTables, empty_tables,
)

logger = logging.getLogger(__name__)

#: One lock per project directory. Both the background widener and a
#: reviewer's "one more month" write ``scenes.parquet``; the lock is what
#: makes the loser merge onto the winner's rows instead of over them.
_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(pdir) -> threading.Lock:
    key = str(Path(pdir).resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def read_index(pdir) -> Tuple[Optional[SceneTables], Optional[SceneManifest]]:
    """The cached index, or ``(None, None)`` when there is none to read.

    A half-written or unreadable index reads as absent rather than raising:
    the caller's answer to both is the same, a rebuild.
    """
    manifest_path = paths.scene_manifest_path(pdir)
    if not (manifest_path.exists() and paths.scenes_path(pdir).exists()):
        return (None, None)
    try:
        manifest = SceneManifest.model_validate_json(manifest_path.read_text())
        scenes = pd.read_parquet(paths.scenes_path(pdir))
        tiles = gpd.read_parquet(paths.tiles_path(pdir))
    except Exception as exc:  # noqa: BLE001
        logger.warning("unreadable scene index in %s: %s", pdir, exc)
        return (None, None)
    return (SceneTables(scenes=scenes[list(SCENE_COLUMNS)], tiles=tiles), manifest)


def _write_manifest(pdir, manifest: SceneManifest) -> None:
    with local_files.write_into(paths.scene_manifest_path(pdir)) as part:
        part.write_text(manifest.model_dump_json(indent=2))


def write_index(pdir, tables: SceneTables, manifest: SceneManifest) -> None:
    """All three files, each replaced atomically — and in an order that makes
    every intermediate state a consistent one.

    ``write_into`` makes each individual replacement atomic, but three
    replacements are not one transaction: a crash, or a reader arriving
    between two of them, sees a mix. The order is what makes every mix
    readable, and it runs **tiles, scenes, manifest**:

    * tiles first, because a tile footprint nothing references yet is inert,
      whereas a scene row naming a tile the tiles table does not hold would
      be silently invisible to ``overlapping_tiles`` — a scene that exists
      and cannot be found;
    * the manifest last, because it is the thing that claims coverage, and a
      manifest describing less than the parquets hold only costs a refetch.

    So the three states a crash can leave are new/old/old, new/new/old and
    new/new/new, and all three answer correctly. ``read_index`` therefore
    needs no lock.
    """
    with local_files.write_into(paths.tiles_path(pdir)) as part:
        gpd.GeoDataFrame(tables.tiles, geometry="geometry", crs=WGS84).to_parquet(part, index=False)
    with local_files.write_into(paths.scenes_path(pdir)) as part:
        tables.scenes.to_parquet(part, index=False)
    _write_manifest(pdir, manifest)


def merge_tables(old: SceneTables, new: SceneTables) -> SceneTables:
    """``new`` onto ``old``, the new row winning where both have one.

    The identity of a granule is ``(source, key)``: a refetched tail brings
    back scenes the index already holds, and a corrected cloud figure should
    replace the old one rather than add a second row that would skew the
    per-alert mean.
    """
    scenes = pd.concat([old.scenes, new.scenes], ignore_index=True)
    scenes = (
        scenes.drop_duplicates(subset=["source", "key"], keep="last")
        .sort_values(["date", "source", "tile"], kind="stable")
        .reset_index(drop=True)
    )
    tiles = pd.concat([old.tiles, new.tiles], ignore_index=True)
    tiles = tiles.drop_duplicates(subset=["tile"], keep="last").reset_index(drop=True)
    return SceneTables(
        scenes=scenes[list(SCENE_COLUMNS)],
        tiles=gpd.GeoDataFrame(tiles, geometry="geometry", crs=WGS84),
    )


def store_chunk(
    pdir, new_tables: SceneTables, window: Tuple[date, date], digest: str, *, now: datetime,
    planet: Optional[PlanetState] = None, replace: bool = False,
) -> Tuple[SceneTables, SceneManifest]:
    """Merge one fetched window into the index on disk and return the result.

    The read, merge and write all happen under the project's lock, and the
    read happens *inside* it rather than being passed in, so a concurrent
    writer's rows are never lost. ``replace`` discards whatever was there —
    a rebuild. An index built for another AOI is discarded the same way
    whether or not ``replace`` was asked for: its tile footprints describe a
    different area.

    ``planet`` is the NICFI access state this fetch observed. ``None`` keeps
    whatever the manifest already recorded, so a widening chunk that did not
    ask Planet anything does not erase the answer the first build got.
    """
    with _lock_for(pdir):
        old, old_manifest = read_index(pdir)
        stale_aoi = old_manifest is not None and old_manifest.aoi_digest != digest
        if replace or old is None or stale_aoi:
            tables = merge_tables(empty_tables(), new_tables)
            covered = Coverage(start=window[0], end=window[1])
        else:
            tables = merge_tables(old, new_tables)
            covered = Coverage(
                start=min(old_manifest.covered.start, window[0]),
                end=max(old_manifest.covered.end, window[1]),
            )
        counts = {str(k): int(v) for k, v in tables.scenes["source"].value_counts().items()}
        kept = old_manifest.planet if (old_manifest is not None and not stale_aoi) else None
        manifest = SceneManifest(
            aoi_digest=digest, refreshed=now, covered=covered, counts=counts,
            planet=planet or kept or PlanetState(),
        )
        write_index(pdir, tables, manifest)
        logger.info("scene index of %s: %d rows, %s..%s", pdir, len(tables.scenes), covered.start, covered.end)
        return (tables, manifest)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_scene_cache.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add gui/scripts/scene_cache.py tests/test_scene_cache.py
git commit -m "feat(imagery): atomic scene-index cache with concurrent-safe chunk merge"
```

---

### Task 9: `SceneIndexLoader` and the background widener

**Files:**
- Create: `gui/scripts/scene_loader.py`
- Modify: `gui/store/state_manager.py:45-75`, `gui/solara_app.py:881`
- Test: `tests/test_scene_loader.py`

**Interfaces:**
- Consumes: `scene_cache.read_index/store_chunk`, `scene_query.needs_refresh/refresh_window`, `scene_index.scene_request/planet_request/parse_scenes/parse_planet`, `gui.scripts.aoi_io.ee_aoi`, `gui.scripts.vis_layers.NICFI_COLLECTIONS/nicfi_region`.
- Produces: `app_state.scene_index` (`SceneTables | None`), `app_state.scene_manifest` (`SceneManifest | None`), `app_state.scene_index_pending` (bool), `app_state.scene_index_error` (str | None), `app_state.scene_index_retry` (int), `app_state.scene_widening` (bool), `app_state.scene_window_request` (`tuple[date, date] | None`); `scene_loader.FOREGROUND_MONTHS = 2`, `BACKGROUND_MONTHS = 6`, `shift_months(d, months) -> date`, `wanted_window(project, today, months) -> Optional[tuple[date, date]]`, `widest(*windows) -> Optional[tuple[date, date]]`, `SceneIndexLoader(app_state, gee_interface)`.

**Why this task is opus.** Three traps at once. The loader is a `@solara.component` mounted at app level beside `RemoteFactsLoader`, so `use_task` applies here — but it must publish the index read from disk *before* attempting any fetch, because of Review Focus #2. The background widener must not hold the project lock across an `await`. And `prefer_threaded=False` matters: `gee_interface.get_info_async` is awaited on the render loop, as `RemoteFactsLoader` does, while the parquet work goes through `to_thread_in_context` so its reactive publishes still reach the browser.

**Review Focus #2 lives here.** When the index is stale and the tail refetch fails — an expired token, a dropped network, an Earth Engine outage — the reviewer must keep the index they had, labelled with its age, and be told the refresh failed. Clearing `scene_index` on a failed refresh would turn a working timeline into an empty one for no reason.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scene_loader.py
import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pytest
import reacton
from pysepal.solara.notifications import NotificationProvider
from shapely.geometry import box

from deforestation_alerts.project import paths
from deforestation_alerts.project.models import AoiRecord, Period, Project
from deforestation_alerts.scripts import scene_index
from gui.scripts import scene_cache
from gui.scripts import scene_loader as sl
from gui.store.state_manager import AppState

S2 = scene_index.SOURCE_S2
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)

RAW = {
    "scenes": {"source": [S2], "millis": [1775174400000], "cloud": [12.5], "tile": ["W"],
               "key": ["COPERNICUS/S2_HARMONIZED/A"]},
    "tiles": {"tile": ["W"], "gtype": ["Polygon"],
              "coords": [[[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]]},
}


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


def test_without_a_project_or_an_active_period_there_is_no_window():
    assert sl.wanted_window(None, date(2026, 10, 2), 2) is None
    assert sl.wanted_window(_p(), date(2026, 10, 2), 2) is None


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


def test_an_empty_project_fetches_the_index_and_caches_it(data_root, monkeypatch):
    asked = []

    async def get_info_async(obj):
        asked.append(obj)
        return RAW

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: ("SCENES", s, e))
    monkeypatch.setattr(sl, "_planet_chunk", lambda *a, **k: None)
    monkeypatch.setattr(sl.aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)  # no widening to wait on
    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: state.scene_index.value is not None)
            assert len(state.scene_index.value.scenes) == 1
            assert state.scene_manifest.value.covered.start == date(2026, 3, 3)
            assert state.scene_index_error.value is None
            assert state.scene_index_pending.value is False
            assert asked == [("SCENES", date(2026, 3, 3), date(2026, 10, 2))]
            _, manifest = scene_cache.read_index(paths.project_dir("sl"))
            assert manifest is not None  # it went to disk, so the next session is free
        finally:
            rc.close()

    asyncio.run(main())


def test_a_fresh_cached_index_is_served_without_any_request(data_root, monkeypatch):
    pdir = paths.project_dir("sl")
    scene_cache.store_chunk(
        pdir, scene_index.parse_scenes(RAW), (date(2026, 3, 1), date(2026, 11, 1)), "d1",
        now=datetime.now(timezone.utc),
    )
    asked = []

    async def get_info_async(obj):
        asked.append(obj)
        return RAW

    monkeypatch.setattr(sl.aoi_io, "ee_aoi", lambda result, pdir: "AOI")
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


def test_a_failed_tail_refresh_keeps_the_stale_index_and_reports_the_failure(data_root, monkeypatch):
    """Review Focus #2: a 24h-stale index whose refetch fails must still answer."""
    pdir = paths.project_dir("sl")
    scene_cache.store_chunk(
        pdir, scene_index.parse_scenes(RAW), (date(2026, 3, 1), date(2026, 11, 1)), "d1",
        now=datetime.now(timezone.utc) - timedelta(hours=30),
    )

    async def get_info_async(obj):
        raise RuntimeError("Earth Engine unavailable")

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: "SCENES")
    monkeypatch.setattr(sl.aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)
    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: state.scene_index_error.value is not None)
            assert state.scene_index.value is not None
            assert len(state.scene_index.value.scenes) == 1
            assert state.scene_manifest.value.refreshed < datetime.now(timezone.utc) - timedelta(hours=24)
            assert state.scene_index_pending.value is False
            assert "Earth Engine unavailable" in state.scene_index_error.value
        finally:
            rc.close()

    asyncio.run(main())


def test_a_widen_request_beyond_the_coverage_is_fetched_in_the_foreground(data_root, monkeypatch):
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
    monkeypatch.setattr(sl, "_planet_chunk", lambda *a, **k: None)
    monkeypatch.setattr(sl.aoi_io, "ee_aoi", lambda result, pdir: "AOI")
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
            assert len(asked) >= 1
        finally:
            rc.close()

    asyncio.run(main())


def test_an_earth_engine_aoi_waits_for_the_picker_instead_of_failing(data_root, monkeypatch):
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
    monkeypatch.setattr(sl, "_planet_chunk", lambda *a, **k: None)
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


def test_the_widener_extends_the_coverage_a_month_at_a_time(data_root, monkeypatch):
    windows = []

    async def get_info_async(obj):
        windows.append(obj)
        return RAW

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: (s, e))
    monkeypatch.setattr(sl, "_planet_chunk", lambda *a, **k: None)
    monkeypatch.setattr(sl.aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    monkeypatch.setattr(sl, "FOREGROUND_MONTHS", 2)
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 4)
    state = AppState()
    state.load_project_state(_with_period(_p()))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: state.scene_widening.value is False
                                 and state.scene_manifest.value is not None
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


def test_a_changed_aoi_rebuilds_the_index_rather_than_merging_into_it(data_root, monkeypatch):
    pdir = paths.project_dir("sl")
    scene_cache.store_chunk(
        pdir, scene_index.parse_scenes(RAW), (date(2026, 3, 1), date(2026, 11, 1)), "OLD",
        now=datetime.now(timezone.utc),
    )

    async def get_info_async(obj):
        return {"scenes": dict(scene_index.EMPTY_SCENE_ARRAYS),
                "tiles": {"tile": [], "coords": [], "gtype": []}}

    monkeypatch.setattr(sl.scene_index, "scene_request", lambda aoi, s, e: "SCENES")
    monkeypatch.setattr(sl, "_planet_chunk", lambda *a, **k: None)
    monkeypatch.setattr(sl.aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    monkeypatch.setattr(sl, "BACKGROUND_MONTHS", 2)
    state = AppState()
    state.load_project_state(_with_period(_p(digest="d1")))

    async def main():
        box_, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await _settle(lambda: state.scene_manifest.value is not None
                                 and state.scene_manifest.value.aoi_digest == "d1")
            assert len(state.scene_index.value.scenes) == 0
        finally:
            rc.close()

    asyncio.run(main())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_scene_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: gui.scripts.scene_loader`.

- [ ] **Step 3: Add the reactives**

In `gui/store/state_manager.py`, inside `AppState.__init__` after the coverage block:

```python
        # The project's scene index, fetched by gui.scripts.scene_loader.
        # SceneIndexLoader (holds DataFrames; cached on disk, not in the
        # project document). The timeline reads it for every alert, so it is
        # compared by identity: a merged copy is a new index.
        self.scene_index = solara.reactive(None, equals=lambda a, b: a is b)
        self.scene_manifest = solara.reactive(None, equals=lambda a, b: a is b)
        self.scene_index_pending = solara.reactive(False)
        self.scene_index_error = solara.reactive(None)
        self.scene_index_retry = solara.reactive(0)  # the timeline's "Try again" bumps it
        self.scene_widening = solara.reactive(False)  # the ±6-month background fill is running
        # The widest window the timeline has been asked to show, published by
        # the strip's "+1 month" buttons. The loader unions it with the default
        # ±2 months, so a month outside the cache is fetched rather than drawn
        # empty. A tuple of two dates, or None for "the default is enough".
        self.scene_window_request = solara.reactive(None)
```

- [ ] **Step 4: Write the loader**

```python
# gui/scripts/scene_loader.py
"""Keeps ``app_state.scene_index`` in step with the active project.

Mounted once beside ``RemoteFactsLoader`` in the shell, not inside a step, so
it survives every navigation and a reviewer opening the Analysis step finds
the index already there.

Two windows. The *foreground* one — the active period's alert window padded by
``FOREGROUND_MONTHS`` on each side — is what the timeline shows by default and
what the loader fetches before anything else. The *background* one, padded by
``BACKGROUND_MONTHS``, is filled afterwards a month at a time, so a reviewer
pressing "one more month" usually finds it already loaded.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple

import pandas as pd
import solara
import solara.lab

from deforestation_alerts.project import paths
from deforestation_alerts.scripts import scene_index, scene_query
from gui.i18n import t
from gui.scripts import aoi_io, remote_facts, scene_cache, vis_layers
from gui.scripts.solara_threads import to_thread_in_context

logger = logging.getLogger(__name__)

FOREGROUND_MONTHS = 2
BACKGROUND_MONTHS = 6


def shift_months(d: date, months: int) -> date:
    """``d`` moved by whole months, the day clamped to the target month's length."""
    import calendar

    total = (d.year * 12 + d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def wanted_window(project, today: date, months: int) -> Optional[Tuple[date, date]]:
    """The active period's alert window padded by ``months`` on each side.

    Clamped at ``today``: there are no images of next week, and asking for
    them would make every index look stale for ever.
    """
    if project is None or not project.active_period:
        return None
    period = project.periods.get(project.active_period)
    if period is None:
        return None
    return (shift_months(period.start, -months), min(today, shift_months(period.end, months)))


def widest(*windows) -> Optional[Tuple[date, date]]:
    """The smallest window containing every one given, ignoring ``None``s."""
    live = [w for w in windows if w is not None]
    if not live:
        return None
    return (min(w[0] for w in live), max(w[1] for w in live))


def _planet_chunk(gee_interface, project, window):
    """``(region, request)`` for ``window``, or ``None`` when the AOI has no
    region mapping. Separated so tests can switch Planet off in one line."""
    bounds = project.aoi.bounds if project.aoi is not None else None
    if not bounds:
        return None
    region = vis_layers.nicfi_region(bounds)
    if region not in vis_layers.NICFI_COLLECTIONS:
        return None
    return (region, scene_index.planet_request(
        vis_layers.NICFI_COLLECTIONS[region], window[0], window[1]))


@solara.component
def SceneIndexLoader(app_state, gee_interface):
    p = app_state.project.value
    name = p.name if p is not None else None
    digest = p.aoi.digest if p is not None and p.aoi is not None else None
    today = date.today()
    # The AOI geometry comes from the picker's selection or from the staged
    # sidecar, and for a moment after a project opens it is available from
    # neither: ``AppState._install`` clears ``aoi_result`` and the always-mounted
    # AOI step republishes it. ``remote_facts.aoi_origin`` is the established
    # answer — None means "wait", and because ``origin`` is a dependency the
    # task re-runs by itself when the AOI step hands the selection over.
    origin = remote_facts.aoi_origin(p, app_state.aoi_result.value)
    requested = app_state.scene_window_request.value
    fg = widest(wanted_window(p, today, FOREGROUND_MONTHS), requested)
    bg = widest(wanted_window(p, today, BACKGROUND_MONTHS), requested)
    retry = app_state.scene_index_retry.value

    def _still_current() -> bool:
        """The project and AOI this fetch was started for are still the open
        ones. ``use_task`` cancels at the next await, but the publish below
        follows one, so the guard is what keeps a slow reply from landing on
        the project that replaced it."""
        live = app_state.project.value
        return (live is not None and live.name == name
                and live.aoi is not None and live.aoi.digest == digest)

    async def _fetch(window, mode: str) -> None:
        """One request, merged into the cache, published."""
        pdir = paths.project_dir(name)
        aoi = (aoi_io.ee_aoi(app_state.aoi_result.value, None) if origin == "result"
               else aoi_io.ee_aoi(None, pdir))
        raw = await gee_interface.get_info_async(scene_index.scene_request(aoi, window[0], window[1]))
        tables = scene_index.parse_scenes(raw)

        chunk = _planet_chunk(gee_interface, p, window)
        planet = scene_index.PlanetState(region=chunk[0] if chunk else None, access=False)
        if chunk is not None:
            try:
                rows = scene_index.parse_planet(await gee_interface.get_info_async(chunk[1]))
                tables = scene_index.SceneTables(
                    scenes=pd.concat([tables.scenes, rows], ignore_index=True), tiles=tables.tiles
                )
                planet = scene_index.PlanetState(region=chunk[0], access=True)
            except Exception as exc:  # noqa: BLE001
                # NICFI is licence-gated: no access is the normal case, and it
                # must not cost the reviewer their Sentinel-2 and Landsat rows.
                # It is *recorded* rather than only logged, so the toolbar can
                # disable the Planet toggle and say why (spec §5.4).
                logger.info("no Planet NICFI rows for %s: %s", name, exc)

        merged, manifest = await to_thread_in_context(
            scene_cache.store_chunk, pdir, tables, window, digest, planet=planet,
            now=datetime.now(timezone.utc), replace=(mode == "rebuild"),
        )
        if not _still_current():
            return  # another project opened while this was in flight
        app_state.scene_index.set(merged)
        app_state.scene_manifest.set(manifest)

    # ---- the index on disk, then the foreground window
    async def _load():
        if name is None or digest is None or fg is None or origin is None:
            app_state.scene_index.set(None)
            app_state.scene_manifest.set(None)
            return
        pdir = paths.project_dir(name)
        tables, manifest = await to_thread_in_context(scene_cache.read_index, pdir)
        # Published before any fetch: a stale index answers while it refreshes,
        # and keeps answering if the refresh fails.
        if tables is not None and manifest is not None and manifest.aoi_digest == digest:
            app_state.scene_index.set(tables)
            app_state.scene_manifest.set(manifest)
        else:
            app_state.scene_index.set(None)
            app_state.scene_manifest.set(None)
            manifest = None

        mode = scene_query.needs_refresh(manifest, fg, digest=digest, now=datetime.now(timezone.utc))
        if not mode:
            app_state.scene_index_error.set(None)
            return
        app_state.scene_index_pending.set(True)
        try:
            await _fetch(scene_query.refresh_window(manifest, fg, mode), mode)
            app_state.scene_index_error.set(None)
        except Exception as exc:  # noqa: BLE001
            # The index already published stays published: see the module note.
            logger.warning("scene index of %s not refreshed: %s", name, exc)
            app_state.scene_index_error.set(str(exc))
        finally:
            app_state.scene_index_pending.set(False)

    load = solara.lab.use_task(_load, dependencies=[name, digest, fg, origin, retry],
                               prefer_threaded=False, raise_error=False)

    # ---- the ±6-month buffer, one month at a time, once the foreground settled
    async def _widen():
        if load.pending or name is None or bg is None or origin is None:
            return
        if app_state.scene_index.value is None:
            return
        app_state.scene_widening.set(True)
        try:
            for _ in range(2 * BACKGROUND_MONTHS + 2):  # a bound, not a count
                manifest = app_state.scene_manifest.value
                if scene_query.needs_refresh(manifest, bg, digest=digest,
                                             now=datetime.now(timezone.utc)) != "widen":
                    return
                side = scene_query.refresh_window(manifest, bg, "widen")
                if side[1] < side[0] + timedelta(days=1):
                    return
                # One month per request: each one publishes, so the timeline
                # widens while the reviewer works instead of in one jump.
                #
                # The chunk has to **touch the existing coverage**, because
                # ``store_chunk`` records coverage as min-of-starts to
                # max-of-ends: a chunk taken from the far end of the missing
                # range would make the manifest claim every month in between
                # without fetching any of them. ``refresh_window`` returns the
                # later side as ``(covered.end, want_end)`` and the earlier one
                # as ``(want_start, covered.start)``, so the anchored end is
                # whichever one equals the coverage edge.
                if side[1] <= manifest.covered.start:
                    chunk = (max(side[0], shift_months(side[1], -1)), side[1])
                else:
                    chunk = (side[0], min(side[1], shift_months(side[0], 1)))
                await _fetch(chunk, "widen")
        except Exception as exc:  # noqa: BLE001
            logger.info("scene index of %s not widened: %s", name, exc)  # the ±2 months still answer
        finally:
            app_state.scene_widening.set(False)

    solara.lab.use_task(_widen, dependencies=[name, digest, bg, origin, retry, load.pending],
                        prefer_threaded=False, raise_error=False)
```

Note on the widen chunking: `refresh_window(..., "widen")` returns the later side first and the earlier side once the later one is covered, so this loop alternates sides as the coverage grows without needing to track which side it is on. What it *does* need to know is which end of the returned range is anchored to the coverage — hence the `side[1] <= manifest.covered.start` test. `store_chunk` has no record of disjoint intervals, only a single `Coverage`, so a non-contiguous chunk would silently widen that range over months nothing fetched; growing one month at a time *from the coverage edge* is what keeps the single interval honest.

Note on `load.pending` in the widen dependencies: it is a `use_task` *status* flag, not a worker publish. It goes True then False once per `_load`, so `_widen` is re-run once per load rather than looping — and the `needs_refresh(...) != "widen"` guard at the top of each iteration is what ends it. `app_state.scene_manifest`, which `_fetch` does publish, is deliberately **not** a dependency.

- [ ] **Step 5: Mount it in the shell**

In `gui/solara_app.py`, beside the existing loader near line 881:

```python
    RemoteFactsLoader(app_state=app_state, gee_interface=gee_interface)
    SceneIndexLoader(app_state=app_state, gee_interface=gee_interface)
```

and with the imports near line 59:

```python
from gui.scripts.scene_loader import SceneIndexLoader
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_scene_loader.py tests/test_app_wiring.py -v`
Expected: PASS. `test_app_wiring.py` may assert the shell's mounted components; if it lists them, add `SceneIndexLoader` there.

- [ ] **Step 7: Commit**

```bash
git add gui/scripts/scene_loader.py gui/store/state_manager.py gui/solara_app.py tests/test_scene_loader.py
git commit -m "feat(imagery): load the scene index per project and widen it in the background"
```

---

### Task 10: The timeline widget

**Files:**
- Create: `gui/tile/analysis/timeline.py`
- Modify: `deforestation_alerts/message/{en,es-ES,fr}/ui.json`
- Test: `tests/test_timeline.py`

**Interfaces:**
- Consumes: `scene_query.scenes_for/month_segments/x_for/auto_collapsed/default_pick/source_counts/month_counts`, `scene_index.SOURCE_*/row_id`, `app_state.scene_index/scene_manifest/scene_index_error/scene_index_pending/scene_index_retry/scene_window_request`, `scene_loader.shift_months`.
- Produces: `timeline.build_timeline(app_state, analysis, widgets) -> ipyvuetify.Card`; `timeline.TRACK_PX = 1120.0`, `LANE_H = 45`, `AXIS_H = 27`, `BAND = {source: (top, height)}`, `SRC_COLOR = {source: css}`; `timeline.view_window(ctx, project, extra) -> Optional[tuple[date, date]]`; `timeline.readout(sel, row_id) -> str`; `timeline.days_apart(before_id, after_id) -> Optional[int]`; `timeline.info_tuple(row) -> list` building the five-element contract; `timeline.stripe_hit_style(segments, d, source) -> str`, `timeline.stripe_bar_style(row, *, selected) -> str`, `timeline.stripe_cursor(row) -> str` and `timeline.month_bar_style(segments, d) -> str` for the Planet month bar; `timeline._walk(widget)`, the child-tree generator Task 11's tests reuse.

**Why this task is opus.** It is a raw-widget builder under MapApp's `v-if`, so none of it may be a `@solara.component` — a Solara component here renders once and comes back blank when the reviewer closes and reopens the step. It is also dense absolute-positioned geometry over a piecewise axis, with roughly 170 clickable children, where an off-by-one in a percentage puts a stripe on the wrong date.

**The geometry, from the approved mockup.** A 1120px nominal track, addressed in percentages so it scales. Two 45px lanes with a 27px axis between them; inside a lane each collection owns a band — Sentinel-2 at top 0 height 17, Landsat at top 19 height 14, Planet at top 35 height 10 — so stripes of different collections cannot collide and position tells them apart before colour does. A stripe is 2.5px wide, 4px when selected, sitting in an 11px hit area; at the ±6-month maximum fully expanded, Sentinel-2's five-day revisit puts stripes about 13px apart, so the hit areas never overlap. Colours: Sentinel-2 `#7fa8e0`, Landsat `#c9a87c`, Planet `#9bd98a`, selected `#bf8f2d` (the theme's `primary_contrast`). A row over the cloud limit draws at 45% height and 0.3 opacity and is not clickable. The alert window is a `#bf8f2d0d` band with dashed `#bf8f2daa` edges.

- [ ] **Step 1: Confirm that an `ipyvuetify.Html` button emits clicks in this environment**

Before building 170 of them, check the one assumption the whole widget rests on:

```bash
python -c "
import ipyvuetify as v
b = v.Html(tag='button', children=['x'])
b.on_event('click', lambda *a: None)
print('events:', b._events)
"
```

Expected: `events: ['click']`. If `on_event` raises or `_events` stays empty, use `v.Btn(icon=True, x_small=True, style_=...)` for the stripes instead and add `min-width: 0 !important; padding: 0 !important; height: auto !important` to each stripe's style to defeat Vuetify's own button sizing. Everything else in this task is unchanged either way.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_timeline.py
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from deforestation_alerts.project.models import AoiRecord, Period, Project
from deforestation_alerts.scripts import scene_index, scene_query
from gui.tile.analysis import timeline

S2, LS, PL = scene_index.SOURCE_S2, scene_index.SOURCE_LANDSAT, scene_index.SOURCE_PLANET

TILES = gpd.GeoDataFrame({"tile": ["W"]},
                         geometry=gpd.GeoSeries([box(0, 0, 2, 2)], crs=scene_index.WGS84),
                         crs=scene_index.WGS84)


def _sel(rows, max_cloud=40.0, start=date(2026, 3, 3), end=date(2026, 10, 23)):
    scenes = pd.DataFrame(
        [(src, pd.Timestamp(d), cloud, "W", f"k{i}") for i, (src, d, cloud) in enumerate(rows)],
        columns=list(scene_index.SCENE_COLUMNS),
    ).astype({"date": "datetime64[ns]"})
    tables = scene_index.SceneTables(scenes=scenes, tiles=TILES)
    return scene_query.scenes_for(tables, box(0.5, 0.5, 1.5, 1.5), start, end, max_cloud=max_cloud)


def _ctx(date_min=date(2026, 5, 3), date_max=date(2026, 8, 23)):
    return {"alert_id": 7, "token": 1, "bbox": box(0.5, 0.5, 1.5, 1.5),
            "date_min": date_min, "date_max": date_max}


def _project():
    p = Project(name="t", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="d1",
                      bounds=(0.0, 0.0, 2.0, 2.0), geometry_file="aoi.geojson")
    p.periods = {"x": Period(id="x", start=date(2026, 4, 1), end=date(2026, 9, 30))}
    p.active_period = "x"
    return p


def test_the_view_is_the_alert_window_padded_by_two_months():
    assert timeline.view_window(_ctx(), _project(), (0, 0)) == (date(2026, 3, 3), date(2026, 10, 23))


def test_the_side_buttons_add_whole_months_to_the_view():
    assert timeline.view_window(_ctx(), _project(), (1, 2)) == (date(2026, 2, 3), date(2026, 12, 23))


def test_an_alert_without_dates_falls_back_to_the_active_period():
    got = timeline.view_window(_ctx(date_min=None, date_max=None), _project(), (0, 0))
    assert got == (date(2026, 2, 1), date(2026, 11, 30))


def test_without_an_alert_or_a_period_there_is_no_view():
    assert timeline.view_window(None, _project(), (0, 0)) is None
    bare = _project()
    bare.active_period = None
    assert timeline.view_window(_ctx(date_min=None, date_max=None), bare, (0, 0)) is None


def test_the_info_tuple_keeps_the_five_element_contract():
    sel = _sel([(S2, "2026-04-24", 8.0)])
    info = timeline.info_tuple(sel.iloc[0])
    assert info[0] == S2
    assert info[1] == 1777593600000  # 2026-04-24T00:00:00Z
    assert info[2] == "8.00"
    assert info[3] == "Sentinel 2 2026-04-24 "  # the trailing space is part of the contract
    assert info[4] == "_240426"


def test_the_info_tuple_of_a_planet_row_has_no_cloud_number():
    sel = _sel([(PL, "2026-04-01", float("nan"))])
    info = timeline.info_tuple(sel.iloc[0])
    assert info[0] == PL and info[2] == "N/A"


def test_the_readout_names_the_source_the_date_and_the_cloud():
    sel = _sel([(S2, "2026-04-24", 8.0)])
    text = timeline.readout(sel, scene_index.row_id(S2, date(2026, 4, 24)))
    assert "2026-04-24" in text and "8" in text


def test_the_readout_of_nothing_selected_says_so():
    sel = _sel([(S2, "2026-04-24", 8.0)])
    assert timeline.readout(sel, None) == timeline.t("analysis.timeline.unset")


def test_days_apart_counts_the_gap_the_pair_brackets():
    assert timeline.days_apart(scene_index.row_id(S2, date(2026, 4, 24)),
                               scene_index.row_id(S2, date(2026, 8, 27))) == 125
    assert timeline.days_apart(None, scene_index.row_id(S2, date(2026, 8, 27))) is None


def test_a_stripe_lands_on_its_own_date():
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 4, 30), set(), timeline.TRACK_PX)
    style = timeline.stripe_hit_style(segs, date(2026, 3, 11), S2)
    pct = 100 * scene_query.x_for(segs, date(2026, 3, 11)) / timeline.TRACK_PX
    assert f"{pct:.3f}%" in style
    assert "width: 11px" in style


def test_a_hidden_stripe_is_drawn_short_faint_and_not_clickable():
    row = _sel([(S2, "2026-04-24", 88.0)]).iloc[0]
    assert row["hidden"]
    style = timeline.stripe_bar_style(row, selected=False)
    assert "opacity: 0.3" in style and "height: 7.65px" in style  # 45% of the 17px band
    assert timeline.stripe_cursor(row) == "default"


def test_a_selected_stripe_is_wider_and_takes_the_selection_colour():
    row = _sel([(S2, "2026-04-24", 8.0)]).iloc[0]
    assert "width: 4px" in timeline.stripe_bar_style(row, selected=True)
    assert timeline.SELECTED_COLOR in timeline.stripe_bar_style(row, selected=True)


def test_every_locale_has_the_timeline_keys():
    import json
    from pathlib import Path

    import deforestation_alerts.message as msg

    root = Path(msg.__file__).parent
    keys = {"before", "after", "unset", "max_cloud", "one_more_month", "collapse_all", "expand_all",
            "images", "no_images", "days_apart", "stale", "refresh_failed", "retry", "loading",
            "legend", "set_before", "set_after", "collapsed_month", "no_cloud_filter"}
    for locale in ("en", "es-ES", "fr"):
        data = json.loads((root / locale / "ui.json").read_text())
        assert keys <= set(data["analysis"]["timeline"]), locale


def test_assigning_a_before_image_publishes_its_info_and_its_keys():
    state = _fake_state()
    analysis = _fake_analysis()
    timeline.build_timeline(state, analysis, _fake_widgets())
    assert analysis.before_info.value is not None and analysis.before_info.value[0] == S2
    assert analysis.before_keys.value  # the granules the rebuild mosaics
    assert analysis.after_info.value is not None and analysis.after_info.value[0] == LS


def test_reset_alert_forgets_the_granule_keys():
    analysis = _fake_analysis()
    analysis.before_keys.set(["k1"])
    analysis.after_keys.set(["k2"])
    analysis.reset_alert()
    assert analysis.before_keys.value == [] and analysis.after_keys.value == []


def test_the_builder_returns_a_raw_widget_and_not_a_solara_component():
    import ipyvuetify as v

    card = timeline.build_timeline(_fake_state(), _fake_analysis(), _fake_widgets())
    assert isinstance(card, v.VuetifyWidget)
    assert not hasattr(timeline.build_timeline, "component")  # never @solara.component


def test_the_builder_renders_an_empty_timeline_before_any_index_arrives():
    state = _fake_state(index=None)
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    assert timeline.t("analysis.timeline.no_images") in _text(card)


def test_no_images_over_the_alert_says_so_rather_than_drawing_an_empty_track():
    # Review Focus #3 reaching the UI.
    state = _fake_state(index=scene_index.SceneTables(
        scenes=_sel([(S2, "2026-04-24", 8.0)]).assign(tile="W", key="k0")[list(scene_index.SCENE_COLUMNS)],
        tiles=TILES))
    widgets = _fake_widgets(bbox=box(80, 80, 81, 81))
    card = timeline.build_timeline(state, _fake_analysis(), widgets)
    assert timeline.t("analysis.timeline.no_images") in _text(card)


def test_a_stale_index_whose_refresh_failed_reports_both_the_age_and_the_failure():
    """Review Focus #2 in full. The error alone leaves the reviewer unable to
    tell whether they are looking at yesterday's index or last month's."""
    state = _fake_state()
    state.scene_manifest.set(_manifest(hours_ago=30))
    state.scene_index_error.set("Earth Engine unavailable")
    text = _text(timeline.build_timeline(state, _fake_analysis(), _fake_widgets()))
    assert "Earth Engine unavailable" in text
    assert timeline.t("analysis.timeline.stale").format(
        when=(datetime.now(timezone.utc) - timedelta(hours=30)).date().isoformat()) in text
    assert timeline.t("analysis.timeline.retry") in text


def test_a_fresh_index_says_nothing_about_its_age():
    state = _fake_state()
    state.scene_manifest.set(_manifest(hours_ago=1))
    assert "Index from" not in _text(timeline.build_timeline(state, _fake_analysis(), _fake_widgets()))


def test_the_planet_toggle_is_disabled_and_says_why_without_nicfi_access():
    """Spec §5.4: a licence-gated collection must not read as an empty one."""
    state = _fake_state()
    state.scene_manifest.set(_manifest(planet=scene_index.PlanetState(region="americas", access=False)))
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    chip = next(w for w in _walk(card)
                if (getattr(w, "attributes", None) or {}).get("title", "").startswith(
                    timeline.t("steps.layers.err_nicfi").format(region="americas")[:20]))
    assert chip.attributes.get("disabled") == "disabled"
    assert chip._events == [] or "click" not in chip._events  # nothing to click


def test_the_planet_toggle_is_live_when_access_was_granted():
    state = _fake_state()
    state.scene_manifest.set(_manifest(planet=scene_index.PlanetState(region="americas", access=True)))
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    chips = [w for w in _walk(card) if (getattr(w, "attributes", None) or {}).get("aria-pressed")]
    planet = next(w for w in chips if timeline.SRC_LABEL[PL] in _text(w))
    assert "disabled" not in (planet.attributes or {})


def test_a_planet_row_is_a_bar_across_its_whole_month_not_a_stripe():
    """A NICFI basemap composites a month; a 2.5px stripe would claim an
    instant it does not have (spec §4.1)."""
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 6, 30), set(), timeline.TRACK_PX)
    bar = timeline.month_bar_style(segs, date(2026, 4, 1))
    width = float(bar.split("width: ")[1].split("%")[0])
    left = float(bar.split("left: ")[1].split("%")[0])
    # April is 30 of the 122 days on an all-expanded four-month axis.
    assert width == pytest.approx(100 * 30 / 122, abs=0.5)
    assert left == pytest.approx(100 * 31 / 122, abs=0.5)
    # and the bar sits in the Planet band, not the Sentinel-2 one
    assert f"top: {timeline.BAND[PL][0]}px" in bar


def test_a_planet_bar_in_a_collapsed_month_is_the_block_width():
    segs = scene_query.month_segments(date(2026, 3, 1), date(2026, 6, 30), {(2026, 4)}, timeline.TRACK_PX)
    bar = timeline.month_bar_style(segs, date(2026, 4, 1))
    width = float(bar.split("width: ")[1].split("%")[0])
    assert width == pytest.approx(100 * scene_query.COLLAPSED_PX / timeline.TRACK_PX, abs=0.5)


def test_widening_past_the_coverage_asks_the_loader_for_the_month():
    """Finding from review: the button widened the *view* only, so a month
    outside the cache drew empty and no fetch was ever started."""
    state = _fake_state()
    card = timeline.build_timeline(state, _fake_analysis(), _fake_widgets())
    assert state.scene_window_request.value is None
    btn = next(w for w in _walk(card)
               if timeline.t("analysis.timeline.one_more_month") in _text(w)
               and "click" in (getattr(w, "_events", None) or []))
    btn.fire_event("click", None)
    assert state.scene_window_request.value is not None
    assert state.scene_window_request.value[0] < date(2026, 3, 3)


# ---- fakes: the builder reads reactives and a context dict, nothing else
def _manifest(hours_ago=0, planet=None):
    return scene_index.SceneManifest(
        aoi_digest="d1",
        refreshed=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        covered=scene_index.Coverage(start=date(2026, 1, 1), end=date(2026, 12, 31)),
        counts={S2: 1, LS: 1},
        planet=planet or scene_index.PlanetState(),
    )


def _fake_state(index="default"):
    import solara

    from deforestation_alerts.scripts.scene_index import SceneTables

    if index == "default":
        scenes = _sel([(S2, "2026-04-24", 8.0), (LS, "2026-08-27", 3.0)])
        scenes = scenes.assign(tile="W", key="k")[list(scene_index.SCENE_COLUMNS)]
        index = SceneTables(scenes=scenes, tiles=TILES)
    return SimpleNamespace(
        project=solara.reactive(_project(), equals=lambda a, b: a is b),
        scene_index=solara.reactive(index, equals=lambda a, b: a is b),
        scene_manifest=solara.reactive(None, equals=lambda a, b: a is b),
        scene_index_pending=solara.reactive(False),
        scene_index_error=solara.reactive(None),
        scene_index_retry=solara.reactive(0),
        scene_widening=solara.reactive(False),
        scene_window_request=solara.reactive(None),
    )


def _fake_analysis():
    from gui.tile.analysis.state import AnalysisState

    return AnalysisState()


def _fake_widgets(bbox=None):
    import solara

    ctx = dict(_ctx())
    if bbox is not None:
        ctx["bbox"] = bbox
    return SimpleNamespace(imagery_ctx=solara.reactive(ctx))


def _text(widget) -> str:
    """Every string anywhere in a widget tree, for asserting on rendered copy."""
    if isinstance(widget, str):
        return widget
    return " ".join(_text(c) for c in (getattr(widget, "children", None) or ()))


def _walk(widget):
    yield widget
    for child in (getattr(widget, "children", None) or ()):
        if not isinstance(child, str):
            yield from _walk(child)
```

`_walk` is defined here and reused by Task 11; if Task 11 is implemented first, keep the single definition.

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/test_timeline.py -v`
Expected: FAIL — `ModuleNotFoundError: gui.tile.analysis.timeline`.

- [ ] **Step 4: Add the locale keys**

In `deforestation_alerts/message/en/ui.json`, under `"analysis"`:

```json
    "timeline": {
      "before": "Before",
      "after": "After",
      "unset": "not set",
      "max_cloud": "Max cloud",
      "one_more_month": "+1 month",
      "collapse_all": "Collapse the alert window",
      "expand_all": "Expand every month",
      "images": "images",
      "no_images": "No images cover this alert in this window.",
      "days_apart": "{days} days apart",
      "stale": "Index from {when}",
      "refresh_failed": "Could not refresh the image index: {error}",
      "retry": "Try again",
      "loading": "Loading the image index…",
      "legend": "Each collection is a stripe in its own band: Sentinel-2 on top, Landsat in the middle, Planet as a bar across its month. A faded half-height stripe is over the cloud limit. The gold band is the alert window, where every image shows partial clearing: selectable, but never picked for you. Click a stripe in the upper lane to set the before image, in the lower lane to set the after image.",
      "set_before": "Set as before image",
      "set_after": "Set as after image",
      "collapsed_month": "{month}, {count} images, collapsed. Expand this month.",
      "no_cloud_filter": "A NICFI basemap is a monthly mosaic with no cloud figure, so the cloud limit never hides it."
    }
```

and the same keys, translated, in `deforestation_alerts/message/es-ES/ui.json` and `deforestation_alerts/message/fr/ui.json`. `tests/test_i18n.py` fails on a key present in one locale and missing in another, so all three go in together.

The Planet toggle's "no access" message is **not** a new key: it reuses the existing `steps.layers.err_nicfi` ("Your Earth Engine account does not have access to Planet NICFI {region}."), which already exists in all three locales and already takes a `{region}`. The spec asks for that string by name.

- [ ] **Step 5: Carry the granule keys on the analysis state**

In `gui/tile/analysis/state.py`, beside `before_info` / `after_info`:

```python
    #: the granule keys the chosen (source, date) mosaics for *this* alert —
    #: what ``image_slider_map_callback`` rebuilds the ee.Image from. Per
    #: alert, because which granules overlap depends on the alert.
    before_keys: solara.Reactive = field(default_factory=lambda: solara.reactive([]))
    after_keys: solara.Reactive = field(default_factory=lambda: solara.reactive([]))
```

and in `reset_alert`, beside the two `*_info` resets:

```python
            self.before_keys.set([])
            self.after_keys.set([])
```

- [ ] **Step 6: Write the widget**

```python
# gui/tile/analysis/timeline.py
"""The shared image timeline: one date axis under both map panes.

A raw-widget builder, never a Solara component. MapApp renders the Analysis
step's content with ``v-if``, which destroys and rebuilds the DOM when the
reviewer closes and reopens the step; a Solara-rendered subtree comes back
blank, while a raw ipyvuetify tree is re-viewed from its widget model.

Two lanes over one axis: clicking a stripe in the upper lane sets the before
image, in the lower lane the after image. One click, no mode to be in.
"""

import logging
from datetime import date, datetime, timezone

import ipyvuetify as v
import pandas as pd

from deforestation_alerts.scripts import scene_query
from deforestation_alerts.scripts.scene_index import (
    SOURCE_LANDSAT, SOURCE_PLANET, SOURCE_S2, row_id,
)
from gui.i18n import t

logger = logging.getLogger(__name__)

TRACK_PX = 1120.0
LANE_H = 45
AXIS_H = 27
VIEW_MONTHS = 2

#: (top offset, height) of each collection's band inside a lane. The bands do
#: not overlap, so two collections on one date never draw over each other and
#: a stripe's row says which collection it is before its colour does.
BAND = {SOURCE_S2: (0, 17), SOURCE_LANDSAT: (19, 14), SOURCE_PLANET: (35, 10)}
SRC_COLOR = {SOURCE_S2: "#7fa8e0", SOURCE_LANDSAT: "#c9a87c", SOURCE_PLANET: "#9bd98a"}
SRC_LABEL = {SOURCE_S2: "Sentinel-2", SOURCE_LANDSAT: "Landsat", SOURCE_PLANET: "Planet"}
SELECTED_COLOR = "#bf8f2d"  # the theme's primary_contrast
HIT_PX = 11
STRIPE_PX, SELECTED_STRIPE_PX = 2.5, 4.0
HIDDEN_SCALE, HIDDEN_OPACITY = 0.45, 0.3


def _pct(px: float) -> float:
    return round(px / TRACK_PX * 100, 3)


def view_window(ctx, project, extra):
    """``(start, end)`` of the axis: the alert window padded by two months,
    plus whatever the side buttons have added.

    An alert whose date pixels were all nodata has no dates of its own
    (``vectorize`` writes NaN, which ``convert_julian_to_date`` renders
    "N/A"); the active period's own window stands in, so the reviewer still
    gets a timeline instead of an error.
    """
    from gui.scripts.scene_loader import shift_months

    a0 = a1 = None
    if ctx:
        a0, a1 = ctx.get("date_min"), ctx.get("date_max")
    if a0 is None or a1 is None:
        period = project.periods.get(project.active_period) if (project and project.active_period) else None
        if period is None:
            return None
        a0, a1 = period.start, period.end
    back, fwd = extra
    return (shift_months(a0, -(VIEW_MONTHS + back)), shift_months(a1, VIEW_MONTHS + fwd))


def alert_window(ctx, project):
    """The gold band's own dates, by the same fallback as ``view_window``."""
    if ctx and ctx.get("date_min") and ctx.get("date_max"):
        return (ctx["date_min"], ctx["date_max"])
    period = project.periods.get(project.active_period) if (project and project.active_period) else None
    return (period.start, period.end) if period is not None else (None, None)


def info_tuple(row) -> list:
    """One selection as ``before_info`` / ``after_info``.

    The five elements are a contract shared with ``paths.alert_image_name``,
    ``export.py`` (which writes elements 1 and 3 into the saved store as
    provenance) and ``edit_tools.py``:
    ``[source, millis, cloud, "<source> <YYYY-MM-DD> ", "_DDMMYY"]``.

    ``millis`` is now the *acquisition* date at UTC midnight, not the
    processing timestamp the old selector stored. Nothing filters on it any
    more — the rebuild uses the granule keys — so this is a provenance change
    only, and the dates in a store saved from here are finally the dates the
    images were taken.
    """
    d = row["date"].date() if isinstance(row["date"], pd.Timestamp) else row["date"]
    millis = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)
    cloud = row["cloud"]
    cloud_str = "N/A" if cloud is None or pd.isna(cloud) else f"{float(cloud):.2f}"
    return [str(row["source"]), millis, cloud_str, f"{row['source']} {d.isoformat()} ", d.strftime("_%d%m%y")]


def month_bar_style(segments, d: date) -> str:
    """A Planet row's hit area: the **whole month** it composites, not an instant.

    A NICFI basemap is a monthly mosaic, so a 2.5px stripe at one instant would
    claim a precision it does not have (spec §4.1). The bar spans its month's
    own segment, which on a collapsed month is the 66px block and on an
    expanded one its proportional share — so it lines up with the axis either
    way, and it is the only row type whose width carries meaning.
    """
    seg = segments.get((d.year, d.month))
    if seg is None:
        return "display: none"
    left = x_for(segments, date(d.year, d.month, 1))
    right = x_for(segments, _month_end(d), end=True)
    top, height = BAND[SOURCE_PLANET]
    return (f"position: absolute; left: {left:.4f}%; width: {max(right - left, 0.1):.4f}%; "
            f"top: {top}px; height: {height}px; padding: 0; border: 0; background: transparent")


def _month_end(d: date) -> date:
    import calendar

    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _row_of(sel, rid):
    if rid is None or sel is None or len(sel) == 0:
        return None
    hit = sel[sel["id"] == rid]
    return None if len(hit) == 0 else hit.iloc[0]


def readout(sel, rid) -> str:
    """The line under the track: what is selected, in words."""
    row = _row_of(sel, rid)
    if row is None:
        return t("analysis.timeline.unset")
    d = row["date"].date()
    cloud = row["cloud"]
    tail = "" if pd.isna(cloud) else f" · {float(cloud):.0f}%"
    return f"{SRC_LABEL[row['source']]} {d.isoformat()}{tail}"


def days_apart(before_id, after_id):
    """The gap the chosen pair brackets, or ``None`` until both are set."""
    if not before_id or not after_id:
        return None
    b = datetime.strptime(before_id[1:], "%Y%m%d").date()
    a = datetime.strptime(after_id[1:], "%Y%m%d").date()
    return (a - b).days


def stripe_hit_style(segments, d: date, source: str) -> str:
    """The clickable box: ``HIT_PX`` wide, centred on the date, band-tall.

    The hit area is wider than the stripe because a 2.5px target is not
    clickable; at the widest view Sentinel-2's stripes still sit ~13px apart,
    so an 11px box never covers its neighbour's date.
    """
    top, height = BAND[source]
    x = scene_query.x_for(segments, d)
    return (f"left: calc({_pct(x)}% - {HIT_PX / 2}px); width: {HIT_PX}px; "
            f"top: {top}px; height: {height}px")


def stripe_bar_style(row, *, selected: bool) -> str:
    """The visible stripe inside the hit area."""
    _, height = BAND[row["source"]]
    hidden = bool(row["hidden"]) and not selected
    h = height * HIDDEN_SCALE if hidden else height
    width = SELECTED_STRIPE_PX if selected else STRIPE_PX
    color = SELECTED_COLOR if selected else SRC_COLOR[row["source"]]
    opacity = f"; opacity: {HIDDEN_OPACITY}" if hidden else ""
    return (f"position: absolute; left: 50%; transform: translateX(-50%); bottom: 0; "
            f"width: {width}px; height: {h}px; border-radius: 1px; background: {color}{opacity}")


def stripe_cursor(row) -> str:
    """A row the cloud limit hides is not clickable: raising the limit is the
    way to reach it, and a click that silently did nothing would read as a bug."""
    return "default" if bool(row["hidden"]) else "pointer"


def _button(style, aria, handler, child_style, cursor="pointer"):
    btn = v.Html(tag="button", attributes={"type": "button", "aria-label": aria},
                 style_=f"position: absolute; {style}; background: none; border: 0; padding: 0; "
                        f"cursor: {cursor}",
                 children=[v.Html(tag="span", style_=child_style)])
    if handler is not None:
        btn.on_event("click", handler)
    return btn


def build_timeline(app_state, analysis, widgets):
    """The strip, built once per session and re-rendered from the reactives.

    Returns a ``v.Card`` holding the toolbar, the track and the readout. Every
    piece of state it reads is a reactive it subscribes to, so the only thing
    an alert change has to do is publish ``widgets.imagery_ctx``.
    """
    import solara

    max_cloud = solara.reactive(40.0)
    sources = solara.reactive(frozenset(SRC_COLOR))
    collapsed = solara.reactive(frozenset())
    extra = solara.reactive((0, 0))
    before_id = solara.reactive(None)
    after_id = solara.reactive(None)
    #: the alert the collapse set and the selection belong to, so a new alert
    #: gets its own auto-collapse instead of inheriting the last one's
    seen_alert = solara.reactive(None)

    lane_before = v.Html(tag="div", style_=f"position: relative; height: {LANE_H}px; margin-top: 11px")
    lane_axis = v.Html(tag="div", style_=f"position: relative; height: {AXIS_H}px")
    lane_after = v.Html(tag="div", style_=f"position: relative; height: {LANE_H}px")
    blocks = v.Html(tag="div", style_="position: absolute; left: 0; right: 0; top: 11px; bottom: 0; "
                                      "pointer-events: none")
    window_band = v.Html(tag="div", style_="display: none")
    track = v.Html(tag="div", style_="position: relative; flex: 1 1 auto; min-width: 0",
                   children=[window_band, lane_before, lane_axis, lane_after, blocks])
    message = v.Html(tag="div", style_="padding: 14px 0; color: #a8a39b; font-size: 12.5px", children=[])
    readout_row = v.Html(tag="div", style_="display: flex; align-items: center; gap: 13px; font-size: 12.5px",
                         children=[])
    chips = v.Html(tag="div", style_="display: flex; align-items: center; gap: 8px", children=[])
    status = v.Html(tag="div", style_="font-size: 11.5px; color: #a8a39b", children=[])

    def _selection():
        tables = app_state.scene_index.value
        ctx = widgets.imagery_ctx.value
        window = view_window(ctx, app_state.project.value, extra.value)
        if tables is None or ctx is None or window is None or ctx.get("bbox") is None:
            return (None, None)
        sel = scene_query.scenes_for(tables, ctx["bbox"], window[0], window[1], max_cloud=max_cloud.value)
        return (sel[sel["source"].isin(sources.value)].reset_index(drop=True), window)

    def _assign(lane, rid, sel):
        row = _row_of(sel, rid)
        if row is None or bool(row["hidden"]):
            return
        (before_id if lane == "b" else after_id).set(rid)
        # The granule keys go first: ``image_slider_map_callback`` reads them
        # when the info publish wakes it, so a half-set pair is never rebuilt.
        (analysis.before_keys if lane == "b" else analysis.after_keys).set(sorted(_keys_for(row, sel)))
        (analysis.before_info if lane == "b" else analysis.after_info).set(info_tuple(row))

    def _keys_for(row, sel):
        """Every granule key the chosen (source, date) mosaics for this alert."""
        tables = app_state.scene_index.value
        ctx = widgets.imagery_ctx.value
        if tables is None or ctx is None:
            return []
        tiles = scene_query.overlapping_tiles(tables.tiles, ctx["bbox"])
        rows = tables.scenes
        same = rows[(rows["source"] == row["source"]) & (rows["date"] == row["date"])]
        return list(same[same["tile"].isin(tiles) | (same["tile"] == "")]["key"])

    def render(*_):
        sel, window = _selection()
        if sel is None or window is None or len(sel) == 0:
            track.style_ = "display: none"
            message.children = [t("analysis.timeline.no_images")]
            message.style_ = "padding: 14px 0; color: #a8a39b; font-size: 12.5px"
            _render_chips(sel)
            _render_status()
            return
        track.style_ = "position: relative; flex: 1 1 auto; min-width: 0"
        message.style_ = "display: none"

        ctx = widgets.imagery_ctx.value
        a0, a1 = alert_window(ctx, app_state.project.value)
        if seen_alert.value != (ctx or {}).get("alert_id"):
            seen_alert.set((ctx or {}).get("alert_id"))
            collapsed.set(frozenset(scene_query.auto_collapsed(window[0], window[1], a0, a1)))
            b, a = scene_query.default_pick(sel, a0, a1)
            before_id.set(b)
            after_id.set(a)
            for lane, rid in (("b", b), ("a", a)):
                if rid:
                    _assign(lane, rid, sel)

        segs = scene_query.month_segments(window[0], window[1], collapsed.value, TRACK_PX)
        _render_band(segs, a0, a1)
        _render_lane(lane_before, "b", sel, segs)
        _render_lane(lane_after, "a", sel, segs)
        _render_axis(segs)
        _render_blocks(segs, sel)
        _render_chips(sel)
        _render_readout(sel)
        _render_status()

    def _render_lane(host, lane, sel, segs):
        chosen = (before_id if lane == "b" else after_id).value
        kids = []
        for _, row in sel.iterrows():
            key = (row["date"].year, row["date"].month)
            if segs[key].collapsed:
                continue  # its count is on the block instead
            selected = row["id"] == chosen
            rid = row["id"]
            aria = (f"{SRC_LABEL[row['source']]}, {row['date'].date().isoformat()}, "
                    f"{t('analysis.timeline.set_before' if lane == 'b' else 'analysis.timeline.set_after')}")
            hit = (month_bar_style(segs, row["date"].date()) if row["source"] == SOURCE_PLANET
                   else stripe_hit_style(segs, row["date"].date(), row["source"]))
            kids.append(_button(
                hit, aria,
                None if bool(row["hidden"]) else (lambda w, e, d, lane=lane, rid=rid: _on_pick(lane, rid)),
                stripe_bar_style(row, selected=selected), stripe_cursor(row)))
        host.children = kids

    def _on_pick(lane, rid):
        sel, _ = _selection()
        _assign(lane, rid, sel)
        render()

    def _render_blocks(segs, sel):
        counts = scene_query.month_counts(sel)
        kids = []
        for key, seg in sorted(segs.items()):
            if not seg.collapsed:
                continue
            label = date(seg.year, seg.month, 1).strftime("%b %Y").upper()
            n = counts.get(key, 0)
            btn = v.Html(
                tag="button",
                attributes={"type": "button",
                            "aria-label": t("analysis.timeline.collapsed_month").format(month=label, count=n)},
                style_=(f"pointer-events: auto; position: absolute; left: {_pct(seg.x)}%; "
                        f"width: {_pct(seg.w)}%; top: 0; bottom: 0; cursor: pointer; "
                        "background: repeating-linear-gradient(135deg, #24221f, #24221f 4px, "
                        "#1e1c1a 4px, #1e1c1a 9px); border-left: 1px dashed #44403a; "
                        "border-right: 1px dashed #44403a; display: flex; flex-direction: column; "
                        "align-items: center; justify-content: center; gap: 3px; color: #a8a39b"),
                children=[
                    v.Html(tag="span", style_="font-size: 9px; letter-spacing: 0.1em", children=[label]),
                    v.Html(tag="span", style_="font-size: 16px; font-weight: 600; color: #f0ece6; "
                                              "line-height: 1", children=[str(n)]),
                    v.Html(tag="span", style_="font-size: 8px", children=[t("analysis.timeline.images")]),
                ])
            btn.on_event("click", lambda w, e, d, key=key: _expand(key))
            kids.append(btn)
        blocks.children = [v.Html(tag="div", style_="position: relative; height: 100%; "
                                                    "pointer-events: none", children=kids)]

    def _expand(key):
        collapsed.set(collapsed.value - {key})
        render()

    def _render_band(segs, a0, a1):
        if a0 is None or a1 is None:
            window_band.style_ = "display: none"
            return
        x0 = scene_query.x_for(segs, a0)
        x1 = scene_query.x_for(segs, a1, end=True)
        window_band.style_ = (f"position: absolute; left: {_pct(x0)}%; width: {_pct(x1 - x0)}%; "
                              f"top: 11px; bottom: 0; background: {SELECTED_COLOR}0d; "
                              f"border-left: 1px dashed {SELECTED_COLOR}aa; "
                              f"border-right: 1px dashed {SELECTED_COLOR}aa; pointer-events: none")

    def _render_axis(segs):
        kids = [v.Html(tag="span", style_="position: absolute; left: 0; right: 0; top: 0; height: 1px; "
                                          "background: #44403a")]
        for key, seg in sorted(segs.items()):
            if seg.collapsed:
                continue
            label = date(seg.year, seg.month, 1).strftime("%b").upper()
            btn = v.Html(tag="button", attributes={"type": "button",
                                                   "aria-label": t("analysis.timeline.collapse_all")},
                         style_=(f"position: absolute; left: calc({_pct(seg.x + seg.w / 2)}% - 26px); "
                                 "top: 7px; width: 52px; height: 18px; background: none; border: 0; "
                                 "color: #a8a39b; font-size: 9.5px; letter-spacing: 0.1em; cursor: pointer"),
                         children=[label])
            btn.on_event("click", lambda w, e, d, key=key: _collapse(key))
            kids.append(btn)
        lane_axis.children = kids

    def _collapse(key):
        collapsed.set(collapsed.value | {key})
        render()

    def _render_chips(sel):
        counts = scene_query.source_counts(sel) if sel is not None else {k: (0, 0) for k in SRC_COLOR}
        planet = getattr(app_state.scene_manifest.value, "planet", None)
        kids = []
        for name in (SOURCE_PLANET, SOURCE_S2, SOURCE_LANDSAT):
            on = name in sources.value
            shown, hidden = counts.get(name, (0, 0))
            label = f"{SRC_LABEL[name]} {shown}" + (f" +{hidden}" if hidden else "")
            # NICFI is licence-gated. A toggle that does nothing because the
            # account has no access must say so rather than read as a
            # collection with no images this month (spec §5.4), which is why
            # the loader records ``planet.access`` instead of only logging it.
            blocked = name == SOURCE_PLANET and planet is not None and not planet.access
            if name == SOURCE_PLANET:
                # Planet has no cloud property, so the slider never hides it.
                title = (t("steps.layers.err_nicfi").format(region=(planet.region if planet else "") or "")
                         if blocked else t("analysis.timeline.no_cloud_filter"))
            else:
                title = ""
            attrs = {"type": "button", "aria-pressed": "true" if (on and not blocked) else "false"}
            if title:
                attrs["title"] = title
                attrs["aria-label"] = f"{label}. {title}"
            if blocked:
                attrs["disabled"] = "disabled"
                attrs["aria-disabled"] = "true"
            chip = v.Html(tag="button", attributes=attrs,
                          style_=(f"height: 32px; padding: 0 10px; border-radius: 4px; "
                                  f"cursor: {'not-allowed' if blocked else 'pointer'}; "
                                  f"background: #24221f; color: #f0ece6; font-size: 11.5px; "
                                  f"border: 1px solid "
                                  f"{SRC_COLOR[name] if (on and not blocked) else '#44403a'}; "
                                  f"opacity: {'0.4' if blocked else ('1' if on else '0.5')}"),
                          children=[label])
            if not blocked:
                chip.on_event("click", lambda w, e, d, name=name: _toggle(name))
            kids.append(chip)
        chips.children = kids

    def _toggle(name):
        sources.set(sources.value ^ {name})
        render()

    def _render_readout(sel):
        kids = []
        for lane, rid in (("b", before_id.value), ("a", after_id.value)):
            tag = t("analysis.timeline.before" if lane == "b" else "analysis.timeline.after")
            kids.append(v.Html(tag="span", children=[f"{tag}: {readout(sel, rid)}"]))
        gap = days_apart(before_id.value, after_id.value)
        if gap is not None:
            kids.append(v.Html(tag="span", style_="margin-left: auto; color: #a8a39b",
                               children=[t("analysis.timeline.days_apart").format(days=gap)]))
        readout_row.children = kids

    def _render_status():
        """The index's age and the refresh's outcome, which are two facts.

        Review Focus #2 is both halves at once: a reviewer whose stale index
        failed to refresh must see *how old what they are looking at is* and
        *that the refresh failed*. Showing only the error would leave them
        with no idea whether the timeline is a day or a month behind, so the
        age line is rendered first and the failure after it, not instead.
        """
        error = app_state.scene_index_error.value
        manifest = app_state.scene_manifest.value
        kids = []
        if manifest is not None:
            age = (datetime.now(timezone.utc) - manifest.refreshed).total_seconds() / 3600
            if age > scene_query.TTL_HOURS or error:
                kids.append(v.Html(tag="span", children=[
                    t("analysis.timeline.stale").format(when=manifest.refreshed.date().isoformat())]))
        if error:
            # The index already on screen keeps answering: this says the
            # refresh failed, not that there is nothing to look at.
            retry = v.Btn(x_small=True, text=True, children=[t("analysis.timeline.retry")])
            retry.on_event("click", lambda w, e, d: app_state.scene_index_retry.set(
                app_state.scene_index_retry.value + 1))
            kids.append(v.Html(tag="span", style_="color: #d9ab4e; margin-left: 8px", children=[
                t("analysis.timeline.refresh_failed").format(error=error)]))
            kids.append(retry)
        elif app_state.scene_index_pending.value and manifest is None:
            kids = [v.Html(tag="span", children=[t("analysis.timeline.loading")])]
        elif app_state.scene_index_pending.value:
            kids.append(v.Html(tag="span", style_="margin-left: 8px",
                               children=[t("analysis.timeline.loading")]))
        status.children = kids

    def _widen(side):
        """One more month on ``side``, and a request for it if the cache is short.

        Widening the local view is not enough: outside the cached coverage the
        extra month would simply draw empty. ``scene_window_request`` is what
        ``SceneIndexLoader`` unions with its own ±2 months, so the month is
        fetched in the foreground and the strip fills in when it lands
        (``scene_index`` is one of the reactives ``render`` subscribes to).
        The request only ever grows, so a reviewer who widens and then moves
        to another alert keeps the context they asked for.
        """
        back, fwd = extra.value
        extra.set((back + 1, fwd) if side == "left" else (back, fwd + 1))
        _, window = _selection()
        window = window or view_window(widgets.imagery_ctx.value, app_state.project.value, extra.value)
        if window is not None:
            was = app_state.scene_window_request.value
            app_state.scene_window_request.set(
                window if was is None else (min(was[0], window[0]), max(was[1], window[1]))
            )
        render()

    slider = v.Slider(v_model=max_cloud.value, min=0, max=100, step=5, hide_details=True,
                      thumb_label=True, style_="max-width: 160px",
                      label=t("analysis.timeline.max_cloud"))
    # @change, not @input: dragging would otherwise rebuild ~170 widgets per pixel.
    slider.on_event("change", lambda w, e, d: (max_cloud.set(float(d)), render()))

    def _side(side, arrow):
        btn = v.Btn(small=True, text=True, children=[arrow + " " + t("analysis.timeline.one_more_month")])
        btn.on_event("click", lambda w, e, d, side=side: _widen(side))
        return btn

    collapse_all = v.Btn(small=True, text=True, children=[t("analysis.timeline.expand_all")])
    collapse_all.on_event("click", lambda w, e, d: (collapsed.set(frozenset()), render()))

    card = v.Card(flat=True, class_="analysis-timeline", children=[
        v.Html(tag="div", style_="display: flex; align-items: center; gap: 10px; padding: 0 8px 6px",
               children=[chips, v.Spacer(), collapse_all, slider, status]),
        v.Html(tag="div", style_="display: flex; gap: 10px; align-items: stretch; padding: 0 8px",
               children=[_side("left", "‹"), track, message, _side("right", "›")]),
        v.Html(tag="div", style_="padding: 8px", children=[readout_row]),
    ])

    for reactive in (app_state.scene_index, app_state.scene_manifest, app_state.scene_index_error,
                     app_state.scene_index_pending, widgets.imagery_ctx):
        reactive.subscribe(render)
    render()
    return card
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_timeline.py tests/test_i18n.py -v`
Expected: PASS. `test_i18n.py` checks every locale carries every key, so a missing Spanish string fails there rather than in production.

- [ ] **Step 8: Commit**

```bash
git add gui/tile/analysis/timeline.py gui/tile/analysis/state.py deforestation_alerts/message/*/ui.json tests/test_timeline.py
git commit -m "feat(analysis): shared image timeline with coloured stripes and collapsible months"
```

---

### Task 11: Keyboard navigation

**Files:**
- Modify: `gui/tile/analysis/timeline.py`
- Test: `tests/test_timeline.py`

**Interfaces:**
- Consumes: `scenes_for`'s result, `month_segments`.
- Produces: `timeline.step(sel, segments, current_id, delta) -> Optional[str]`, `timeline.edge(sel, segments, last=False) -> Optional[str]`.

A reviewer going through a queue of alerts does the same two clicks hundreds of times, and the targets are 11px wide. Arrow keys make that a key press. Tab moves between the two lanes — one tab stop per lane, not one per stripe, which is why every stripe carries `tabindex="-1"`. Left and Right move one row at a time; Home and End jump to the ends; **Enter assigns**. A row the cloud limit hides is skipped, exactly as a click on it is ignored. A row inside a collapsed month is skipped too, since it is not drawn — so arrowing never moves the cursor somewhere invisible.

**Arrows move a cursor; Enter is what assigns** (spec §4.8). That separation is not ceremony: `_assign` publishes `before_info`, which is what redraws the map pane, so assigning on every arrow key would fire one Earth Engine tile request per key press — a reviewer walking ten scenes would queue ten map redraws to look at one. The cursor is drawn as a dashed ring on the focused stripe and named by `aria-activedescendant` on the lane, so where it is is visible and announced without anything being fetched. A click still assigns directly, because a click is already a deliberate choice of one row.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_timeline.py
VIEW = (date(2026, 3, 1), date(2026, 6, 30))


def _segs(collapsed=()):
    return scene_query.month_segments(VIEW[0], VIEW[1], set(collapsed), timeline.TRACK_PX)


def test_arrowing_moves_to_the_next_visible_row():
    sel = _sel([(S2, "2026-03-05", 5.0), (S2, "2026-03-10", 5.0), (S2, "2026-03-15", 5.0)],
               start=VIEW[0], end=VIEW[1])
    mid = scene_index.row_id(S2, date(2026, 3, 10))
    assert timeline.step(sel, _segs(), mid, 1) == scene_index.row_id(S2, date(2026, 3, 15))
    assert timeline.step(sel, _segs(), mid, -1) == scene_index.row_id(S2, date(2026, 3, 5))


def test_arrowing_past_the_end_stays_put():
    sel = _sel([(S2, "2026-03-05", 5.0), (S2, "2026-03-10", 5.0)], start=VIEW[0], end=VIEW[1])
    last = scene_index.row_id(S2, date(2026, 3, 10))
    assert timeline.step(sel, _segs(), last, 1) == last


def test_arrowing_skips_a_row_the_cloud_limit_hides():
    sel = _sel([(S2, "2026-03-05", 5.0), (S2, "2026-03-10", 95.0), (S2, "2026-03-15", 5.0)],
               start=VIEW[0], end=VIEW[1])
    first = scene_index.row_id(S2, date(2026, 3, 5))
    assert timeline.step(sel, _segs(), first, 1) == scene_index.row_id(S2, date(2026, 3, 15))


def test_arrowing_skips_a_row_inside_a_collapsed_month():
    sel = _sel([(S2, "2026-03-05", 5.0), (S2, "2026-04-10", 5.0), (S2, "2026-05-15", 5.0)],
               start=VIEW[0], end=VIEW[1])
    first = scene_index.row_id(S2, date(2026, 3, 5))
    assert timeline.step(sel, _segs(collapsed=[(2026, 4)]), first, 1) == \
        scene_index.row_id(S2, date(2026, 5, 15))


def test_arrowing_from_nothing_selected_takes_the_first_visible_row():
    sel = _sel([(S2, "2026-03-05", 5.0), (S2, "2026-03-10", 5.0)], start=VIEW[0], end=VIEW[1])
    assert timeline.step(sel, _segs(), None, 1) == scene_index.row_id(S2, date(2026, 3, 5))
    assert timeline.step(sel, _segs(), None, -1) == scene_index.row_id(S2, date(2026, 3, 10))


def test_the_edges_are_the_first_and_last_visible_rows():
    sel = _sel([(S2, "2026-03-05", 5.0), (S2, "2026-03-10", 95.0), (S2, "2026-03-15", 5.0)],
               start=VIEW[0], end=VIEW[1])
    assert timeline.edge(sel, _segs()) == scene_index.row_id(S2, date(2026, 3, 5))
    assert timeline.edge(sel, _segs(), last=True) == scene_index.row_id(S2, date(2026, 3, 15))


def test_the_edges_of_an_empty_selection_are_nothing():
    sel = _sel([(S2, "2026-03-05", 95.0)], start=VIEW[0], end=VIEW[1])
    assert timeline.edge(sel, _segs()) is None
    assert timeline.step(sel, _segs(), None, 1) is None


def test_each_lane_is_one_tab_stop():
    card = timeline.build_timeline(_fake_state(), _fake_analysis(), _fake_widgets())
    stops = [w for w in _walk(card)
             if (getattr(w, "attributes", None) or {}).get("tabindex") == "0"]
    assert len(stops) == 2  # the before lane and the after lane


def test_arrowing_moves_the_cursor_without_assigning_anything():
    """Spec §4.8: arrows move, Enter assigns. If an arrow assigned, walking ten
    scenes would queue ten map redraws."""
    analysis = _fake_analysis()
    card = timeline.build_timeline(_fake_state(), analysis, _fake_widgets())
    lane = _lane(card, "0")
    before = analysis.before_info.value
    lane.fire_event("keydown.right", None)
    assert analysis.before_info.value == before            # nothing published
    assert _cursor_id(lane) is not None                    # but the cursor moved
    assert _cursor_id(lane) != (before and before[3])


def test_enter_assigns_the_row_the_cursor_is_on():
    """``row_id`` is ``<source letter><YYYYMMDD>`` and ``info_tuple``'s fourth
    element carries the ISO date, so the two can be checked against each other
    without the test knowing which scene End landed on."""
    analysis = _fake_analysis()
    card = timeline.build_timeline(_fake_state(), analysis, _fake_widgets())
    lane = _lane(card, "0")
    lane.fire_event("keydown.end", None)
    cursor = _cursor_id(lane)                     # "b-s20260404"
    lane.fire_event("keydown.enter", None)
    info = analysis.before_info.value
    assert info is not None
    assert cursor.endswith(info[3].strip().rsplit(" ", 1)[-1].replace("-", ""))
    assert _cursor_id(lane) == cursor             # Enter assigns; it does not move


def test_the_focused_stripe_is_named_for_a_screen_reader():
    card = timeline.build_timeline(_fake_state(), _fake_analysis(), _fake_widgets())
    lane = _lane(card, "0")
    lane.fire_event("keydown.home", None)
    named = _cursor_id(lane)
    ids = {(getattr(w, "attributes", None) or {}).get("id") for w in _walk(card)}
    assert named in ids  # aria-activedescendant points at a stripe that exists


def _lane(card, which):
    lanes = [w for w in _walk(card)
             if (getattr(w, "attributes", None) or {}).get("role") == "listbox"]
    return lanes[0] if which == "0" else lanes[1]


def _cursor_id(lane):
    return (lane.attributes or {}).get("aria-activedescendant") or None
```

`fire_event` is `ipywidgets`' own synchronous dispatch of a registered handler, so these drive the Python side of the same callbacks a key press reaches. Step 1 of Task 10 is what establishes that the browser side arrives at all; if the `Html` fallback was taken there, these tests take the `v.Btn` host instead and are otherwise unchanged.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_timeline.py -k "arrow or edge or tab_stop" -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'step'`.

- [ ] **Step 3: Add the navigation helpers**

Add to `gui/tile/analysis/timeline.py`:

```python
def _navigable(sel, segments):
    """The row ids a key press can reach, in date order.

    Hidden rows and rows inside a collapsed month are not drawn, so moving the
    selection onto one would leave the reviewer with a selection they cannot
    see. A click on either is ignored for the same reason.
    """
    ids = []
    for _, row in sel.iterrows():
        if bool(row["hidden"]):
            continue
        key = (row["date"].year, row["date"].month)
        seg = segments.get(key)
        if seg is None or seg.collapsed:
            continue
        ids.append(str(row["id"]))
    return ids


def step(sel, segments, current_id, delta: int):
    """The row ``delta`` places from ``current_id``, clamped at the ends.

    From nothing selected, a Right takes the first row and a Left the last, so
    the first key press always lands somewhere rather than doing nothing.
    """
    ids = _navigable(sel, segments)
    if not ids:
        return None
    if current_id not in ids:
        return ids[0] if delta > 0 else ids[-1]
    i = ids.index(current_id)
    return ids[max(0, min(len(ids) - 1, i + delta))]


def edge(sel, segments, *, last: bool = False):
    ids = _navigable(sel, segments)
    return (ids[-1] if last else ids[0]) if ids else None
```

- [ ] **Step 4: Wire the keys onto the lanes**

In `build_timeline`, give each lane host a tab stop and key handlers. Replace the two lane constructions with:

```python
    def _lane_host(height_style, lane, label):
        host = v.Html(tag="div", style_=f"position: relative; {height_style}; outline: none",
                      attributes={"tabindex": "0", "role": "listbox", "aria-label": label})
        # ``aria-activedescendant`` is refreshed by ``_render_lane``: it is how
        # a screen reader follows the cursor while focus stays on the lane.
        for name, move in (("keydown.left", -1), ("keydown.right", 1)):
            host.on_event(name, lambda w, e, d, lane=lane, move=move: _arrow(lane, move))
        host.on_event("keydown.home", lambda w, e, d, lane=lane: _jump(lane, last=False))
        host.on_event("keydown.end", lambda w, e, d, lane=lane: _jump(lane, last=True))
        host.on_event("keydown.enter", lambda w, e, d, lane=lane: _commit(lane))
        return host

    lane_before = _lane_host(f"height: {LANE_H}px; margin-top: 11px", "b",
                             t("analysis.timeline.before"))
    lane_after = _lane_host(f"height: {LANE_H}px", "a", t("analysis.timeline.after"))
```

and add the two handlers beside `_on_pick`:

```python
    def _cursor(lane):
        """The keyboard cursor of a lane, which starts at its selection."""
        focus = (focus_before if lane == "b" else focus_after)
        if focus.value is None:
            return (before_id if lane == "b" else after_id).value
        return focus.value

    def _arrow(lane, move):
        sel, window = _selection()
        if sel is None or window is None:
            return
        segs = scene_query.month_segments(window[0], window[1], collapsed.value, TRACK_PX)
        target = step(sel, segs, _cursor(lane), move)
        if target and target != _cursor(lane):
            (focus_before if lane == "b" else focus_after).set(target)
            render()

    def _jump(lane, *, last):
        sel, window = _selection()
        if sel is None or window is None:
            return
        segs = scene_query.month_segments(window[0], window[1], collapsed.value, TRACK_PX)
        target = edge(sel, segs, last=last)
        if target:
            (focus_before if lane == "b" else focus_after).set(target)
            render()

    def _commit(lane):
        """Enter: assign the row the cursor is on. This is the only key that
        publishes ``before_info`` / ``after_info``, and so the only one that
        costs a map redraw."""
        sel, _ = _selection()
        target = _cursor(lane)
        if sel is None or target is None:
            return
        _assign(lane, target, sel)
        render()
```

and the two cursors beside the selection reactives at the top of `build_timeline`:

```python
    #: the keyboard cursor per lane — where the arrows have walked to, which
    #: is not a selection until Enter. ``None`` means "wherever the selection
    #: is", and a new alert clears it along with the selection.
    focus_before = solara.reactive(None)
    focus_after = solara.reactive(None)
```

with the clear in the per-alert block of `render`, beside `before_id.set(b)`:

```python
            focus_before.set(None)
            focus_after.set(None)
```

In `_render_lane`, keep the lane the single tab stop by giving each stripe button an explicit tabindex:

```python
            focused = rid == _cursor(lane)
            hit = _button(
                (month_bar_style(segs, row["date"].date()) if row["source"] == SOURCE_PLANET
                 else stripe_hit_style(segs, row["date"].date(), row["source"]))
                + ("; outline: 1px dashed #bf8f2daa; outline-offset: 1px" if focused else ""),
                aria,
                None if bool(row["hidden"]) else (lambda w, e, d, lane=lane, rid=rid: _on_pick(lane, rid)),
                stripe_bar_style(row, selected=selected), stripe_cursor(row))
            hit.attributes = {**hit.attributes, "tabindex": "-1", "id": f"{lane}-{rid}",
                              "aria-selected": "true" if selected else "false"}
            kids.append(hit)
```

and, at the end of `_render_lane`, point the lane at its cursor:

```python
        host.children = kids
        cursor = _cursor(lane)
        host.attributes = {**host.attributes,
                           "aria-activedescendant": f"{lane}-{cursor}" if cursor else ""}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_timeline.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/tile/analysis/timeline.py tests/test_timeline.py
git commit -m "feat(analysis): keyboard navigation on the shared image timeline"
```

---

### Task 12: Integration, image rebuild, and retiring the old selectors

**Files:**
- Modify: `gui/tile/analysis/widgets.py:294-295`, `gui/tile/analysis/analysis_tile.py:74,262-280,446,475-500,545-548`, `gui/tile/analysis/imagery.py:516-546`, `gui/tile/analysis/state.py:36-40`, `gui/widget/custom_sw.py:158-194`, `deforestation_alerts/scripts/imagery_service.py`, `deforestation_alerts/scripts/mosaics_helper.py:46-71`
- Test: `tests/test_analysis_state.py`, `tests/test_set_context.py`, `tests/test_timeline.py`
- Create: `tests/test_timeline_ee.py`

**Interfaces:**
- Consumes: `timeline.build_timeline`, `analysis.before_keys/after_keys`.
- Produces: `imagery.rebuild_image(source, keys, geom) -> ee.Image`; the `imagery_ctx` payload gains `bbox` (shapely, EPSG:4326), `date_min` and `date_max` (`date | None`) and loses `before_planet` / `after_planet`.

**Why this task is opus.** It touches eight files at once and it *inverts* an existing invariant: today's selectors only offer out-of-window images and the tests assert that; from here the timeline offers them and refuses to auto-pick them. Mechanically renaming those tests would leave the guarantee untested, so each one has to be rewritten to assert the new guarantee rather than deleted.

**The layout change.** Today the two selectors sit side by side, one under each map pane, so between them they cost the height of one. The new strip spans both panes: a 32px toolbar, a card of about 148px (two 45px lanes, a 27px axis, an 11px band label and padding) and a 36px readout, roughly 235px against the 300px `.custom-map-class2` reserves — so the maps gain about 50px, not more. The legend moves into the Analysis info dialog (`widgets.py:193`) rather than taking a row of its own.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_timeline.py
def test_rebuilding_a_sentinel2_image_mosaics_exactly_the_given_granules(monkeypatch):
    from gui.tile.analysis import imagery

    calls = {}

    class FakeImage:
        def __init__(self, key):
            calls.setdefault("images", []).append(key)

    class FakeCollection:
        def __init__(self, images):
            calls["collection"] = len(images)

        def mosaic(self):
            return self

        def clip(self, geom):
            calls["clipped"] = geom
            return self

    monkeypatch.setattr(imagery.ee, "Image", FakeImage)
    monkeypatch.setattr(imagery.ee, "ImageCollection", FakeCollection)
    imagery.rebuild_image(S2, ["a", "b"], "GEOM")
    assert calls["images"] == ["a", "b"] and calls["collection"] == 2
    assert calls["clipped"] == "GEOM"


def test_rebuilding_without_keys_returns_nothing_rather_than_an_empty_mosaic():
    from gui.tile.analysis import imagery

    assert imagery.rebuild_image(S2, [], "GEOM") is None


def test_rebuilding_a_landsat_image_looks_the_scene_ids_up_in_both_collections(monkeypatch):
    from gui.tile.analysis import imagery

    seen = []
    monkeypatch.setattr(imagery, "_landsat_pair", lambda keys, geom: seen.append((keys, geom)) or "IMG")
    assert imagery.rebuild_image(LS, ["LC8X"], "GEOM") == "IMG"
    assert seen == [(["LC8X"], "GEOM")]
```

```python
# tests/test_timeline_ee.py — the live tier, because only Earth Engine knows
# which scene ids are in T1_TOA
"""Tier 2: the Landsat rebuild against real Earth Engine. Skips without credentials."""

import pytest

pytestmark = pytest.mark.ee


def test_a_landsat_scene_with_no_toa_match_still_builds_an_image(gee):
    """Spec §9: "Landsat TOA match missing → fall back to the unsharpened SR
    image". T1_TOA is not a perfect mirror of T1_L2 — a scene can be in one and
    not the other. Pan-sharpening against an empty mosaic raises inside
    ``SFIM_pan_sharpen``, which would take down the pane for a scene the
    reviewer can see on the timeline."""
    import ee
    from gui.tile.analysis import imagery

    geom = ee.Geometry.Rectangle([-55.0, -10.0, -54.9, -9.9])
    img = imagery._landsat_pair(["LC82270682026100LGN00_NOT_A_REAL_ID"], geom)
    assert img is not None
    assert img.bandNames().size().getInfo() > 0  # the SR fallback, not a raise
```

```python
# tests/test_analysis_state.py — replace the three imagery token tests with these
def test_the_timeline_does_not_auto_pick_an_image_from_inside_the_alert_window():
    """What the retired selectors enforced by only offering out-of-window
    images, the timeline enforces in ``default_pick`` instead — because a long
    GLAD window can leave a reviewer no other choice than an in-window image,
    so hiding them was never right."""
    from deforestation_alerts.scripts import scene_query

    sel = _selection([(SOURCE_S2, "2026-06-10", 1.0), (SOURCE_S2, "2026-07-01", 1.0)])
    assert scene_query.default_pick(sel, date(2026, 5, 3), date(2026, 8, 23)) == (None, None)
    assert len(sel) == 2  # offered, just not chosen for the reviewer


def test_an_image_chosen_for_a_previous_alert_is_not_drawn():
    """The token gate the old fetch threads needed still holds: the timeline
    publishes into ``before_info``, and a rebuild that lands after the next
    alert change must drop its image."""
    analysis = AnalysisState()
    token = analysis.alert_token.value
    analysis.reset_alert()
    assert not analysis.is_current(token)
    assert analysis.apply_if_current(token, lambda: analysis.before_image.set("X")) is False
    assert analysis.before_image.value is None


def test_reset_alert_clears_the_selection_and_its_granule_keys():
    analysis = AnalysisState()
    analysis.before_info.set(["Sentinel 2", 1, "1.00", "x ", "_010126"])
    analysis.before_keys.set(["k"])
    analysis.reset_alert()
    assert analysis.before_info.value is None and analysis.before_keys.value == []


def test_nothing_under_analysis_holds_a_retired_planet_reactive():
    analysis = AnalysisState()
    assert not hasattr(analysis, "before_planet") and not hasattr(analysis, "after_planet")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_timeline.py tests/test_analysis_state.py -v`
Expected: FAIL — `AttributeError: module 'gui.tile.analysis.imagery' has no attribute 'rebuild_image'`, and the retired-reactive test fails because `before_planet` is still there.

- [ ] **Step 3: Rebuild the image from the granule keys**

In `gui/tile/analysis/imagery.py`, replace the body of `image_slider_map_callback`'s source branches with a call to a new function, and delete `image_slider_info_callback`, `_normalize_source_data`, `_SOURCE_TO_INDEX`, `_fetch_and_rebuild_s2`, `_fetch_and_rebuild_landsat`, `_on_ctx` and **`build_imagery` itself** (`imagery.py:516-546`) along with their imports. `build_imagery` is nothing but the `_on_ctx` subscription — with `_on_ctx` gone it has no body, and the `*_info` subscriptions added in this step replace what it was for. Its import at `analysis_tile.py:74` and its call at `analysis_tile.py:493-500` go with it; leaving either behind is an `ImportError` on mount or a second, stale fetch path per alert.

```python
# beside the existing imports in gui/tile/analysis/imagery.py
from deforestation_alerts.scripts.scene_index import SOURCE_LANDSAT, SOURCE_PLANET


def _landsat_pair(keys, geom):
    """One Landsat scene, pan-sharpened from its own TOA band 8.

    The surface-reflectance and TOA products are two assets of the same
    acquisition, which is why a Landsat row's key is its ``LANDSAT_SCENE_ID``
    rather than an asset id: the id is what finds the pair.
    """
    sr = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2").merge(
        ee.ImageCollection("LANDSAT/LC09/C02/T1_L2"))
    toa = ee.ImageCollection("LANDSAT/LC08/C02/T1_TOA").merge(
        ee.ImageCollection("LANDSAT/LC09/C02/T1_TOA"))
    where = ee.Filter.inList("LANDSAT_SCENE_ID", list(keys))
    sr_img = sr.filter(where).mosaic().select(
        ["SR_B1", "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"])
    toa_match = toa.filter(where)
    # T1_TOA is not a perfect mirror of T1_L2: a scene can be in the surface
    # product and missing from the TOA one. ``SFIM_pan_sharpen`` against an
    # empty mosaic raises, so fall back to the unsharpened SR image — coarser,
    # but a drawn pane rather than a dead one (spec §9). The branch is server
    # side, inside ``ee.Algorithms.If``, because asking which it is from Python
    # would cost a ``getInfo`` round trip per pick.
    sharpened = harmonizeL8ToS2_scaled(
        SFIM_pan_sharpen(sr_img, toa_match.mosaic().select("B8")))
    plain = harmonizeL8ToS2_scaled(sr_img)
    return ee.Image(
        ee.Algorithms.If(toa_match.size().gt(0), sharpened, plain)
    ).clip(geom)


def rebuild_image(source, keys, geom):
    """The chosen row as one ee.Image, clipped to the alert's grid.

    ``keys`` are the granules of that (collection, date) that overlap *this*
    alert, which the timeline worked out locally from the cached index. No
    ``filterBounds``, no cloud filter and no date filter: the choice was
    already made, and re-deriving it here is how the two could disagree.

    ``None`` when there are no keys — a selection cleared by an alert change,
    which the caller draws nothing for.
    """
    if not keys:
        return None
    if source == SOURCE_LANDSAT:
        return _landsat_pair(keys, geom)
    if source == SOURCE_PLANET:
        return ee.Image(list(keys)[0]).clip(geom)
    return ee.ImageCollection([ee.Image(k) for k in keys]).mosaic().clip(geom)
```

and in `image_slider_map_callback`, replace the whole `if img_source == ...` chain and the two `widgets.imgSelection*.disabled` pairs with:

```python
    if not analysis.is_current(token):
        return
    geom = widgets.actual_alert_grid
    img_source = selected_item["source"]
    vis1, vis2 = (
        ({"min": 0, "max": 1600, "bands": ["R", "G", "B"]},
         {"min": 0, "max": [6500, 1600, 1600], "bands": ["N", "R", "G"]})
        if img_source == SOURCE_PLANET else
        ({"min": 0, "max": 3000, "bands": ["B4", "B3", "B2"]},
         {"min": 0, "max": [6500, 3000, 3000], "bands": ["B8", "B4", "B3"]})
    )
    img = rebuild_image(img_source, selected_item.get("keys") or (), geom)
    if img is None:
        return
```

Wire the two lanes to it: in `analysis_tile.py`, where the panes are built, subscribe each `*_info` reactive so a timeline pick redraws its pane:

```python
    def _draw(pane, info_reactive, keys_reactive, map_element):
        def _on_pick(*_):
            info = info_reactive.value
            if not info:
                return
            item = {"source": info[0], "keys": list(keys_reactive.value)}
            spawn_in_context(imagery.image_slider_map_callback,
                             (item, map_element, pane, widgets, analysis,
                              selected_alerts_model, analysis.alert_token.value))

        info_reactive.subscribe(_on_pick)

    _draw(0, analysis.before_info, analysis.before_keys, widgets.map31)
    _draw(1, analysis.after_info, analysis.after_keys, widgets.map32)
```

- [ ] **Step 4: Publish what the timeline needs on the context**

In `analysis_tile.py` around line 262, the payload becomes:

```python
        widgets.imagery_ctx.value = {
            "alert_id": alert_id,
            # the alert the imagery is fetched for: a rebuild that ends after
            # the next alert change drops its result (``AnalysisState.alert_token``)
            "token": analysis.alert_token.value,
            "grid": widgets.actual_alert_grid,
            # the alert's own bounding box, in EPSG:4326, as the timeline's
            # local tile-overlap test needs it: a shapely geometry, no ee call
            "bbox": alerta["bounding_box"],
            # the acquisition-date window, as dates rather than the "N/A"-able
            # strings ``fecha1``/``fecha2`` the info card shows
            "date_min": _as_date(alerta["alert_date_min"]),
            "date_max": _as_date(alerta["alert_date_max"]),
            "fecha1": fecha1,
            "fecha2": fecha2,
            "show": list(widgets.show_image_collection),
        }
```

with, near `refresh_alert_info`:

```python
def _as_date(julian):
    """An alert's Julian date as a ``date``, or ``None``.

    ``vectorize`` writes NaN when a polygon's date pixels are all nodata, and
    ``convert_julian_to_date`` renders that "N/A"; the timeline needs the
    absence itself so it can fall back to the period's window.
    """
    text = convert_julian_to_date(julian)
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 5: Put the strip in the layout**

In `analysis_tile.py`, the two columns lose their selectors and a full-width row gains the strip:

```python
    # LEFT/STAGE: the two map columns, now carrying only their map — the
    # selector that used to sit under each one is replaced by the single strip
    # below both of them.
    left_inner_row = v.Html(
        tag="div",
        class_="analysis-row",
        children=[
            v.Html(tag="div", class_="analysis-col", children=[widgets.map31]),
            v.Html(tag="div", class_="analysis-col", children=[widgets.map32]),
        ],
    )
    strip_row = v.Html(tag="div", class_="analysis-strip", children=[widgets.timeline])
```

and the stage, at `analysis_tile.py:545-548`, has to list the strip — this is the one line that makes the whole plan visible, and `v.Container` renders exactly the children it is given:

```python
    stage = v.Container(
        fluid=True,
        class_="pa-0 ma-0 analysis-stage",
        children=[style_el, left_inner_row, strip_row],
    )
```

with the strip's own rule beside the `.custom-map-class2` one in `style_el`:

```python
            ".analysis-strip { width: 100%; padding: 0 4px 4px 4px; }"
```

In `widgets.py`, replace lines 294-295:

```python
        # One shared timeline under both panes replaces the two per-pane
        # selectors; built by the tile, which has the app and analysis state.
        self.timeline = None
```

and delete `_build_image_selector`, `imgInfo1`, `imgInfo2` and their helpers. In the tile, before the rows are assembled — and before `left_inner_row`, since `strip_row` holds the result:

```python
    widgets.timeline = build_timeline(app_state, analysis, widgets)
```

And at line 446, the maps get the reclaimed height:

```python
            ".custom-map-class2 { height: calc(100vh - 250px) !important; min-height: 320px; }"
```

- [ ] **Step 6: Delete what nothing calls any more**

```bash
grep -rn "CustomSlideGroup\|getIndividualS2\|getIndividualLandsat\|get_sentinel2_dates\|imgSelection\|before_planet\|after_planet\|image_slider_info_callback\|build_imagery" \
  gui/ deforestation_alerts/ tests/
```

Every hit must be in a file this task edits or a test it rewrites. Then remove:
- `gui/widget/custom_sw.py:158-194` — `CustomSlideGroup`
- `deforestation_alerts/scripts/imagery_service.py` — `getIndividualS2` and `getIndividualLandsat`, the N+1 `.getInfo()` loops this whole plan replaces
- `deforestation_alerts/scripts/mosaics_helper.py:46-71` — `get_sentinel2_dates`, whose after-window opened on `input_date2.replace(day=1)` and so offered an image from *before* the alert closed
- `gui/tile/analysis/state.py:38-40` — `before_planet` / `after_planet`, read but never written on this branch
- `gui/tile/analysis/imagery.py:516-546` — `build_imagery`, plus its import at `analysis_tile.py:74` and its call at `analysis_tile.py:493-500`
- `tests/test_set_context.py` — the `imgSelection` reference and the NICFI `image_id` fixture at line 563

The grep must come back with **zero** hits outside the deletions above. A surviving `build_imagery` reference is the failure mode this step exists for: the import is at the top of a 900-line file, far from the call, so deleting one and not the other is easy and the app only tells you on mount.

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS, with no `ee`-tier failures. Then the live tier once, which is where the Landsat fallback is checked: `DEFO_ALERTS_EE_TESTS=1 python -m pytest -m ee -q`.

- [ ] **Step 8: Check the strip by hand**

Start the app, open Analysis on a set with a multi-month alert window and confirm, in this order — each one has already caught a different bug class in this codebase:

1. Both lanes draw, the gold band matches the alert's dates, and the months wholly inside it are collapsed with a count on each.
2. Click a stripe in the upper lane: the left map redraws and the readout names that date. Click one in the lower lane: the right map redraws.
3. Move to the next alert: the strip repopulates with **no spinner and no Earth Engine wait**. That is the whole point of the change.
4. Close the Analysis step, open another step, come back: the strip is still drawn. A blank strip here means something became a Solara component.
5. Drag the cloud slider: stripes fade and reappear on release, and the chip counts follow.
6. Press `+1 month` on each side twice, then `Expand every month`: nothing overlaps and the axis labels still line up.
7. Tab into a lane and arrow left and right: a dashed ring walks the stripes and **the map does not move**; press Enter and that stripe becomes the selection and its pane redraws. If the map redraws on each arrow, the cursor and the selection have been conflated again and every key press is costing an Earth Engine tile request.
8. Save the alert and reopen the saved store: `before_img` holds the acquisition date you chose.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat(analysis): one shared timeline replaces the two image selectors"
```

---

## Notes for whoever executes this

**The acquisition-date change is deliberate and unmigrated.** Stores saved before this change hold *processing* dates in `before_img` / `after_img` — for Landsat Collection 2 those can be years off, because the collection was bulk-reprocessed. Stores saved after it hold acquisition dates. Nothing converts the old ones: the processing date of a scene is not recoverable from a date string, and the alerts they describe have already been reviewed. A store is therefore not comparable across the change, and that is the accepted cost of having the dates be right from here on.

**Planet is inert on this branch.** `before_planet` / `after_planet` were read and never written, so the Planet lane will be empty until NICFI access is configured. The lane, the chip and its zero count all still render — a collection that exists and holds nothing reads differently from one that is missing, and the reviewer should be able to tell which.

**If `scenes.parquet` grows uncomfortably**, the row count is roughly (granules over the AOI) × (dates in coverage). A national AOI at ±6 months is tens of thousands of rows, a few megabytes of parquet — fine. An AOI of a whole continent is not, and the answer there is to narrow the AOI, not to shorten the index: `scenes_for` is a vectorized filter and does not care, but a `getInfo` of a million-element array does.
