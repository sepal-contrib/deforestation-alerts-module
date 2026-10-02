# SEPAL gate (spec §13, §14)

Run this in a SEPAL sandbox (test.sepal.io or sepal.io) with the branch
`feat/alert-monitoring-slices2-6` checked out and the module's environment
(`sepal_environment.yml`, kernel `venv-deforestation-alerts-module`). It has two
parts: the app in a real browser through SEPAL's proxy, and one national-scale
measurement. Record the results at the end of this file and in the slice-1
plan's "Slice 1 measurement" section
(`docs/superpowers/plans/2026-09-30-slice1-foundation.md`, in the main
checkout; the plans are not committed on this branch).

## 1. The app through `/api/sandbox/jupyter/proxy/<port>`

1. Open `ui.ipynb` with Voila from the SEPAL apps page (or the sandbox's Jupyter),
   and keep the browser's developer tools open on the Network tab.
2. Create a project on a small AOI (one district), add GLAD-L, create a period
   of one recent month, then in Download & harmonize: the grid's ⌖ button (the AOI's UTM
   zone) at 30 m, **Save grid**, **Run all**.
3. Show the harmonized GLAD-L row on the map. Expected: tile requests to
   `/api/sandbox/jupyter/proxy/<port>/api/tiles/...` answer **200** and the
   raster draws, with its legend.
4. Integrate & vectorize → the set opens in Analysis. The Original Alert layer
   on the two maps answers **200** for its tiles.
5. Overview: the set's PMTiles requests (`.../alerts.pmtiles`) answer **206**
   and the polygons draw; the legend lists the four review statuses.
6. Switch the language to Español and to Français: every step reads translated,
   then back to English.

Pass: every request above answers as written, no error toast, no traceback in
the Voila log. Tiles whose URL starts with `blob:` mean the jupyter-loopback
comm bridge carried them instead of the proxy (`gui/scripts/tile_proxy.py`
switches it off when `LOCALTILESERVER_CLIENT_PREFIX` is set): that is a fail,
note the variable's value.

## 2. The national 10 m run (the user's to execute)

```bash
python scripts/measure_prepare.py --level 0 --admin 194 --start 2026-01-01 --end 2026-01-31 --res 10
```
(Paraguay, GAUL-2015 level 0 code 194; files under
`<DATA_DIR>/_measure`; it needs about 7 GB free per Download & harmonize row
and about 13 GB for Integrate.) The script first checks the AOI's coverage
(one Earth Engine request, printed per source) and skips a source the runner
refuses ("RADD: skipped, not_covered" where RADD does not reach), as Run all
does. Copy the printed coverage, grid size, per-source wall time, raw and
harmonized sizes, harmonize-phase peak RSS, whole-run peak RSS and the
Integrate line. Delete `<DATA_DIR>/_measure` afterwards.

If the peak memory approaches the instance's memory, the levers, cheapest
first: `DEFORESTATION_ALERTS_NUM_THREADS=1` (alignment peaks grow with the
worker count); a coarser resolution; the vectorizer's int64 date band (slice-1
Task 7 deferral); the TensorFlow import the measurement process pulls in through
`convert_to_geopandas` (about 600 MB; the app imports it anyway).

## Results

| Date | Instance | Part 1 (pass/fail, notes) | Grid (px) | Time | Sizes | Peak RSS |
|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |
