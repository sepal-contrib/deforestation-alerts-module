# Shared image timeline — design

**Date:** 2026-10-02
**Branch:** `feat/alert-monitoring-slices2-6`
**Replaces:** the two per-pane image selectors in `gui/tile/analysis/imagery.py`
**Mockups:** Design canvas "Analysis Panel Redesign", board *Proposed — shared timeline, stripes, collapsible months*

## 1. Problem

The Analysis step picks a *before* and an *after* image for each alert from two
independent three-tab selectors. Reviewing a set means doing this hundreds of
times, and the current path is slow, spatially misleading and in two places
wrong.

**It is slow because of an N+1 loop, not because of request count.**
`getIndividualS2` ([imagery_service.py:9](../../../deforestation_alerts/scripts/imagery_service.py))
fetches the date list with one `.getInfo()`, then calls `.getInfo()` again
*inside a Python loop*, once per scene, for the cloud mean. `getIndividualLandsat`
does it twice per scene. `_on_ctx` runs four such windows per alert, so one
alert costs tens of sequential blocking round trips.

**The dates are product-generation dates, not acquisition dates.** S2 groups on
`GENERATION_TIME` and Landsat on `DATE_PRODUCT_GENERATED`. Landsat Collection 2
was reprocessed in bulk, so a scene can be labelled years from when it was
acquired. That date is not only a label: `export.py` writes it into the saved
alert row as `before_img` / `before_img_info`, so it is recorded review
provenance, and it forms the composite GeoTIFF filename via `before_info[4]`.

**The after-window starts too early.** `get_sentinel2_dates` opens the after
window on the *first day of the month* containing `alert_date_max`, so an alert
ending 23 Aug offers a 16 Aug scene as its "after" image — a scene taken before
the clearing finished.

**Equal-width chips discard the calendar.** `create_horizontal_slide_group_v3`
lays scenes out at fixed width, so 20 Feb → 21 Feb reads the same distance as
21 Feb → 7 Mar. The alert window is never drawn although `fecha1`/`fecha2` are
already in `imagery_ctx`.

**Planet is dead code.** Nothing in this branch writes `analysis.before_planet`
or `after_planet` ([state.py:38](../../../gui/tile/analysis/state.py)) — they are
only read. Planet therefore always falls through the "Not available" sentinel and
the visible tab auto-flips to Sentinel-2.

Smaller defects: cloud cover is bucketed to three icons and its real value
appears only after a click; the default pick is silent and asymmetric (`len-1`
for after, `len-3` for before); `_set_all_loading` blanks all six strips on every
alert change; empty and failed fetches collapse into the same "Not available"
state; `"Searching images.."` is the one string in the tile not routed through
`t()`; and the two selectors take roughly 300px from the maps.

## 2. Goals

- One timeline shared by both panes, drawn on a real date axis.
- Default coverage: the alert window ±2 months, with a control at each end that
  widens it by one month.
- Shared Planet / Sentinel-2 / Landsat toggles, each showing its scene count.
- A max-cloud filter over the whole timeline.
- The analyst marks a scene as *before* or *after* from that one control.
- One Earth Engine request for the whole AOI, cached; per-alert selection filtered
  locally, so moving between alerts costs no Earth Engine round trip.

**Non-goals.** No change to the review questionnaire, the drawing tools, the CNN
run, the export, or the side-panel layout. No change to the pan-sharpening or
harmonisation of the chosen image. No migration of already-saved review records.

## 3. Decisions taken

| Decision | Choice |
|---|---|
| Index scope | Per **project**, shared by the project and all its sets |
| Index location | `<project_dir>/imagery/`, beside `masks/` |
| Staleness | Refresh when more than 24h has passed since `refreshed` |
| Background buffer | Widen to the period ±6 months in the background |
| Default view | Alert window ±2 months |
| Dates | Switch to acquisition date; **no migration** of existing records |
| Collection encoding | Coloured stripes in per-collection bands, not distinct shapes |
| Long windows | Months collapse; a collapsed month shows its image count |

## 4. The timeline

One strip spanning both map panes, replacing `imgSelection1`/`imgSelection2` and
both `imgInfo` cards.

### 4.1 Layout

A **before lane** above a **date axis** and an **after lane** below it. Inside
each lane, every collection occupies its own horizontal band:

| Band | Collection | Mark |
|---|---|---|
| top (17px) | Sentinel-2 | 2.5px vertical stripe at its acquisition instant |
| middle (14px) | Landsat | 2.5px vertical stripe at its acquisition instant |
| bottom (10px) | Planet NICFI | a bar spanning the whole month it composites |

Bands mean position encodes the collection, so colour is redundant rather than
load-bearing, and stripes from different collections can never collide. Within a
collection the revisit interval guarantees spacing: at the ±6-month maximum,
fully expanded, Sentinel-2 stripes sit about 13px apart, so the 11px hit areas
do not overlap at any reachable zoom.

Planet is drawn as a bar, not a stripe, because a NICFI basemap is a monthly
composite rather than an instant.

### 4.2 Marking

**The two lanes are the marking gesture.** Clicking a stripe in the upper lane
makes that scene the before image; clicking it in the lower lane makes it the
after image. One click, no mode, no popover, and the lanes map spatially onto the
two map panes.

Every scene is drawn in both lanes. Legality is shown, not enforced by absence: a
GLAD alert window can run several months, and hiding in-window scenes would leave
long-window alerts with no after image at all. The gold band drawn across the
whole strip marks the alert window, so a stripe inside it is visibly a
partial-clearing scene.

### 4.3 Default selection

Replaces the silent `len-1` / `len-3` pick. On each alert:

- **before** = the latest scene with cloud ≤ the current limit whose acquisition
  is at or before `alert_date_min`;
- **after** = the earliest scene with cloud ≤ the current limit whose acquisition
  is at or after `alert_date_max`;
- scenes inside the window are **never** auto-picked;
- Planet is never auto-picked, because a monthly composite is only unambiguous
  when the whole month falls outside the window;
- when either side has no candidate, that side is left unselected and the strip
  says so, pointing at the widen control and the cloud slider.

This is a visible behaviour change: the app will sometimes show no selection
where today it shows a wrong one.

### 4.4 Stripe states

| State | Rendering |
|---|---|
| available | full band height, solid collection colour |
| selected | 4px wide, `primary_contrast` gold, with a `B` / `A` badge at the lane edge |
| over the cloud limit | 45% band height, 30% opacity, not clickable |
| inside the alert window | no per-stripe treatment; the window band carries it |

### 4.5 Collapsible months

Each month label carries a chevron that collapses that month. A collapsed month
becomes a fixed 66px hatched block showing the month, **the number of images it
contains** and a mini stacked bar of its composition by collection. Clicking the
block expands it.

The count is taken **after** the cloud filter, so it reports what expanding would
actually reveal and moves with the slider.

**Default collapse rule:** a month lying entirely inside the alert window starts
collapsed. Every scene in such a month shows partial clearing, so its detail is
never the decision, and the rule scales — a short alert window has no fully
interior month and collapses nothing.

**Collapse state is per-alert.** The months in view move with each alert, so a
remembered set would not mean anything on the next one. The rule recomputes on
each alert; a manual collapse or expand applies to the current alert only.

The axis is therefore piecewise: collapsed months take a fixed width and expanded
months share the remainder in proportion to their day count.

### 4.6 Toolbar and readout

Three collection toggles, each with its in-view scene count and, when the cloud
filter is hiding scenes, a `+N` in `warning` amber. A max-cloud slider with its
value shown as a number. Planet has no cloud property, so it is never
cloud-filtered and its toggle says so.

One readout line replaces both `imgInfo` cards: the before scene, the after
scene, each with source, acquisition date and exact cloud percentage, plus the
number of days between them. A selected in-window scene is flagged there.

### 4.7 Persistence and vertical budget

Max-cloud, active collections and window width persist across alerts — they are
analyst preferences, not per-alert state.

The vertical saving is modest and should not be oversold. Today's two selectors
sit side by side, one under each pane, so they cost the same height as one:
`.custom-map-class2` reserves 300px of chrome. So `calc(100vh - 300px)` becomes
`calc(100vh - 270px)`: the track is 128px (an 11px hit margin, two 45px lanes
and the 27px axis), which with the toolbar, the window label, the readout and
the gaps makes the strip about 203–206px, and the per-column chrome outside the
strip is 64px — 270px in all. That is roughly 30px back to the maps, and a
little more when a collection is toggled off and its band collapses. 270 was
measured twice independently while the strip was built, and the measurement
beat the 250px this section first estimated, so do not "fix" the code back to
250. The legend lives in the existing info dialog rather than on the strip,
which is what keeps the strip to about 205px. The win here is correctness and
speed; the height is a bonus.

### 4.8 Keyboard access

A 2.5px stripe with an 11px hit area is well below the 44px touch target the rest
of the app follows. This is a deliberate density trade for a mouse-driven desktop
tool, and it is compensated, not ignored: Tab moves between lanes, left/right
arrows step scene to scene within the focused lane, Home and End jump to its
ends, and Enter assigns. Arrows move a *cursor*, not the selection: assigning
publishes `before_info`, which redraws a map pane, so an arrow that assigned
would cost one Earth Engine tile request per key press. The cursor is drawn as a
dashed ring and named by `aria-activedescendant` on the lane, so it is visible
and announced without anything being fetched. A click still assigns directly,
being already a deliberate choice of one row. Keyboard access is in scope, not
deferred.

## 5. The scene index

### 5.1 One request

`scene_index.scene_request(aoi, start, end)` returns a single `ee.Dictionary`
holding parallel arrays, so one `.getInfo()` retrieves everything and no Python
loop touches Earth Engine:

```python
fc = s2.map(_row).merge(landsat.map(_row))      # one feature per granule
ee.Dictionary({
    "source": fc.aggregate_array("source"),
    "millis": fc.aggregate_array("millis"),     # acquisition, from system:time_start
    "cloud":  fc.aggregate_array("cloud"),
    "tile":   fc.aggregate_array("tile"),       # MGRS_TILE, or P{path}R{row}
    "key":    fc.aggregate_array("key"),        # see 5.3
    "tile_keys":   distinct.aggregate_array("tile"),
    "tile_coords": distinct.aggregate_array("coords"),
})
```

where `distinct = fc.distinct("tile")` and `coords` is `f.geometry().coordinates()`.

`_row` builds each feature with the granule's **true footprint** as its
geometry: `ee.Feature(img.geometry(), {...})`. That is load-bearing.
`distinct("tile")` therefore yields one true footprint per tile, which is what
makes the exact test in §6 exact; if the feature geometry were the bounds, that
test would only be a bbox test and the Landsat over-selection it exists to catch
would survive it.

A scene row carries **no geometry of its own, not even a bounding box**. Every
granule of a tile has the same footprint — that is what a tile is — so a
per-scene bbox would repeat one of a few hundred footprints across tens of
thousands of rows, and the spatial test would then run per scene instead of per
tile. `tile` is the foreign key into `tiles.parquet`, and §6 tests the footprint
once per tile.

Collections and the `CLOUDY_PIXEL_PERCENTAGE` / `CLOUD_COVER` < 90 pre-filters
stay exactly as `imagery_service` has them today, so the candidate set does not
change — only how its metadata is retrieved.

### 5.2 Files

```
<project_dir>/imagery/
  scenes.parquet   one row per granule
  tiles.parquet    one row per distinct tile: tile, geometry (WKB, EPSG:4326)
  index.json       manifest
```

`scenes.parquet` columns: `source` (str), `date` (datetime64 at **millisecond**
resolution, UTC — `pd.to_datetime(..., unit="ms")`, and the value itself is the
acquisition **day**, not the instant within it, because that is the resolution
the granule metadata is read at), `cloud` (float32), `tile` (str), `key` (str)
— and nothing else. There is deliberately **no per-scene geometry and no per-scene bounding
box**, for the reason §5.1 gives: every granule of a tile shares the tile's
footprint, `tile` is the foreign key into `tiles.parquet`, and §6 tests the
footprint once per tile rather than once per scene. An earlier draft of this
section listed `acquired` and `minx` / `miny` / `maxx` / `maxy`; that
contradicted §5.1 and the implemented frame follows §5.1.

`index.json`:

```json
{ "version": 1,
  "aoi_digest": "...",
  "refreshed": "2026-10-02T09:14:00Z",
  "covered": {"start": "2026-01-01", "end": "2026-10-02"},
  "counts": {"Sentinel 2": 412, "Landsat": 96, "Planet NICFI": 0},
  "chunks": [{"start": "...", "end": "...", "fetched_at": "...", "ok": true}],
  "planet": {"region": "americas", "access": true} }
```

Both parquet files are written through the atomic pattern the project already
uses — `local_files.write_into` to a `.part` beside the destination, then
`os.replace` — as `alert_store.save_gdf_to_parquet` does. A crash mid-write must
not leave a half-written index that the next session reads as authoritative.

`source` keeps the exact strings the rest of the app already switches on —
`"Sentinel 2"`, `"Landsat"`, `"Planet NICFI"` — because `_scale_for_source` in
`mosaics_helper` and `before_info[0]` depend on them. The *display* label
"Sentinel-2" is a UI string only.

### 5.3 The `key` column

The chosen image is rebuilt from `key`, whose meaning is per source:

| Source | `key` | Why |
|---|---|---|
| Sentinel 2 | granule asset id | the per-date mosaic is built from exactly these ids |
| Landsat | `LANDSAT_SCENE_ID` | the existing path needs it to pull the matching `T1_TOA` scene for SFIM pan-sharpening |
| Planet NICFI | NICFI asset id | `ee.Image(key)` as today |

### 5.4 Planet

NICFI is a monthly mosaic per region, with no cloud property and continental
coverage. One small request aggregates the available months for the region
resolved by `vis_layers.nicfi_region(aoi.bounds)`; the region and the access
flag land in `index.json` under `planet`.

Each available month still becomes **one scene row**, dated to the first of the
month, with `cloud` as NaN and `tile` as the empty string. It has to: the
timeline's lanes, chips, counts and selection all read scene rows, so a Planet
basemap that was not a row could not be drawn, counted or chosen without a
second code path beside every one of them. The empty `tile` is what says
"AOI-wide mosaic, no footprint to test", and §6 reads it as covering every
alert; the NaN `cloud` is what keeps a basemap from ever being hidden by the
cloud limit, which has no figure to compare it against. A Planet row is drawn as
a bar spanning its whole month rather than a stripe, per §4.1. When the collection is inaccessible
— NICFI requires granted access — `planet.access` is false and the Planet toggle
renders disabled carrying the existing `steps.layers.err_nicfi` message. This
replaces today's silent "Not available".

### 5.5 Refresh policy

- **First build.** Foreground fetch of the active set's period ±2 months,
  clamped to today. This is the only blocking wait.
- **Background widening.** Once the foreground result lands, a background task
  extends coverage to the period ±6 months in one-month chunks, appending to
  `scenes.parquet` and updating `chunks` as each lands. It carries **no progress
  surface**: an earlier draft of this section routed it through
  `notify_bridge.tracked_job`, which turned out to be unimplementable where the
  widener runs. `tracked_job` must be entered on the thread executing the body
  and `forward_logs_to` keys on `threading.get_ident()`, while the widener is a
  Solara `use_task` sharing its thread with every other component's render work
  — measured identical idents, and with the app logger at INFO (which
  `gui/solara_app.py` sets) an unrelated component's render-loop line was
  delivered into the widener's tracker. Giving it a pill would mean first moving
  it onto its own worker thread via `spawn_in_context`, which is a design change
  rather than a progress indicator. The widen is silent by design; its result is
  visible as the strip gaining months.
- **Staleness.** On load, if `now - refreshed > 24h`, re-fetch only the tail
  (`covered.end - 10 days` → today) and merge, deduplicating on
  `(source, key)`. Only the recent edge of an archive gains scenes.
- **Rebuild.** A full rebuild happens only when `aoi_digest` does not match the
  project's AOI. In practice `Project.locked` freezes the AOI once a set exists,
  so this is a safety net.
- **Widen control.** A request inside `covered` is served from the cache
  instantly. Outside it, one foreground single-month fetch. With the ±6-month
  buffer the control has four months of headroom on each side of the default
  view before that can happen.

### 5.6 Where it fires

`SceneIndexLoader`, a Solara leaf in `gui/scripts/scene_cache.py` mounted beside
`RemoteFactsLoader` in `solara_app.py`. It follows that module's established
shape: `use_task(prefer_threaded=False)` over `gee_interface.get_info_async`,
dependencies that no worker publish mutates, and results applied only to the
project and AOI they were fetched for. Firing at project level rather than on
Analysis entry means the index is usually warm before the analyst arrives.

## 6. Local per-alert query

`scene_index.scenes_for(index, bbox, start, end, *, max_cloud, sources)`, pure
pandas and numpy, no Earth Engine:

1. **which tiles cover this alert** — `overlapping_tiles(tiles, bbox)`, two
   steps over `tiles.parquet` only: the GeoPandas spatial index narrows by
   bounding box, then `intersects` settles it against the **true footprint**. A
   bbox over-selects, most visibly for Landsat, whose footprints are rotated
   parallelograms; without the second step the timeline would offer scenes that
   render empty over the alert. A few hundred rows, once per alert.
