# Overview Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Overview right-panel count table with a 1300 px dialog dashboard of area-by-category, history and distribution charts, and move the alert map layers to Integrate & vectorize so they appear on the shared map as soon as a run completes.

**Architecture:** A Solara-free aggregator (`deforestation_alerts/scripts/set_summary.py`) reads seven columns out of a set's parquet store and returns a frozen `SetSummary`; a Solara-free renderer (`gui/scripts/charts.py`) turns a `SetSummary` into matplotlib-drawn SVG strings that the tile inlines with `solara.HTML(unsafe_innerHTML=…)`. The ~350 lines of map plumbing in `overview_tile.py` move to `gui/scripts/alert_layers.py`, driven by a new shell leaf `_AlertLayers` beside `_ActiveSetLoader`, with two switches in Integrate & vectorize. Overview itself becomes a MapApp dialog rail item, which needs no new shell machinery.

**Tech Stack:** Python 3.10+, pysepal 4.1.0, Solara 1.62.0, ipyvuetify 1.x (`rv.*`), matplotlib ≥3.9 (Agg/SVG only), pandas ≥2.2, pyarrow, geopandas, pytest + reacton render harnesses. Deployment is Voila (`ui.ipynb`), **not** solara-server: nothing may need a CDN.

**Spec:** `docs/superpowers/specs/2026-10-02-overview-dashboard-design.md` (commits `472e9fc`, `c5d9ee2`). Visual reference: the `Dashboard.dc.html` / `DashboardDark.dc.html` artboards at <https://claude.ai/artifact/FsydPdhJqfj6kJ3R4zTnkf>.

## Task / model assignment

| # | Task | Model | Why |
|---|---|---|---|
| 1 | `set_summary.py` — the pure aggregator | **opus** | Dense numeric logic, eight output fields, many null/edge branches; wrong here is wrong everywhere downstream. |
| 2 | `STACK_ORDER` in `legend_specs.py` | **sonnet** | Four lines and two assertions, no judgement needed. |
| 3 | `charts.py` — seven figures + `to_inline_svg` | **opus** | matplotlib layout, SVG post-processing, the no-hex-literal rule and the theme contract all interact. |
| 4 | `alert_layers.py` — extract the map plumbing | **opus** | A move of concurrent, deferral-aware code; a subtle slip reintroduces a race the comments warn about. |
| 5 | `_AlertLayers` leaf + the two Integrate switches | **opus** | Effect keys, auto-show-once semantics and `inflight` handling; the user-visible promise of the feature. |
| 6 | Overview becomes a dialog | **sonnet** | Mechanical: one `RailItem`, four shell edits, three test files. |
| 7 | The dashboard itself + i18n + retire `overview_helper` | **opus** | The largest surface: a rewritten tile, per-card error isolation, 40+ message keys in three locales. |

## Global Constraints

Copied from the spec; every task's requirements implicitly include this section.

- **Mutate-then-replace** for any project write: `p = app_state.project.value; p.<mutate>; app_state.project.set(p.model_copy())`. This plan writes no project field, so no task should need it.
- **No traitlets.** New state lives in the pydantic `Project` or in Solara reactives/`use_state`. `tests/test_app_wiring.py` fails the build on `from traitlets` / `import traitlets` in a tile.
- **`deforestation_alerts/` must never import from `gui/`.** Verified clean today. `set_summary.py` therefore takes a `drivers_of` callable instead of importing `gui.scripts.loss_drivers`.
- **`gui/scripts/` and `deforestation_alerts/` must not import solara or ipyvuetify.** Heavy geo imports (`geopandas`, `pyarrow`, `matplotlib.pyplot`) go **inside** functions.
- **No palette hex outside `legend_specs.py`.** `tests/test_legend_specs.py::test_no_other_module_writes_these_colours` reads every `gui/**/*.py` and fails on any `ALERT_CLASSES` / `STATUS_CLASSES` / `MASK_COLOR` hex. `charts.py` must read status colours from `legend_specs.status_colors()`; only its own chrome tokens (surface, ink, grid, de-emphasis, hatch) may be literals.
- **No new third-party dependency.** `sepal_environment.yml` is the packaging authority and must not change; `tests/test_environment_file.py` fails on an undeclared import. `matplotlib>=3.9`, `pandas>=2.2`, `pyarrow` are already declared. No plotly, no echarts, no anywidget.
- **No string resolved at import time.** `t()` is only called inside a function or component body. `gui/i18n.py` exposes `t(key, /, **values)`, `tn(key, count, /, **values)`, `format_number(value, digits=0)`, `has_message(key)`.
- **Every new message key exists in `en`, `es-ES` and `fr`** (`tests/test_i18n.py::test_the_catalogue_is_complete_in_every_locale`), with no English left in the translations (`test_no_english_is_left_in_the_translations`). `app.json`, `ui.json` and `about.json` are **pruned files**: a key in them that no code uses fails `test_every_english_key_of_the_legacy_files_is_used`. Catalogue files are `json.dumps(obj, indent=2, ensure_ascii=False) + "\n"` exactly (`test_every_catalogue_file_keeps_one_layout`).
- **A key built with an f-string must be registered** in `tests/test_i18n.py::_dynamic_keys()`. Prefer literal keys and avoid adding entries.
- **Absolute paths only.** The SEPAL install directory is read-only; nothing writes to the CWD. Store paths come from `set_store.set_folder` / `paths.resolve`.
- **Background work only via `spawn_in_context`** (`gui/scripts/solara_threads.py`) or `solara.lab.use_task`. A bare `threading.Thread` never reaches the browser. **This plan uses `spawn_in_context` from an effect**, never `use_task`: Integrate records a set from its worker thread, so the Overview tile can re-render on a worker, and a task started from such a render has no event loop (the `_ActiveSetLoader` ruling, `gui/solara_app.py:272`).
- **`matplotlib` must not pick a GUI backend.** `charts.py` calls `matplotlib.use("Agg")` before importing `pyplot`, inside the function.
- **The deployment is Voila.** Figures are inlined SVG; nothing loads from a CDN.
- Status codes are `engine.pmtiles.STATUS_CODES` = `{"Not reviewed": 0, "Confirmed": 1, "False Positive": 2, "Need revision": 3}`.
- `alert_date_min` / `alert_date_max` are **decimal years**, `year + doy/1000` (`engine/vectorize.yyyyddd_to_decimal`).
- `description` (loss drivers) and `admin1` are written **only during Analysis review**; `admin2`/`admin3` are created empty and never written. Driver and admin cards are reviewed-only and carry that in their subtitle.
- Run the tests from the worktree root: `cd /home/jserafini/Desktop/projects/deforestation_alerts/slices2-6-wt`. Earth-Engine tests are skipped unless `DEFO_ALERTS_EE_TESTS=1`; nothing in this plan needs EE.
- **Commit messages carry no `Co-Authored-By` trailer.**

