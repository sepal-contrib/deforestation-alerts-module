# Multiple Custom Alert Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users register up to five named, RADD-shaped Earth Engine alert sources in the Optional files dialog, each selectable in the Filter step and decoded to its own name in the analyzed alerts.

**Architecture:** A pure module (`custom_sources.py`) owns the slot/name rules and the alerts-dictionary helpers. `AuxModel` stores a list. The AOI search applies the list from a click-time snapshot, and the rasters it builds carry their `custom_slot`. The Filter step decodes names from those rasters, never from live settings. The Aux dialog gets a raw-ipyvuetify list editor (add/edit modal plus a remove confirmation) modelled on spatial_risk's variables list.

**Tech Stack:** Python 3.11, Earth Engine (`ee`), geedim 2.0.0, rasterio ≥ 1.4, traitlets, ipyvuetify (raw widgets), Solara, pytest.

**Spec:** `docs/superpowers/specs/2026-09-30-multiple-custom-alert-sources-design.md`

## Execution table

| Task | Deliverable | Model |
|---|---|---|
| 0 | Commit the pending Optional-files redesign | sonnet |
| 1 | `custom_sources.py` pure rules + dictionary helpers | sonnet |
| 2 | `AuxModel.custom_alerts` list + legacy recipe import | sonnet |
| 3 | Multi-slot decoding (`get_unique_alerts`, `convert_to_geopandas`) | sonnet |
| 4 | Slot-aware raster build in `aoi_helper` (Int32, `custom_slot`) | sonnet |
| 5 | dtype-aware vectorizer block sizing + float64 round-trip | sonnet |
| 6 | AOI search applies the snapshot; remove the custom observer | opus |
| 7 | Filter step: Int32 union + names from the search rasters | sonnet |
| 8 | `CustomSourceList` widget + i18n | opus |
| 9 | Wire the list into the Aux dialog, wiring test, visual check, changelog | sonnet |

## Global Constraints

- Earth Engine only. Local raster sources are out of scope. Do not add file pickers to the custom-source modal.
- `MAX_CUSTOM_SOURCES = 5`. Custom slot `s` (0–4) contributes `10 ** (4 + s)` × {1 = confirmed, 2 = potential}. This matches RADD's remap `[0,1,2,3] → [0, 0, 2v, v]`. Built-in digits stay as they are: GLAD-L 10^0, RADD 10^1, GLAD-S2 10^2, CCDC 10^3.
- Custom alert band and the Filter union's `alert` band are **Int32**. Export nodata stays **255**.
- Reserved names, compared case-insensitively after trimming: `GLAD-L`, `RADD`, `GLAD-S2`, `CCDC`.
- The Filter step decodes names **only** from `available_alerts_raster_list` entries' `custom_slot` (spec constraint 6).
- Dialog content is **raw ipyvuetify**. No `@solara.component` or `solara.display` inside the Aux dialog tree, because content rendered that way vanishes on reopen.
- Every user-facing string lives in `deforestation_alerts/message/{en,es-ES,fr}/ui.json` under `aux_tile`, in all three locales, sentence case.
- Test command: `micromamba run -n defo-alerts python -m pytest -q <path>`, run from the module root `deforestation-alerts-module/`.
- **Earth Engine precondition:** importing `gui.tile.aoi_tile` or `deforestation_alerts.scripts.aoi_helper` runs `init_ee()`. That raises `NameError` on this machine until `earthengine set_project <project>` is done (`~/.config/earthengine/credentials` has no `project_id`). Tests in Tasks 4 and 6 need it. If it is still missing, report those tests as blocked; do not work around it by editing the credentials.
- Commits: conventional prefix, **no `Co-Authored-By` trailer** (user preference).

## Review Focus

1. **A recipe saved before this change** (`custom_alert_layer` + `custom_alert_name`) must load as a one-item list, and the next search must register it. Tests: Task 2 (migration) and Task 6 (search uses the snapshot).
2. **Editing the Aux list after a search, then clicking Analyze** must label alerts with the names the rasters were built with. Tests: Task 1 (`custom_names_from_rasters`) and Task 7 (wiring pinned).
3. **Codes arriving as floats or float strings** (`10000.0`, `"[200000020.0]"`, from the float64 export) must decode the same as ints. Test: Task 3.
4. **Names with odd spacing or case** (`"  radd "`, `"Foo"` vs `" foo"`) must be rejected as reserved or duplicate, both in the modal and on recipe import. Tests: Task 1 and Task 8.
5. **Deleting the middle of three sources and searching again** must compact the slots to 0, 1 and keep each name on the right slot. Test: Task 1 (`set_custom_alerts_dictionary`).

---

### Task 0: Commit the pending Optional-files redesign

The settings-list redesign of the dialog (previous session) is uncommitted in `gui/tile/aux_tile.py` and the three `ui.json` files. Commit it on its own so later diffs only show this feature.

**Files:**
- Commit: `gui/tile/aux_tile.py`, `deforestation_alerts/message/{en,es-ES,fr}/ui.json`

**Model:** sonnet

- [ ] **Step 1: Confirm the tree holds only that work**

Run: `git status --short`
Expected: ` M` on exactly `gui/tile/aux_tile.py` and the three `ui.json` files, plus untracked `docs/superpowers/...` files. If anything else is modified, stop and ask.

- [ ] **Step 2: Run the suite**

Run: `micromamba run -n defo-alerts python -m pytest -q --ignore=tests/test_aoi_ccdc_race.py`
Expected: all pass. The ignored file fails to collect only because of the Earth Engine precondition.

- [ ] **Step 3: Commit**

```bash
git add gui/tile/aux_tile.py deforestation_alerts/message/en/ui.json deforestation_alerts/message/es-ES/ui.json deforestation_alerts/message/fr/ui.json docs/superpowers/specs/2026-09-30-multiple-custom-alert-sources-design.md docs/superpowers/plans/2026-09-30-multiple-custom-alert-sources.md
git commit -m "feat(aux): settings-list layout for the Optional files dialog"
```

---

### Task 1: `custom_sources.py` — pure rules and dictionary helpers

**Files:**
- Create: `deforestation_alerts/scripts/custom_sources.py`
- Test: `tests/test_custom_sources.py`

**Model:** sonnet

**Interfaces:**
- Produces (used by Tasks 2, 3, 4, 6, 7, 8):
  - `MAX_CUSTOM_SOURCES: int = 5`, `CUSTOM_BASE_EXPONENT: int = 4`, `BUILTIN_SOURCE_NAMES: tuple[str, ...]`, `CUSTOM_KIND: str = "CUSTOM"`
  - `slot_value(slot: int) -> int`
  - `validate_name(name, existing_names, current=None) -> str | None` (`"empty"`, `"reserved"` or `"duplicate"`)
  - `normalize_sources(items) -> list[dict]` (`[{"name": str, "asset": str}, ...]`)
  - `is_custom_entry(entry) -> bool`
  - `remove_custom_alerts_dictionary(md: dict) -> dict`
  - `set_custom_alerts_dictionary(md: dict, sources, start_date: str, end_date: str) -> dict`
  - `custom_names_from_rasters(raster_dict) -> dict[int, str]`

- [ ] **Step 1: Write the failing tests**

```python
"""Pure custom-alert-source rules (no Earth Engine)."""

import pytest

from deforestation_alerts.scripts.custom_sources import (
    CUSTOM_KIND,
    MAX_CUSTOM_SOURCES,
    custom_names_from_rasters,
    is_custom_entry,
    normalize_sources,
    remove_custom_alerts_dictionary,
    set_custom_alerts_dictionary,
    slot_value,
    validate_name,
)


def test_slot_values_follow_the_ten_thousands_digit():
    assert [slot_value(s) for s in range(5)] == [10**4, 10**5, 10**6, 10**7, 10**8]


@pytest.mark.parametrize("bad", [-1, 5, 1.0, None])
def test_slot_value_rejects_out_of_range(bad):
    with pytest.raises(ValueError):
        slot_value(bad)


@pytest.mark.parametrize(
    "name, existing, current, expected",
    [
        ("", [], None, "empty"),
        ("   ", [], None, "empty"),
        (None, [], None, "empty"),
        ("RADD", [], None, "reserved"),
        ("  radd ", [], None, "reserved"),
        ("glad-s2", [], None, "reserved"),
        ("Foo", [" foo"], None, "duplicate"),
        ("foo", ["Foo", "Bar"], "Foo", None),  # editing its own name
        ("Bar", ["Foo", "Bar"], "Foo", "duplicate"),  # renaming onto another
        ("Sentinel-1", ["Foo"], None, None),
    ],
)
def test_validate_name(name, existing, current, expected):
    assert validate_name(name, existing, current=current) == expected


def test_normalize_sources_cleans_and_caps():
    items = [
        {"name": " A ", "asset": " users/x/a "},
        {"name": "a", "asset": "users/x/dup"},  # duplicate (case) -> dropped
        {"name": "RADD", "asset": "users/x/r"},  # reserved -> dropped
        {"name": "B", "asset": ""},  # no asset -> dropped
        "not a dict",  # dropped
        {"name": "C", "asset": "users/x/c"},
        {"name": "D", "asset": "users/x/d"},
        {"name": "E", "asset": "users/x/e"},
        {"name": "F", "asset": "users/x/f"},
        {"name": "G", "asset": "users/x/g"},  # 6th valid -> truncated
    ]
    out = normalize_sources(items)
    assert out == [
        {"name": "A", "asset": "users/x/a"},
        {"name": "C", "asset": "users/x/c"},
        {"name": "D", "asset": "users/x/d"},
        {"name": "E", "asset": "users/x/e"},
        {"name": "F", "asset": "users/x/f"},
    ]
    assert len(out) == MAX_CUSTOM_SOURCES


@pytest.mark.parametrize("junk", [None, "", "x", {"name": "A"}, 3])
def test_normalize_sources_tolerates_junk(junk):
    assert normalize_sources(junk) == []


def _md():
    return {"GLAD-L": {"collection": "projects/glad/alert/UpdResult"}}


def test_set_custom_assigns_slots_in_order_and_keeps_builtins():
    md = _md()
    set_custom_alerts_dictionary(
        md,
        [{"name": "A", "asset": "users/x/a"}, {"name": "B", "asset": "users/x/b"}],
        "2015-01-01",
        "2026-09-30",
    )
    assert md["A"] == {
        "collection": "users/x/a",
        "kind": CUSTOM_KIND,
        "slot": 0,
        "start_date": "2015-01-01",
        "end_date": "2026-09-30",
    }
    assert md["B"]["slot"] == 1
    assert "GLAD-L" in md


def test_set_custom_replaces_the_whole_set_and_compacts_slots():
    md = _md()
    three = [
        {"name": "A", "asset": "users/x/a"},
        {"name": "B", "asset": "users/x/b"},
        {"name": "C", "asset": "users/x/c"},
    ]
    set_custom_alerts_dictionary(md, three, "2015-01-01", "2026-09-30")
    # delete the middle one and rename the first
    set_custom_alerts_dictionary(
        md,
        [{"name": "A2", "asset": "users/x/a"}, {"name": "C", "asset": "users/x/c"}],
        "2015-01-01",
        "2026-09-30",
    )
    assert "A" not in md and "B" not in md
    assert md["A2"]["slot"] == 0
    assert md["C"]["slot"] == 1
    assert "GLAD-L" in md


def test_set_custom_with_empty_list_clears_and_remove_keeps_builtins():
    md = _md()
    set_custom_alerts_dictionary(md, [{"name": "A", "asset": "a"}], "s", "e")
    set_custom_alerts_dictionary(md, [], "s", "e")
    assert list(md) == ["GLAD-L"]
    set_custom_alerts_dictionary(md, [{"name": "A", "asset": "a"}], "s", "e")
    remove_custom_alerts_dictionary(md)
    assert list(md) == ["GLAD-L"]


def test_set_custom_rejects_more_than_the_cap():
    too_many = [{"name": f"S{i}", "asset": f"a{i}"} for i in range(6)]
    with pytest.raises(ValueError):
        set_custom_alerts_dictionary({}, too_many, "s", "e")


def test_is_custom_entry():
    assert is_custom_entry({"kind": "CUSTOM"})
    assert not is_custom_entry({"collection": "x"})
    assert not is_custom_entry("CUSTOM")


def test_custom_names_from_rasters_reads_only_custom_entries():
    rasters = {
        "RADD": {"alert_raster": object()},
        "S1": {"alert_raster": object(), "custom_slot": 0},
        "Planet": {"alert_raster": object(), "custom_slot": 2},
    }
    assert custom_names_from_rasters(rasters) == {0: "S1", 2: "Planet"}


@pytest.mark.parametrize("unset", ["", [], None])
def test_custom_names_from_rasters_unset_model_values(unset):
    assert custom_names_from_rasters(unset) == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `micromamba run -n defo-alerts python -m pytest -q tests/test_custom_sources.py`
Expected: FAIL (`ModuleNotFoundError: deforestation_alerts.scripts.custom_sources`).

- [ ] **Step 3: Implement**

```python
"""User-defined (RADD-shaped) custom alert sources: slots, names, dictionary entries.

Pure — no Earth Engine import — so it is testable offline; ``aoi_helper`` runs
``init_ee()`` at import. AuxModel (recipe import), the Aux dialog (name
checks), the AOI search (dictionary entries) and the Filter step (decoding)
all share these rules.

Encoding: each alert source owns one decimal digit of the per-pixel alert sum
(GLAD-L 10^0, RADD 10^1, GLAD-S2 10^2, CCDC 10^3). Custom source ``slot`` s
owns digit 10^(4+s), so five custom sources fit an Int32 band.
"""

import logging

logger = logging.getLogger(__name__)

MAX_CUSTOM_SOURCES = 5
CUSTOM_BASE_EXPONENT = 4
BUILTIN_SOURCE_NAMES = ("GLAD-L", "RADD", "GLAD-S2", "CCDC")
CUSTOM_KIND = "CUSTOM"


def slot_value(slot):
    """Digit value of custom ``slot`` (0-based): 10^4 for slot 0 … 10^8 for slot 4."""
    if not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot < MAX_CUSTOM_SOURCES:
        raise ValueError(f"custom source slot out of range: {slot!r}")
    return 10 ** (CUSTOM_BASE_EXPONENT + slot)


def _key(name):
    return str(name or "").strip().casefold()


def validate_name(name, existing_names, current=None):
    """Why ``name`` cannot be used, or ``None`` when it can.

    ``existing_names`` are the names already in the list; ``current`` is the
    name of the entry being edited, so keeping it unchanged is not a duplicate.
    """
    key = _key(name)
    if not key:
        return "empty"
    if key in {_key(n) for n in BUILTIN_SOURCE_NAMES}:
        return "reserved"
    others = [_key(n) for n in existing_names]
    if current is not None and _key(current) in others:
        others.remove(_key(current))
    if key in others:
        return "duplicate"
    return None


def normalize_sources(items):
    """Clean a list read from a recipe: trimmed name/asset, valid unique names, ≤ 5."""
    if not isinstance(items, (list, tuple)):
        if items:
            logger.warning("custom_alerts is not a list; ignoring %r", items)
        return []
    kept = []
    for item in items:
        if not isinstance(item, dict):
            logger.warning("Dropping malformed custom alert source %r", item)
            continue
        name = str(item.get("name") or "").strip()
        asset = str(item.get("asset") or "").strip()
        error = validate_name(name, [k["name"] for k in kept])
        if not asset or error is not None:
            logger.warning("Dropping custom alert source %r (%s)", item, error or "no asset")
            continue
        if len(kept) == MAX_CUSTOM_SOURCES:
            logger.warning("Dropping custom alert source %r (more than %d)", item, MAX_CUSTOM_SOURCES)
            continue
        kept.append({"name": name, "asset": asset})
    return kept


def is_custom_entry(entry):
    """True for an alerts-dictionary entry that is a user-defined custom source."""
    return isinstance(entry, dict) and entry.get("kind") == CUSTOM_KIND


def remove_custom_alerts_dictionary(alert_metadata):
    """Remove every custom entry, leaving the built-in sources."""
    for key in [k for k, v in alert_metadata.items() if is_custom_entry(v)]:
        del alert_metadata[key]
    return alert_metadata


def set_custom_alerts_dictionary(alert_metadata, sources, start_date, end_date):
    """Replace the custom entries with ``sources``, slot = list position.

    The name is the dictionary key shown in the Filter source select. Replacing
    the whole set handles renames and deletions without tracking old names.
    """
    remove_custom_alerts_dictionary(alert_metadata)
    for slot, source in enumerate(sources):
        slot_value(slot)  # raises past the cap
        alert_metadata[source["name"]] = {
            "collection": source["asset"],
            "kind": CUSTOM_KIND,
            "slot": slot,
            "start_date": start_date,
            "end_date": end_date,
        }
    return alert_metadata