2. **select the scenes of those tiles** — a vectorised `isin` on the `tile`
   column, plus the date window. Microseconds over tens of thousands of rows.
   A row whose `tile` is empty is an AOI-wide mosaic (a Planet basemap) and
   covers every alert.
3. **group** to one row per `(source, acquisition date)`, with `cloud` as the
   **mean over the granules overlapping this alert** and `n` as how many there
   are. Their `key` values are not carried on the row: the chosen row's granule
   keys are looked up from the same frame when it is assigned, since only one
   row per lane is ever rebuilt and materialising a tuple per row would cost
   every row for the two that are used. The mean reproduces today's
   `aggregate_mean` semantics exactly —
   `CLOUDY_PIXEL_PERCENTAGE` has always been a whole-granule statistic, and
   `filterBounds` only ever chose *which* granules — so moving the query AOI-wide
   costs no precision.
4. **per-month counts** for the collapsed blocks, from the same frame.

Moving between alerts therefore performs no Earth Engine call at all.

## 7. Rebuilding the chosen image

`image_slider_map_callback` keeps its structure, including the Landsat SR + TOA
SFIM pan-sharpening and `harmonizeL8ToS2_scaled`, and the Planet and Sentinel-2
visualisation dictionaries. Only how the image is identified changes:

- **Sentinel 2:** `ee.ImageCollection([ee.Image(k) for k in keys]).mosaic().clip(geom)`,
  replacing the `ee.Filter.eq("GENERATION_TIME", image_id)` filter. Exact, and it
  removes a server-side filter pass.
- **Landsat:** unchanged — filter `T1_L2` and `T1_TOA` on `LANDSAT_SCENE_ID`.
  One robustness fix: when the TOA match is missing the builder falls back to the
  unsharpened SR image instead of raising, since `.first()` returning `None`
  currently fails the whole selection.
- **Planet NICFI:** `ee.Image(key).clip(geom)`, unchanged.

Nothing is evaluated until the map layer renders, so selecting a scene stays one
tile request and no metadata round trip.

### 7.1 The `before_info` contract is preserved

`analysis.before_info` / `after_info` keep their five-element shape, so
`paths.alert_image_name`, `export.py` and `edit_tools.py` need no change:

| Index | Meaning | Change |
|---|---|---|
| 0 | source string | none |
| 1 | millis | **acquisition** instead of generation |
| 2 | cloud, formatted `"%.2f"` | none |
| 3 | `f"{source} {YYYY-MM-DD} "` | derived from acquisition |
| 4 | `strftime("_%d%m%y")` | derived from acquisition |

Because `[4]` feeds the composite GeoTIFF filename, a re-opened alert resolves to
a new name and re-downloads once. Already-saved rows keep the strings they were
saved with; this is the agreed no-migration choice.

## 8. Module boundaries

**New**

| Module | Responsibility | Depends on |
|---|---|---|
| `deforestation_alerts/scripts/scene_index.py` | the manifest model, the two request builders, array→DataFrame parsing | `ee`, pandas, geopandas |
| `deforestation_alerts/scripts/scene_query.py` | `scenes_for`, month layout, default-pick, collapse and staleness rules | pandas, shapely — **never imports `ee`** |
| `gui/scripts/scene_cache.py` | read/write `imagery/`, staleness decision, chunked background widening, `SceneIndexLoader`, the index reactive | `scene_index`, solara, `gee_interface` |
| `gui/tile/analysis/timeline.py` | the raw-widget strip: bands, stripes, collapse, toolbar, readout, keyboard | `scene_index`, ipyvuetify |

The split between `scene_index` and `scene_query` is on the `ee` import, not on
subject matter. The test suite runs offline by default and *fails* any test that
reaches Earth Engine without the `ee` marker (`tests/conftest.py`), so every rule
that can be expressed over plain data must live where no `ee` import can drag a
live call into the offline tier. `scene_query` is therefore pure pandas, numpy
and shapely over DataFrames, and `timeline.py` holds no date arithmetic at all.