## Review Focus

Five input classes the spec implies but whose tests no task would otherwise write. Each one's test is attached to the task that owns the code.

1. **`alert_date_min` is null or NaN for some alerts.** A source harmonized without a date band leaves it empty; `by_week` must skip those rows and `largest`'s date must be `None`, rather than raising in `decimal_year_to_date`. → Task 1.
2. **A set with alerts but zero reviewed.** Every project is in this state between Integrate and the first review: `n_reviewed == 0`, `by_driver` and `by_admin` empty, the Reviewed % KPI must not divide by zero and the two cards must show their empty state instead of an axis with no bars. → Tasks 1 and 7.
3. **A language switch while the dashboard is open.** Chart text is baked into the SVG at render time, so the figures must be re-rendered, not just the surrounding labels. → Task 7.
4. **A theme toggle while the dashboard is open.** Dark mode is a chosen second set of steps, so a toggle must re-render every figure against `#1e1e1e`; a light SVG left on a dark card is unreadable. → Task 7.
5. **A map click with the alert layer on and no project, or alerts belonging to another set.** The clicks are no longer view-gated, so the handler runs in states the old code could never reach: it must return quietly instead of raising inside an ipyleaflet callback (where the traceback never reaches the user). → Task 5.

## File Structure

**New**

| File | Responsibility |
|---|---|
| `deforestation_alerts/scripts/set_summary.py` | Solara-free aggregation: `SetSummary`, `StatusBreakdown`, `SUMMARY_COLUMNS`, `summarize_alerts`, `read_summary`, `decimal_year_to_date`. Knows nothing about colour, translation or Solara. |
| `gui/scripts/charts.py` | `SetSummary` → SVG string. Seven figure functions, `THEMES`, `theme_for(dark)`, `to_inline_svg(fig, label)`. Imports `t`/`format_number` and `legend_specs`; no solara. |
| `gui/scripts/alert_layers.py` | The alert layers on the shared map: keys, `earlier_of`, `add_centroid_layer`, `active_builder`, `earlier_builder`, `hit`, `popup_child`. No solara component. |
| `tests/test_set_summary.py` | Task 1's tests. |
| `tests/test_charts.py` | Task 3's tests. |
| `tests/test_alert_layers.py` | Tasks 4 and 5's tests (the leaf is rendered here, not in `test_overview_render.py`). |

**Changed**

| File | Change |
|---|---|
| `gui/scripts/legend_specs.py` | `STACK_ORDER` beside `STATUS_CLASSES` (Task 2). |
| `gui/scripts/set_store.py` | `active_set_alerts(project, gdf)`, moved from `overview_tile._set_alerts` (Task 4). |
| `gui/tile/overview_tile.py` | Loses all map plumbing (Task 4), the back button (Task 6), then is rewritten as the dashboard (Task 7). |
| `gui/tile/integrate_step.py` | `_IntegrateForm` gains `layers` and `project`; two switches below the Run button (Task 5). |
| `gui/solara_app.py` | `_AlertLayers` leaf + mount (Task 5); the Overview dialog wiring (Task 6). |
| `gui/store/workflow_steps.py` | The Overview `RailItem` and the module docstring (Task 6). |
| `gui/store/navigation.py` | The dead `display == "panel"` branch goes (Task 6). |
| `deforestation_alerts/message/{en,es-ES,fr}/{steps,ui}.json` | Keys moved (Task 5) and added/removed (Tasks 6, 7). |
| `tests/test_legend_specs.py` | `STACK_ORDER` assertions (Task 2). |
| `tests/test_workflow_steps.py` | The rail-item assertion (Task 6). |
| `tests/test_navigation.py`, `tests/test_app_wiring.py` | The Overview-as-panel assertions (Task 6). |
| `tests/test_overview_render.py` | The layer/click tests move out (Task 4), the dashboard tests replace the count-table ones (Task 7). |
| `tests/test_overview_export.py` | The export card survives; its harness follows the tile's new signature (Task 7). |
| `tests/test_i18n.py` | `_dynamic_keys()` only if a task adds an f-string key (it should not). |

**Deleted**

| File | Why |
|---|---|
| `deforestation_alerts/scripts/overview_helper.py` | Its one function `calculate_alert_classes` has one caller and no test; `set_summary` supersedes it (Task 7). |

---

### Task 1: The aggregator — `set_summary.py`

**Model:** opus

**Files:**
- Create: `deforestation_alerts/scripts/set_summary.py`
- Test: `tests/test_set_summary.py`

**Interfaces:**
- Consumes: `deforestation_alerts.engine.pmtiles.STATUS_CODES` (`{"Not reviewed": 0, "Confirmed": 1, "False Positive": 2, "Need revision": 3}`); `deforestation_alerts.scripts.report_builder.parse_formatted_string(formatted_string) -> list[str]`.
- Produces:
  - `SUMMARY_COLUMNS: list[str] = ["alert_id", "status", "area_ha", "alert_date_min", "alert_sources", "description", "admin1"]`
  - `SIZE_EDGES: tuple[float, ...] = (0.5, 1.0, 2.0, 5.0, 10.0, 25.0)`
  - `UNKNOWN_DRIVER = "unknown"`, `UNATTRIBUTED = ""`, `UNKNOWN_SOURCE = ""`
  - `@dataclass(frozen=True) StatusBreakdown(code: int, n: int, area_ha: float, median_ha: float)`
  - `@dataclass(frozen=True) SetSummary(set_id: str, n_alerts: int, area_ha: float, n_reviewed: int, by_status: tuple[StatusBreakdown, ...], size_bins: tuple[tuple[float, float, int, float], ...], by_week: tuple[tuple[date, float, int, bool], ...], by_driver: tuple[tuple[str, float, int], ...], by_source: tuple[tuple[str, float, int], ...], by_admin: tuple[tuple[str, float, float, int], ...], largest: tuple[int, float, str, date | None] | None)`
  - `decimal_year_to_date(value) -> date | None`
  - `summarize_alerts(frame, *, set_id="", period_start=None, drivers_of=None) -> SetSummary`
  - `read_summary(store_path, set_id, *, period_start=None, drivers_of=None) -> SetSummary`
  - `by_status` is in **status-code order** (0, 1, 2, 3) and always has four entries. `charts.py` (Task 3) applies `STACK_ORDER`; this module must not import from `gui/`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_set_summary.py`:

```python
"""The Overview dashboard's aggregation (spec: Data layer).

``summarize_alerts`` is pure — it takes a DataFrame — so every branch is
covered here without a store, a project or a render harness.
"""

from datetime import date

import pandas as pd
import pytest

from deforestation_alerts.engine.pmtiles import STATUS_CODES
from deforestation_alerts.scripts import set_summary as ss


def _frame(**overrides):
    """Four alerts, one per status, with every summary column filled."""
    data = {
        "alert_id": [10, 11, 12, 13],
        "status": ["Confirmed", "False Positive", "Need revision", "Not reviewed"],
        "area_ha": [2.0, 0.25, 1.5, 0.75],
        "alert_date_min": [2026.045, 2026.045, 2026.052, 2026.010],
        "alert_sources": ["GLAD-L and RADD", "RADD", "GLAD-L", "GLAD-L"],
        "description": ["logging, mining", "", "agriculture_expansion", ""],
        "admin1": ["Canindeyu", "Canindeyu", "Amambay", ""],
    }
    data.update(overrides)
    return pd.DataFrame(data)


def _status(summary, name):
    code = STATUS_CODES[name]
    return next(b for b in summary.by_status if b.code == code)


def test_an_empty_frame_is_zeroed_not_none():
    s = ss.summarize_alerts(pd.DataFrame(), set_id="x")
    assert s.set_id == "x" and s.n_alerts == 0 and s.area_ha == 0.0 and s.n_reviewed == 0
    assert [b.code for b in s.by_status] == [0, 1, 2, 3]
    assert all(b.n == 0 and b.area_ha == 0.0 and b.median_ha == 0.0 for b in s.by_status)
    assert s.by_week == () and s.by_driver == () and s.by_source == () and s.by_admin == ()
    assert s.largest is None


def test_none_is_zeroed_too():
    assert ss.summarize_alerts(None).n_alerts == 0


def test_totals_and_status_breakdown_are_in_code_order():
    s = ss.summarize_alerts(_frame(), set_id="jan")
    assert s.n_alerts == 4 and s.area_ha == pytest.approx(4.5)
    assert s.n_reviewed == 3  # everything but "Not reviewed"
    assert [b.code for b in s.by_status] == [0, 1, 2, 3]
    assert _status(s, "Confirmed").area_ha == pytest.approx(2.0)
    assert _status(s, "Confirmed").median_ha == pytest.approx(2.0)
    assert _status(s, "Not reviewed").n == 1


def test_all_unreviewed_leaves_the_review_only_rollups_empty():
    s = ss.summarize_alerts(_frame(status=["Not reviewed"] * 4))
    assert s.n_reviewed == 0
    assert s.by_driver == () and s.by_admin == ()
    assert s.n_alerts == 4 and _status(s, "Not reviewed").n == 4  # the set itself is still summarised


def test_all_confirmed():
    s = ss.summarize_alerts(_frame(status=["Confirmed"] * 4))
    assert s.n_reviewed == 4 and _status(s, "Confirmed").n == 4
    assert _status(s, "Confirmed").median_ha == pytest.approx(1.125)  # median of 0.25, 0.75, 1.5, 2.0


def test_an_unknown_status_falls_to_not_reviewed():
    s = ss.summarize_alerts(_frame(status=["Reviewed by Bob", None, "Confirmed", "Not reviewed"]))
    assert _status(s, "Not reviewed").n == 3 and _status(s, "Confirmed").n == 1


def test_a_missing_column_does_not_raise():
    frame = _frame().drop(columns=["description", "admin1", "alert_sources", "alert_date_min"])
    s = ss.summarize_alerts(frame)
    assert s.n_alerts == 4 and s.by_week == () and s.by_source == ()
    assert s.by_driver == ((ss.UNKNOWN_DRIVER, pytest.approx(3.75), 3),)
    assert s.by_admin == ((ss.UNATTRIBUTED, pytest.approx(3.75), pytest.approx(2.0), 3),)


def test_a_negative_or_null_area_counts_as_zero():
    s = ss.summarize_alerts(_frame(area_ha=[2.0, None, -4.0, "x"]))
    assert s.area_ha == pytest.approx(2.0)
    assert _status(s, "False Positive").area_ha == 0.0


# --- drivers -------------------------------------------------------------

def test_an_alerts_full_area_counts_under_each_driver_it_names():
    """``description`` is multi-code (loss_drivers._SEP), so the bars may sum
    past the reviewed total — the spec's ruling."""
    s = ss.summarize_alerts(_frame())
    assert dict((c, n) for c, _, n in s.by_driver) == {"logging": 1, "mining": 1, "agriculture_expansion": 1,
                                                      ss.UNKNOWN_DRIVER: 1}
    assert dict((c, ha) for c, ha, _ in s.by_driver)["logging"] == pytest.approx(2.0)
    assert dict((c, ha) for c, ha, _ in s.by_driver)["mining"] == pytest.approx(2.0)
    assert sum(ha for _, ha, _ in s.by_driver) > s.area_ha - 0.75  # more than the reviewed area


def test_drivers_are_sorted_by_area_descending():
    s = ss.summarize_alerts(_frame())
    areas = [ha for _, ha, _ in s.by_driver]
    assert areas == sorted(areas, reverse=True)


def test_a_reviewed_alert_with_no_driver_falls_to_unknown():
    s = ss.summarize_alerts(_frame(status=["Confirmed"] * 4, description=[None, "", "   ", "mining"]))
    assert dict((c, n) for c, _, n in s.by_driver) == {ss.UNKNOWN_DRIVER: 3, "mining": 1}


def test_drivers_of_is_used_when_given():
    s = ss.summarize_alerts(_frame(), drivers_of=lambda value: ["only"])
    assert [c for c, _, _ in s.by_driver] == ["only"]


# --- sources -------------------------------------------------------------

def test_a_source_combination_is_one_bar_parsed_not_split():
    """``alert_sources`` is report_builder.format_list prose, so "GLAD-L and
    RADD" is one combination, counted once — never once per source."""
    s = ss.summarize_alerts(_frame())
    assert dict((k, n) for k, _, n in s.by_source) == {"GLAD-L + RADD": 1, "RADD": 1, "GLAD-L": 2}


def test_a_combination_is_order_insensitive():
    s = ss.summarize_alerts(_frame(alert_sources=["GLAD-L and RADD", "RADD and GLAD-L", "GLAD-L", "GLAD-L"]))
    assert dict((k, n) for k, _, n in s.by_source)["GLAD-L + RADD"] == 2


def test_a_null_source_falls_to_the_unknown_bucket():
    s = ss.summarize_alerts(_frame(alert_sources=[None, "", "RADD", "RADD"]))
    assert dict((k, n) for k, _, n in s.by_source) == {ss.UNKNOWN_SOURCE: 2, "RADD": 2}


# --- admin ---------------------------------------------------------------

def test_admin_is_reviewed_only_and_carries_the_confirmed_area():
    s = ss.summarize_alerts(_frame())
    assert s.by_admin == (("Canindeyu", pytest.approx(2.25), pytest.approx(2.0), 2),
                          ("Amambay", pytest.approx(1.5), 0.0, 1))
    assert sum(n for *_, n in s.by_admin) == s.n_reviewed  # the unreviewed one is absent


def test_a_reviewed_alert_without_an_admin_name_groups_under_the_sentinel():
    s = ss.summarize_alerts(_frame(status=["Confirmed"] * 4, admin1=[None, "", "Amambay", "Amambay"]))
    assert dict((k, n) for k, _, _, n in s.by_admin) == {ss.UNATTRIBUTED: 2, "Amambay": 2}


# --- dates ---------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (2026.045, date(2026, 2, 14)),   # day of year 45
    (2024.060, date(2024, 2, 29)),   # a leap year's 60th day
    (2026.001, date(2026, 1, 1)),
    (2026.365, date(2026, 12, 31)),
])
def test_decimal_year_to_date_inverts_yyyyddd_to_decimal(value, expected):
    assert ss.decimal_year_to_date(value) == expected