def custom_names_from_rasters(raster_dict):
    """``{slot: name}`` for the custom sources of one search's raster dictionary.

    The Filter step decodes with this, so names always match the slots the
    rasters were built with, whatever changed in the Aux dialog since.
    """
    if not isinstance(raster_dict, dict):
        return {}
    return {
        entry["custom_slot"]: name
        for name, entry in raster_dict.items()
        if isinstance(entry, dict) and "custom_slot" in entry
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `micromamba run -n defo-alerts python -m pytest -q tests/test_custom_sources.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add deforestation_alerts/scripts/custom_sources.py tests/test_custom_sources.py
git commit -m "feat(alerts): pure rules for multiple custom alert sources"
```

---

### Task 2: `AuxModel.custom_alerts` list + legacy import

**Files:**
- Modify: `deforestation_alerts/model/aux_model.py`
- Test: `tests/test_models.py` (update `test_aux_model_roundtrip`, add migration tests)

**Model:** sonnet

**Interfaces:**
- Consumes: `normalize_sources` (Task 1)
- Produces: `AuxModel.custom_alerts: List[Dict]`, default `[]`. The `custom_alert_layer` and `custom_alert_name` traits are **removed**. Export key `"custom_alerts"`.

- [ ] **Step 1: Update and add tests** in `tests/test_models.py`

Replace the custom lines in `test_aux_model_roundtrip`:

```python
def test_aux_model_roundtrip():
    m = AuxModel()
    m.aux_layer = "projects/x/aux"
    m.aux_layer_vis = {"palette": ["#000000"]}
    m.custom_alerts = [
        {"name": "MyAlerts", "asset": "projects/x/custom"},
        {"name": "Other", "asset": "projects/x/other"},
    ]
    m.attribute_raster = {
        "source": "projects/x/forest", "kind": "gee",
        "bands": [{"band": "forest", "stat": "majority", "column": "forest"}],
    }
    exported = m.export_dictionary()
    assert set(exported) == {
        "ccdc_layer", "custom_alerts",
        "mask_layer", "aux_layer", "aux_layer_vis",
        "custom_report_template", "attribute_raster",
    }

    loaded = AuxModel()
    loaded.import_from_dictionary(exported)
    assert loaded.aux_layer == "projects/x/aux"
    assert loaded.aux_layer_vis == {"palette": ["#000000"]}
    assert loaded.custom_alerts == [
        {"name": "MyAlerts", "asset": "projects/x/custom"},
        {"name": "Other", "asset": "projects/x/other"},
    ]
    assert loaded.attribute_raster == {
        "source": "projects/x/forest", "kind": "gee",
        "bands": [{"band": "forest", "stat": "majority", "column": "forest"}],
    }


def test_aux_model_imports_legacy_single_custom_source():
    loaded = AuxModel()
    loaded.import_from_dictionary(
        {"custom_alert_layer": "projects/x/custom", "custom_alert_name": "MyAlerts"}
    )
    assert loaded.custom_alerts == [{"name": "MyAlerts", "asset": "projects/x/custom"}]


def test_aux_model_legacy_partial_or_reserved_is_ignored():
    loaded = AuxModel()
    loaded.import_from_dictionary({"custom_alert_layer": "projects/x/custom"})
    assert loaded.custom_alerts == []
    loaded.import_from_dictionary(
        {"custom_alert_layer": "projects/x/c", "custom_alert_name": "RADD"}
    )
    assert loaded.custom_alerts == []


def test_aux_model_import_without_custom_keys_keeps_current():
    m = AuxModel()
    m.custom_alerts = [{"name": "Keep", "asset": "a"}]
    m.import_from_dictionary({"mask_layer": "projects/x/mask"})
    assert m.custom_alerts == [{"name": "Keep", "asset": "a"}]


def test_aux_model_import_cleans_new_key():
    m = AuxModel()
    m.import_from_dictionary(
        {"custom_alerts": [{"name": "ccdc", "asset": "a"}, {"name": "Ok", "asset": "b"}]}
    )
    assert m.custom_alerts == [{"name": "Ok", "asset": "b"}]
```

- [ ] **Step 2: Run to verify it fails**

Run: `micromamba run -n defo-alerts python -m pytest -q tests/test_models.py -k aux_model`
Expected: FAIL. The roundtrip export set differs, and the migration tests fail because `custom_alerts` doesn't exist.

- [ ] **Step 3: Implement** in `deforestation_alerts/model/aux_model.py`

Change the imports and traits:

```python
from pysepal import model
from traitlets import Any, Dict, List, Unicode

from deforestation_alerts.message import cm
from deforestation_alerts.scripts.custom_sources import normalize_sources
```

Replace the two traits `custom_alert_layer` / `custom_alert_name` with:

```python
    custom_alerts = List(Dict(), default_value=[]).tag(sync=True)
    "user-defined custom alert sources, [{'name', 'asset'}] (RADD-shaped GEE assets); list order is the code slot. Always reassign, never mutate in place, so observers fire"
```

In `export_dictionary`, replace the two custom keys with:

```python
            "custom_alerts": [dict(s) for s in self.custom_alerts],
```

In `import_from_dictionary`, replace the two custom assignments with:

```python
            if "custom_alerts" in data:
                self.custom_alerts = normalize_sources(data["custom_alerts"])
            elif data.get("custom_alert_layer") and data.get("custom_alert_name"):
                # Recipes saved before multiple sources: one legacy source.
                self.custom_alerts = normalize_sources(
                    [{"name": data["custom_alert_name"], "asset": data["custom_alert_layer"]}]
                )
            elif "custom_alert_layer" in data or "custom_alert_name" in data:
                self.custom_alerts = []
```

(The last branch makes a legacy recipe with only half the pair clear the list, matching the partial-config rule the old observer had. `test_aux_model_legacy_partial_or_reserved_is_ignored` pins it.)

- [ ] **Step 4: Run to verify it passes**

Run: `micromamba run -n defo-alerts python -m pytest -q tests/test_models.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add deforestation_alerts/model/aux_model.py tests/test_models.py
git commit -m "feat(model): AuxModel stores a list of custom alert sources"
```

(`gui/tile/aoi_tile.py`, `alerts_filter_tile.py` and `aux_tile.py` still reference the removed traits until Tasks 6, 7 and 9. That's expected mid-plan. Their tests are updated in those tasks.)

---

### Task 3: Multi-slot decoding

**Files:**
- Modify: `deforestation_alerts/scripts/report_builder.py` (`get_unique_alerts`)
- Modify: `deforestation_alerts/scripts/alert_filter_helper.py` (`convert_to_geopandas`)
- Test: `tests/test_report_builder.py`, `tests/test_convert_to_geopandas.py`

**Model:** sonnet

**Interfaces:**
- Consumes: `MAX_CUSTOM_SOURCES`, `slot_value` (Task 1)
- Produces: `get_unique_alerts(alert_list, custom_names=None)` and `convert_to_geopandas(vectorized_gdf, custom_names=None)`, where `custom_names: dict[int, str] | None`. The `custom_name` keyword is **removed**. A missing name falls back to `f"Custom {slot + 1}"`.

- [ ] **Step 1: Update tests**

In `tests/test_report_builder.py`, replace `test_get_unique_alerts_decodes_custom_tenthousands` and `test_get_unique_alerts_custom_defaults_to_generic_label` with:

```python
@pytest.mark.parametrize(
    "value, expected",
    [
        (10000, {"MyAlerts"}),                       # slot 0, code 1
        (20000, {"MyAlerts"}),                       # slot 0, code 2
        (10001, {"GLAD-L", "MyAlerts"}),             # union with GLAD-L
        (11000, {"CCDC", "MyAlerts"}),               # CCDC stays position-bounded
        (1, {"GLAD-L"}),                             # custom digits empty
        (100000, {"Sentinel"}),                      # slot 1
        (200000020, {"RADD", "Planet"}),             # slot 4 code 2 + RADD code 2
        (210010, {"RADD", "MyAlerts", "Sentinel"}),  # RADD + slot 0 (1) + slot 1 (2)
        (1010010, {"RADD", "MyAlerts", "Other"}),    # RADD + slot 0 + slot 2
    ],
)
def test_get_unique_alerts_decodes_custom_slots(value, expected):
    names = {0: "MyAlerts", 1: "Sentinel", 2: "Other", 4: "Planet"}
    assert set(get_unique_alerts([value], custom_names=names)) == expected
```

```python
def test_get_unique_alerts_unnamed_slot_falls_back_to_numbered_label():
    assert set(get_unique_alerts([10000])) == {"Custom 1"}
    assert set(get_unique_alerts([1000000], custom_names={0: "A"})) == {"Custom 3"}


@pytest.mark.parametrize("value", [10000.0, "10000", "10000.0", "[10000.0]"])
def test_get_unique_alerts_accepts_float_codes(value):
    # The float64 export and CSV round-trips hand codes over as floats/strings.
    arg = value if isinstance(value, str) and value.startswith("[") else [value]
    assert set(get_unique_alerts(arg, custom_names={0: "A"})) == {"A"}
```

In `tests/test_convert_to_geopandas.py`, replace `test_convert_threads_custom_name_into_alert_sources` with:

```python
def test_convert_threads_custom_names_into_alert_sources():
    out = convert_to_geopandas(_custom_coded_gdf(), custom_names={0: "MyAlerts"})
    assert out["alert_sources"].iloc[0] == "MyAlerts"
    parts = out["alert_sources"].iloc[1].replace(" and ", ", ").split(", ")
    assert set(parts) == {"GLAD-L", "MyAlerts"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `micromamba run -n defo-alerts python -m pytest -q tests/test_report_builder.py tests/test_convert_to_geopandas.py`
Expected: FAIL (`TypeError: unexpected keyword argument 'custom_names'`).

- [ ] **Step 3: Implement**

In `report_builder.py`, add the import next to the other module imports:

```python
from deforestation_alerts.scripts.custom_sources import MAX_CUSTOM_SOURCES, slot_value
```

Change the signature and docstring bullets, the int conversion, and the custom check:

```python
def get_unique_alerts(alert_list, custom_names=None):
    """
    ...
    - CCDC: thousands digit (1000 or 2000 → digit 1 or 2 if triggered)
    - custom slot s (0–4): digit 10^(4+s) (1 or 2 if triggered)

    Parameters:
        alert_list (list): A list of integers (or strings representing numbers).
        custom_names (dict[int, str] | None): ``{slot: name}`` for the custom
            sources of the run; a slot without a name decodes as "Custom <s+1>".
    ...
    """
```

Inside the loop, replace `value = int(value)` with:

```python
        try:
            # float() first: the float64 export yields 10000.0, and CSV
            # round-trips can hand the same over as "10000.0".
            value = int(float(value))
        except (TypeError, ValueError):
            continue  # Skip invalid entries
```

Replace the single ten-thousands check with:

```python
        # Custom sources: slot s owns digit 10^(4+s).
        for slot in range(MAX_CUSTOM_SOURCES):
            if (value // slot_value(slot)) % 10 in (1, 2):
                unique_alerts.add(
                    (custom_names or {}).get(slot) or f"Custom {slot + 1}"
                )
```

In `alert_filter_helper.py`:

```python
def convert_to_geopandas(vectorized_gdf, custom_names=None):
    """...
    ``custom_names`` (``{slot: name}``) labels the user-defined custom alert
    sources in ``alert_sources``; unnamed slots decode as "Custom <n>".
    """
    ...
    gdf["alert_sources"] = gdf["alert_type_unique"].apply(
        lambda x: format_list(get_unique_alerts(x, custom_names=custom_names))
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `micromamba run -n defo-alerts python -m pytest -q tests/test_report_builder.py tests/test_convert_to_geopandas.py tests/test_overview_render.py tests/test_alert_export.py tests/test_parquet_store.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add deforestation_alerts/scripts/report_builder.py deforestation_alerts/scripts/alert_filter_helper.py tests/test_report_builder.py tests/test_convert_to_geopandas.py
git commit -m "feat(alerts): decode up to five named custom alert slots"
```

---

### Task 4: Slot-aware raster build in `aoi_helper`

**Files:**
- Modify: `deforestation_alerts/scripts/aoi_helper.py` (`_custom_alert_keys`, `add_custom_alerts_dictionary`, `remove_custom_alerts_dictionary`, `create_available_alert_dictionary`, `create_filtered_alert_raster_dictionary`, `get_alerts`, `_from_custom`)
- Rewrite: `tests/test_custom_alert_source.py`

**Model:** sonnet

**Interfaces:**
- Consumes: `CUSTOM_KIND`, `is_custom_entry`, `slot_value`, `set_custom_alerts_dictionary`, `remove_custom_alerts_dictionary` (Task 1)
- Produces:
  - `aoi_helper.get_alerts(collection, start, end, aoi, asset, slot=0)`
  - `aoi_helper._from_custom(start, end, aoi, asset, slot=0)` → Int32 `alert` band with codes `v` / `2v`
  - `create_filtered_alert_raster_dictionary(...)` entries for custom sources are `{"alert_raster": ee.Image, "custom_slot": int}`
  - `aoi_helper` re-exports `set_custom_alerts_dictionary` and `remove_custom_alerts_dictionary` (imported from `custom_sources`). `add_custom_alerts_dictionary` is **gone**.

- [ ] **Step 1: Rewrite `tests/test_custom_alert_source.py`**

```python
"""Custom alert sources in aoi_helper — slot-aware raster build.

aoi_helper runs init_ee() at import, so everything is imported inside the test
and depends on the ``gee`` fixture (skips without Earth Engine).
"""


def test_filtered_raster_dictionary_carries_custom_slot(gee, monkeypatch):
    from deforestation_alerts.scripts import aoi_helper

    calls = []

    def fake_get_alerts(collection, start, end, aoi, asset, slot=0):
        calls.append((collection, asset, slot))
        return f"img:{collection}:{asset}:{slot}"

    monkeypatch.setattr(aoi_helper, "get_alerts", fake_get_alerts)
    full = {"RADD": {"collection": "x"}}
    aoi_helper.set_custom_alerts_dictionary(
        full,
        [{"name": "A", "asset": "users/x/a"}, {"name": "B", "asset": "users/x/b"}],
        "2015-01-01",
        "2026-09-30",
    )
    out = aoi_helper.create_filtered_alert_raster_dictionary(
        ["RADD", "B"], "aoi", "2020-01-01", "2021-01-01", None, full_dictionary=full
    )
    assert out["RADD"] == {"alert_raster": "img:RADD:None:0"}
    assert out["B"] == {"alert_raster": "img:CUSTOM:users/x/b:1", "custom_slot": 1}
    assert ("CUSTOM", "users/x/b", 1) in calls


def test_from_custom_writes_slot_codes_as_int32(gee, tiny_aoi, monkeypatch):
    import ee
    from deforestation_alerts.scripts import aoi_helper

    # A synthetic RADD-shaped image stands in for the asset: ee.Image(img)
    # returns img unchanged, and getAsset is stubbed to report an IMAGE.
    monkeypatch.setattr(aoi_helper.ee.data, "getAsset", lambda _id: {"type": "IMAGE"})
    confirmed = ee.Image.constant([3, 23001]).rename(["Alert", "Date"])
    img = aoi_helper._from_custom("2022-01-01", "2024-01-01", tiny_aoi, confirmed, slot=2)

    assert img.bandTypes().getInfo()["alert"]["precision"] == "int"
    stats = img.select("alert").reduceRegion(
        ee.Reducer.max(), tiny_aoi, 30
    ).getInfo()
    assert stats["alert"] == 10**6
```

- [ ] **Step 2: Run to verify it fails**

Run: `micromamba run -n defo-alerts python -m pytest -q tests/test_custom_alert_source.py`
Expected: FAIL (`set_custom_alerts_dictionary` missing from `aoi_helper` / unexpected `slot`). If both tests SKIP with "Earth Engine unavailable", the precondition in Global Constraints is not met. Report that and continue to Step 3; the offline `tests/test_custom_sources.py` already covers the dictionary logic.

- [ ] **Step 3: Implement** in `aoi_helper.py`

Add near the other imports:

```python
from deforestation_alerts.scripts.custom_sources import (  # noqa: F401 (re-exported)
    CUSTOM_KIND,
    is_custom_entry,
    remove_custom_alerts_dictionary,
    set_custom_alerts_dictionary,
    slot_value,
)
```

Delete `_custom_alert_keys`, `add_custom_alerts_dictionary` and the local `remove_custom_alerts_dictionary` (the imported one replaces it).

In `create_available_alert_dictionary`, replace `if isinstance(data, dict) and data.get("kind") == "CUSTOM":` with `if is_custom_entry(data):`.

Replace the loop body of `create_filtered_alert_raster_dictionary`:

```python
    for nombre in dataset:
        entry = full_dictionary.get(nombre, {})
        if is_custom_entry(entry):
            slot = entry.get("slot", 0)
            raster = get_alerts(
                CUSTOM_KIND, start_date, end_date, aoi, entry["collection"], slot=slot
            )
            # The slot travels with the raster: the Filter step decodes names
            # from here, never from the live Aux settings.
            result[nombre] = {"alert_raster": raster, "custom_slot": slot}
        else:
            raster = get_alerts(nombre, start_date, end_date, aoi, ccdc_asset)
            result[nombre] = {"alert_raster": raster}
    return result
```

`get_alerts`: add `slot=0` to the signature and docstring (`slot (int): custom source slot, CUSTOM only`), and change the branch to `alerts = _from_custom(start, end, aoi, asset, slot)`.

`_from_custom(start, end, aoi, asset, slot=0)`: update the docstring's "ten-thousands slot" wording to "digit 10^(4+slot)", then replace the alert band block:

```python
    # unique alert band in this source's digit (v = confirmed, 2v = potential,
    # as RADD's remap); Int32 because slots 1+ exceed Int16.
    value = slot_value(slot)
    alert_band = (
        alerts.select("Alert")
        .remap([0, 1, 2, 3], [0, 0, 2 * value, value])
        .toInt32()
        .rename("alert")
    )
```

- [ ] **Step 4: Run tests**

Run: `micromamba run -n defo-alerts python -m pytest -q tests/test_custom_alert_source.py tests/test_custom_sources.py`
Expected: PASS, or SKIP for the two Earth Engine tests if the precondition is unmet (say so in the report).

- [ ] **Step 5: Commit**

```bash
git add deforestation_alerts/scripts/aoi_helper.py tests/test_custom_alert_source.py
git commit -m "feat(alerts): build custom alert rasters per slot as Int32"
```

---

### Task 5: dtype-aware vectorizer block sizing + float64 round-trip

**Files:**
- Modify: `deforestation_alerts/scripts/geo_utils.py:28-34` (`optimal_block_shape`)
- Modify: `deforestation_alerts/scripts/vectorize.py:128-129`
- Modify: `sepal_environment.yml` (`rasterio>=1.3` → `rasterio>=1.4`: float64 `features.shapes`, verified on 1.4.4)
- Modify: `tests/test_raster_pipeline_integration.py` (Earth Engine round-trip)
- Test: `tests/test_vectorize.py`

**Model:** sonnet

**Interfaces:**
- Produces: `optimal_block_shape(ny, nx, nband, ram_gb, ram_fraction=0.7, bytes_per_value=4)`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_vectorize.py`)

```python
def test_optimal_block_shape_scales_with_bytes_per_value():
    rows4, _ = optimal_block_shape(10**7, 10000, 1, ram_gb=1.0)
    rows8, _ = optimal_block_shape(10**7, 10000, 1, ram_gb=1.0, bytes_per_value=8)
    assert rows8 == rows4 // 2


def test_float64_high_slot_code_survives_vectorize(tmp_path):
    """A slot-4 custom code in a float64 raster (what geedim exports for an
    Int32 alert + float32 date) must come out exact, and nodata 255 must not
    be read as an alert."""
    from rasterio.transform import from_origin
    from deforestation_alerts.scripts.report_builder import get_unique_alerts

    code = 2 * 10**8 + 20  # slot 4 potential + RADD code 2
    alert = np.full((6, 6), 255.0)
    alert[1:4, 1:4] = code
    date = np.full((6, 6), 255.0)
    date[1:4, 1:4] = 2023.5
    path = tmp_path / "f64.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=6, width=6, count=2, dtype="float64",
        crs="EPSG:4326", transform=from_origin(10.0, 0.0, 0.01, 0.01), nodata=255,
    ) as dst:
        dst.write(alert, 1)
        dst.write(date, 2)

    gdf = vectorize_alert_raster(str(path), min_cluster_size=1, connectivity=8)
    codes = [c for row in gdf["alert_type_unique"] for c in row]
    assert codes == [code]
    assert set(get_unique_alerts(codes, custom_names={4: "Planet"})) == {"RADD", "Planet"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `micromamba run -n defo-alerts python -m pytest -q tests/test_vectorize.py`
Expected: `test_optimal_block_shape_scales_with_bytes_per_value` FAILS (unexpected keyword). The float64 test may already pass. If so, keep it as a regression pin.

- [ ] **Step 3: Implement**

`geo_utils.py`:

```python
def optimal_block_shape(ny, nx, nband, ram_gb, ram_fraction=0.7, bytes_per_value=4):
    """Return (blk_rows, blk_cols) for memory-bounded sequential reading.

    ``bytes_per_value`` is the raster's itemsize: the alert export is float64
    (8) once the alert band is Int32, float32 (4) before.
    """
    target_gb = ram_gb * ram_fraction
    bytes_per_row = nx * nband * bytes_per_value
    max_rows = int((target_gb * 1024**3) / bytes_per_row)
    max_rows = max(1, min(ny, max_rows))
    return max_rows, nx
```

`vectorize.py` (inside `vectorize_alert_raster`):

```python
        if block_rows is None:
            itemsize = np.dtype(src.dtypes[alert_band - 1]).itemsize
            block_rows, _ = optimal_block_shape(
                height, width, 2, ram_gb=4.0, bytes_per_value=itemsize
            )
```

(`numpy` is already imported as `np` in `vectorize.py`. Confirm with `grep -n "import numpy" deforestation_alerts/scripts/vectorize.py`, and add `import numpy as np` if missing.)

`sepal_environment.yml`: change `  - rasterio>=1.3` to `  - rasterio>=1.4`.

Append to `tests/test_raster_pipeline_integration.py`:

```python
def test_high_slot_int32_code_survives_export_and_vectorize(tiny_aoi, tmp_path):
    """Int32 alert + float32 date exports as float64; a slot-4 code must survive
    exactly and nodata 255 must not become an alert."""
    import ee
    code = 2 * 10**8 + 20
    alert = ee.Image.constant(code).toInt32().rename("alert")
    date = ee.Image.constant(2023.5).toFloat().rename("date")
    out = str(tmp_path / "slot4.tif")
    download_ee_image(alert.addBands(date), out, region=tiny_aoi, scale=30,
                      crs="EPSG:4326", unmask_value=255, nodata_value=255)
    with rasterio.open(out) as src:
        assert src.dtypes[0] == "float64"
        present = set(np.unique(src.read(1)).tolist()) - {255}
    assert present == {code}
    vgdf = vectorize_alert_raster(out, min_cluster_size=1, connectivity=8)
    assert {c for row in vgdf["alert_type_unique"] for c in row} == {code}
```

- [ ] **Step 4: Run to verify it passes**

Run: `micromamba run -n defo-alerts python -m pytest -q tests/test_vectorize.py tests/test_raster_pipeline_integration.py`
Expected: `test_vectorize.py` all PASS; the integration tests PASS, or SKIP without Earth Engine.

- [ ] **Step 5: Commit**

```bash
git add deforestation_alerts/scripts/geo_utils.py deforestation_alerts/scripts/vectorize.py sepal_environment.yml tests/test_vectorize.py tests/test_raster_pipeline_integration.py
git commit -m "fix(vectorize): size read blocks by the raster's real dtype"
```

---

### Task 6: AOI search applies the custom-source snapshot

**Files:**
- Modify: `gui/tile/aoi_tile.py`: imports (73–82), `SearchRequest` (117–125), `_run_search_sync` (159–196), delete `_update_dictionary_custom` (244–267), delete `_register_custom_observer` + its `use_effect` (~419–431), `SearchRequest(...)` call (~517–525)
- Create: `tests/test_aoi_search_custom.py`

**Model:** opus

**Interfaces:**
- Consumes: `set_custom_alerts_dictionary` (Task 1, via the `aoi_helper` re-export), `AuxModel.custom_alerts` (Task 2), `create_filtered_alert_raster_dictionary` with `custom_slot` (Task 4)
- Produces: `SearchRequest.custom_alerts: tuple = ()` (last field, has a default). `_run_search_sync` never mutates `request.alerts_dictionary`. The completion effect's write-back rule is **unchanged** (only when `dictionary_rebuilt`); see the spec for why.

- [ ] **Step 1: Write the failing tests**

```python
"""The AOI search registers custom sources from its click-time snapshot.

Importing gui.tile.aoi_tile runs aoi_helper's init_ee(); see the plan's Earth
Engine precondition. All EE work is monkeypatched out.
"""

from datetime import date

import pytest

from gui.tile import aoi_tile

SOURCES = (
    {"name": "S1", "asset": "users/x/s1"},
    {"name": "Planet", "asset": "users/x/planet"},
)


@pytest.fixture
def captured(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        aoi_tile, "check_aoi_inputs", lambda shim: ("fc", "2020-01-01", "2021-01-01")
    )
    monkeypatch.setattr(
        aoi_tile, "create_available_alert_dictionary", lambda d, *a: list(d)
    )

    def fake_rasters(available, aoi, d1, d2, ccdc, full_dictionary=None):
        seen["full"] = full_dictionary
        return {n: {"alert_raster": n} for n in available}

    monkeypatch.setattr(aoi_tile, "create_filtered_alert_raster_dictionary", fake_rasters)
    monkeypatch.setattr(
        aoi_tile, "create_basic_alerts_dictionary", lambda: {"RADD": {"collection": "r"}}
    )
    return seen


def _request(dictionary, custom=SOURCES):
    return aoi_tile.SearchRequest(
        feature_collection="fc", start_date="2020-01-01", end_date="2021-01-01",
        alerts_dictionary=dictionary, ccdc_layer=None, custom_alerts=custom,
    )


def test_init_shaped_dictionary_gets_the_snapshot(captured):
    # The init task writes built-ins only; the search must still add customs.
    init_dict = {"RADD": {"collection": "r"}}
    outcome = aoi_tile._run_search_sync(_request(init_dict))
    full = captured["full"]
    assert full["S1"]["slot"] == 0 and full["Planet"]["slot"] == 1
    assert full["S1"]["end_date"] == date.today().strftime("%Y-%m-%d")
    assert init_dict == {"RADD": {"collection": "r"}}  # input not mutated
    assert set(outcome.available_alerts_list) == {"RADD", "S1", "Planet"}
    assert outcome.dictionary_rebuilt is False


def test_rebuilt_dictionary_gets_the_snapshot(captured):
    outcome = aoi_tile._run_search_sync(_request(None))
    assert outcome.dictionary_rebuilt is True
    assert {"RADD", "S1", "Planet"} <= set(outcome.alerts_dictionary)


def test_stale_custom_entries_are_replaced(captured):
    stale = {"RADD": {"collection": "r"},
             "Old": {"collection": "users/x/old", "kind": "CUSTOM", "slot": 0}}
    aoi_tile._run_search_sync(_request(stale, custom=(SOURCES[1],)))
    full = captured["full"]
    assert "Old" not in full
    assert full["Planet"]["slot"] == 0


def test_request_defaults_to_no_custom_sources():
    req = aoi_tile.SearchRequest(
        feature_collection="fc", start_date="a", end_date="b",
        alerts_dictionary={}, ccdc_layer=None,
    )
    assert req.custom_alerts == ()


def test_custom_observer_is_gone():
    assert not hasattr(aoi_tile, "_update_dictionary_custom")
```

- [ ] **Step 2: Run to verify it fails**

Run: `micromamba run -n defo-alerts python -m pytest -q tests/test_aoi_search_custom.py`
Expected: FAIL (`unexpected keyword argument 'custom_alerts'`). If it errors at import with the Earth Engine `NameError`, the precondition is unmet. Stop and report; don't proceed blind.

- [ ] **Step 3: Implement** in `gui/tile/aoi_tile.py`

Imports: remove `add_custom_alerts_dictionary` and `remove_custom_alerts_dictionary`, and add `set_custom_alerts_dictionary` to the `aoi_helper` import list.

`SearchRequest`: append the field (it must stay last, because it has a default):

```python
    ccdc_layer: Any
    # Snapshot of aux_model.custom_alerts at click time. The worker registers
    # these in its own copy of the dictionary, so no observer can race it.
    custom_alerts: tuple = ()
```

`_run_search_sync`: replace the dictionary block with:

```python
    if isinstance(request.alerts_dictionary, dict):
        # A copy: the worker must not mutate the model's dictionary.
        dictionary = dict(request.alerts_dictionary)
        dictionary_rebuilt = False
    else:
        # Defensive: the init task has not landed yet (or it failed). Rebuild
        # here — still on the worker thread — and hand it back for the effect.
        dictionary = create_basic_alerts_dictionary()
        dictionary_rebuilt = True

    # Custom sources come from the click-time snapshot, whatever the init task
    # or a recipe load did to the model's dictionary. A wide date range keeps
    # them available; ``_from_custom`` does the real date filtering.
    set_custom_alerts_dictionary(
        dictionary,
        list(request.custom_alerts),
        "2015-01-01",
        date.today().strftime("%Y-%m-%d"),
    )
```

Delete `_update_dictionary_custom` entirely. Delete the `# custom-alert traitlets observer` comment block, `_register_custom_observer`, and its `solara.use_effect(_register_custom_observer, dependencies=[])`. Update the module docstring if it mentions the custom observer (`grep -n "custom" gui/tile/aoi_tile.py`).

In the `SearchRequest(...)` construction, add:

```python
                ccdc_layer=aux_model.ccdc_layer,
                custom_alerts=tuple(dict(s) for s in aux_model.custom_alerts),
```

Leave `_apply_search` unchanged (it still writes `alerts_dictionary` only when `outcome.dictionary_rebuilt`).

- [ ] **Step 4: Run tests**

Run: `micromamba run -n defo-alerts python -m pytest -q tests/test_aoi_search_custom.py tests/test_aoi_ccdc_race.py tests/test_app_wiring.py tests/test_feedback_notifications.py tests/test_navigation.py tests/test_app_state.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/tile/aoi_tile.py tests/test_aoi_search_custom.py
git commit -m "feat(aoi): search registers custom sources from a click-time snapshot"
```

---

### Task 7: Filter step — Int32 union and names from the search rasters

**Files:**
- Modify: `gui/tile/alerts_filter_tile.py`: imports (~86–92), `AnalyzeRequest` (145–162), `create_filtered_alert_raster` (219–269), `_finish_analyze_sync` (641–660), `AnalyzeRequest(...)` construction (~1030–1050)
- Test: `tests/test_app_wiring.py` (append)

**Model:** sonnet

**Interfaces:**
- Consumes: `custom_names_from_rasters` (Task 1), `convert_to_geopandas(..., custom_names=)` (Task 3), rasters with `custom_slot` (Task 4)
- Produces: `AnalyzeRequest.custom_names: Any` replaces `custom_alert_name`

- [ ] **Step 1: Write the failing test** (append to `tests/test_app_wiring.py`, which already has `_src`)

```python
def test_filter_decodes_custom_names_from_the_search_rasters():
    """Names must come from the rasters the union is built from (spec
    constraint 6), never from the live Aux settings."""
    src = _src("gui/tile/alerts_filter_tile.py")
    flat = " ".join(src.split())  # whitespace-insensitive
    assert "custom_alert_name" not in src
    assert (
        "custom_names=custom_names_from_rasters( "
        "alert_filter_model.available_alerts_raster_list )"
    ) in flat
    assert "convert_to_geopandas(gdf, custom_names=request.custom_names)" in flat
    assert '.select("alert").toInt32()' in flat
```

- [ ] **Step 2: Run to verify it fails**

Run: `micromamba run -n defo-alerts python -m pytest -q tests/test_app_wiring.py::test_filter_decodes_custom_names_from_the_search_rasters`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add the import:

```python
from deforestation_alerts.scripts.custom_sources import custom_names_from_rasters
```

`AnalyzeRequest`: replace `custom_alert_name: Any` with:

```python
    # {slot: name} of the custom sources, read from the search's rasters.
    custom_names: Any
```

`create_filtered_alert_raster`: after the `if len(alert_source) == 1: ... else: ...` block and before `alerta = alert_union`, insert:

```python
    # Int32 alert band whatever the inputs: custom slots reach 2·10^8, and a
    # fixed type makes the export dtype (float64 with the float date) stable.
    alert_union = alert_union.addBands(
        alert_union.select("alert").toInt32(), overwrite=True
    )
```

Update that function's docstring line "Unioning multiple sources …" to add: "The `alert` band is cast to Int32 in both branches."

`_finish_analyze_sync`: change the docstring's `custom_name` sentence to "``custom_names`` labels the custom sources' slots…", and the call to:

```python
        alerts_gdf=convert_to_geopandas(gdf, custom_names=request.custom_names),
```

`AnalyzeRequest(...)` construction: replace `custom_alert_name=aux_model.custom_alert_name,` with exactly:

```python
                custom_names=custom_names_from_rasters(
                    alert_filter_model.available_alerts_raster_list
                ),
```

- [ ] **Step 4: Run tests**

Run: `micromamba run -n defo-alerts python -m pytest -q tests/test_app_wiring.py tests/test_convert_to_geopandas.py`
Then: `grep -rn "custom_alert_name\|custom_alert_layer\|custom_name=" --include=*.py gui deforestation_alerts`
Expected: tests PASS. The grep hits only `gui/tile/aux_tile.py` (fixed in Task 9) and the legacy-import branch in `aux_model.py`.

- [ ] **Step 5: Commit**

```bash
git add gui/tile/alerts_filter_tile.py tests/test_app_wiring.py
git commit -m "feat(filter): Int32 alert union; decode custom names from search rasters"
```

---

### Task 8: `CustomSourceList` widget + i18n

**Files:**
- Create: `gui/widget/custom_source_list.py`
- Modify: `deforestation_alerts/message/{en,es-ES,fr}/ui.json` (`aux_tile`)
- Test: `tests/test_custom_source_list.py`

**Model:** opus

**Interfaces:**
- Consumes: `MAX_CUSTOM_SOURCES`, `validate_name` (Task 1), `AuxModel.custom_alerts` (Task 2), `MUTED` (`gui/widget/text_style.py`)
- Produces: `CustomSourceList(aux_model, gee_interface=None)`, a `v.Html` subclass with:
  - public widgets `rows_box`, `add_btn`, `counter`, `dialog`, `modal_title`, `name_field`, `asset_field`, `save_btn`, `confirm`, `confirm_text`, `remove_btn`
  - methods `open_add()`, `open_edit(index)`, `close_modal()`, `save()`, `ask_remove(index)`, `confirm_remove()`, `cancel_remove()`
  - it contains exactly **one** `sw.inputs.AssetSelect(` call, with `gee_interface=gee_interface` first

- [ ] **Step 1: Add the i18n keys**

In each `ui.json`, inside `aux_tile`, replace the `custom_alerts_title` and `custom_alerts_desc` values and insert the new keys right after `"custom_alerts_name_hint"`. Keep valid JSON (commas), then check with `python3 -c "import json;json.load(open('<file>'))"`.

en:
```json
"custom_alerts_title": "Custom alert sources",
"custom_alerts_desc": "Earth Engine assets in RADD format, with Alert and Date bands. Up to 5; each name is how it appears in the Filter step.",
"custom_add": "Add source",
"custom_count": "{n} of {max}",
"custom_empty": "No custom sources yet.",
"custom_modal_add_title": "Add custom source",
"custom_modal_edit_title": "Edit custom source",
"custom_save": "Save",
"custom_cancel": "Cancel",
"custom_remove": "Remove",
"custom_remove_confirm": "Remove {name}? It will no longer appear in the Filter step.",
"custom_edit_label": "Edit source",
"custom_delete_label": "Remove source",
"custom_name_duplicate": "Another custom source already uses this name.",
"custom_name_reserved": "This name belongs to a built-in alert source.",
```

es-ES:
```json
"custom_alerts_title": "Fuentes de alertas personalizadas",
"custom_alerts_desc": "Assets de Earth Engine con formato RADD, con bandas Alert y Date. Hasta 5; cada nombre es como aparece en el paso Filtro.",
"custom_add": "Añadir fuente",
"custom_count": "{n} de {max}",
"custom_empty": "Aún no hay fuentes personalizadas.",
"custom_modal_add_title": "Añadir fuente personalizada",
"custom_modal_edit_title": "Editar fuente personalizada",
"custom_save": "Guardar",
"custom_cancel": "Cancelar",
"custom_remove": "Quitar",
"custom_remove_confirm": "¿Quitar {name}? Dejará de aparecer en el paso Filtro.",
"custom_edit_label": "Editar fuente",
"custom_delete_label": "Quitar fuente",
"custom_name_duplicate": "Otra fuente personalizada ya usa este nombre.",
"custom_name_reserved": "Este nombre pertenece a una fuente de alertas integrada.",
```

fr:
```json
"custom_alerts_title": "Sources d'alertes personnalisées",
"custom_alerts_desc": "Assets Earth Engine au format RADD, avec des bandes Alert et Date. Jusqu'à 5 ; chaque nom est celui affiché à l'étape Filtre.",
"custom_add": "Ajouter une source",
"custom_count": "{n} sur {max}",
"custom_empty": "Aucune source personnalisée pour l'instant.",
"custom_modal_add_title": "Ajouter une source personnalisée",
"custom_modal_edit_title": "Modifier la source personnalisée",
"custom_save": "Enregistrer",
"custom_cancel": "Annuler",
"custom_remove": "Retirer",
"custom_remove_confirm": "Retirer {name} ? Elle n'apparaîtra plus à l'étape Filtre.",
"custom_edit_label": "Modifier la source",
"custom_delete_label": "Retirer la source",
"custom_name_duplicate": "Une autre source personnalisée utilise déjà ce nom.",
"custom_name_reserved": "Ce nom appartient à une source d'alertes intégrée.",
```

- [ ] **Step 2: Write the failing tests**

```python
"""CustomSourceList: add / edit / remove custom alert sources in the Aux dialog."""

import ipyvuetify as v
import pytest

from deforestation_alerts.message import cm
from deforestation_alerts.model.aux_model import AuxModel


@pytest.fixture
def make(monkeypatch):
    # AssetSelect lists GEE assets on construction; a plain Select stands in.
    from pysepal import sepalwidgets as sw

    monkeypatch.setattr(
        sw.inputs, "AssetSelect", lambda **kwargs: v.Select(items=[], v_model=None)
    )
    from gui.widget.custom_source_list import CustomSourceList

    def _make(items=()):
        model = AuxModel()
        model.custom_alerts = [dict(i) for i in items]
        return model, CustomSourceList(model)

    return _make


def _rows(widget):
    return [c for c in widget.rows_box.children if not isinstance(c, v.Divider)]


def _fill(widget, name, asset):
    widget.name_field.v_model = name
    widget.asset_field.v_model = asset


TWO = [{"name": "A", "asset": "users/x/a"}, {"name": "B", "asset": "users/x/b"}]


def test_empty_state(make):
    _, w = make()
    assert len(_rows(w)) == 1
    assert cm.aux_tile.custom_empty in str(_rows(w)[0].children)
    assert w.counter.children == [cm.aux_tile.custom_count.format(n=0, max=5)]
    assert not w.add_btn.disabled


def test_add_writes_the_model_and_closes(make):
    model, w = make()
    w.open_add()
    assert w.dialog.v_model is True
    assert w.save_btn.disabled  # nothing filled yet
    _fill(w, "  Sentinel-1 ", "users/x/s1")
    assert not w.save_btn.disabled
    w.save()
    assert model.custom_alerts == [{"name": "Sentinel-1", "asset": "users/x/s1"}]
    assert w.dialog.v_model is False
    assert len(_rows(w)) == 1


def test_edit_keeps_position_and_own_name_is_fine(make):
    model, w = make(TWO)
    w.open_edit(0)
    assert w.name_field.v_model == "A" and w.asset_field.v_model == "users/x/a"
    assert not w.save_btn.disabled  # unchanged own name is not a duplicate
    _fill(w, "A2", "users/x/a2")
    w.save()
    assert model.custom_alerts == [
        {"name": "A2", "asset": "users/x/a2"},
        {"name": "B", "asset": "users/x/b"},
    ]


@pytest.mark.parametrize(
    "name, message",
    [(" b ", "custom_name_duplicate"), ("radd", "custom_name_reserved")],
)
def test_bad_names_disable_save_with_a_message(make, name, message):
    model, w = make(TWO)
    w.open_add()
    _fill(w, name, "users/x/new")
    assert w.save_btn.disabled
    assert w.name_field.error_messages == [getattr(cm.aux_tile, message)]
    w.save()  # ignored while invalid
    assert len(model.custom_alerts) == 2


def test_empty_name_or_missing_asset_disable_save_silently(make):
    _, w = make()
    w.open_add()
    _fill(w, "", "users/x/a")
    assert w.save_btn.disabled and w.name_field.error_messages == []
    _fill(w, "A", None)
    assert w.save_btn.disabled


def test_cap_disables_add(make):
    items = [{"name": f"S{i}", "asset": f"a{i}"} for i in range(5)]
    _, w = make(items)
    assert w.add_btn.disabled
    w.open_add()
    assert w.dialog.v_model is False


def test_remove_after_confirmation(make):
    model, w = make(TWO)
    w.ask_remove(1)
    assert w.confirm.v_model is True
    assert "B" in w.confirm_text.children[0]
    w.cancel_remove()
    assert len(model.custom_alerts) == 2
    w.ask_remove(1)
    w.confirm_remove()
    assert model.custom_alerts == [{"name": "A", "asset": "users/x/a"}]
    assert w.confirm.v_model is False


def test_model_changes_rerender(make):
    model, w = make()
    model.custom_alerts = TWO
    assert len(_rows(w)) == 2
    assert w.counter.children == [cm.aux_tile.custom_count.format(n=2, max=5)]
```

- [ ] **Step 3: Run to verify it fails**

Run: `micromamba run -n defo-alerts python -m pytest -q tests/test_custom_source_list.py`
Expected: FAIL (`ModuleNotFoundError: gui.widget.custom_source_list`).

- [ ] **Step 4: Implement** `gui/widget/custom_source_list.py`

```python
"""Custom alert sources editor for the Optional files dialog.

A list of the user's RADD-shaped Earth Engine alert sources with an add/edit
modal and a remove confirmation, modelled on spatial_risk's variables list.
Raw ipyvuetify on purpose: MapApp rebuilds dialog content on reopen, and only
raw widget trees survive that (see ``aux_tile.build_aux_layout``).
"""

import ipyvuetify as v
from pysepal import sepalwidgets as sw

from deforestation_alerts.message import cm
from deforestation_alerts.scripts.custom_sources import MAX_CUSTOM_SOURCES, validate_name
from gui.widget.text_style import MUTED

# Same hairline as the dialog's group boxes (aux_tile._group).
_BORDER = "border: thin solid rgba(128, 128, 128, 0.3);"


class CustomSourceList(v.Html):
    def __init__(self, aux_model, gee_interface=None):
        super().__init__(tag="div")
        t = cm.aux_tile
        self.aux_model = aux_model
        self._editing = None  # index being edited; None while adding
        self._removing = None

        self.rows_box = v.Html(tag="div", class_="rounded", style_=_BORDER, children=[])
        self.add_btn = v.Btn(
            text=True, small=True, color="primary",
            children=[v.Icon(left=True, small=True, children=["mdi-plus"]), t.custom_add],
        )
        self.counter = v.Html(tag="span", class_="caption ml-2", style_=MUTED, children=[])
        footer = v.Html(tag="div", class_="d-flex align-center mt-2",
                        children=[self.add_btn, self.counter])

        # Add / edit modal.
        self.name_field = sw.TextField(label=t.custom_alerts_name_hint, v_model=None)
        self.asset_field = sw.inputs.AssetSelect(
            gee_interface=gee_interface, types=["IMAGE", "IMAGE_COLLECTION"], label=t.asset_label
        )
        self.modal_title = v.CardTitle(children=[t.custom_modal_add_title])
        self.save_btn = v.Btn(color="primary", depressed=True, disabled=True,
                              children=[t.custom_save])
        cancel_btn = v.Btn(text=True, children=[t.custom_cancel])
        self.dialog = v.Dialog(
            v_model=False, persistent=True, max_width=480,
            children=[v.Card(children=[
                self.modal_title,
                v.CardText(children=[self.name_field, self.asset_field]),
                v.CardActions(children=[v.Spacer(), cancel_btn, self.save_btn]),
            ])],
        )

        # Remove confirmation.
        self.confirm_text = v.CardText(class_="pt-5 body-1", children=[])
        self.remove_btn = v.Btn(color="error", text=True, children=[t.custom_remove])
        keep_btn = v.Btn(text=True, children=[t.custom_cancel])
        self.confirm = v.Dialog(
            v_model=False, max_width=420,
            children=[v.Card(children=[
                self.confirm_text,
                v.CardActions(children=[v.Spacer(), keep_btn, self.remove_btn]),
            ])],
        )

        self.children = [self.rows_box, footer, self.dialog, self.confirm]

        self.add_btn.on_event("click", lambda *_: self.open_add())
        cancel_btn.on_event("click", lambda *_: self.close_modal())
        self.save_btn.on_event("click", lambda *_: self.save())
        keep_btn.on_event("click", lambda *_: self.cancel_remove())
        self.remove_btn.on_event("click", lambda *_: self.confirm_remove())
        self.name_field.observe(self._validate, "v_model")
        self.asset_field.observe(self._validate, "v_model")
        aux_model.observe(self._render, "custom_alerts")
        self._render()

    # -- state helpers -------------------------------------------------------
    def _items(self):
        return [dict(s) for s in self.aux_model.custom_alerts]

    def _validate(self, *_):
        names = [s["name"] for s in self.aux_model.custom_alerts]
        current = names[self._editing] if self._editing is not None else None
        error = validate_name(self.name_field.v_model, names, current=current)
        # An empty name only disables Save; a fresh modal should not nag.
        shown = error if error in ("duplicate", "reserved") else None
        self.name_field.error_messages = (
            [getattr(cm.aux_tile, f"custom_name_{shown}")] if shown else []
        )
        self.save_btn.disabled = bool(error) or not self.asset_field.v_model

    # -- add / edit -----------------------------------------------------------
    def _open(self, index, title, name, asset):
        self._editing = index
        self.modal_title.children = [title]
        self.name_field.v_model = name
        self.asset_field.v_model = asset
        self._validate()
        self.dialog.v_model = True

    def open_add(self):
        if len(self.aux_model.custom_alerts) >= MAX_CUSTOM_SOURCES:
            return
        self._open(None, cm.aux_tile.custom_modal_add_title, None, None)

    def open_edit(self, index):
        source = self.aux_model.custom_alerts[index]
        self._open(index, cm.aux_tile.custom_modal_edit_title,
                   source["name"], source["asset"])

    def close_modal(self):
        self.dialog.v_model = False
        self._editing = None

    def save(self):
        self._validate()
        if self.save_btn.disabled:
            return
        item = {"name": self.name_field.v_model.strip(),
                "asset": str(self.asset_field.v_model).strip()}
        items = self._items()
        if self._editing is None:
            items.append(item)
        else:
            items[self._editing] = item  # keeps its slot
        self.aux_model.custom_alerts = items
        self.close_modal()

    # -- remove ---------------------------------------------------------------
    def ask_remove(self, index):
        self._removing = index
        name = self.aux_model.custom_alerts[index]["name"]
        self.confirm_text.children = [cm.aux_tile.custom_remove_confirm.format(name=name)]
        self.confirm.v_model = True

    def cancel_remove(self):
        self.confirm.v_model = False
        self._removing = None

    def confirm_remove(self):
        if self._removing is not None:
            items = self._items()
            del items[self._removing]
            self.aux_model.custom_alerts = items
        self.cancel_remove()

    # -- rendering ------------------------------------------------------------
    def _icon_button(self, icon, label, on_click):
        btn = v.Btn(icon=True, small=True,
                    attributes={"aria-label": label, "title": label},
                    children=[v.Icon(small=True, children=[icon])])
        btn.on_event("click", lambda *_: on_click())
        return btn

    def _row(self, index, source):
        t = cm.aux_tile
        text = v.Html(tag="div", style_="min-width: 0; flex: 1;", children=[
            v.Html(tag="div", class_="font-weight-medium text-truncate",
                   children=[source["name"]]),
            v.Html(tag="div", class_="caption text-truncate", style_=MUTED,
                   attributes={"title": source["asset"]}, children=[source["asset"]]),
        ])
        return v.Html(tag="div", class_="d-flex align-center px-3 py-2", children=[
            text,
            self._icon_button("mdi-pencil", t.custom_edit_label,
                              lambda: self.open_edit(index)),
            self._icon_button("mdi-delete", t.custom_delete_label,
                              lambda: self.ask_remove(index)),
        ])

    def _render(self, *_):
        t = cm.aux_tile
        items = self.aux_model.custom_alerts
        if not items:
            rows = [v.Html(tag="div", class_="body-2 px-3 py-3", style_=MUTED,
                           children=[t.custom_empty])]
        else:
            rows = []
            for index, source in enumerate(items):
                if index:
                    rows.append(v.Divider())
                rows.append(self._row(index, source))
        self.rows_box.children = rows
        self.counter.children = [t.custom_count.format(n=len(items), max=MAX_CUSTOM_SOURCES)]
        self.add_btn.disabled = len(items) >= MAX_CUSTOM_SOURCES
```

- [ ] **Step 5: Run to verify it passes**

Run: `micromamba run -n defo-alerts python -m pytest -q tests/test_custom_source_list.py tests/test_i18n.py`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/widget/custom_source_list.py tests/test_custom_source_list.py deforestation_alerts/message/en/ui.json deforestation_alerts/message/es-ES/ui.json deforestation_alerts/message/fr/ui.json
git commit -m "feat(aux): list editor for custom alert sources"
```

---

### Task 9: Wire the list into the Aux dialog, visual check, changelog

**Files:**
- Modify: `gui/tile/aux_tile.py` (`build_aux_layout`: custom inputs, the row, bindings)
- Modify: `tests/test_app_wiring.py` (`test_gee_widgets_get_the_session_interface`)
- Modify: `tests/test_aux_tile_pickers.py` (assert the list is in the layout)
- Modify: `CHANGELOG.md`

**Model:** sonnet

**Interfaces:**
- Consumes: `CustomSourceList(aux_model, gee_interface)` (Task 8), `AuxModel.custom_alerts` (Task 2)

- [ ] **Step 1: Update the tests**

In `tests/test_app_wiring.py::test_gee_widgets_get_the_session_interface`, replace the `aux`/`calls` part with:

```python
    aux = _src("gui/tile/aux_tile.py")
    calls = aux.split("sw.inputs.AssetSelect(")[1:]
    assert len(calls) == 4  # ccdc, mask, reference layer, raster attributes
    for call in calls:
        assert "gee_interface=gee_interface" in call[: call.index(")")]
    custom = _src("gui/widget/custom_source_list.py")
    custom_calls = custom.split("sw.inputs.AssetSelect(")[1:]
    assert len(custom_calls) == 1
    assert "gee_interface=gee_interface" in custom_calls[0][: custom_calls[0].index(")")]
    assert "CustomSourceList(aux_model, gee_interface)" in aux
```

(Keep the `shell` assertions below it unchanged.)

Append to `tests/test_aux_tile_pickers.py`:

```python
def test_layout_hosts_the_custom_source_list(ws, monkeypatch):
    import ipyvuetify as v
    from pysepal import sepalwidgets as sw
    from gui.widget.custom_source_list import CustomSourceList

    monkeypatch.setattr(sw.inputs, "AssetSelect", lambda **kwargs: v.Select(items=[], v_model=None))
    model = AuxModel()
    layout = build_aux_layout(model, ws)

    def walk(w):
        yield w
        for c in getattr(w, "children", None) or []:
            if hasattr(c, "children"):
                yield from walk(c)

    lists = [w for w in walk(layout) if isinstance(w, CustomSourceList)]
    assert len(lists) == 1 and lists[0].aux_model is model
```

- [ ] **Step 2: Run to verify it fails**

Run: `micromamba run -n defo-alerts python -m pytest -q tests/test_app_wiring.py tests/test_aux_tile_pickers.py`
Expected: FAIL (still 5 AssetSelect calls; no `CustomSourceList`).

- [ ] **Step 3: Implement** in `gui/tile/aux_tile.py`

Import: `from gui.widget.custom_source_list import CustomSourceList`

In `build_aux_layout`, replace the custom-source block (the comment, `custom_name_input` and `custom_alerts_input`) with:

```python
    # Custom alert sources: a list of named, RADD-shaped GEE assets (up to 5).
    custom_list = CustomSourceList(aux_model, gee_interface)
```

Replace the custom row in the *Extra alert sources* group with:

```python
            _row(["custom_alerts"], t.custom_alerts_title, t.custom_alerts_desc,
                 [custom_list]),
```

In the `.bind()` chain, remove `.bind(custom_name_input, "custom_alert_name")` and `.bind(custom_alerts_input, "custom_alert_layer")`. The chain becomes:

```python
    aux_model.bind(ccdc_alerts_input, "ccdc_layer").bind(
        fnf_input, "mask_layer"
    ).bind(aux_input, "aux_layer").bind(aux_viz, "aux_layer_vis").bind(
        form_input, "custom_report_template"
    )
```

(`_row`'s status icon uses `all(getattr(owner, n) for n in names)`, so an empty list reads as not set, with no change needed.)

- [ ] **Step 4: Run the whole suite and the leftover-reference grep**

Run: `micromamba run -n defo-alerts python -m pytest -q`
Then: `grep -rn "custom_alert_name\|custom_alert_layer\|add_custom_alerts_dictionary\|custom_name=" --include=*.py gui deforestation_alerts tests`
Expected: suite PASS (Earth Engine tests may SKIP); the grep hits only the legacy-import branch in `aux_model.py` and the legacy test in `tests/test_models.py`.

- [ ] **Step 5: Visual check through a standalone preview page**

The full app needs the Earth Engine project (Global Constraints). Preview the dialog instead with a throwaway Solara page in the session scratchpad (never in the repo):

```python
# <scratchpad>/preview_app.py — throwaway
import os, sys
ROOT = "/home/jserafini/Desktop/projects/deforestation_alerts/deforestation-alerts-module"
sys.path[:0] = [ROOT, ROOT + "/tests"]
os.environ.setdefault("DEFORESTATION_ALERTS_GDAL_TMPDIR", "/tmp/claude-1000/defo-scratch")
import solara, ipyvuetify as v
from pysepal import sepalwidgets as sw

class _Asset(v.Combobox):  # stands in for AssetSelect (no EE)
    def __init__(self, gee_interface=None, types=None, **kw):
        kw.setdefault("v_model", None); kw.setdefault("dense", True)
        kw.setdefault("prepend_inner_icon", "mdi-sync")
        kw.setdefault("placeholder", "projects/{project}/assets")
        super().__init__(**kw)
sw.inputs.AssetSelect = _Asset

from conftest import FakeSepalClient
from deforestation_alerts.scripts.workspace import Workspace
from deforestation_alerts.model.aux_model import AuxModel
from gui.tile.aux_tile import build_aux_layout

@solara.component
def Page():
    model = solara.use_memo(AuxModel, [])
    def build():
        lay = build_aux_layout(model, Workspace(FakeSepalClient()))
        model.custom_alerts = [
            {"name": "Sentinel-1 radar", "asset": "projects/demo/assets/s1_alerts"},
            {"name": "Planet NICFI detections", "asset": "projects/demo/assets/nicfi_detections_2024"},
            {"name": "National system", "asset": "projects/demo/assets/national/alerts"},
        ]
        return v.Card(style_="width:900px;margin:24px auto;", children=[
            v.CardTitle(children=["Optional files"]), v.Divider(),
            v.CardText(class_="pt-4", children=[lay])])
    solara.lab.theme.dark = os.environ.get("PREVIEW_DARK") == "1"
    solara.display(solara.use_memo(build, []))
```

Serve it with `SOLARA_ASSETS_PROXY=true <env>/bin/solara run preview_app.py --port 8931 --no-open` (background). Screenshot with Playwright at 1200×1900. Check these states:

- three sources listed
- the modal after clicking **Add source**
- the modal with name `radd` (error shown, Save disabled)
- the remove confirmation
- dark mode (`PREVIEW_DARK=1`, after restarting the server)

Stop the server by PID afterwards, in a separate command from the launch. Look at every screenshot. The modal and the confirmation must sit above the list, and the rows must truncate long asset ids.

- [ ] **Step 6: Changelog**

Add under the top unreleased section of `CHANGELOG.md` (match its existing heading style):

```markdown
- Optional files: add up to five custom alert sources (RADD-format Earth Engine assets), each with its own name in the Filter step and in the alert list. Recipes with the previous single custom source still load.
```

- [ ] **Step 7: Commit**

```bash
git add gui/tile/aux_tile.py tests/test_app_wiring.py tests/test_aux_tile_pickers.py CHANGELOG.md
git commit -m "feat(aux): multiple custom alert sources in the Optional files dialog"
```
