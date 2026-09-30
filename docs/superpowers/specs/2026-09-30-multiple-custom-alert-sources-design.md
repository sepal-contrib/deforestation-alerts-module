# Multiple custom alert sources — design

Date: 2026-09-30
Branch: `feat/workflow-side-panel`
Status: approved in chat; reviewed by Codex 2026-09-30 (slot identity moved
onto the search result, custom entries applied in the search worker, reserved
names cleaned on import, dtype-aware block sizing, EE round-trip test).

## Goal

Let a user register **up to five** user-defined (RADD-shaped) alert sources in
the Optional files dialog instead of exactly one, each with its own name, and
have every one of them show up as a selectable source in the Filter step and
decode to its own name in the resulting alerts.

Success looks like: add three custom sources → run AOI search → all three are
in the Filter source select → analyze a union of two of them plus RADD → the
`alert_sources` column names each contributing source correctly.

## Scope

In scope:

- A list editor for custom sources in the Aux ("Optional files") dialog,
  modelled on spatial_risk's variables list (list + add/edit modal + confirm on
  delete).
- `AuxModel` stores a list; old single-source recipes still load.
- Alerts dictionary, per-source raster build, union, export and decoding handle
  N ≤ 5 custom sources.

Out of scope (explicitly deferred by the user):

- **Local raster files as alert sources.** Sources stay Earth Engine assets
  (IMAGE or IMAGE_COLLECTION). Local rasters are a later feature (options
  recorded: merge locally after the GEE export, or ingest to EE).
- Changing the RADD-shaped input contract (`Alert` 0–3 + `Date` yyddd bands).
- The CCDC source (stays its own single-asset row).
- The Filter map's alert visualisation range (`_ALERT_VIS_PARAMS` min 1/max 2
  already saturates the existing custom 10000/20000 codes; unchanged).

## Constraints discovered in the code

1. **Source encoding is positional decimal digits.** Each source contributes
   `1×` or `2×` its slot value (GLAD-L 10^0, RADD 10^1, GLAD-S2 10^2, CCDC
   10^3, custom 10^4); the Filter union is a per-pixel **sum**
   (`custom_reduce_image_collection`), and `report_builder.get_unique_alerts`
   decodes each digit. Five custom slots (10^4 … 10^8) give a max value of
   222,222,222 — within Int32 (2,147,483,647).
2. **Width of the alert band.** `_from_custom` currently casts to `toInt16()`
   (max 32,767) — too small from slot 1 upward. The band must be Int32 for all
   custom slots, and the union's `alert` band must also be cast to Int32 after
   the sum reducer so the export type is deterministic.
3. **Export dtype.** `download_ee_image` exports `[alert, date]` with no
   explicit dtype; geedim promotes `(int32, float32)` to `float64`, which
   represents every code exactly (float32 would not: 2^24 ≈ 16.7M < 10^8).
   Today's `(int16, float32)` promotes to float32, which is exact for the
   existing codes. Codex's review confirmed no int16/uint8 cast downstream:
   `vectorize.py` keeps the band's dtype and rasterio shapes accept float64.
   Nodata stays 255: each source owns one digit and contributes 0, 1 or 2 to
   it, so every valid code has digits in {0, 1, 2}, and 255 (digit 5) can
   never be one.
4. **Dialog content must be raw ipyvuetify.** MapApp tears down the dialog DOM
   on close; `solara.display`-captured content does not come back on reopen.
   spatial_risk's `ProductTable`/`VariableModal` are Solara components, so the
   look is copied, not the components. A nested `v.Dialog` inside the raw tree
   is fine.
5. **Decoded names are persisted, codes are not.** `convert_to_geopandas`
   writes names into `alert_sources` at vectorize time, so slot numbers only
   need to be stable between the AOI search (raster build) and the Filter
   run — never across recipes.
6. **The Filter step runs on the search result, not on the live settings.**
   `available_alerts_raster_list` is built once per AOI search and is not
   saved in recipes. Aux edits and recipe loads after a search do not rebuild
   it. So the slot → name mapping must travel **with the rasters** (review
   finding), never be re-derived from `alerts_dictionary` or `AuxModel` at
   Analyze time.