def test_decimal_year_to_date_round_trips_the_engines_own_encoding():
    from deforestation_alerts.engine.vectorize import yyyyddd_to_decimal

    for stamp, expected in ((2026045, date(2026, 2, 14)), (2024060, date(2024, 2, 29))):
        assert ss.decimal_year_to_date(yyyyddd_to_decimal(stamp)) == expected


@pytest.mark.parametrize("value", [None, float("nan"), 0, 0.0, "", "x", -1.0])
def test_decimal_year_to_date_returns_none_for_anything_unusable(value):
    assert ss.decimal_year_to_date(value) is None


# --- weeks (Review focus 1) ---------------------------------------------

def test_weeks_are_mondays_with_the_area_of_their_alerts():
    s = ss.summarize_alerts(_frame())
    assert [w for w, *_ in s.by_week] == [date(2026, 1, 5), date(2026, 2, 9), date(2026, 2, 16)]
    assert dict((w, n) for w, _, n, _ in s.by_week)[date(2026, 2, 9)] == 2  # the two Feb-14 alerts


def test_period_start_marks_the_look_back_weeks():
    s = ss.summarize_alerts(_frame(), period_start=date(2026, 2, 10))
    flags = dict((w, inside) for w, _, _, inside in s.by_week)
    assert flags[date(2026, 1, 5)] is False     # before the period
    assert flags[date(2026, 2, 9)] is True      # the week the period starts in
    assert flags[date(2026, 2, 16)] is True


def test_without_a_period_start_every_week_is_in_period():
    assert all(inside for *_, inside in ss.summarize_alerts(_frame()).by_week)


def test_an_unusable_date_is_skipped_rather_than_raising():
    """Review focus 1: a source harmonized without a date band leaves
    ``alert_date_min`` empty."""
    s = ss.summarize_alerts(_frame(alert_date_min=[None, float("nan"), 0.0, 2026.010]))
    assert [w for w, *_ in s.by_week] == [date(2026, 1, 5)]
    assert s.n_alerts == 4  # the three dateless alerts are still counted everywhere else


# --- sizes and the largest alert -----------------------------------------

def test_size_bins_cover_every_alert_once_and_end_open():
    s = ss.summarize_alerts(_frame())
    assert len(s.size_bins) == len(ss.SIZE_EDGES) + 1
    assert s.size_bins[0][0] == 0.0 and s.size_bins[-1][1] == float("inf")
    assert sum(n for _, _, n, _ in s.size_bins) == s.n_alerts
    assert sum(ha for *_, ha in s.size_bins) == pytest.approx(s.area_ha)


def test_the_largest_alert_carries_its_id_area_admin_and_date():
    assert ss.summarize_alerts(_frame()).largest == (10, pytest.approx(2.0), "Canindeyu", date(2026, 2, 14))


def test_the_largest_alert_may_be_unreviewed_and_dateless():
    s = ss.summarize_alerts(_frame(status=["Not reviewed"] * 4, alert_date_min=[None] * 4))
    assert s.largest[0] == 10 and s.largest[3] is None


# --- the store read ------------------------------------------------------

def test_read_summary_reads_only_the_summary_columns(tmp_path):
    """Constraint 1: the projected read is what makes the open-gate
    unnecessary. The guard is that every name it asks for exists."""
    import pyarrow.parquet as pq

    store = tmp_path / "alert_db.parquet"
    _frame().to_parquet(store)
    assert set(ss.SUMMARY_COLUMNS) <= set(pq.read_schema(store).names)
    s = ss.read_summary(store, "jan", period_start=date(2026, 2, 10))
    assert s.set_id == "jan" and s.n_alerts == 4 and s.n_reviewed == 3


def test_read_summary_survives_a_store_missing_an_optional_column(tmp_path):
    store = tmp_path / "alert_db.parquet"
    _frame().drop(columns=["admin1"]).to_parquet(store)
    assert ss.read_summary(store, "jan").by_admin == ((ss.UNATTRIBUTED, pytest.approx(3.75),
                                                       pytest.approx(2.0), 3),)


def test_read_summary_raises_for_a_missing_store(tmp_path):
    with pytest.raises(Exception):
        ss.read_summary(tmp_path / "gone.parquet", "jan")


def test_the_summary_columns_are_what_a_real_store_carries(tmp_path):
    """Guards against a column rename in alert_table/vectorize drifting away
    from the reader."""
    from deforestation_alerts.engine.vectorize import COLUMNS

    assert "area_ha" in COLUMNS and "alert_date_min" in COLUMNS
    assert set(ss.SUMMARY_COLUMNS) - {"alert_id", "status", "alert_sources", "description", "admin1"} <= set(COLUMNS)


