# Analysis Side Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Analysis right panel from six always-expanded cards into a review-queue console: position and facts on top, the verdict in the middle with **Save & next** as the one primary button, and the occasional work (refine, export) collapsed below.

**Architecture:** Two new raw-widget builders — `context.py` (block A: queue counter, progress, navigation, facts grid, status chip) and `section.py` (a collapsible header + body whose content is hidden with `display`, never `v-if`) — plus a rewritten `review_panel.py` (block B) that drives a save runner extracted from `export.py`. The verdict stops being a widget's `v_model` and becomes `AnalysisState.verdict`; a new `AnalysisState.dirty` feeds the save-state line. `navigator.py` is absorbed into `context.py` and deleted.

**Tech Stack:** Python 3.10+, pysepal 4.1.0, Solara reactives, raw ipyvuetify (`v.*`) + `pysepal.sepalwidgets` (`sw.*`), pandas, geopandas, pytest. Deployment is Voila (`ui.ipynb`) — nothing may need a CDN.

**Spec:** [`docs/superpowers/specs/2026-10-03-analysis-side-panel-design.md`](../specs/2026-10-03-analysis-side-panel-design.md) (commit `9ee179b`). Visual reference: the `Current.dc.html` / `Main.dc.html` / `Refine.dc.html` artboards of the Design canvas <https://claude.ai/artifact/41BwTTPpDGBNEe9jdgVeaU>.

**Worktree:** run every command from `/home/jserafini/Desktop/projects/deforestation_alerts/slices2-6-wt`, on branch `feat/alert-monitoring-slices2-6`. Never `cd` to the original repository root. Never use bare `git stash` / `git stash pop` — the stash stack is shared across worktrees.

**Running the tests:** `.venv`-free; the environment is micromamba. Use
`micromamba run -n defo-alerts python -m pytest <args>` (or activate the env
first). The default run is offline; nothing in this plan needs Earth Engine.

## Task / model assignment

| # | Task | Model | Why |
|---|---|---|---|
| 1 | `review_counts` + the queue line | sonnet | Pure counting plus one widget row, cases enumerated below |
| 2 | Navigation row and the jump field | sonnet | Three buttons and one 1-based/0-based conversion, fully specified |
| 3 | Facts grid, status chip, retiring `selected_alert_info` | **opus** | Moves a guard other code depends on and inverts an existing test file's anchors |
| 4 | `section.py` — the collapsible | sonnet | One small builder; the trap (`display`, not `v-if`) is stated |
| 5 | `verdict` / `dirty` / `prediction_shapes` state + `capture_alert` | **opus** | Changes the save contract read by four test files; locking and seeding order matter |
| 6 | `make_save_runner` extracted from `export._on_save` | **opus** | A move of threaded, tracked, writer-registered code; a slip loses a toast or a refusal path |
| 7 | Block B — verdict buttons, driver hiding, Save & next, state line | **opus** | The user-visible promise of the feature; advance-only-on-a-save-that-wrote is subtle |
| 8 | `edit_tools.py` — labels, dead button, CNN rows, vectorise at run | **opus** | Touches worker/token code and adds work inside a tracked job |
| 9 | Export section body and its badge | sonnet | Mechanical: drop a title and a button, add three badge states |
| 10 | Assemble `controls`, delete `navigator.py` | **opus** | Eight imports and the once-per-session subscription contract |
| 11 | Help text, retired keys, full-suite green | **opus** | Prose in three locales plus the catalogue-pruning tests |

Never use the `fable` model for any task; where this table says opus, run opus at effort `xhigh`.

## Global Constraints

Every task's requirements implicitly include all of these.