7. **The alerts dictionary is replaced wholesale.** The AOI init task sets
   `alerts_dictionary` to the built-ins only, and the search worker rebuilds
   it from scratch when unset (`aoi_tile.py` `_apply_init`, `_search_sync`).
   A custom entry written earlier by an observer can be lost. Custom entries
   are therefore applied **inside the search worker** from a snapshot, not by
   an observer.
8. **Vectorizer block sizing assumes 4 bytes per band**
   (`geo_utils.optimal_block_shape`). A float64 export doubles real memory
   per block, so block sizing must use the raster's actual dtype.

## Data model

`AuxModel`:

- Remove `custom_alert_layer` and `custom_alert_name`.
- Add `custom_alerts = List(Dict()).tag(sync=True)`, default `[]`. Each item:
  `{"name": str, "asset": str}`. Order = display order = slot order.
- `export_dictionary()` writes `"custom_alerts": [...]` (new key only).
- `import_from_dictionary(data)`:
  - if `"custom_alerts"` present → `normalize_sources(...)` of it.
  - else if legacy `"custom_alert_layer"` and `"custom_alert_name"` both
    non-empty → `normalize_sources([{"name": ..., "asset": ...}])`.
  - Both paths go through the same cleaning, so a reserved or duplicate name
    in a recipe is dropped rather than overwriting a built-in source.
  - else leave the current value.
- Mutation is always by **assigning a new list** (`model.custom_alerts =
  [...]`) so the traitlets observer fires; never append in place.

Constants in a small pure module `deforestation_alerts/scripts/custom_sources.py`:

- `MAX_CUSTOM_SOURCES = 5`
- `CUSTOM_BASE_EXPONENT = 4`
- `BUILTIN_SOURCE_NAMES = ("GLAD-L", "RADD", "GLAD-S2", "CCDC")`
- `slot_value(slot) -> int` = `10 ** (4 + slot)`; raises for slot outside 0–4.
- `validate_name(name, existing_names, current=None) -> str | None` — returns
  an error key (`empty`, `duplicate`, `reserved`) or `None`. Comparison is
  case-insensitive and whitespace-trimmed. `current` is the name being edited
  (so saving an unchanged name is not a duplicate).
- `normalize_sources(items) -> list[dict]` — the import-time cleaning: keeps
  dicts with non-empty (trimmed) `name` and `asset`, drops any item for which
  `validate_name` fails against the items kept so far (so empty, duplicate
  and reserved names all go), truncates to 5, logs a warning per drop.
- `custom_names_from_rasters(raster_dict) -> dict[int, str]` —
  `{entry["custom_slot"]: name for name, entry in raster_dict.items() if
  "custom_slot" in entry}`; tolerant of `""`/`[]`/`None` (the model's
  unset values) → `{}`.

## Alerts dictionary (AOI step)

`custom_sources` (pure, so it is testable without Earth Engine — importing
`aoi_helper` runs `init_ee()`) also owns the dictionary helpers, and
`aoi_helper` imports them:

- Replace `aoi_helper.add_custom_alerts_dictionary(md, name, asset, start,
  end)` with `set_custom_alerts_dictionary(md, sources, start, end)`: removes every
  existing `kind == "CUSTOM"` entry, then for `slot, src in enumerate(sources)`
  adds `md[src["name"]] = {"collection": src["asset"], "kind": "CUSTOM",
  "slot": slot, "start_date": start, "end_date": end}`. An empty list just
  clears. `remove_custom_alerts_dictionary` stays (used for clearing).
- `create_available_alert_dictionary`: unchanged (CUSTOM entries always
  available).
- `create_filtered_alert_raster_dictionary`: pass `entry["slot"]` through to
  `get_alerts("CUSTOM", ..., asset, slot=entry.get("slot", 0))`, and store it
  on the result: `result[name] = {"alert_raster": raster, "custom_slot":
  slot}` for custom entries (built-ins unchanged). This is the single source
  of truth for decoding (constraint 6).
- `get_alerts(..., slot=0)` forwards `slot` to `_from_custom`.
- `_from_custom(start, end, aoi, asset, slot=0)`: remap
  `[0,1,2,3] → [0, 0, 2*v, v]` with `v = slot_value(slot)`, `.toInt32()`.

`aoi_tile` (constraint 7):