def test_the_module_does_not_import_gui_or_solara():
    from pathlib import Path

    src = Path("deforestation_alerts/scripts/set_summary.py").read_text(encoding="utf-8")
    for banned in ("import gui", "from gui", "import solara", "import ipyvuetify"):
        assert banned not in src, banned
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_set_summary.py -x -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'deforestation_alerts.scripts.set_summary'`

- [ ] **Step 3: Write the implementation**

Create `deforestation_alerts/scripts/set_summary.py`:

```python
"""Everything the Overview dashboard counts, read from a set's alert store.

Solara-free and ``gui``-free (architecture contract #7): ``summarize_alerts``
takes a DataFrame, so every branch is unit-testable without a store, a project
or a render harness, and ``read_summary`` only adds the projected parquet read.

Two details of the store shape drive this module:

* ``alert_date_min`` is a **decimal year** (``year + doy/1000``, written by
  ``engine/vectorize.yyyyddd_to_decimal``), not a date.
* ``description`` (loss drivers) and ``admin1`` are written per alert **during
  Analysis review**, so ``by_driver`` and ``by_admin`` cover reviewed alerts
  only. ``description`` holds several comma-joined codes, and an alert's full
  area counts once under each code it names: splitting it between the drivers
  would invent a number the review never recorded.

Driver codes are split by a caller-supplied ``drivers_of`` (the GUI passes
``gui.scripts.loss_drivers.codes``, which also reads legacy label stores back
to codes) because this package must never import from ``gui/``.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Optional

from deforestation_alerts.engine.pmtiles import STATUS_CODES
from deforestation_alerts.scripts.report_builder import parse_formatted_string

#: The only columns the dashboard reads. A projected parquet read of these is
#: ~36x cheaper than loading the geometry too, which is what makes recomputing
#: on every set switch cheaper than caching (spec Constraint 1).
SUMMARY_COLUMNS = ["alert_id", "status", "area_ha", "alert_date_min",
                   "alert_sources", "description", "admin1"]

#: Upper edges of the size histogram, in hectares; the last bin is open.
SIZE_EDGES = (0.5, 1.0, 2.0, 5.0, 10.0, 25.0)

#: A reviewed alert that recorded no loss driver.
UNKNOWN_DRIVER = "unknown"
#: A reviewed alert whose ``admin1`` was never written. Empty on purpose: the
#: label is a translation, and this module resolves none.
UNATTRIBUTED = ""
#: An alert whose ``alert_sources`` is empty.
UNKNOWN_SOURCE = ""

_NOT_REVIEWED = STATUS_CODES["Not reviewed"]
_CONFIRMED = STATUS_CODES["Confirmed"]
_SEP = ", "  # gui.scripts.loss_drivers._SEP


@dataclass(frozen=True)
class StatusBreakdown:
    """One review status's share of a set."""

    code: int
    n: int
    area_ha: float
    median_ha: float


@dataclass(frozen=True)
class SetSummary:
    """Everything the dashboard draws for one alert set.

    ``by_status`` is in status-code order and always has one entry per code, so
    a chart or a table can index it without checking. The stacking order that
    keeps Confirmed and False positive apart lives in
    ``gui.scripts.legend_specs.STACK_ORDER`` and is applied by the charts.
    """

    set_id: str
    n_alerts: int
    area_ha: float
    n_reviewed: int
    by_status: tuple
    size_bins: tuple      # (lo, hi, n, area_ha)
    by_week: tuple        # (Monday, area_ha, n, in_period)
    by_driver: tuple      # (code, area_ha, n), area-descending
    by_source: tuple      # (combination, area_ha, n), area-descending
    by_admin: tuple       # (admin1, area_ha, confirmed_ha, n), confirmed-descending
    largest: Optional[tuple]  # (alert_id, area_ha, admin1, date | None)