- **Raw-widget builders only.** Plain functions returning `v.Card` / `v.Html` / `v.Container`, never `@solara.component`. MapApp renders the Analysis step with `v-if`, so it destroys and rebuilds the step DOM; a reacton tree rendered through `render_fixed` / `solara.display` goes blank on reopen, a raw ipyvuetify tree is re-viewed from its model and survives.
- **Reactive `.subscribe()`, not `use_effect`.** The panel is built once per session (`solara.use_memo(..., dependencies=[])` in `gui/solara_app.py`), so every subscription is registered exactly once and its unsubscriber is deliberately discarded.
- **No traitlets, no pysepal model classes** anywhere under `gui/tile/analysis/`. `tests/test_analysis_state.py::test_nothing_under_analysis_imports_traitlets_or_pysepal_model` enforces it.
- **Collapse and hide with `display`, never `v-if`, and never remove a body from the widget tree.** The refine body holds the maps' draw controls and the CNN toolbars, whose imperative `.show()` / `.hide()` / `.disabled` state must survive a collapse.
- **No palette hex outside `gui/scripts/legend_specs.py`.** `tests/test_legend_specs.py::test_no_other_module_writes_these_colours` reads every `gui/**/*.py` and fails on any `ALERT_CLASSES` / `STATUS_CLASSES` / `MASK_COLOR` hex. The status chip reads `legend_specs.status_colors()`.
- **Stored strings are unchanged:** the review statuses are exactly `"Confirmed"`, `"False Positive"`, `"Need revision"`, and `"Not reviewed"` is the unreviewed row's value. `engine.pmtiles.STATUS_CODES` maps them to `1 / 2 / 3 / 0`.
- **Every user-visible string goes through `gui.i18n.t()`** / `tn()` / `format_number()`, with the key added to **all three** locales in the same commit as its first use — `deforestation_alerts/message/{en,es-ES,fr}/ui.json`. `tests/test_i18n.py` fails on a key missing from a locale, on an English key no code uses, and on a translation still reading English (≥3 letters after placeholders are stripped; genuine identical words go in that test's `SAME_IN[locale]` set).
- **English plural nodes are exactly `{"one", "other"}`**; the `es-ES` and `fr` nodes add `"many"` (`test_every_catalogue_file_keeps_one_layout` / the plural-shape check at `tests/test_i18n.py:304`).
- **Catalogue files are `json.dumps(obj, indent=2, ensure_ascii=False) + "\n"` exactly.**
- **No string resolved at import time.** `t()` is called inside a builder, a handler or a render — never at module level.
- **Blocking work goes off the UI thread** through `gui.scripts.solara_threads.spawn_in_context`, with feedback through `notify_bridge.tracked_job` and `notify_bridge.current_notifier()` — never `use_notifications()` from a worker.
- **Add no dependencies.** `sepal_environment.yml` must not change; a test asserts every imported package is declared.
- **Touch targets ≥ 44px**; icon-only buttons carry an `aria-label` (`attributes={"aria-label": ...}`); the verdict group carries `aria-label` = `t("analysis_tile.questionarie.confirmation_q")`.
- **Both themes.** No hard-coded background or ink colour; blocks use vuetify classes and theme colours.
- **No keyboard shortcuts and no footer legend** — explicitly out of scope (spec §2).
- **Commit messages carry no `Co-Authored-By` trailer.**

## Review Focus

Five conditions the spec implies that no task's happy path exercises, most likely to bite first. Each has its test added to the task that owns the code.

1. **A save that is refused while Save & next was pressed.** No verdict chosen, another set's alerts, a rebuilt store: the reviewer must stay on the alert with the warning, never be advanced past an unsaved one. → Task 7.
2. **A row whose `status` is NaN, empty, or a value no longer in the three.** `review_counts` must not count it as reviewed and the chip must fall back to *Not reviewed* instead of raising a `KeyError` out of a colour lookup. → Tasks 1 and 3.
3. **A collapse while the drawing tools are mid-edit.** The body is hidden, so the draw controls and both CNN toolbars must keep the exact `.disabled` / shown state they had, and expanding again must show it unchanged. → Task 4.
4. **A language switch with the panel already built.** Every label in this panel is resolved once, when the block is built; the numbers and the plural badges must still be formatted with the locale in force **at the moment they are written**, not at build time. → Tasks 1 and 9.
5. **A CNN run whose vectorisation finds nothing.** The new at-run vectorisation can return an empty frame, where the old code only discovered that at *Add* time: the row must report the existing `dl_404` warning and leave `prediction_shapes` empty rather than divide by zero or show *Found 0 patches · 0.0 ha*. → Task 8.

## File Structure

**Create**

| File | Responsibility |
|---|---|
| `gui/tile/analysis/context.py` | Block A: `review_counts`, `counter_text`, `tally_text`, `facts_of`, `status_label_key`, `status_color`, `build_context` |
| `gui/tile/analysis/section.py` | `Section` + `build_section`: the collapsible header, badge and `display`-toggled body |
| `tests/test_analysis_context.py` | Offline: counting, padding, 1-based conversion, empty state, facts guard, chip |
| `tests/test_analysis_sections.py` | Offline: collapse keeps the body and its state, badges follow |
| `tests/test_analysis_verdict.py` | Offline: verdict state, driver hiding, dirty transitions, Save & next |

**Modify**

| File | Change |
|---|---|
| `gui/tile/analysis/review_panel.py` | rewritten as block B |
| `gui/tile/analysis/state.py` | `verdict`, `dirty`, `prediction_shapes`, `reset_alert`, `capture_alert` |
| `gui/tile/analysis/export.py` | `make_save_runner`; the save button and the card title leave |
| `gui/tile/analysis/edit_tools.py` | labelled buttons, `clear_btn` deleted, CNN rows, vectorise at run, refine badge |
| `gui/tile/analysis/analysis_tile.py` | `controls` becomes head + A + B + C; `refresh_alert_info` deleted; `view_actual_alert` seeds the verdict |
| `gui/tile/analysis/widgets.py` | `selected_alert_info` and `confirmation_select` leave the contract |
| `deforestation_alerts/message/{en,es-ES,fr}/ui.json` | new keys per task; retired keys in Task 11 |
| `tests/test_analysis_alert_info.py`, `test_review_status.py`, `test_set_context.py`, `test_feedback_notifications.py`, `test_analysis_state.py`, `test_i18n.py` | retargeted |

**Delete**

| File | Why |
|---|---|
| `gui/tile/analysis/navigator.py` | absorbed into `context.py` (Task 10) |

---

### Task 1: `review_counts` and the queue line

**Files:**
- Create: `gui/tile/analysis/context.py`
- Create: `tests/test_analysis_context.py`
- Modify: `deforestation_alerts/message/{en,es-ES,fr}/ui.json`

**Interfaces:**
- Consumes: `app_state.alerts_gdf`, `app_state.actual_alert_id` (`gui/store/state_manager.py`).
- Produces:
  - `review_counts(gdf) -> tuple[int, int]` — `(total, reviewed)`
  - `counter_text(index: int, total: int) -> str` — `"012 / 530"`, or the empty-state text when `total <= 0`
  - `tally_text(total: int, reviewed: int) -> str` — `"212 reviewed · 318 left"`, `""` when `total <= 0`
  - `build_context(app_state, id_field) -> v.Card` — block A; this task returns a card holding only the queue line. Tasks 2 and 3 add to it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analysis_context.py
"""Block A of the Analysis controls: the queue counter, the tally and the
progress bar. Pure helpers plus one raw-widget block driven by a real
``AppState``, as tests/test_analysis_alert_info.py drives the tile.
"""

from datetime import datetime
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pytest
from pysepal.i18n import set_locale
from shapely.geometry import box

from deforestation_alerts.project.models import AlertSet, AlertSetParams, Project
from gui.store.state_manager import AppState
from gui.tile.analysis import context


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    s = AppState()
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    p.alert_sets["x"] = AlertSet(
        id="x", period_id="per", sources=["GLAD-L"], digit_map={0: "GLAD-L"},
        params=AlertSetParams(), store_rel_path="periods/per/sets/glad-l/alert_db.parquet",
        combined_rel_path="periods/per/sets/glad-l/c.tif",
        footprint_rel_path="periods/per/sets/glad-l/footprint.tif",
        fingerprint="f", created_at=datetime.now())
    p.active_set = "x"
    s.new_project_state(p)
    return s


def _alerts(statuses, set_id="x"):
    n = len(statuses)
    return gpd.GeoDataFrame(
        {"status": list(statuses), "alert_id": list(range(n)), "set_id": [set_id] * n,
         "alert_sources": ["GLAD-L"] * n, "alert_date_min": [2026.010] * n,
         "alert_date_max": [2026.020] * n},
        geometry=[box(i, 0, i + 1, 1) for i in range(n)], crs="EPSG:4326")


def test_review_counts_counts_only_real_reviews():
    gdf = _alerts(["Not reviewed", "Confirmed", "False Positive", "Need revision", None, ""])
    assert context.review_counts(gdf) == (6, 3)


def test_review_counts_tolerates_a_nan_status_and_a_missing_column():
    gdf = _alerts(["Confirmed", np.nan])
    assert context.review_counts(gdf) == (2, 1)
    bare = gdf.drop(columns=["status"])
    assert context.review_counts(bare) == (2, 0)


def test_review_counts_of_nothing_is_zero_zero():
    assert context.review_counts(None) == (0, 0)
    assert context.review_counts(_alerts([])) == (0, 0)


def test_the_counter_is_one_based_and_padded_to_the_total():
    assert context.counter_text(11, 530) == "012 / 530"
    assert context.counter_text(0, 9) == "1 / 9"
    assert context.counter_text(0, 1200) == "0001 / 1,200"


def test_the_counter_and_the_tally_of_an_empty_queue_say_so():
    assert context.counter_text(-1, 0) == "No alert set is loaded"
    assert context.tally_text(0, 0) == ""


def test_the_tally_names_what_is_left():
    assert context.tally_text(530, 212) == "212 reviewed · 318 left"


def test_the_queue_line_follows_the_alerts_and_the_position(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    card = context.build_context(s, SimpleNamespace(v_model=None))
    counter, tally, bar = context.queue_widgets(card)
    assert counter.children == ["No alert set is loaded"]

    s.alerts_gdf.set(_alerts(["Confirmed"] + ["Not reviewed"] * 9))
    assert s.actual_alert_id.value == 0
    assert counter.children == ["01 / 10"]
    assert tally.children == ["1 reviewed · 9 left"]
    assert bar.value == 10.0

    s.go_to(4)
    assert counter.children == ["05 / 10"]


def test_the_tally_is_formatted_in_the_language_in_force_when_it_is_written(tmp_path, monkeypatch):
    """The block is built once per session and a language switch never rebuilds
    it, so the strings must be resolved at write time, not at build time."""
    s = _state(tmp_path, monkeypatch)
    card = context.build_context(s, SimpleNamespace(v_model=None))
    _, tally, _ = context.queue_widgets(card)
    set_locale("es-ES")
    s.alerts_gdf.set(_alerts(["Confirmed", "Not reviewed"]))
    assert tally.children == ["1 revisadas · 1 pendientes"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_analysis_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gui.tile.analysis.context'`.

- [ ] **Step 3: Add the messages**

In `deforestation_alerts/message/en/ui.json`, inside `analysis_tile`, add a
`queue` block (keep the file's key order otherwise and its exact layout —
`json.dumps(obj, indent=2, ensure_ascii=False) + "\n"`):

```json
"queue": {
  "counter": "{index} / {total}",
  "tally": "{reviewed} reviewed · {left} left",
  "empty": "No alert set is loaded"
}
```

`es-ES`:

```json
"queue": {
  "counter": "{index} / {total}",
  "tally": "{reviewed} revisadas · {left} pendientes",
  "empty": "No hay ningún conjunto de alertas cargado"
}
```

`fr`:

```json
"queue": {
  "counter": "{index} / {total}",
  "tally": "{reviewed} examinées · {left} restantes",
  "empty": "Aucun lot d'alertes n'est chargé"
}
```

`queue.counter` has no letters once its placeholders are stripped, so the
identical translations do not trip `test_no_english_is_left_in_the_translations`.

- [ ] **Step 4: Write the module**

```python
# gui/tile/analysis/context.py
"""Block A of the Analysis controls: where am I, and what is this alert.

Architecture layer: view (raw-widget builder, no ``@solara.component``). Holds
the set selector, the set-status banner, the queue position with its progress
bar, the navigation row, and the facts grid with its status chip — the part of
the panel that answers "which alert of how many, and what does the table say
about it" (see
``docs/superpowers/specs/2026-10-03-analysis-side-panel-design.md`` §3).

Every number is counted from the live alerts table rather than from
``AlertSet.n_reviewed``, so the tally moves the moment a save lands, and every
string is resolved when it is written rather than when the block is built: the
panel is built once per session and a language switch never rebuilds it.
"""

import ipyvuetify as v

from gui.i18n import format_number, t

#: The panel's own slots, kept on the returned card so the tests (and nothing
#: else) can reach them without re-deriving the tree's shape.
_SLOTS = "_defo_context_slots"


def review_counts(gdf) -> tuple:
    """``(total, reviewed)`` of the alerts table.

    Reviewed is a row whose ``status`` is present and is not ``"Not reviewed"``
    — the rule ``set_context.record_review`` writes into ``AlertSet.n_reviewed``.
    A table without the column (an older store) counts as nothing reviewed.
    """
    if gdf is None or len(gdf) == 0:
        return 0, 0
    if "status" not in gdf.columns:
        return len(gdf), 0
    status = gdf["status"]
    reviewed = int((status.notna() & (status != "Not reviewed") & (status != "")).sum())
    return len(gdf), reviewed


def counter_text(index: int, total: int) -> str:
    """``012 / 530``: the 1-based position, zero-padded to the total's width."""
    if total <= 0:
        return t("analysis_tile.queue.empty")
    width = len(str(total))
    return t("analysis_tile.queue.counter",
             index=str(max(index, 0) + 1).zfill(width), total=format_number(total))


def tally_text(total: int, reviewed: int) -> str:
    if total <= 0:
        return ""
    return t("analysis_tile.queue.tally", reviewed=format_number(reviewed),
             left=format_number(total - reviewed))


def queue_widgets(card):
    """``(counter, tally, bar)`` of a card built by ``build_context``."""
    slots = getattr(card, _SLOTS)
    return slots["counter"], slots["tally"], slots["bar"]


def build_context(app_state, id_field) -> v.Card:
    """Block A, built once per session; it subscribes to the alerts table and
    the current position and refreshes itself."""
    counter = v.Html(tag="span", class_="text-h6 font-weight-medium",
                     style_="font-variant-numeric: tabular-nums;", children=[])
    tally = v.Html(tag="span", class_="caption text--secondary", children=[])
    bar = v.ProgressLinear(value=0, height=4, color="primary", rounded=True)

    queue_line = v.Html(
        tag="div",
        class_="d-flex align-baseline",
        children=[counter, v.Html(tag="span", style_="flex: 1 1 auto;", children=[]), tally],
    )

    def _refresh(*_):
        gdf = app_state.alerts_gdf.value
        total, reviewed = review_counts(gdf)
        counter.children = [counter_text(app_state.actual_alert_id.value, total)]
        tally.children = [tally_text(total, reviewed)]
        bar.value = (100.0 * reviewed / total) if total else 0.0

    app_state.alerts_gdf.subscribe(_refresh)
    app_state.actual_alert_id.subscribe(_refresh)
    _refresh()

    card = v.Card(class_="pa-3 ma-2", hover=True, children=[queue_line, bar])
    setattr(card, _SLOTS, {"counter": counter, "tally": tally, "bar": bar})
    return card
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_analysis_context.py tests/test_i18n.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/tile/analysis/context.py tests/test_analysis_context.py deforestation_alerts/message
git commit -m "feat(analysis): the review queue's position, tally and progress"
```

---

### Task 2: The navigation row and the jump field

**Files:**
- Modify: `gui/tile/analysis/context.py`
- Modify: `tests/test_analysis_context.py`
- Modify: `deforestation_alerts/message/{en,es-ES,fr}/ui.json`

**Interfaces:**
- Consumes: `context.build_context` (Task 1); `app_state.go_prev/go_next/go_to`; the `id_field` the tile owns (`v.TextField`, whose `.v_model` `view_actual_alert` writes).
- Produces: `context.nav_widgets(card) -> (prev, next, jump, field_box, go)` for the tests; the row inside block A. The field shows the **1-based** number, so `go_to` receives `value - 1` — the only place the panel's numbering and `app_state`'s differ.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_analysis_context.py`:

```python
def test_the_nav_buttons_move_the_selection(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    card = context.build_context(s, SimpleNamespace(v_model=None))
    prev, nxt, jump, field_box, go = context.nav_widgets(card)
    s.alerts_gdf.set(_alerts(["Not reviewed"] * 4))

    nxt.fire_event("click", None)
    assert s.actual_alert_id.value == 1
    prev.fire_event("click", None)
    assert s.actual_alert_id.value == 0


def test_the_nav_buttons_are_safe_with_no_alerts(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    card = context.build_context(s, SimpleNamespace(v_model=None))
    prev, nxt, _, _, _ = context.nav_widgets(card)
    nxt.fire_event("click", None)
    prev.fire_event("click", None)
    assert s.actual_alert_id.value == -1


def test_the_jump_field_is_one_based_and_hidden_until_asked(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    field = SimpleNamespace(v_model=None)
    card = context.build_context(s, field)
    _, _, jump, field_box, go = context.nav_widgets(card)
    s.alerts_gdf.set(_alerts(["Not reviewed"] * 9))

    assert "display: none" in field_box.style_
    jump.fire_event("click", None)
    assert "display: none" not in field_box.style_

    field.v_model = 7          # the reviewer types the number the counter shows
    go.fire_event("click", None)
    assert s.actual_alert_id.value == 6

    field.v_model = "not a number"
    go.fire_event("click", None)
    assert s.actual_alert_id.value == 6   # unchanged, no exception

    field.v_model = 99         # out of range: go_to clamps
    go.fire_event("click", None)
    assert s.actual_alert_id.value == 8
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_analysis_context.py -k nav_or_jump -v`
(then without `-k`): Expected FAIL — `AttributeError: module 'gui.tile.analysis.context' has no attribute 'nav_widgets'`.

- [ ] **Step 3: Add the messages**

`en`, inside `analysis_tile`:

```json
"nav": {
  "previous": "Previous",
  "next": "Next",
  "jump": "Jump to alert number",
  "go": "Go"
}
```

`es-ES`:

```json
"nav": {
  "previous": "Anterior",
  "next": "Siguiente",
  "jump": "Ir al número de alerta",
  "go": "Ir"
}
```

`fr`:

```json
"nav": {
  "previous": "Précédente",
  "next": "Suivante",
  "jump": "Aller au numéro d'alerte",
  "go": "Aller"
}
```

- [ ] **Step 4: Write the implementation**

Add to `gui/tile/analysis/context.py` (and extend the card's children and
slots — `build_context` now returns `children=[queue_line, bar, nav_row]`):

```python
def nav_widgets(card):
    """``(prev, next, jump, field_box, go)`` of a card built by ``build_context``."""
    slots = getattr(card, _SLOTS)
    return slots["prev"], slots["next"], slots["jump"], slots["field_box"], slots["go"]


def _build_nav(app_state, id_field):
    """Previous / Next / jump. The jump field shows the 1-based number the
    counter shows, so ``go_to`` takes ``value - 1``; ``go_to`` clamps and
    no-ops with no alerts loaded (``max_alert_id`` is -1), so every button is
    safe to press at smoke time."""
    prev = v.Btn(outlined=True, class_="flex-grow-1", style_="height: 44px;",
                 children=[v.Icon(left=True, children=["mdi-chevron-left"]),
                           t("analysis_tile.nav.previous")])
    nxt = v.Btn(outlined=True, class_="flex-grow-1", style_="height: 44px;",
                children=[t("analysis_tile.nav.next"),
                          v.Icon(right=True, children=["mdi-chevron-right"])])
    jump = v.Btn(outlined=True, style_="height: 44px; min-width: 44px;",
                 attributes={"aria-label": t("analysis_tile.nav.jump")},
                 children=[v.Icon(children=["mdi-pound"])])
    go = v.Btn(outlined=True, color="primary", class_="ml-2", children=[t("analysis_tile.nav.go")])
    field_box = v.Html(tag="div", class_="d-flex align-center mt-2",
                       style_="display: none;", children=[id_field, go])

    prev.on_event("click", lambda *_: app_state.go_prev())
    nxt.on_event("click", lambda *_: app_state.go_next())

    def _toggle(*_):
        hidden = "display: none" in (field_box.style_ or "")
        field_box.style_ = "" if hidden else "display: none;"

    jump.on_event("click", _toggle)

    def _on_go(*_):
        try:
            target = int(id_field.v_model) - 1
        except (TypeError, ValueError):
            return
        app_state.go_to(target)

    go.on_event("click", _on_go)

    row = v.Html(tag="div", class_="mt-3", children=[
        v.Html(tag="div", class_="d-flex", style_="gap: 8px;", children=[prev, nxt, jump]),
        field_box,
    ])
    return row, {"prev": prev, "next": nxt, "jump": jump, "field_box": field_box, "go": go}
```

In `build_context`, after the queue line:

```python
    nav_row, nav_slots = _build_nav(app_state, id_field)
    ...
    card = v.Card(class_="pa-3 ma-2", hover=True, children=[queue_line, bar, nav_row])
    setattr(card, _SLOTS, {"counter": counter, "tally": tally, "bar": bar, **nav_slots})
```

The `id_field` stays the same widget object the tile built: `view_actual_alert`
writes `.v_model` into it on every alert change. **Task 10** changes that write
to `alert_id + 1` so the field agrees with the counter.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_analysis_context.py tests/test_i18n.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/tile/analysis/context.py tests/test_analysis_context.py deforestation_alerts/message
git commit -m "feat(analysis): labelled navigation and a jump field in block A"
```

---

### Task 3: The facts grid, the status chip, and retiring `selected_alert_info`

**Files:**
- Modify: `gui/tile/analysis/context.py`
- Modify: `gui/tile/analysis/analysis_tile.py` (delete `refresh_alert_info`, drop its subscription and its call)
- Modify: `gui/tile/analysis/widgets.py` (delete `selected_alert_info`)
- Modify: `tests/test_analysis_context.py`, `tests/test_analysis_alert_info.py`
- Modify: `deforestation_alerts/message/{en,es-ES,fr}/ui.json`

**Interfaces:**
- Consumes: `context.build_context`; `set_store.alerts_belong_to`, `workflow_steps.active_set`, `mosaics_helper.convert_julian_to_date`, `legend_specs.status_colors`, `engine.pmtiles.STATUS_CODES`.
- Produces:
  - `facts_of(gdf, alert_id, project) -> dict | None` — `{"source", "first", "last", "status"}`, or `None` when there is nothing to show (no table, no selection, an id out of range, or alerts of another set than the active one). **This is the guard `refresh_alert_info` used to own**, and Overview applies the same one.
  - `status_label_key(status) -> str`, `status_color(status) -> str`
  - `facts_widgets(card) -> (source, detected, chip)`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_analysis_context.py`:

```python
def test_facts_of_reads_the_row(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    gdf = _alerts(["Confirmed", "Not reviewed"])
    facts = context.facts_of(gdf, 0, s.project.value)
    assert facts["source"] == "GLAD-L"
    assert facts["status"] == "Confirmed"
    assert facts["first"] == "2026-01-10" and facts["last"] == "2026-01-20"


@pytest.mark.parametrize("gdf_id", [(None, 0), ("ok", -1), ("ok", 9)])
def test_facts_of_guards_nothing_to_show(tmp_path, monkeypatch, gdf_id):
    s = _state(tmp_path, monkeypatch)
    which, alert_id = gdf_id
    gdf = None if which is None else _alerts(["Confirmed"])
    assert context.facts_of(gdf, alert_id, s.project.value) is None


def test_facts_of_refuses_another_sets_alerts(tmp_path, monkeypatch):
    """Integrate records a new set before it publishes that set's alerts."""
    s = _state(tmp_path, monkeypatch)
    assert context.facts_of(_alerts(["Confirmed"], set_id="old"), 0, s.project.value) is None


@pytest.mark.parametrize("status,key", [
    ("Not reviewed", "legend.status.not_reviewed"),
    ("Confirmed", "legend.status.confirmed"),
    ("False Positive", "legend.status.false_positive"),
    ("Need revision", "legend.status.needs_revision"),
    (None, "legend.status.not_reviewed"),
    ("Who knows", "legend.status.not_reviewed"),
])
def test_the_status_chip_names_and_paints_a_status(status, key):
    from gui.i18n import t as translate
    from gui.scripts import legend_specs

    assert context.status_label_key(status) == key
    expected = legend_specs.status_colors()[
        {"Confirmed": 1, "False Positive": 2, "Need revision": 3}.get(status, 0)]
    assert context.status_color(status) == expected
    assert translate(key)


def test_the_facts_grid_follows_a_review_save(tmp_path, monkeypatch):
    """A Save writes the status into the row and rebinds ``alerts_gdf`` to a
    copy with the same alert still selected; the chip and the tally must both
    move (the old card read "Not reviewed" until the next alert change)."""
    s = _state(tmp_path, monkeypatch)
    card = context.build_context(s, SimpleNamespace(v_model=None))
    source, detected, chip = context.facts_widgets(card)
    _, tally, _ = context.queue_widgets(card)

    gdf = _alerts(["Not reviewed"] * 3)
    s.alerts_gdf.set(gdf)
    assert source.children == ["GLAD-L"]
    assert chip.children == [context_label("legend.status.not_reviewed")]

    gdf.at[0, "status"] = "Confirmed"
    s.alerts_gdf.value = gdf.copy()
    assert chip.children == [context_label("legend.status.confirmed")]
    assert tally.children == ["1 reviewed · 2 left"]


def context_label(key):
    from gui.i18n import t as translate
    return translate(key)


def test_another_sets_alerts_leave_the_facts_alone(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    card = context.build_context(s, SimpleNamespace(v_model=None))
    source, _, _ = context.facts_widgets(card)
    s.alerts_gdf.set(_alerts(["Confirmed"]))
    before = list(source.children)
    s.alerts_gdf.value = _alerts(["Confirmed"], set_id="old")
    assert source.children == before
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_analysis_context.py -v`
Expected: FAIL — `module 'gui.tile.analysis.context' has no attribute 'facts_of'`.

- [ ] **Step 3: Add the messages**

`en`, inside `analysis_tile`:

```json
"facts": {
  "source": "Source",
  "detected": "Detected",
  "status": "Status",
  "range": "{first} → {last}"
}
```

`es-ES`: `"source": "Fuente"`, `"detected": "Detectada"`, `"status": "Estado"`, `"range": "{first} → {last}"`.
`fr`: `"source": "Source"`, `"detected": "Détectée"`, `"status": "Statut"`, `"range": "{first} → {last}"`.

`facts.source` is the same word in French, so add `"analysis_tile.facts.source"`
to `SAME_IN["fr"]` in `tests/test_i18n.py`.

- [ ] **Step 4: Write the implementation**

Add to `gui/tile/analysis/context.py`:

```python
from deforestation_alerts.engine.pmtiles import STATUS_CODES
from deforestation_alerts.scripts.mosaics_helper import convert_julian_to_date
from gui.scripts import legend_specs, set_store
from gui.store.workflow_steps import active_set

#: status -> the legend key that names it, so the panel, the map and the
#: charts read the same words. Anything else is an unreviewed row.
_STATUS_KEYS = {
    "Not reviewed": "legend.status.not_reviewed",
    "Confirmed": "legend.status.confirmed",
    "False Positive": "legend.status.false_positive",
    "Need revision": "legend.status.needs_revision",
}


def status_label_key(status) -> str:
    return _STATUS_KEYS.get(status if isinstance(status, str) else "", _STATUS_KEYS["Not reviewed"])


def status_color(status) -> str:
    """The colour the map and the charts paint this status (never a literal:
    ``legend_specs`` is the one place the palette is written)."""
    code = STATUS_CODES.get(status if isinstance(status, str) else "", 0)
    return legend_specs.status_colors()[code]


def facts_of(gdf, alert_id, project):
    """``{"source", "first", "last", "status"}`` of the selected alert, or
    ``None`` when there is nothing to show: no table, no selection, an id out
    of range, or alerts of another set than the active one — Integrate records
    a new set before it publishes that set's alerts, and Overview skips them
    the same way."""
    s = active_set(project) if project is not None else None
    if s is not None and not set_store.alerts_belong_to(gdf, s):
        return None
    if gdf is None or alert_id is None or alert_id < 0 or alert_id >= len(gdf):
        return None
    row = gdf.iloc[alert_id]
    status = row.get("status")
    return {
        "source": str(row["alert_sources"]),
        "first": convert_julian_to_date(row["alert_date_min"]),
        "last": convert_julian_to_date(row["alert_date_max"]),
        "status": status if isinstance(status, str) and status else "Not reviewed",
    }


def facts_widgets(card):
    slots = getattr(card, _SLOTS)
    return slots["source"], slots["detected"], slots["chip"]


def _build_facts():
    mono = "font-family: monospace; font-variant-numeric: tabular-nums;"
    source = v.Html(tag="span", style_=mono, children=[])
    detected = v.Html(tag="span", style_=mono, children=[])
    chip = v.Chip(small=True, outlined=True, children=[])

    def _label(key):
        return v.Html(tag="span", class_="caption text--secondary text-uppercase", children=[t(key)])

    grid = v.Html(
        tag="div",
        class_="mt-3",
        style_="display: grid; grid-template-columns: 80px minmax(0, 1fr); "
               "gap: 6px 10px; align-items: center;",
        children=[
            _label("analysis_tile.facts.source"), source,
            _label("analysis_tile.facts.detected"), detected,
            _label("analysis_tile.facts.status"), v.Html(tag="div", children=[chip]),
        ],
    )
    return grid, {"source": source, "detected": detected, "chip": chip}
```

In `build_context`, build the grid, add it to the card's children and slots, and
extend `_refresh` — the facts refresh is **guarded**, so a `None` leaves the
grid exactly as it was:

```python
    facts_grid, facts_slots = _build_facts()
    ...
    def _refresh(*_):
        gdf = app_state.alerts_gdf.value
        total, reviewed = review_counts(gdf)
        counter.children = [counter_text(app_state.actual_alert_id.value, total)]
        tally.children = [tally_text(total, reviewed)]
        bar.value = (100.0 * reviewed / total) if total else 0.0

        facts = facts_of(gdf, app_state.actual_alert_id.value, app_state.project.value)
        if facts is None:
            return
        facts_slots["source"].children = [facts["source"]]
        facts_slots["detected"].children = [
            t("analysis_tile.facts.range", first=facts["first"], last=facts["last"])
        ]
        facts_slots["chip"].children = [t(status_label_key(facts["status"]))]
        facts_slots["chip"].color = status_color(facts["status"])
```

The queue line refreshes **before** the guard returns, so another set's alerts
still update the totals (they are that table's real totals) while leaving the
facts of the alert on screen alone.

- [ ] **Step 5: Retire the old card**

1. In `gui/tile/analysis/analysis_tile.py`: delete `refresh_alert_info`
   entirely, delete the `refresh_alert_info(widgets, app_state)` call inside
   `view_actual_alert` (its own `alerts_belong_to` guard above stays), delete
   the `_refresh_info_cb` subscription in `wire_alert_navigation` and its
   docstring paragraph, and delete `alert_info_card` from `controls` (it is
   replaced wholesale in Task 10 — leaving it would reference a deleted
   widget). Remove the now-unused `pd` import only if nothing else uses it
   (`refresh_alert_info` was its only user: check with
   `grep -n "pd\." gui/tile/analysis/analysis_tile.py`).
2. In `gui/tile/analysis/widgets.py`: delete the `selected_alert_info` widget,
   its comment block and its line in the class docstring's attribute list.
3. Rewrite `tests/test_analysis_alert_info.py` against `context`: the four
   tests become the facts-grid tests added in Step 1 (the file keeps only what
   is still about the tile — `test_another_sets_alerts_leave_the_card_and_the_review_state_alone`
   is retargeted to assert `view_actual_alert` leaves `id_field` and
   `analysis.defo_layer` alone, with the card assertions dropped). Delete the
   file if nothing tile-specific is left, and say so in the commit message.

- [ ] **Step 6: Run the tests**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_analysis_context.py tests/test_analysis_alert_info.py tests/test_i18n.py tests/test_legend_specs.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(analysis): the facts grid and a status chip replace the alert-info card"
```

---

### Task 4: `section.py` — the collapsible

**Files:**
- Create: `gui/tile/analysis/section.py`
- Create: `tests/test_analysis_sections.py`

**Interfaces:**
- Produces:
  - `build_section(title: str, body, *, badge: str = "", open: bool = False) -> Section`
  - `Section` — a `SimpleNamespace` with `root` (the widget to put in the panel), `header`, `body_box`, `badge` (the chip widget), `set_badge(text, color=None)`, `set_open(flag)`, `is_open()`.
- Consumed by: Tasks 8, 9 and 10.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analysis_sections.py
"""The two collapsible sections of the Analysis panel. The body is hidden with
``display``, never dropped from the tree: it holds the maps' draw controls and
the CNN toolbars, whose imperative shown/disabled state must survive a
collapse, and the step container already destroys and rebuilds this DOM.
"""

import ipyvuetify as v
import pytest
from pysepal.i18n import set_locale

from gui.tile.analysis.section import build_section


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


def test_a_section_starts_collapsed_with_its_body_in_the_tree():
    body = v.Btn(disabled=True, children=["inner"])
    s = build_section("Refine polygon", body)
    assert s.is_open() is False
    assert "display: none" in s.body_box.style_
    assert body in s.body_box.children


def test_the_header_toggles_and_the_body_keeps_its_state():
    body = v.Btn(disabled=True, children=["inner"])
    s = build_section("Refine polygon", body)
    s.header.fire_event("click", None)
    assert s.is_open() is True and "display: none" not in s.body_box.style_
    body.disabled = False          # the reviewer starts an edit
    s.header.fire_event("click", None)
    assert s.is_open() is False
    s.header.fire_event("click", None)
    assert body.disabled is False  # the edit survived the collapse
    assert body in s.body_box.children


def test_the_badge_follows_what_it_is_told():
    s = build_section("Export this alert", v.Html(tag="div"), badge="available after save")
    assert s.badge.children == ["available after save"]
    s.set_badge("2 files ready", color="primary")
    assert s.badge.children == ["2 files ready"] and s.badge.color == "primary"
    s.set_badge("")
    assert s.badge.children == [] and "display: none" in s.badge.style_


def test_set_open_drives_the_same_state():
    s = build_section("Refine polygon", v.Html(tag="div"))
    s.set_open(True)
    assert s.is_open() is True and "display: none" not in s.body_box.style_
    s.set_open(False)
    assert s.is_open() is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_analysis_sections.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gui.tile.analysis.section'`.

- [ ] **Step 3: Write the module**

```python
# gui/tile/analysis/section.py
"""One collapsible section of the Analysis controls: a 50px header carrying a
chevron, a title and a summary badge, over a body that is hidden with
``display`` (spec §5).

The body is NEVER removed from the widget tree and NEVER rendered through
``v-if``: it holds the maps' draw controls and the two CNN toolbars, whose
imperative ``.show()`` / ``.hide()`` / ``.disabled`` state is the edit session
itself, and MapApp already destroys and rebuilds this DOM when the step closes
and reopens.
"""

from types import SimpleNamespace

import ipyvuetify as v

_HIDDEN = "display: none;"


def build_section(title: str, body, *, badge: str = "", open: bool = False):
    """A collapsed-by-default section. ``title`` and ``badge`` are already
    translated by the caller (nothing here resolves a message)."""
    chevron = v.Icon(small=True, children=["mdi-chevron-right"])
    badge_chip = v.Chip(x_small=True, outlined=True, children=[badge] if badge else [],
                        style_="" if badge else _HIDDEN)
    header = v.Btn(
        text=True, block=True, class_="px-3",
        style_="height: 50px; justify-content: flex-start; text-transform: none;",
        children=[
            chevron,
            v.Html(tag="span", class_="ml-2 body-2", children=[title]),
            v.Html(tag="span", style_="flex: 1 1 auto;", children=[]),
            badge_chip,
        ],
    )
    body_box = v.Html(tag="div", class_="px-3 pb-3", children=[body])

    state = {"open": False}

    def is_open() -> bool:
        return state["open"]

    def set_open(flag: bool) -> None:
        state["open"] = bool(flag)
        body_box.style_ = "" if state["open"] else _HIDDEN
        chevron.children = ["mdi-chevron-down" if state["open"] else "mdi-chevron-right"]

    def set_badge(text: str, color=None) -> None:
        badge_chip.children = [text] if text else []
        badge_chip.style_ = "" if text else _HIDDEN
        if color is not None:
            badge_chip.color = color

    header.on_event("click", lambda *_: set_open(not state["open"]))
    set_open(open)

    root = v.Card(class_="ma-2", hover=True, children=[header, body_box])
    return SimpleNamespace(root=root, header=header, body_box=body_box, badge=badge_chip,
                           set_badge=set_badge, set_open=set_open, is_open=is_open)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_analysis_sections.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/tile/analysis/section.py tests/test_analysis_sections.py
git commit -m "feat(analysis): a collapsible section that keeps its body alive"
```

---

### Task 5: `verdict`, `dirty` and `prediction_shapes` state

**Files:**
- Modify: `gui/tile/analysis/state.py`
- Modify: `tests/test_analysis_state.py`
- Modify: `tests/test_set_context.py`, `tests/test_feedback_notifications.py` (the stand-in `confirmation_select` namespaces)

**Interfaces:**
- Produces, on `AnalysisState`:
  - `verdict: solara.Reactive` — `None | "Confirmed" | "False Positive" | "Need revision"`
  - `dirty: solara.Reactive` — `bool`
  - `prediction_shapes: solara.Reactive` — `{model_number: (n_patches, area_ha)}`
  - `reset_alert()` clears all three (`None`, `False`, `{}`) under the same lock and token bump as everything else it clears.
  - `capture_alert(analysis, widgets, app_state)` reads `status` from `analysis.verdict.value`; its other reads are unchanged.
  - `touch(analysis)` — the one-liner handlers call to mark the alert dirty.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_analysis_state.py`:

```python
def test_the_verdict_and_the_dirty_flag_are_per_alert_state():
    from gui.tile.analysis.state import AnalysisState

    s = AnalysisState()
    assert s.verdict.value is None and s.dirty.value is False
    s.verdict.set("Confirmed")
    s.dirty.set(True)
    s.prediction_shapes.set({1: (2, 3.9)})
    s.reset_alert()
    assert s.verdict.value is None
    assert s.dirty.value is False
    assert s.prediction_shapes.value == {}


def test_capture_alert_takes_the_verdict_from_the_state():
    from types import SimpleNamespace

    from gui.store.state_manager import AppState
    from gui.tile.analysis.state import AnalysisState, capture_alert

    app_state = AppState()
    analysis = AnalysisState()
    analysis.verdict.set("False Positive")
    widgets = SimpleNamespace(driver_combobox=SimpleNamespace(v_model=["mining"]))
    alert = capture_alert(analysis, widgets, app_state)
    assert alert.status == "False Positive"
    assert alert.drivers == ["mining"]


def test_touch_marks_the_alert_dirty():
    from gui.tile.analysis.state import AnalysisState, touch

    s = AnalysisState()
    touch(s)
    assert s.dirty.value is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_analysis_state.py -v`
Expected: FAIL — `AttributeError: 'AnalysisState' object has no attribute 'verdict'`.

- [ ] **Step 3: Write the implementation**

In `gui/tile/analysis/state.py`, add to the dataclass (keeping the comment style
of its neighbours):

```python
    #: the reviewer's answer for this alert: None until one of the three
    #: statuses is chosen. The store, the PMTiles paint values and Overview's
    #: counts all read these exact strings, so the verdict IS the stored value
    #: and never a label. None is refused by the Save (``confirmation_missing``).
    verdict: solara.Reactive = field(default_factory=_plain)
    #: whether the reviewer changed the verdict, the drivers or the polygon
    #: since the last save of THIS alert. Cleared by ``reset_alert`` (an alert
    #: change forgets it) and by a save that wrote.
    dirty: solara.Reactive = field(default_factory=lambda: solara.reactive(False))
    #: what each CNN run found, by model number: ``{1: (n_patches, area_ha)}``.
    #: Derived from the prediction the worker vectorises, beside the ``.tif``
    #: paths in ``prediction_files``.
    prediction_shapes: solara.Reactive = field(default_factory=lambda: solara.reactive({}))
```

In `reset_alert`, inside the `with self._alert_lock:` block, add:

```python
            self.verdict.set(None)
            self.dirty.set(False)
            self.prediction_shapes.set({})
```

In `capture_alert`'s `_read`, replace the `status` line:

```python
            "status": analysis.verdict.value,
```

and add at module level:

```python
def touch(analysis: AnalysisState) -> None:
    """Mark the current alert as changed since its last save (the save-state
    line in the verdict block reads it)."""
    analysis.dirty.set(True)
```

- [ ] **Step 4: Fix the tests that stub the old contract**

`tests/test_set_context.py` (lines ~243, 457, 668, 708, 729) and
`tests/test_feedback_notifications.py` (~496, 1220) build stand-in widgets with
`confirmation_select=SimpleNamespace(v_model="Confirmed")`. The verdict no
longer comes from `widgets`, so in each of those tests: drop the
`confirmation_select=...` argument and set the verdict on the `AnalysisState`
the test already builds — `analysis.verdict.set("Confirmed")` (or
`"False Positive"` / `"Yes"` where the test deliberately passes a bad value, so
the refusal it asserts still happens). Find every occurrence with
`grep -rn "confirmation_select" tests/`.

- [ ] **Step 5: Run the tests**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_analysis_state.py tests/test_set_context.py tests/test_feedback_notifications.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/tile/analysis/state.py tests
git commit -m "feat(analysis): the verdict and the unsaved flag become per-alert state"
```

---

### Task 6: `make_save_runner` extracted from `export._on_save`

**Files:**
- Modify: `gui/tile/analysis/export.py`
- Modify: `tests/test_feedback_notifications.py` (the save-flow tests call the runner)

**Interfaces:**
- Consumes: `capture_alert` reading `analysis.verdict` (Task 5).
- Produces: `make_save_runner(widgets, app_state, analysis, app_tile_model) -> Callable[..., None]`, whose returned `run_save(button, then=None)`:
  - spins `button` (`.loading` / `.disabled`) for the duration, exactly as `_on_save` did;
  - captures the alert on the calling (UI) thread;
  - runs the same worker through `spawn_in_context`, with the same `tracked_job`, the same `writing(writer_name(app_state))` registration, the same `SaveRefused` → warning path, the same `save_done` success toast and the same `_apply_save()`;
  - calls `then()` **only** after a save that wrote (inside the success branch, after `_apply_save`), guarded by `try/except` so a failing `then` cannot swallow the success toast;
  - `button` may be `None` (nothing to spin).
- `build_export` keeps working: its own save button is removed in Task 9, and until then it calls the runner.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feedback_notifications.py` (it already holds the save-flow
tests and their notifier/thread harness — reuse that file's existing fixtures
and the `_run_inline` monkeypatching of `spawn_in_context` it uses for the other
workers; `grep -n "spawn_in_context" tests/test_feedback_notifications.py` shows
the pattern):

```python
def test_the_save_runner_advances_only_after_a_save_that_wrote(tmp_path, monkeypatch):
    """Save & next passes ``go_next`` as ``then``: a refused save must leave
    the reviewer on the alert that was not saved."""
    from gui.tile.analysis import export as export_mod

    app_state, analysis, widgets, model = _save_harness(tmp_path, monkeypatch)  # the file's helper
    moved = []
    run_save = export_mod.make_save_runner(widgets, app_state, analysis, model)

    analysis.verdict.set(None)              # no answer: _save_attributes refuses
    run_save(None, then=lambda: moved.append(1))
    assert moved == []

    analysis.verdict.set("Confirmed")
    run_save(None, then=lambda: moved.append(1))
    assert moved == [1]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_feedback_notifications.py -k save_runner -v`
Expected: FAIL — `module 'gui.tile.analysis.export' has no attribute 'make_save_runner'`.

- [ ] **Step 3: Write the implementation**

In `gui/tile/analysis/export.py`, lift the body of `_on_save` into a
module-level factory, above `build_export`. The worker body is **moved
verbatim** — same `tracked_job`, same `writing(...)`, same refusal handling,
same toasts, same `finally` — with three changes: the button is a parameter and
may be `None`, `_apply_save` comes from the factory's argument, and `then()`
runs after it.

```python
def make_save_runner(widgets, app_state, analysis, app_tile_model, apply_save=None):
    """The review save, as a callable the verdict block drives.

    ``run_save(button, then=None)``: spin ``button`` (optional), capture the
    alert on the UI thread, write the store on a worker, and run ``then`` only
    after a save that actually wrote — Save & next passes ``go_next``, and a
    refusal must leave the reviewer on the alert it refused, with the message
    that explains it.

    ``apply_save`` is the post-save UI step (the export section's download
    button + the ``alerts_gdf`` rebind); the default rebinds alone, so the
    runner is usable before the export section is built.
    """

    def _default_apply():
        gdf = app_state.alerts_gdf.value
        if gdf is not None:
            app_state.alerts_gdf.value = gdf.copy()

    apply = apply_save or _default_apply

    def run_save(button=None, then=None):
        if button is not None:
            button.loading = True
            button.disabled = True
        alert = capture_alert(analysis, widgets, app_state)

        def _worker():
            notifier = current_notifier()
            try:
                with tracked_job(
                    notifier, t("analysis_tile.export_labels.save_task_title")
                ) as task:
                    task.step(t("analysis_tile.export_labels.save_step"))
                    with writing(writer_name(app_state)):
                        try:
                            saved = _save_attributes(
                                widgets, app_state, analysis, app_tile_model, alert=alert
                            )
                            refused = None
                        except SaveRefused as refusal:
                            saved, refused = False, error_text(refusal)
                    if not saved:
                        reason = refused or t("analysis_tile.export_labels.save_no_alert")
                        task.fail(reason)
                        notifier.warning(reason)
                        logger.warning("Analysis: save refused — %s", reason)
                    else:
                        apply()
            except Exception:  # noqa: BLE001 — tracked_job toasted and logged it
                pass
            else:
                if saved:
                    notifier.success(t("analysis_tile.export_labels.save_done"))
                    analysis.dirty.set(False)
                    if then is not None:
                        try:
                            then()
                        except Exception:
                            logger.exception("Analysis: after-save step failed")
            finally:
                if button is not None:
                    button.loading = False
                    button.disabled = False

        spawn_in_context(_worker)

    return run_save
```

Then replace `build_export`'s `_on_save` with a call into the runner, keeping
its own `_apply_save` as the `apply_save` argument:

```python
    run_save = make_save_runner(widgets, app_state, analysis, app_tile_model,
                               apply_save=_apply_save)
    ns["save_btn"].on_event("click", lambda *_: run_save(ns["save_btn"]))
```

and delete the old `_on_save`. `build_export` also returns the runner so the
tile can hand it to block B:

```python
    return SimpleNamespace(card=card, run_save=run_save, set_badge=None)
```

**Callers of `build_export` must be updated in the same commit** — today
`analysis_tile.build_analysis_layout` puts its return value straight into
`controls`. Change that line to `build_export(...).card` for now; Task 10
rewires it properly.

- [ ] **Step 4: Run the tests**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_feedback_notifications.py tests/test_analysis_download.py tests/test_set_context.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/tile/analysis/export.py gui/tile/analysis/analysis_tile.py tests
git commit -m "refactor(analysis): the review save becomes a runner the panel can drive"
```

---

### Task 7: Block B — the verdict

**Files:**
- Modify: `gui/tile/analysis/review_panel.py` (rewritten)
- Create: `tests/test_analysis_verdict.py`
- Modify: `tests/test_review_status.py`
- Modify: `deforestation_alerts/message/{en,es-ES,fr}/ui.json`

**Interfaces:**
- Consumes: `AnalysisState.verdict` / `.dirty` / `.last_save_time` and `touch` (Task 5); `export.make_save_runner` (Task 6); `loss_drivers` (unchanged).
- Produces:
  - `REVIEW_CHOICES` — unchanged name and statuses, new label keys:
    `(("Confirmed", "analysis_tile.verdict.true"), ("False Positive", "analysis_tile.verdict.false"), ("Need revision", "analysis_tile.verdict.revision"))`
    (`export.review_status` keeps importing it.)
  - `build_review_panel(widgets, app_state, analysis, app_tile_model) -> v.Card` — same signature as today, now using all four arguments.
  - `verdict_widgets(card) -> (buttons, driver_box, note, save_next, save, state_line)` for the tests. `buttons` is a dict keyed by status.
  - `widgets.driver_combobox` stays the contract; `widgets.confirmation_select` is **gone**.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analysis_verdict.py
"""Block B: the three-button verdict, the driver that disappears for a false
positive, Save & next, and the save-state line.
"""

from datetime import datetime
from types import SimpleNamespace

import geopandas as gpd
import pytest
from pysepal.i18n import set_locale
from shapely.geometry import box

from deforestation_alerts.project.models import AlertSet, AlertSetParams, Project
from gui.i18n import t
from gui.scripts.set_context import SetContext
from gui.store.state_manager import AppState
from gui.tile.analysis import review_panel
from gui.tile.analysis.state import AnalysisState


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


def _panel(monkeypatch, tmp_path, saves=None):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    app_state = AppState()
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    app_state.new_project_state(p)
    analysis = AnalysisState()
    widgets = SimpleNamespace()
    if saves is not None:
        monkeypatch.setattr(review_panel, "make_save_runner",
                            lambda *a, **k: lambda button=None, then=None: saves(button, then))
    card = review_panel.build_review_panel(widgets, app_state, analysis, SetContext(app_state))
    return card, widgets, analysis, app_state


def test_a_button_sets_the_verdict_and_marks_the_alert_dirty(tmp_path, monkeypatch):
    card, _, analysis, _ = _panel(monkeypatch, tmp_path)
    buttons, *_ = review_panel.verdict_widgets(card)
    assert analysis.verdict.value is None

    buttons["Confirmed"].fire_event("click", None)
    assert analysis.verdict.value == "Confirmed"
    assert analysis.dirty.value is True


def test_the_chosen_button_is_the_filled_one(tmp_path, monkeypatch):
    card, _, analysis, _ = _panel(monkeypatch, tmp_path)
    buttons, *_ = review_panel.verdict_widgets(card)
    analysis.verdict.set("Need revision")
    assert buttons["Need revision"].outlined is False
    assert buttons["Confirmed"].outlined is True


def test_a_false_positive_hides_the_driver_but_keeps_it_in_the_tree(tmp_path, monkeypatch):
    card, widgets, analysis, _ = _panel(monkeypatch, tmp_path)
    buttons, driver_box, note, *_ = review_panel.verdict_widgets(card)
    assert "display: none" not in driver_box.style_

    analysis.verdict.set("False Positive")
    assert "display: none" in driver_box.style_
    assert "display: none" not in note.style_
    assert widgets.driver_combobox in driver_box.children

    analysis.verdict.set("Confirmed")
    assert "display: none" not in driver_box.style_
    assert "display: none" in note.style_


def test_a_driver_change_marks_the_alert_dirty(tmp_path, monkeypatch):
    card, widgets, analysis, _ = _panel(monkeypatch, tmp_path)
    widgets.driver_combobox.fire_event("change", ["mining"])
    assert analysis.dirty.value is True


def test_the_state_line_reads_unsaved_then_saved(tmp_path, monkeypatch):
    card, _, analysis, _ = _panel(monkeypatch, tmp_path)
    *_, state_line = review_panel.verdict_widgets(card)
    assert state_line.children == []

    analysis.dirty.set(True)
    assert state_line.children == [t("analysis_tile.verdict.unsaved")]

    analysis.dirty.set(False)
    analysis.last_save_time.set(datetime(2026, 10, 3, 14, 32).timestamp())
    assert state_line.children == [t("analysis_tile.verdict.saved_at", time="14:32")]


def test_save_and_next_asks_to_advance_and_plain_save_does_not(tmp_path, monkeypatch):
    calls = []
    card, _, _, _ = _panel(monkeypatch, tmp_path, saves=lambda b, then: calls.append(then))
    *_, save_next, save, _ = review_panel.verdict_widgets(card)

    save.fire_event("click", None)
    assert calls == [None]

    save_next.fire_event("click", None)
    assert calls[1] is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_analysis_verdict.py -v`
Expected: FAIL — `module 'gui.tile.analysis.review_panel' has no attribute 'verdict_widgets'`.

- [ ] **Step 3: Add the messages**

`en`, inside `analysis_tile`:

```json
"verdict": {
  "title": "Verdict",
  "true": "True alert",
  "false": "False positive",
  "revision": "Needs review",
  "no_driver": "No loss driver is stored for a false positive.",
  "save_next": "Save & next",
  "unsaved": "Unsaved changes on this alert",
  "saved_at": "Saved {time}"
}
```

`es-ES`:

```json
"verdict": {
  "title": "Veredicto",
  "true": "Alerta verdadera",
  "false": "Falso positivo",
  "revision": "Requiere revisión",
  "no_driver": "En un falso positivo no se guarda ninguna causa de la pérdida.",
  "save_next": "Guardar y siguiente",
  "unsaved": "Cambios sin guardar en esta alerta",
  "saved_at": "Guardada a las {time}"
}
```

`fr`:

```json
"verdict": {
  "title": "Verdict",
  "true": "Alerte réelle",
  "false": "Faux positif",
  "revision": "À réexaminer",
  "no_driver": "Aucune cause de perte n'est enregistrée pour un faux positif.",
  "save_next": "Enregistrer et suivante",
  "unsaved": "Modifications non enregistrées sur cette alerte",
  "saved_at": "Enregistrée à {time}"
}
```

`verdict.title` is the same word in French: add `"analysis_tile.verdict.title"`
to `SAME_IN["fr"]` in `tests/test_i18n.py`.

- [ ] **Step 4: Rewrite the module**

```python
# gui/tile/analysis/review_panel.py
"""Block B of the Analysis controls: the verdict.

Architecture layer: view (raw-widget builder, no ``@solara.component``). Three
buttons write ``AnalysisState.verdict`` — the status string the store, the
PMTiles paint values and Overview's counts all read, never a label — the loss
driver disappears for a false positive (none is stored for one), and **Save &
next** is the panel's one primary button: it saves and advances only if the
save wrote (spec §4).

Nothing is stashed on ``widgets`` any more except ``driver_combobox``, which
``_save_attributes`` reads for the drivers.
"""

from datetime import datetime

import ipyvuetify as v
from pysepal import sepalwidgets as sw

from gui.i18n import t
from gui.scripts import loss_drivers as drivers
from gui.tile.analysis.export import make_save_runner
from gui.tile.analysis.state import touch

#: The review statuses a Save stores, with the key of each one's button label.
#: The store always gets the status; the label follows the locale.
REVIEW_CHOICES = (
    ("Confirmed", "analysis_tile.verdict.true"),
    ("False Positive", "analysis_tile.verdict.false"),
    ("Need revision", "analysis_tile.verdict.revision"),
)

#: status -> the icon on its button
_ICONS = {
    "Confirmed": "mdi-check-bold",
    "False Positive": "mdi-close-thick",
    "Need revision": "mdi-flag-outline",
}

_SLOTS = "_defo_verdict_slots"
_HIDDEN = "display: none;"


def verdict_widgets(card):
    """``(buttons, driver_box, note, save_next, save, state_line)``."""
    s = getattr(card, _SLOTS)
    return (s["buttons"], s["driver_box"], s["note"], s["save_next"], s["save"], s["state_line"])


def build_review_panel(widgets, app_state, analysis, app_tile_model):
    """Build and return the verdict card."""
    buttons = {}
    for status, key in REVIEW_CHOICES:
        btn = v.Btn(
            outlined=True, color="primary", class_="flex-grow-1 pa-1",
            style_="height: 58px; text-transform: none;",
            children=[v.Icon(small=True, children=[_ICONS[status]]),
                      v.Html(tag="span", class_="caption ml-1", children=[t(key)])],
        )
        btn.on_event("click", lambda *_, status=status: _pick(analysis, status))
        buttons[status] = btn

    group = v.Html(
        tag="div", class_="d-flex", style_="gap: 6px;",
        attributes={"role": "group",
                    "aria-label": t("analysis_tile.questionarie.confirmation_q")},
        children=list(buttons.values()),
    )

    # Codes as values, labels as text: the store gets the codes whatever the
    # language the widgets were built in. Objects in and out, as Vuetify 2's
    # combobox draws a chip from the selected value itself.
    driver_combobox = sw.Combobox(
        items=drivers.items(),
        v_model=drivers.selection(drivers.DEFAULT),
        return_object=True,
        label=t("analysis_tile.questionarie.loss_driver_hint"),
        multiple=True,
        clearable=True,
        chips=True,
    )
    driver_combobox.on_event("change", lambda *_: touch(analysis))
    widgets.driver_combobox = driver_combobox

    driver_box = v.Html(tag="div", class_="mt-3", children=[
        v.Html(tag="div", class_="body-2", children=[t("analysis_tile.questionarie.loss_driver_label")]),
        driver_combobox,
    ])
    note = v.Html(tag="div", class_="mt-3 pa-2 caption text--secondary",
                  style_=_HIDDEN, children=[t("analysis_tile.verdict.no_driver")])

    save_next = v.Btn(color="primary", class_="flex-grow-1", style_="height: 44px;",
                      children=[t("analysis_tile.verdict.save_next"),
                                v.Icon(right=True, children=["mdi-arrow-right"])])
    save = v.Btn(outlined=True, style_="height: 44px;",
                 children=[t("analysis_tile.export_labels.save_btn")])
    state_line = v.Html(tag="div", class_="caption mt-2", children=[])

    run_save = make_save_runner(widgets, app_state, analysis, app_tile_model)
    save.on_event("click", lambda *_: run_save(save))
    save_next.on_event("click", lambda *_: run_save(save_next, then=app_state.go_next))

    def _sync_verdict(*_):
        chosen = analysis.verdict.value
        for status, btn in buttons.items():
            btn.outlined = status != chosen
        is_false = chosen == "False Positive"
        driver_box.style_ = _HIDDEN if is_false else ""
        note.style_ = "" if is_false else _HIDDEN

    def _sync_state_line(*_):
        if analysis.dirty.value:
            state_line.children = [t("analysis_tile.verdict.unsaved")]
            state_line.class_ = "caption mt-2 warning--text"
            return
        when = analysis.last_save_time.value
        if when:
            state_line.children = [t("analysis_tile.verdict.saved_at",
                                     time=datetime.fromtimestamp(when).strftime("%H:%M"))]
            state_line.class_ = "caption mt-2 success--text"
        else:
            state_line.children = []
            state_line.class_ = "caption mt-2"

    # Registered once: the panel is built once per session.
    analysis.verdict.subscribe(_sync_verdict)
    analysis.dirty.subscribe(_sync_state_line)
    analysis.last_save_time.subscribe(_sync_state_line)
    _sync_verdict()
    _sync_state_line()

    card = v.Card(class_="pa-3 ma-2", hover=True, children=[
        v.Html(tag="div", class_="subtitle-2 font-weight-bold", children=[t("analysis_tile.verdict.title")]),
        v.Html(tag="div", class_="mt-2", children=[group]),
        driver_box,
        note,
        v.Html(tag="div", class_="d-flex mt-3", style_="gap: 8px;", children=[save_next, save]),
        state_line,
    ])
    setattr(card, _SLOTS, {"buttons": buttons, "driver_box": driver_box, "note": note,
                           "save_next": save_next, "save": save, "state_line": state_line})
    return card


def _pick(analysis, status):
    analysis.verdict.set(status)
    touch(analysis)
```

- [ ] **Step 5: Retarget `tests/test_review_status.py`**

`test_the_select_holds_statuses_and_shows_labels` tested a select that no
longer exists. Replace it with the button form, keeping its point — the stored
values are language-neutral, the labels follow the locale:

```python
@pytest.mark.parametrize("locale", ["en", "es-ES", "fr"])
def test_the_buttons_hold_statuses_and_show_labels(tmp_path, monkeypatch, locale):
    set_locale(locale)
    app_state = AppState()
    analysis = AnalysisState()
    card = build_review_panel(SimpleNamespace(), app_state, analysis, SetContext(app_state))
    buttons, *_ = verdict_widgets(card)
    assert list(buttons) == STATUSES
    labels = [b.children[1].children[0] for b in buttons.values()]
    assert labels == [t(key) for _, key in REVIEW_CHOICES]
    assert analysis.verdict.value is None     # no default answer is ever saved
```

`test_review_status` is unchanged except that
`t("analysis_tile.questionarie.confirmation_yes")` in its bad-value list becomes
`t("analysis_tile.verdict.true")`.

- [ ] **Step 6: Run the tests**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_analysis_verdict.py tests/test_review_status.py tests/test_i18n.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add gui/tile/analysis/review_panel.py tests deforestation_alerts/message
git commit -m "feat(analysis): the verdict block with Save & next"
```

---

### Task 8: Refine polygon — labels, the dead button, the CNN rows

**Files:**
- Modify: `gui/tile/analysis/edit_tools.py`
- Modify: `tests/test_feedback_notifications.py` (the CNN worker tests)
- Modify: `deforestation_alerts/message/{en,es-ES,fr}/ui.json`

**Interfaces:**
- Consumes: `AnalysisState.prediction_shapes` (Task 5); `section.Section.set_badge` (Task 4), passed in as `set_badge`.
- Produces: `build_edit_tools(widgets, app_state, analysis, app_tile_model, set_badge=None) -> v.Html` — the section **body** (no card, no title; the section owns those). It also keeps the refine badge current from `analysis.defo_layer`.
- `vectorise(path) -> tuple[list, int, float]` — `(features, n_patches, area_ha)`, the at-run vectorisation shared by the worker and *Add*.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feedback_notifications.py`:

```python
def test_a_cnn_run_records_what_it_found(tmp_path, monkeypatch):
    """The worker vectorises its prediction inside its tracked job, so the row
    can say what was found and *Add* can reuse the features."""
    from gui.tile.analysis import edit_tools

    analysis = AnalysisState()
    monkeypatch.setattr(edit_tools, "vectorise", lambda path: (["f1", "f2"], 2, 3.9))
    edit_tools._record_prediction_shapes(analysis, 1, 2, 3.9)
    assert analysis.prediction_shapes.value == {1: (2, 3.9)}
    analysis.reset_alert()
    assert analysis.prediction_shapes.value == {}


def test_a_prediction_with_nothing_in_it_is_the_404_warning(tmp_path, monkeypatch):
    """The at-run vectorisation can come back empty, where the old code only
    found out at Add time: no "Found 0 patches", no division by zero."""
    from gui.tile.analysis import edit_tools

    analysis = AnalysisState()
    monkeypatch.setattr(edit_tools, "vectorise", lambda path: ([], 0, 0.0))
    text = edit_tools.found_text(analysis, 1)
    assert text == ""
    edit_tools._record_prediction_shapes(analysis, 1, 0, 0.0)
    assert analysis.prediction_shapes.value == {}


def test_the_refine_badge_follows_the_drawn_polygon(tmp_path, monkeypatch):
    import geopandas as gpd
    from shapely.geometry import box as shapely_box

    from gui.tile.analysis import edit_tools

    analysis = AnalysisState()
    seen = []
    edit_tools.wire_refine_badge(analysis, lambda text, color=None: seen.append(text))
    assert seen[-1] == t("analysis_tile.sections.refine_empty")

    gdf = gpd.GeoDataFrame(geometry=[shapely_box(0, 0, 0.01, 0.01),
                                     shapely_box(1, 1, 1.01, 1.01)], crs="EPSG:4326")
    analysis.defo_layer.set(gdf)
    assert "2" in seen[-1] and "ha" in seen[-1]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_feedback_notifications.py -k "cnn_run_records or nothing_in_it or refine_badge" -v`
Expected: FAIL — `module 'gui.tile.analysis.edit_tools' has no attribute 'vectorise'`.

- [ ] **Step 3: Add the messages**

`en`, inside `analysis_tile`:

```json
"sections": {
  "refine": "Refine polygon",
  "refine_empty": "nothing drawn",
  "refine_summary": "{shapes} · {area} ha",
  "shapes": {
    "one": "{n} shape",
    "other": "{n} shapes"
  }
}
```

and inside `analysis_tile.model_labels`:

```json
"run": "Run",
"found": "Found {patches} · {area} ha",
"patches": {
  "one": "{n} patch",
  "other": "{n} patches"
}
```

and inside `analysis_tile.edition_labels`:

```json
"apply_edit_button": "Apply"
```

`es-ES`: `"refine": "Precisar el polígono"`, `"refine_empty": "sin dibujar"`,
`"refine_summary": "{shapes} · {area} ha"`,
`"shapes": {"one": "{n} forma", "many": "{n} de formas", "other": "{n} formas"}`;
`"run": "Ejecutar"`, `"found": "Encontrados {patches} · {area} ha"`,
`"patches": {"one": "{n} parche", "many": "{n} de parches", "other": "{n} parches"}`;
`"apply_edit_button": "Aplicar"`.

`fr`: `"refine": "Affiner le polygone"`, `"refine_empty": "rien de dessiné"`,
`"refine_summary": "{shapes} · {area} ha"`,
`"shapes": {"one": "{n} forme", "many": "{n} de formes", "other": "{n} formes"}`;
`"run": "Exécuter"`, `"found": "Trouvé {patches} · {area} ha"`,
`"patches": {"one": "{n} parcelle", "many": "{n} de parcelles", "other": "{n} parcelles"}`;
`"apply_edit_button": "Appliquer"`.

The `sections.export*` keys are added by Task 9.

- [ ] **Step 4: Write the implementation**

In `gui/tile/analysis/edit_tools.py`:

1. **The vectorisation, shared and done once.** Add, beside the existing
   `_PRED_THRESHOLD` / `_SIMPLIFY_TOLERANCE` constants:

```python
def vectorise(path):
    """``(features, n_patches, area_ha)`` of a prediction raster.

    Ran at *Add* time before; the worker now does it inside its tracked job, so
    the row can report what was found and *Add* is instant. ``color`` is applied
    by the caller (``convertir_formato3``), which is what distinguishes the two
    models' features on the map.
    """
    gdf = raster_to_gdf(path, "4326", _PRED_THRESHOLD)
    if gdf is None or len(gdf) == 0:
        return [], 0, 0.0
    return (simplify_and_extract_features(gdf, "geometry", _SIMPLIFY_TOLERANCE),
            int(len(gdf)), float(calculate_total_area(gdf)))


def _record_prediction_shapes(analysis, model, n_patches, area_ha):
    """Remember what a run found, replacing the dict in one locked read-and-set
    so two runs finishing together cannot drop each other's numbers. Nothing is
    recorded for an empty prediction: the row shows the ``dl_404`` warning."""
    if n_patches <= 0:
        return
    with analysis._alert_lock:
        shapes = dict(analysis.prediction_shapes.value)
        shapes[model] = (int(n_patches), float(area_ha))
        analysis.prediction_shapes.set(shapes)


def found_text(analysis, model) -> str:
    """The row's result line, or ``""`` when this model found nothing yet."""
    found = analysis.prediction_shapes.value.get(model)
    if not found:
        return ""
    n, area = found
    return t("analysis_tile.model_labels.found",
             patches=tn("analysis_tile.model_labels.patches", n),
             area=format_number(area, 1))
```

Import `tn` and `format_number` from `gui.i18n`, and `calculate_total_area` from
`deforestation_alerts.scripts.mosaics_helper` (next to the existing
`raster_to_gdf` / `simplify_and_extract_features` imports).

2. **The worker vectorises.** In `_on_dl1_run` / `_on_dl2_run`, after
   `_run_cnn_model` returns `pred_path` and still inside the `tracked_job`
   block, call `features, n, area = vectorise(pred_path)`; hand the features to
   the existing `_apply_model(...)` path through `analysis.apply_if_current`
   together with `_record_prediction_shapes(analysis, model, n, area)`, then set
   the row's result line from `found_text(analysis, model)`. An empty result
   keeps today's behaviour exactly: `notifier.warning(t("analysis_tile.model_labels.dl_404"))`
   and no add/remove enabled. Cache the features on the same locked dict the
   paths use (`analysis.prediction_files` keeps the paths; the features ride in
   a module-level `_FEATURES = {}` keyed by `(token, model)` and cleared by
   `_reset_for_new_alert`, so nothing per-alert leaks into the next alert).
3. **`_add_model_prediction` reuses them.** It takes the cached features when
   they are there and falls back to `vectorise(path)` when they are not (a
   reopened panel), then does exactly what it does today:
   `convertir_formato3(features, color_name)` and `set_synced_draw_data`.
4. **Labelled buttons, no tooltips, no dead button.** `start_btn`, `save_btn`
   and `stop_btn` become labelled 44px buttons reading
   `t("analysis_tile.edition_labels.start_edit_button")`,
   `t("analysis_tile.edition_labels.apply_edit_button")` and
   `t("analysis_tile.edition_labels.stop_edit_button")`; their `sw.Tooltip`
   wrappers go. **Delete `clear_btn`, its enable/disable lines in
   `_start_edition` / `_stop_edition`, `_clear_edition`, and its handler
   registration** — it is built and wired today but is not among
   `toolbar_edition`'s children, so it has never been on screen.
5. **The CNN rows.** Each model becomes a row: its name
   (`model_labels.dl_button1` / `dl_button2`) + a **Run** button
   (`model_labels.run`), then the add/remove pair as labelled buttons
   (`dl_button_add` / `dl_button_remove`), then a `v.Html` result line fed by
   `found_text`. `dl*_msg` keeps its place and its tooltip.
6. **The refine badge.**

```python
def wire_refine_badge(analysis, set_badge):
    """Keep a section badge reading the drawn polygon: *nothing drawn*, or the
    shape count and the area — the same ``calculate_total_area`` the save
    stores as ``area_ha``, so the badge and the column can never disagree."""

    def _show(gdf=None):
        gdf = analysis.defo_layer.value if gdf is None else gdf
        if gdf is None or len(gdf) == 0:
            set_badge(t("analysis_tile.sections.refine_empty"))
            return
        set_badge(t("analysis_tile.sections.refine_summary",
                    shapes=tn("analysis_tile.sections.shapes", len(gdf)),
                    area=format_number(calculate_total_area(gdf), 1)), color="primary")

    analysis.defo_layer.subscribe(lambda gdf: _show(gdf))
    _show()
    return _show
```

   `build_edit_tools` calls it when `set_badge` is given.
7. **Return the body, not a card.** `build_edit_tools` returns the
   `v.Html(tag="div", children=[toolbar_edition, dl1_row, dl2_row])` body; its
   `v.Card` + `CardTitle` go (the section owns them).

- [ ] **Step 5: Run the tests**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_feedback_notifications.py tests/test_i18n.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/tile/analysis/edit_tools.py tests deforestation_alerts/message
git commit -m "feat(analysis): the refine section says what is drawn and what the models found"
```

---

### Task 9: The export section body and its badge

**Files:**
- Modify: `gui/tile/analysis/export.py`
- Modify: `deforestation_alerts/message/{en,es-ES,fr}/ui.json`
- Modify: `tests/test_analysis_download.py`

**Interfaces:**
- Consumes: `section.Section.set_badge` (Task 4), passed in as `set_badge`.
- Produces: `build_export(widgets, app_state, aux_model, analysis, app_tile_model, set_badge=None) -> SimpleNamespace` with `.body` (the section body: the template field, the build button, the downloads toolbar), `.run_save` (the runner, for the tile) — and **no save button and no card title**.
- Badge states: `sections.export_after_save` while the build button is disabled; `sections.export_build` once a save has enabled it; `sections.export_files` (a count) once the downloads toolbar is revealed.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_analysis_download.py`:

```python
def test_the_export_badge_follows_what_is_available(tmp_path, monkeypatch):
    from gui.i18n import t, tn
    from gui.tile.analysis import export as export_mod

    seen = []
    parts = _export_harness(tmp_path, monkeypatch, set_badge=lambda text, color=None: seen.append(text))
    assert seen[-1] == t("analysis_tile.sections.export_after_save")

    parts.apply_save()                       # a save landed: the build is possible
    assert seen[-1] == t("analysis_tile.sections.export_build")

    parts.apply_download({"zip_path": tmp_path / "a.zip", "report_path": tmp_path / "a.docx"})
    assert seen[-1] == tn("analysis_tile.sections.export_files", 2)
```

(`_export_harness` is this file's existing builder of `build_export`'s
arguments — extend it with the `set_badge` keyword and have it return the
internal `_apply_save` / `_apply_download` it already reaches for the other
tests.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_analysis_download.py -k export_badge -v`
Expected: FAIL — `build_export() got an unexpected keyword argument 'set_badge'`.

- [ ] **Step 3: Add the messages**

`en`, inside `analysis_tile.sections`:

```json
"export": "Export this alert",
"export_after_save": "available after save",
"export_build": "ready to build",
"export_files": {
  "one": "{n} file ready",
  "other": "{n} files ready"
}
```

`es-ES`: `"export": "Exportar esta alerta"`, `"export_after_save": "disponible después de guardar"`,
`"export_build": "listo para generar"`,
`"export_files": {"one": "{n} archivo listo", "many": "{n} de archivos listos", "other": "{n} archivos listos"}`.

`fr`: `"export": "Exporter cette alerte"`, `"export_after_save": "disponible après l'enregistrement"`,
`"export_build": "prêt à générer"`,
`"export_files": {"one": "{n} fichier prêt", "many": "{n} de fichiers prêts", "other": "{n} fichiers prêts"}`.

- [ ] **Step 4: Write the implementation**

In `build_export`:

1. Delete `ns["save_btn"]` and `ns["toolbar_save_export"]`; the build button
   (`download_alert_data_btn`) stands alone in a row. The runner stays (Task 6)
   and is returned for the verdict block.
2. Add the badge:

```python
    def _badge(text, color=None):
        if set_badge is not None:
            set_badge(text, color=color)

    def _show_badge_for_state():
        if ns["download_alert_data_btn"].disabled:
            _badge(t("analysis_tile.sections.export_after_save"))
        elif ns["toolbar_downloads"].viz:
            _badge(tn("analysis_tile.sections.export_files", 2), color="primary")
        else:
            _badge(t("analysis_tile.sections.export_build"))
```

   called at the end of `_apply_save`, at the end of `_apply_download`, and once
   while building. (`sw.SepalWidget.viz` is the shown flag `.show()` / `.hide()`
   write; use `getattr(..., "viz", True)` so a plain widget never breaks it.)
   The two files are the zip and the report, which is why the count is 2 — read
   it from the result dict's keys rather than hard-coding:
   `len([p for p in (result.get("zip_path"), result.get("report_path")) if p])`.
3. Drop the `v.Card` + `CardTitle`; return

```python
    return SimpleNamespace(
        body=v.Html(tag="div", children=[template_field, ns["toolbar_build"], ns["toolbar_downloads"]]),
        run_save=run_save,
    )
```

   and delete `export_labels.export_title`'s use.

- [ ] **Step 5: Run the tests**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_analysis_download.py tests/test_i18n.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/tile/analysis/export.py tests deforestation_alerts/message
git commit -m "feat(analysis): the export section and its availability badge"
```

---

### Task 10: Assemble the panel

**Files:**
- Modify: `gui/tile/analysis/analysis_tile.py`
- Delete: `gui/tile/analysis/navigator.py`
- Modify: `tests/test_set_context.py` (if it imports `navigator`), any smoke test anchored on the old card titles

**Interfaces:**
- Consumes: `context.build_context` (Tasks 1-3), `review_panel.build_review_panel` (Task 7), `section.build_section` (Task 4), `edit_tools.build_edit_tools(..., set_badge=)` (Task 8), `export.build_export(..., set_badge=)` (Task 9).
- Produces: `controls` = `[info_button, context, verdict, refine.root, export.root]`; `view_actual_alert` additionally seeds `analysis.verdict` and writes the **1-based** number into `id_field`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_analysis_verdict.py`:

```python
def test_an_alert_change_seeds_the_verdict_from_the_row(tmp_path, monkeypatch):
    """A reviewed alert opens on its stored status; an unreviewed one opens
    with no answer, so nothing is ever saved by default. The seeding happens
    after ``reset_alert``, which clears it."""
    from gui.tile.analysis import analysis_tile

    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    app_state = AppState()
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    app_state.new_project_state(p)
    analysis = AnalysisState()
    widgets = SimpleNamespace(driver_combobox=SimpleNamespace(v_model=None))
    id_field = SimpleNamespace(v_model=None)

    gdf = gpd.GeoDataFrame(
        {"status": ["Confirmed", "Not reviewed"], "alert_id": [0, 1], "set_id": ["x", "x"],
         "alert_sources": ["GLAD-L"] * 2, "alert_date_min": [2026.010] * 2,
         "alert_date_max": [2026.020] * 2, "alert_polygon": [None, None],
         "description": [None, None], "bounding_box": [box(0, 0, 1, 1)] * 2},
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)], crs="EPSG:4326")
    app_state.alerts_gdf.value = gdf
    app_state.actual_alert_id.value = 0

    analysis_tile.view_actual_alert(widgets, app_state, analysis, SetContext(app_state), id_field)
    assert analysis.verdict.value == "Confirmed"
    assert analysis.dirty.value is False          # seeding is not a change
    assert id_field.v_model == 1                  # the 1-based number the counter shows

    app_state.actual_alert_id.value = 1
    analysis_tile.view_actual_alert(widgets, app_state, analysis, SetContext(app_state), id_field)
    assert analysis.verdict.value is None
    assert id_field.v_model == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_analysis_verdict.py -k seeds_the_verdict -v`
Expected: FAIL — `assert None == 'Confirmed'` (nothing seeds the verdict yet).

- [ ] **Step 3: Seed the verdict and the field**

In `view_actual_alert`, after the `analysis.reset_alert()` call (which clears
the verdict) and beside the driver-combobox seeding:

```python
    # --- a reviewed alert opens on its stored verdict; an unreviewed one on no
    # answer at all, so a default is never saved. After reset_alert, which
    # clears it; the seeding is not a change, so ``dirty`` stays False. ---
    stored = alerta.get("status")
    analysis.verdict.set(stored if isinstance(stored, str) and stored != "Not reviewed" else None)
    analysis.dirty.set(False)
```

and change the nav-field sync to the 1-based number the counter shows:

```python
    id_field.v_model = alert_id + 1
```

- [ ] **Step 4: Assemble `controls`**

Replace the `controls` block of `build_analysis_layout` with:

```python
    refine = build_section(t("analysis_tile.sections.refine"), v.Html(tag="div"))
    refine_body = build_edit_tools(
        widgets=widgets, app_state=app_state, analysis=analysis,
        app_tile_model=app_tile_model, set_badge=refine.set_badge,
    )
    refine.body_box.children = [refine_body]

    exports = build_section(t("analysis_tile.sections.export"), v.Html(tag="div"))
    export_parts = build_export(
        widgets=widgets, app_state=app_state, aux_model=aux_model, analysis=analysis,
        app_tile_model=app_tile_model, set_badge=exports.set_badge,
    )
    exports.body_box.children = [export_parts.body]

    controls = v.Html(
        tag="div",
        class_="pa-0 ma-0",
        children=[
            info_button(t("analysis_tile.description"), t("analysis_tile.info_header"),
                        t("analysis_tile.info_md")),
            build_set_selector(app_state),
            build_set_status_banner(app_state),
            build_context(app_state, id_field),
            build_review_panel(widgets=widgets, app_state=app_state, analysis=analysis,
                               app_tile_model=app_tile_model),
            refine.root,
            exports.root,
        ],
    )
```

Imports: add `context.build_context`, `section.build_section`; drop
`navigator.build_navigator` and `git rm gui/tile/analysis/navigator.py`. Check
nothing else imports it: `grep -rn "navigator" gui/ tests/`.

The section bodies are built **after** their sections so each builder can be
handed its `set_badge`; assigning `body_box.children` once, at build time, is
not a `v-if` rebuild — the body then stays in the tree for the session.

- [ ] **Step 5: Run the tests**

Run: `micromamba run -n defo-alerts python -m pytest tests/ -x -q`
Expected: PASS (fix any smoke test still anchored on `"Alert info"`,
`"Alert drawing"` or `"Save and Export"` by pointing it at the new block
titles).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(analysis): the review-queue console replaces the six stacked cards"
```

---

### Task 11: The help text, the retired keys, and the whole suite

**Files:**
- Modify: `deforestation_alerts/message/{en,es-ES,fr}/ui.json`
- Modify: `tests/test_i18n.py` (the `SAME_IN` additions, if Tasks 3 and 7 did not add them)

**Interfaces:** none produced; this task closes the catalogue.

- [ ] **Step 1: Run the pruning test to see what is dead**

Run: `micromamba run -n defo-alerts python -m pytest tests/test_i18n.py -v`
Expected: FAIL — `test_every_english_key_of_the_legacy_files_is_used` lists the
keys nothing uses any more.

- [ ] **Step 2: Delete the retired keys from all three locales**

`analysis_tile.alert_info` (the whole block: `card_title`, `alert_source`,
`first_date`, `last_date`, `status`, `status_reviewed`, `status_not_reviewed`),
`analysis_tile.questionarie.confirmation_yes` / `.confirmation_no` /
`.confirmation_revision`, `analysis_tile.edition_labels.save_edit_button`,
`analysis_tile.edition_labels.edit_title`,
`analysis_tile.export_labels.export_title`.

Keep `analysis_tile.img_selection.*`, `questionarie.confirmation_q`,
`questionarie.confirmation_missing`, `questionarie.false_positive_drawn`,
`questionarie.loss_driver_label` / `.loss_driver_hint` / `.loss_driver1..9`,
`edition_labels.start_edit_button` / `.stop_edit_button`, and every
`export_labels.*` the save/download flow still uses, `save_btn` included.

Let the test name what is actually dead — it is the authority, not this list.

- [ ] **Step 3: Rewrite the help text**

`analysis_tile.description` and `analysis_tile.info_md` name a navigator card,
an export card and a *Save* that have all moved. English:

```json
"description": "Review each alert against its before and after imagery: give a verdict, set the loss driver, then save and move on.",
"info_md": "The set selector switches alert sets; the counter under it shows which alert of how many, how many are reviewed and how many are left, and **Previous** / **Next** (or the **#** button, to jump to a number) move between them. The two maps show the before and after images picked on the shared timeline below them. Answer **Is this a true alert?** with one of the three buttons, choose the loss drivers, then **Save & next** writes the review into the set's alert store and opens the following alert — **Save** keeps you on this one. **Refine polygon** opens the drawing tools and the two auto-detection models; **Export this alert** builds its GeoPackage, its before/after images and a Word report from the project's report template, chosen in that section."
```

Translate both into `es-ES` and `fr` (full prose, no English left). Keep every
Markdown marker and the button names consistent with the keys added in Tasks
2, 7, 8 and 9.

- [ ] **Step 4: Run the whole suite**

Run: `micromamba run -n defo-alerts python -m pytest tests/ -q`
Expected: PASS, with the Earth-Engine tier skipped (`DEFO_ALERTS_EE_TESTS` unset).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "docs(analysis): the panel's help text and the pruned catalogue"
```