- `SearchRequest` gains `custom_alerts: tuple` — a snapshot of
  `aux_model.custom_alerts` taken at click time.
- `_search_sync` works on a **copy** of the dictionary (`dict(...)`, or the
  rebuilt basic one), calls `set_custom_alerts_dictionary(copy,
  request.custom_alerts, "2015-01-01", today)`, and returns the copy.
- The completion effect keeps writing `outcome.alerts_dictionary` back only
  when it was rebuilt (as today). Writing the copy back unconditionally would
  drop a CCDC entry the CCDC observer added while the search ran; the model's
  dictionary does not need custom entries, because every search re-applies
  them from the snapshot.
- `_update_dictionary_custom` and the `custom_alert_*` observer are
  **removed**; nothing else writes custom entries. Result: whatever order the
  init task, recipe load and Aux edits land in, the next search uses the
  current list, and the Filter step keeps using the slots of the search it
  came from until the user searches again (same as CCDC edits today).

## Filter step

- `create_filtered_alert_raster`: cast the union's `alert` band to Int32 in
  both branches (single source and multi-source), keeping `date` as is.
  Built-in sources' values are unchanged.
- `AnalyzeRequest.custom_alert_name` → `custom_names: dict[int, str]` built
  at click time with `custom_names_from_rasters(
  alert_filter_model.available_alerts_raster_list)` — the same dict whose
  rasters are unioned, so names always match codes, even after Aux edits or a
  recipe load that happened since the search.
- `optimal_block_shape` takes a `bytes_per_value` argument (default 4);
  `vectorize_alert_raster` passes `np.dtype(src.dtypes[0]).itemsize`.
- `convert_to_geopandas(gdf, custom_names=None)` and
  `get_unique_alerts(values, custom_names=None)`: for each slot 0–4 check
  digit `(value // 10**(4+slot)) % 10 in (1, 2)` and add
  `custom_names.get(slot, f"Custom {slot + 1}")`. The CCDC check stays
  position-bounded. With `custom_names={0: "X"}` the result for any legacy
  value equals today's result with `custom_name="X"`.
- Any other `custom_name=` caller is migrated (grep at implementation time).

## Aux dialog UI

The "Custom alert source" row in the *Extra alert sources* group becomes
**"Custom alert sources"** (description: RADD format, Alert + Date bands, up
to 5). Status icon: set when the list is non-empty.

Right column, built by `gui/widget/custom_source_list.py`
(`build_custom_source_list(aux_model, gee_interface) -> v widget`), raw
ipyvuetify:

- **Rows** (one per source, in slot order): name (bold, ellipsis) + asset id
  (muted, ellipsis, full id in `title`), then icon buttons *Edit*
  (`mdi-pencil`) and *Delete* (`mdi-delete`) with `aria-label`s. Hairline
  divider between rows; the list sits in the same bordered sheet style as the
  groups.
- **Empty state:** a muted line "No custom sources yet."
- **Footer:** `+ Add source` (text button, primary) and a muted "n of 5"
  counter. Add is disabled at 5.