def decimal_year_to_date(value) -> Optional[date]:
    """The calendar date of a decimal year, or ``None`` when unusable.

    ``engine/vectorize.yyyyddd_to_decimal`` stores ``year + doy/1000``, so the
    fraction is the day of year over 1000 — never over 365.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or not 1900.0 <= v <= 2200.0:  # NaN, 0, or a sentinel
        return None
    year = int(v)
    doy = max(1, int(round((v - year) * 1000)))
    try:
        return date(year, 1, 1) + timedelta(days=doy - 1)
    except (ValueError, OverflowError):
        return None


def summarize_alerts(frame, *, set_id: str = "", period_start: Optional[date] = None,
                     drivers_of: Optional[Callable] = None) -> SetSummary:
    """Summarise an alert frame. Never raises on a missing column or value.

    Parameters
    ----------
    frame
        The alerts, or ``None``. Only ``SUMMARY_COLUMNS`` are read; a column
        that is absent is treated as empty.
    set_id
        Carried through onto the summary, so a stale one is recognisable.
    period_start
        The active period's first day. Weeks before the week it falls in are
        marked ``in_period=False``: they are look-back alerts, and the chart
        de-emphasises them so they cannot read as this period's own signal.
    drivers_of
        ``description`` -> list of driver codes. Defaults to a plain split on
        ``", "``; the GUI passes ``loss_drivers.codes``.
    """
    import pandas as pd

    if frame is None or len(frame) == 0:
        return _zeroed(set_id)

    split = drivers_of if drivers_of is not None else _split_drivers
    n = len(frame)
    area = _area(frame, n)
    code = _code(frame, n)
    reviewed = [c != _NOT_REVIEWED for c in code]

    return SetSummary(
        set_id=set_id,
        n_alerts=int(n),
        area_ha=float(sum(area)),
        n_reviewed=int(sum(reviewed)),
        by_status=_by_status(area, code),
        size_bins=_size_bins(area),
        by_week=_by_week(_column(frame, "alert_date_min", n), area, period_start),
        by_driver=_by_driver(_column(frame, "description", n), area, reviewed, split),
        by_source=_by_source(_column(frame, "alert_sources", n), area),
        by_admin=_by_admin(_column(frame, "admin1", n), area, code, reviewed),
        largest=_largest(frame, area, n),
    )


def read_summary(store_path, set_id: str, *, period_start: Optional[date] = None,
                 drivers_of: Optional[Callable] = None) -> SetSummary:
    """``summarize_alerts`` of a set's store, read column-projected.

    Blocking (parquet): call it from a worker thread. Raises when the store
    cannot be read — the dashboard then shows its MISSING text and no charts.
    """
    import pandas as pd
    import pyarrow.parquet as pq

    names = set(pq.read_schema(store_path).names)
    columns = [c for c in SUMMARY_COLUMNS if c in names]
    frame = pd.read_parquet(store_path, columns=columns) if columns else pd.DataFrame()
    return summarize_alerts(frame, set_id=set_id, period_start=period_start, drivers_of=drivers_of)


# ---- internals ------------------------------------------------------------


def _zeroed(set_id: str) -> SetSummary:
    return SetSummary(set_id=set_id, n_alerts=0, area_ha=0.0, n_reviewed=0,
                      by_status=tuple(StatusBreakdown(code=c, n=0, area_ha=0.0, median_ha=0.0)
                                      for c in sorted(set(STATUS_CODES.values()))),
                      size_bins=(), by_week=(), by_driver=(), by_source=(), by_admin=(), largest=None)


def _text(value) -> str:
    """A cell as text: ``""`` for None, NaN or NaT."""
    import pandas as pd

    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass
    return str(value)


def _split_drivers(value) -> list:
    return [part.strip() for part in _text(value).split(_SEP) if part.strip()]


def _column(frame, name: str, n: int) -> list:
    return list(frame[name]) if name in frame.columns else [None] * n


def _area(frame, n: int) -> list:
    """``area_ha`` as non-negative floats; anything unusable counts as zero."""
    out = []
    for value in _column(frame, "area_ha", n):
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 0.0
        out.append(v if v == v and v > 0.0 else 0.0)
    return out


def _code(frame, n: int) -> list:
    """``status`` as codes; an unknown or missing status falls to 0."""
    return [STATUS_CODES.get(_text(value), _NOT_REVIEWED) for value in _column(frame, "status", n)]


def _median(values: list) -> float:
    from statistics import median

    return float(median(values)) if values else 0.0


def _by_status(area: list, code: list) -> tuple:
    out = []
    for c in sorted(set(STATUS_CODES.values())):
        mine = [a for a, k in zip(area, code) if k == c]
        out.append(StatusBreakdown(code=c, n=len(mine), area_ha=float(sum(mine)), median_ha=_median(mine)))
    return tuple(out)


def _size_bins(area: list) -> tuple:
    edges = (0.0,) + tuple(SIZE_EDGES) + (float("inf"),)
    out = []
    for lo, hi in zip(edges, edges[1:]):
        mine = [a for a in area if lo <= a < hi]
        out.append((float(lo), float(hi), len(mine), float(sum(mine))))
    return tuple(out)


def _by_week(dates: list, area: list, period_start: Optional[date]) -> tuple:
    weeks = {}
    for value, a in zip(dates, area):
        d = decimal_year_to_date(value)
        if d is None:
            continue  # a source harmonized without a date band: skipped, not fatal
        monday = d - timedelta(days=d.weekday())
        ha, n = weeks.get(monday, (0.0, 0))
        weeks[monday] = (ha + a, n + 1)
    first = period_start - timedelta(days=period_start.weekday()) if period_start is not None else None
    return tuple((w, ha, n, first is None or w >= first) for w, (ha, n) in sorted(weeks.items()))


def _ranked(table: dict) -> tuple:
    """``{key: (area, n)}`` as area-descending rows, ties broken by name."""
    return tuple((k, ha, n) for k, (ha, n) in sorted(table.items(), key=lambda kv: (-kv[1][0], kv[0])))


def _by_driver(descriptions: list, area: list, reviewed: list, split: Callable) -> tuple:
    table = {}
    for value, a, keep in zip(descriptions, area, reviewed):
        if not keep:
            continue  # drivers are recorded by review only
        for c in (split(value) or [UNKNOWN_DRIVER]):
            ha, n = table.get(c, (0.0, 0))
            table[c] = (ha + a, n + 1)  # the alert's FULL area, once per code it names
    return _ranked(table)


def _by_source(sources: list, area: list) -> tuple:
    table = {}
    for value, a in zip(sources, area):
        parts = sorted(p.strip() for p in parse_formatted_string(_text(value)) if p.strip())
        key = " + ".join(parts) if parts else UNKNOWN_SOURCE
        ha, n = table.get(key, (0.0, 0))
        table[key] = (ha + a, n + 1)
    return _ranked(table)


def _by_admin(names: list, area: list, code: list, reviewed: list) -> tuple:
    table = {}
    for value, a, c, keep in zip(names, area, code, reviewed):
        if not keep:
            continue  # admin1 is recorded by review only
        key = _text(value).strip() or UNATTRIBUTED
        ha, confirmed, n = table.get(key, (0.0, 0.0, 0))
        table[key] = (ha + a, confirmed + (a if c == _CONFIRMED else 0.0), n + 1)
    return tuple((k, ha, confirmed, n) for k, (ha, confirmed, n)
                 in sorted(table.items(), key=lambda kv: (-kv[1][1], -kv[1][0], kv[0])))


def _largest(frame, area: list, n: int) -> Optional[tuple]:
    if not area:
        return None
    i = max(range(n), key=lambda j: area[j])
    row = frame.iloc[i]
    ids = _column(frame, "alert_id", n)
    try:
        alert_id = int(ids[i])
    except (TypeError, ValueError):
        alert_id = i
    return (alert_id, float(area[i]), _text(row.get("admin1")).strip(),
            decimal_year_to_date(row.get("alert_date_min")))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_set_summary.py -q`
Expected: PASS (all tests).

Then confirm nothing else broke and the style gates hold:

Run: `python -m pytest tests/test_environment_file.py -q && ruff check deforestation_alerts/scripts/set_summary.py`
Expected: PASS; ruff reports no findings.

- [ ] **Step 5: Commit**

```bash
git add deforestation_alerts/scripts/set_summary.py tests/test_set_summary.py
git commit -m "feat: aggregate an alert set for the Overview dashboard

summarize_alerts is pure (it takes a DataFrame) and read_summary adds the
projected parquet read of the seven columns the dashboard needs. Driver codes
are split by a caller-supplied callable so deforestation_alerts never imports
gui; by_status stays in status-code order because the stacking order is a
palette decision and lives in legend_specs."
```

---

### Task 2: `STACK_ORDER` — the colourblind-safe segment order

**Model:** sonnet

**Files:**
- Modify: `gui/scripts/legend_specs.py` (after `STATUS_CLASSES`, around line 24)
- Test: `tests/test_legend_specs.py` (extend)

**Interfaces:**
- Consumes: `STATUS_CLASSES` (already there).
- Produces: `STACK_ORDER: tuple[int, ...] = (1, 3, 2, 0)` — the order every chart stacks and legends status segments in. Task 3 imports it.

**Why it is only an order:** Confirmed `#d32f2f` and False positive `#388e3c` are 2.8 apart under simulated deuteranopia (OKLab x100) — invisible. Separating them with Needs revision `#f9a825` lifts the worst *adjacent* pair to 13.8. All-pairs separation still fails, which is why status may never be drawn as a pie, donut, scatter or choropleth; stacked bars, grouped bars and lines are fine. The hexes do not change, so the map is untouched.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_legend_specs.py`:

```python
def test_stack_order_is_a_permutation_of_the_status_codes():
    from gui.scripts.legend_specs import STACK_ORDER, STATUS_CLASSES

    assert sorted(STACK_ORDER) == sorted(code for code, _, _ in STATUS_CLASSES)


def test_confirmed_and_false_positive_are_never_adjacent_in_the_stack():
    """Measured deuteranopia separation between #d32f2f and #388e3c is 2.8
    (OKLab x100) — invisible. They are never adjacent in a stack or a legend;
    separating them lifts the worst adjacent pair to 13.8."""
    from gui.scripts.legend_specs import STACK_ORDER, STATUS_CLASSES

    codes = {label.rsplit(".", 1)[-1]: code for code, _, label in STATUS_CLASSES}
    pair = {codes["confirmed"], codes["false_positive"]}
    adjacent = [{a, b} for a, b in zip(STACK_ORDER, STACK_ORDER[1:])]
    assert pair not in adjacent


def test_stack_order_is_the_only_new_name_and_carries_no_colour():
    """It is an order, not a palette: the hexes are unchanged, so the map and
    the PMTiles paint values are untouched."""
    from gui.scripts.legend_specs import STACK_ORDER

    assert all(isinstance(code, int) for code in STACK_ORDER)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_legend_specs.py -x -q`
Expected: FAIL with `ImportError: cannot import name 'STACK_ORDER' from 'gui.scripts.legend_specs'`

- [ ] **Step 3: Write the implementation**

In `gui/scripts/legend_specs.py`, directly after the `STATUS_CLASSES` tuple:

```python
#: The order charts stack and list the review statuses in. Confirmed (#d32f2f)
#: and False positive (#388e3c) are 2.8 apart under simulated deuteranopia
#: (OKLab x100) — indistinguishable — so Needs revision sits between them,
#: which lifts the worst adjacent pair to 13.8. The hexes are unchanged: this
#: is an order, not a palette, and the map draws the same colours as before.
#: All-pairs separation still fails, so status is never drawn as a pie, donut,
#: scatter or choropleth; stacked bars, grouped bars and lines are fine.
STACK_ORDER = (1, 3, 2, 0)  # Confirmed, Needs revision, False positive, Not reviewed
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_legend_specs.py -q`
Expected: PASS, including `test_no_other_module_writes_these_colours` (no hex was added).

- [ ] **Step 5: Commit**

```bash
git add gui/scripts/legend_specs.py tests/test_legend_specs.py
git commit -m "feat: STACK_ORDER keeps Confirmed and False positive apart

Their measured deuteranopia separation is 2.8 (OKLab x100): invisible. Putting
Needs revision between them lifts the worst adjacent pair to 13.8 without
touching a single hex, so the map is unchanged. It lives beside STATUS_CLASSES
because colour decisions have exactly one home."
```