**Changed:** `analysis_tile.py` (`imagery_ctx` payload, the controls column, the
map-height CSS), `widgets.py` (the strip replaces the two selectors and the two
info cards), `export.py` and `state.py` (untouched contracts, but
`before_planet` / `after_planet` are removed), `solara_app.py` (mount the loader),
`deforestation_alerts/project/paths.py` (the `imagery/` paths), the `en` and `es`
locale files.

**Retired:** `create_horizontal_slide_group_v3`, `_set_all_loading`, `_show_tab`,
`_build_image_selector`, `_normalize_source_data`, `image_slider_info_callback`,
`widgets.imgSelection1` / `imgSelection2`, `widgets.show_image_collection`,
`CustomSlideGroup` in `gui/widget/custom_sw.py` (its only caller is `widgets.py`),
`getIndividualS2`, `getIndividualLandsat`, and `get_sentinel2_dates`. Today's
657-line `imagery.py` is split across the three new modules; the file was doing
fetching, layout, selection and map drawing at once.

## 9. Error handling

Today an empty collection and a failed request both become the same "Not
available" sentinel. They separate:

| Condition | Behaviour |
|---|---|
| Earth Engine unreachable on first build | the strip offers a retry; no silent empty state |
| Earth Engine unreachable with a cache present | the cached index is used **and labelled with its age** |
| No scenes in the window | "no images in this window", pointing at the widen control and the cloud slider |
| No legal before or after candidate | that side unselected, with the same guidance |
| Payload too large for one request | month-chunked fetching; `chunks` records per-chunk success, so a partial failure still leaves a usable index |
| NICFI access not granted | Planet toggle disabled with `steps.layers.err_nicfi` |
| Landsat TOA match missing | fall back to the unsharpened SR image |

`"Searching images.."` is finally routed through `gui.i18n.t()`.

## 10. Testing

New `tests/test_scene_index.py`, all Earth Engine free — the parsers take plain
dicts, the filters take DataFrames:

- array payload → DataFrame, including an empty payload and a payload whose
  arrays disagree in length (a malformed response must raise, not truncate);
- tile-overlap correctness, including a tile whose bounding box contains the
  alert but whose true footprint does not, and a row with an empty `tile`, which
  must match every alert;
- per-granule cloud averaging reproduces an `aggregate_mean` worked example;
- the piecewise month layout: a collapsed month at each end and in the middle,
  all months collapsed, none collapsed, and a view starting mid-month;
- the auto-collapse rule across a one-day window, a window spanning exactly one
  calendar month, and a four-month window;
- the default-pick rule: a clean candidate each side, only in-window candidates,
  candidates all above the cloud limit, and a Planet-only window;
- the staleness decision at 23h, 24h and 25h, and on an `aoi_digest` mismatch;
- chunk merge and `(source, key)` deduplication across an overlapping refetch.

Rewritten: the three imagery token tests in `tests/test_analysis_state.py`. The
invariant **inverts** — an index fetch is project-scoped, so one that lands after
an alert change must *not* be dropped, while the per-alert filter is synchronous
and has no race left to test. `alert_token` keeps its role for the CNN run and
the download. `tests/test_set_context.py` references `imgSelection` and needs
updating.

`sepal_environment.yml` gains nothing: pandas, geopandas, shapely and pyarrow are
already runtime dependencies. The existing test that every imported package is
declared will confirm it.

## 11. Risks

- **Payload size on a very large AOI.** A national AOI over ten months is a few
  thousand rows of scalars, comfortably inside one `getInfo`. `aoi_io.py` already
  notes that a several-MB payload is near the practical limit. Month chunking is
  the mitigation and is in the design from the start, not retrofitted.
- **±2 months is still slightly narrower before the alert than today**, whose
  before-window reaches three months back. The widen control and the "no clean
  scene" message cover it.
- **Stripe density at full ±6-month expansion.** Computed above as ~13px between
  Sentinel-2 stripes, so hit areas stay separate; collapsing is the escape valve
  if an archive is denser than the nominal revisit.
- **`distinct("tile")` footprints assume granules of one tile share a footprint.**
  True for S2 MGRS tiles and Landsat WRS-2 path/rows.

## 12. Deferred

- Per-alert cloud computed from the S2 cloud-probability band. Today's figure is
  a whole-granule statistic and the new one matches it; a true per-alert figure
  needs a reduction per alert and would reintroduce the round trip this design
  removes.
- Migrating already-saved `before_img` / `after_img` strings to acquisition
  dates.
- Sharing one index across projects with the same AOI.
- Hover thumbnails on stripes.