- **Add/Edit modal** (`v.Dialog`, `max_width=480`, persistent): title "Add
  custom source" / "Edit custom source"; `Source name` text field; `Earth
  Engine asset` `sw.inputs.AssetSelect(gee_interface=gee_interface,
  types=["IMAGE","IMAGE_COLLECTION"], ...)`; actions *Cancel* and *Save*.
  Save is disabled until both fields are set and `validate_name` passes; the
  name field shows the error message inline. Save replaces the list with the
  new/edited item (edit keeps the slot position).
- **Delete** opens a small confirm `v.Dialog` ("Remove <name>? It will no
  longer appear in the Filter step.") with *Cancel* / *Remove*.
- Rows re-render from an `aux_model.observe(..., "custom_alerts")` handler, so
  a recipe load updates the list.
- The single `AssetSelect` in the modal keeps
  `test_gee_widgets_get_the_session_interface` meaningful: the test's count of
  `sw.inputs.AssetSelect(` calls in `aux_tile.py` drops from 5 to 4 and a
  matching assertion is added for the new module.

i18n (en / es-ES / fr) in `ui.json` → `aux_tile`: rename
`custom_alerts_title` to the plural wording, update `custom_alerts_desc`, and
add `custom_add`, `custom_count` (`"{n} of {max}"`), `custom_empty`,
`custom_modal_add_title`, `custom_modal_edit_title`, `custom_save`,
`custom_cancel`, `custom_remove`, `custom_remove_confirm` (`"Remove {name}?
It will no longer appear in the Filter step."`), `custom_edit_label`,
`custom_delete_label`, and name errors `custom_name_duplicate`, `custom_name_reserved` (an empty
name only disables Save; no message).

## Error handling

- Invalid names never reach the model (Save disabled + inline message).
- Recipe import cleans input (`normalize_sources`); malformed entries are
  dropped with a `logger.warning`, never raised.
- An asset that fails in EE at AOI/Filter time behaves as today (the existing
  analyze error path surfaces it); no new handling.
- `slot_value` raises `ValueError` for an out-of-range slot — a programming
  error, not user input.

## Testing

Unit (pytest, no EE):

- `custom_sources`: `slot_value`, `validate_name` (empty / duplicate
  case-insensitive / reserved / editing own name OK), `normalize_sources`
  (truncate to 5, drop duplicates, reserved names and incomplete items),
  `custom_names_from_rasters` (mixed dict; unset `""`/`[]`/`None`).
- `aoi_tile._search_sync` (EE calls monkeypatched): custom entries come from
  the request snapshot; a dictionary without custom entries (the init-task
  shape) still yields them; the rebuilt-dictionary path yields them too; the
  input dictionary is not mutated.
- `optimal_block_shape` with `bytes_per_value=8` halves the rows.
- `AuxModel`: export/import round-trip of `custom_alerts`; legacy two-key
  recipe migrates to a one-item list; missing keys leave the value alone.
- `aoi_helper.set_custom_alerts_dictionary`: add 3 → 3 entries with slots
  0–2; rename/delete replaces the set (no stale keys); empty clears; built-in
  entries untouched. (Update `tests/test_custom_alert_source.py`.)
- `get_unique_alerts`: multi-slot value decodes to every name; legacy value
  with `{0: name}` equals today's output; missing name falls back to
  `Custom n`; CCDC + custom slot together decode both.
- UI build test (`AssetSelect` stubbed like `test_aux_tile_pickers`): add via
  modal writes the model; edit keeps position; delete after confirm; Add
  disabled at 5; duplicate/reserved name disables Save; model change
  re-renders rows.
- Update `test_app_wiring.test_gee_widgets_get_the_session_interface` for the
  moved AssetSelect.

EE-backed (skipped without EE, like the existing ones): `_from_custom` with
`slot=2` yields `10^6`/`2·10^6` codes as Int32; and
`test_raster_pipeline_integration.py` is extended with a union carrying a
slot-4 code (`2·10^8 + 20`) through `download_ee_image` +
`vectorize_alert_raster`, asserting the exact code survives and nodata 255
is not read as an alert. This replaces a NumPy-only dtype check, which could
not notice a geedim change.

Visual: screenshot the dialog via the standalone preview page (empty list,
three sources, modal open, duplicate-name error), light and dark.

## Files touched

- `deforestation_alerts/scripts/custom_sources.py` (new)
- `deforestation_alerts/model/aux_model.py`
- `deforestation_alerts/scripts/aoi_helper.py`
- `deforestation_alerts/scripts/report_builder.py` (`get_unique_alerts`)
- `deforestation_alerts/scripts/alert_filter_helper.py` (`convert_to_geopandas`)
- `gui/tile/aoi_tile.py` (`SearchRequest` snapshot, `_search_sync`, effect;
  remove the custom observer + `_update_dictionary_custom`)
- `deforestation_alerts/scripts/geo_utils.py` + `vectorize.py` (block sizing
  by dtype)
- `gui/tile/alerts_filter_tile.py` (Int32 cast, `AnalyzeRequest`, `_finish_analyze_sync`)
- `gui/widget/custom_source_list.py` (new)
- `gui/tile/aux_tile.py` (use the list; bindings)
- `deforestation_alerts/message/{en,es-ES,fr}/ui.json`
- tests: `test_custom_sources.py` (new), `test_custom_source_list.py` (new),
  `test_models.py`, `test_custom_alert_source.py`, `test_app_wiring.py`, and a
  `get_unique_alerts` test file (existing or new).
