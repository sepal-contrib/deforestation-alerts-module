# User-file inventory — the checklist for Tasks 11–12

**Produced by:** Task 10 (Phase 6, `2026-08-31-sepal-app-requirements-update`).
**Status:** authoritative. Where this file and the phase plan disagree, this file
wins — it was built by reading every call site, not by prediction.

## How to read it

Every filesystem touch in `deforestation_alerts/` and `gui/` is classified into
exactly one of:

| Cat | Meaning | Action in Tasks 11–12 |
| --- | --- | --- |
| **(a)** | **User data** — recipes, alert stores, exports, reports, user-supplied inputs. Lives in the user's SEPAL workspace. | Route through `Workspace` (`deforestation_alerts/scripts/workspace.py`). |
| **(b)** | **Packaged read-only asset** shipped with the code (report template, message JSONs, About markdown). | **Keep container-local.** Paths must stay absolute (resolved from `__file__`), never CWD-relative. |
| **(c)** | **Scratch** — intermediates a local-only library must write, then consumed and discarded or uploaded. | Keep local, but move under `Workspace.scratch_file()` / `.scratch_dir()` instead of `module_dir` / `temp/`. |

Many (a) rows are marked **(a) via (c)** — the artifact is user data, but the
library that produces or consumes it (rasterio, geedim, pyarrow, GeoPandas,
python-docx, zipfile, matplotlib) only speaks local paths. Pattern:
`p = ws.scratch_file(".tif")` → let the library write `p` →
`ws.upload_from_scratch(p, ws.results_dir(recipe, name))`; and in reverse
`local = ws.download_to_scratch(remote)` → hand `local` to the library.

**Counts:** (a) 36 · (b) 8 · (c) 19 — 63 classified call sites across 21 files.

---

## (a) User data — migrate to `Workspace` (36)

### The root

| # | File:line | Call | Migration |
| --- | --- | --- | --- |
| a1 | `deforestation_alerts/parameter/directory.py:24` | `module_dir = Path.home() / "module_results" / "deforestation_alerts"` | **The single root cause.** Replaced by `Workspace.root` / `.results_dir(*parts)`. Every `module_dir` importer below dies with it. |
| a2 | `deforestation_alerts/parameter/directory.py:26` | `module_dir.mkdir(parents=True, exist_ok=True)` | Import-time mkdir of a *user* dir → `Workspace.ensure_dir()` (memoized, called off the render path). |
| a3 | `deforestation_alerts/parameter/directory.py:15-22` | `DEFORESTATION_ALERTS_DATA_DIR` override | Decide: keep only as a *test* seam, or delete. It must not become a local-mode branch. `DEFORESTATION_ALERTS_GDAL_TMPDIR` (scratch, category c) stays. |

### `deforestation_alerts/scripts/recipe_helper.py`

| # | File:line | Call | Migration |
| --- | --- | --- | --- |
| a4 | `recipe_helper.py:39-45` | `create_directory()` — `module_dir/<recipe>` + `module_dir/temp` mkdir; returns `os.path.abspath` | → `ws.ensure_dir(recipe_name)`, returns the remote path. The `temp/` half is **(c)** — drop it here, callers use `ws.scratch_dir()`. |
| a5 | `recipe_helper.py:83-84` | `open(file_path,"w")` + `json.dump` (recipe parameters) | → `ws.write_json(path, model_parameters)`. |
| a6 | `recipe_helper.py:111-112` | `open(file_name,"r")` + `json.load` | → `ws.read_json(path)`. |
| a7 | `recipe_helper.py:169-170` | `open(file_name,"r")` + `json.load` (btn-state variant) | → `ws.read_json(path)`. |
| a8 | `recipe_helper.py:337-349` | `update_saved_dictionary` read+rewrite JSON | → `ws.read_json` / `ws.write_json`. Also swallows `FileNotFoundError`; keep the guard but on `pysepal_api.NotFound`. |
| a9 | `recipe_helper.py:223` | `pd.read_csv(csv_file)` (legacy alert store) | **(a) via (c)** — `ws.download_to_scratch()` then read. *Not matched by the plan's grep pattern.* |
| a10 | `recipe_helper.py:251` | `out.to_csv(path)` | **(a) via (c)**. |
| a11 | `recipe_helper.py:296` | `out.to_parquet(path)` (canonical alert store) | **(a) via (c)** — pyarrow needs a local file. |
| a12 | `recipe_helper.py:302` | `gpd.read_parquet(path)` | **(a) via (c)** — `download_to_scratch`. |
| a13 | `recipe_helper.py:316,319` | `os.path.exists(parquet/csv)` in `load_alert_store` | → `ws.exists(...)`. |

### `deforestation_alerts/scripts/alert_export.py`

| # | File:line | Call | Migration |
| --- | --- | --- | --- |
| a14 | `alert_export.py:36` | `save_gdf_to_csv(gdf, path, ...)` | **(a) via (c)** (inherits a10). |
| a15 | `alert_export.py:47` | `out.to_file(path, driver=GPKG/GeoJSON)` | **(a) via (c)** — GDAL writes locally; GPKG may emit sidecars, so use `ws.scratch_dir()`. |
| a16 | `alert_export.py:32-33` | `name = alert_artifact_name(...)`; `path = os.path.join(folder, name)` | `folder` becomes a remote path → `posixpath`/`PurePosixPath` join, or `ws.results_dir(recipe, name)`. |

### `gui/tile/overview_tile.py`

| # | File:line | Call | Migration |
| --- | --- | --- | --- |
| a17 | `overview_tile.py:87` | `export_alerts_gdf(gdf, folder, ...)` with `folder = app_state.recipe_folder_path` | Folder becomes remote; export writes via a14–a16. |
| a18 | `overview_tile.py:88` | `btn.set_url(path=path)` | **Not predicted.** `su.create_download_link` builds `.../jupyter/files/<Path(path).relative_to(Path.home())>`; in the container `Path.home()` is the *container's* home, so the link silently degrades to a dead absolute path. Pass `ws.home_relative(remote_path)`. Applies to every download button below. |

### `gui/tile/analysis/export.py`

| # | File:line | Call | Migration |
| --- | --- | --- | --- |
| a19 | `analysis/export.py:228` | `save_gdf_to_parquet(..., os.path.join(recipe_folder,"alert_db.parquet"))` | **(a) via (c)**. |
| a20 | `analysis/export.py:258-261` | `selected_gdf...to_file(gpkg_name, driver="GPKG")` (`alert_<id>.gpkg`) | **(a) via (c)**. |
| a21 | `analysis/export.py:271-279` | `Path(image_name).exists()` then `download_both_images(...)` | `ws.exists(remote_tif)`; the download itself is (c) then uploaded (see c14). |
| a22 | `analysis/export.py:282-283` | `add_files_to_zip(zipfile_name, image_name, gpkg_name)` | **(a) via (c)** — zip the *staged* local copies in one `ws.scratch_dir()`, then upload. *`zipfile` was not in the plan's grep.* |
| a23 | `analysis/export.py:284` | `zip_path = os.path.abspath(zipfile_name)` → returned for `set_url` | → remote path + `ws.home_relative` (see a18). |
| a24 | `analysis/export.py:287-294` | `output_report_path = recipe_folder + "/alert_<id>.docx"`; report generated into it | **(a) via (c)** — docx built in scratch, uploaded. |
| a25 | `analysis/export.py:289-292` | `aux_model.custom_report_template` (user-uploaded .docx) | **(a)** — a *remote* path once `aux_tile`'s FileInput is remote (a33): `ws.download_to_scratch()` before `Document()`. The **default** template is **(b)** (b1) — do not reclassify. |

### `deforestation_alerts/scripts/report_builder.py`

| # | File:line | Call | Migration |
| --- | --- | --- | --- |
| a26 | `report_builder.py:168` | `gpd.read_file(geodataframe_path)` | Reads the alert GPKG → staged local copy (a20 already produces one; reuse it rather than round-tripping). |
| a27 | `report_builder.py:286` | `doc.save(output_path)` | **(a) via (c)** — write to `ws.scratch_file(".docx")`, upload. |

### `gui/tile/alerts_filter_tile.py`

| # | File:line | Call | Migration |
| --- | --- | --- | --- |
| a28 | `alerts_filter_tile.py:435-448` | `_filtered_alerts_path()` → `recipe_folder or module_dir` / `filtered_alerts_<sources>_<dates>.tif` | **(a) via (c)** — geedim writes locally only; upload after download. The `module_dir` fallback disappears (no recipe → scratch only, or refuse). |
| a29 | `alerts_filter_tile.py:86` | `from deforestation_alerts.parameter import module_dir` | Delete with a1. |
| a30 | `alerts_filter_tile.py:643-648` | `save_gdf_to_parquet(alerts_gdf, recipe_folder/"alert_db.parquet")` | **(a) via (c)**. |

### `deforestation_alerts/scripts/attribute_source.py`

| # | File:line | Call | Migration |
| --- | --- | --- | --- |
| a31 | `attribute_source.py:47-56` | `base = recipe_folder or module_dir`; `attribute_<asset>.tif`; `download_ee_image` then `_set_band_descriptions` (rasterio `r+`) | **Boundary case / decision needed.** Nothing ever re-reads this file (every call re-downloads), so it is functionally **(c)**. It is written into the user's recipe folder today, so a user may expect it there. Recommendation: build it in `ws.scratch_dir()`, and upload only if Task 11 decides the artifact is a deliverable. Either way `module_dir` (line 11) goes. |
| a32 | `attribute_source.py:31` | `config["kind"] == "local"` → `config["source"]` returned as-is | A user-picked raster path from `aux_tile`'s FileInput → becomes remote (a33): `ws.download_to_scratch()` before `attribute_alerts`. |

### Widgets that browse or link to the filesystem

| # | File:line | Call | Migration |
| --- | --- | --- | --- |
| a33 | `gui/tile/aux_tile.py:57` and `:192` | `sw.inputs.FileInput(extensions=[".tif",".tiff"])` / `([".docx"])` | **Not predicted.** A bare `FileInput` browses the *container's* filesystem. `pysepal.sepalwidgets.file_input.FileInput(sepal_client=..., initial_folder=..., root=...)` lists remotely (`get_remote_files` → `sepal_client.files.list`) — pass the session client. `.v_model` then holds a remote path (see a25/a32). |
| a34 | `gui/tile/recipe_tile.py:128-133` | `sw.FileInput(extensions=[".json"], folder=directory.module_dir)` | Same fix: `sepal_client=` + `initial_folder=ws.root`. Note the kwarg is `initial_folder`, not `folder`. |
| a35 | `gui/tile/recipe_tile.py:235-242` | `os.path.exists(file_name)`, `open(file_name)` + `json.load` | → `ws.exists` / `ws.read_json`. |
| a36 | `gui/widget/custom_sw.py:83-105` | `RecipeBtn.zip_set_url`: `module_dir/<recipe>`, `.exists()`, `.iterdir()`, `zipfile.ZipFile(dir/<name>.zip,"w")`, `download_button.set_url(zip_filename)` | Whole method: `ws.exists`/`ws.list` for the guard, download the two members to one `ws.scratch_dir()`, zip there, `ws.upload_from_scratch(...)`, then `set_url(ws.home_relative(remote_zip))`. Line 20 `from ...parameter import module_dir` dies with a1. |

---

## (b) Packaged read-only assets — keep container-local (8)

These ship inside the image and are read, never written. Task 4 deliberately
made several absolute (resolved from `__file__`, not the CWD) — **do not
reclassify or "migrate" them.**

| # | File:line | Asset | Note |
| --- | --- | --- | --- |
| b1 | `gui/tile/analysis/export.py:112-114` | `data/report_template.docx` via `Path(__file__).resolve().parents[3]` | Task 4 made this absolute on purpose. Stays local. Only the *custom* template (a25) is user data. |
| b2 | `gui/i18n.py:20` | `MESSAGES_DIR = .../deforestation_alerts/message` | Stays local. |
| b3 | `gui/i18n.py:66` | `MESSAGES_DIR.glob("[!._]*")` (locale discovery) | Stays local. |
| b4 | `gui/widget/custom_sw.py:321-322` | `folder.glob("*.json")` + `read_text` (translator merge) | Reads the packaged message JSONs. Stays local. |
| b5 | `gui/widget/custom_sw.py:354` | `folder.glob("**/*")` over `.py`/`.ipynb` (`key_use`) | Dev-only translator lint over the *source tree*. Stays local. |
| b6 | `gui/widget/custom_sw.py:374` | `f.read_text(...)` in the same helper | Stays local. |
| b7 | `gui/tile/about_tile.py:12` | `_ABOUT_DIR = parents[2]/docs/about` | Stays local (already absolute). |
| b8 | `gui/tile/about_tile.py:22` | `about_path.read_text()` | Stays local. |

---

## (c) Scratch — local, but move under `Workspace.scratch_*` (19)

Legitimate container-local writes. The rule: **never** `module_dir`, `temp/`, or
the CWD; always `gdal_env.scratch_dir()` (directly, or via `Workspace`).

| # | File:line | Call | Note |
| --- | --- | --- | --- |
| c1 | `deforestation_alerts/gdal_env.py:36-39` | `d.mkdir` + write probe + `unlink` | The scratch machinery itself. Unchanged. |
| c2 | `gdal_env.py:56` | `tempfile.gettempdir()` candidate | Unchanged. |
| c3 | `gdal_env.py:65-71` | last-resort fallback `Path.home()/module_results/deforestation_alerts/.gdal_tmp` | **Keep, but note:** this is an *independent copy* of the results-root arithmetic that survives a1's deletion. In the container it resolves to a container path — correct for scratch, wrong if anyone mistakes it for the user root. Add a comment when a1 lands so nobody re-couples them. |
| c4 | `report_builder.py:192,223` | `folder_temp = os.path.abspath(module_dir/"temp")` | → one `ws.scratch_dir()` per report. Kills the shared-`temp/` collision between concurrent reports. |
| c5 | `report_builder.py:193-247` | `output_image1..6.jpg` under `folder_temp` | Same scratch dir; consumed by `run.add_picture` and discarded. |
| c6 | `report_builder.py:143` | `plt.savefig(output_path, format="jpg")` | Writes c5. |
| c7 | `report_builder.py:66` | `rasterio.open(tiff_path)` | Reads the staged before/after GeoTIFF (c14). |
| c8 | `deforestation_alerts/scripts/vectorize.py:122` | `rasterio.open(raster_path)` | Reads the staged filtered-alert raster (a28). |
| c9 | `deforestation_alerts/scripts/raster_attribute.py:51,99` | `rasterio.open(raster_path)` | Reads a staged attribution raster (a31). |
| c10 | `deforestation_alerts/scripts/attribute_source.py:25` | `rasterio.open(tif_path,"r+")` | Stamps band names on the staged file *before* any upload. |
| c11 | `deforestation_alerts/scripts/cnn_worker.py:49-50` | `os.path.exists` + `os.remove(output_path)` | Local prediction raster. |
| c12 | `cnn_worker.py:53,63,72` | `rasterio.open` read/write of the prediction | Local, consumed by the vectorizer. |
| c13 | `deforestation_alerts/scripts/mosaics_helper.py:248,315` | `rasterio.open(raster_path)` | Local reads. |
| c14 | `mosaics_helper.py:205-231` | `download_both_images` → `os.path.exists(image_name)` + `download_ee_image` | Writes the before/after composite locally. The *artifact* is (a) (`alert_<id><dates>.tif`, cached in the recipe folder and reused by export + CNN), so: stage in scratch, upload, and let `ws.exists` answer the cache check. |
| c15 | `mosaics_helper.py:458,472` | `rasterio.open` read/write in `save_prediction` | Local. |
| c16 | `mosaics_helper.py:527-533` | `zipfile.ZipFile(zip_filename,"w")` (`add_files_to_zip`) | Zips *local* members; the zip is (a) (a22). |
| c17 | `deforestation_alerts/scripts/raster_export.py:34+` | `download_ee_image(image, filename, ...)` | geedim writes local files only — the reason `download_to_scratch`/`upload_from_scratch` exist. Every (a)-raster path funnels through here. |
| c18 | `gui/tile/analysis/edit_tools.py:158-160` | `hf_hub_download(repo_id=..., local_dir="/tmp")` | **Not predicted.** CNN weights cache: container-local and correct, but hard-codes `/tmp` — move to `gdal_env.scratch_dir()` so the one writable-dir policy holds everywhere. |
| c19 | `gui/tile/analysis/paths.py:12-25` | `alert_image_name(recipe_folder, ...)` | Pure string building; becomes a remote path builder once `recipe_folder` is remote. No IO. |

---

## Not filesystem access (grep noise, listed so nobody re-checks)

`aoi_date_model.py:54-55`, `app_model.py:38-39`, `aux_model.py:63-64` — the
`# with open(file_path...)` lines are **commented-out dead code**; the models'
real persistence is `export_dictionary()`/`import_from_dictionary()` into the
recipe JSON (a5–a7), so the "persisted settings" the plan predicted are already
covered there and need no separate migration. `alert_filter_helper.py:73-74`,
`aoi_helper.py:*`, `alerts_filter_tile.py:280` — `ee.Image.rename()`.
`gui/widget/help.py:34,53` — `on_click=`. `solara_threads.py:86`,
`project_writers.py:4` — docstrings.

---

## Cross-cutting findings the phase plan did not predict

1. **Download links break silently.** `sw.DownloadBtn.set_url` /
   `custom_sw.ShareBtn.set_url` → `su.create_download_link` → `Path(path).relative_to(Path.home())`.
   In the container that raises and falls back to the raw absolute path, producing
   a 404 link with no error. Every download button must be handed
   `ws.home_relative(remote_path)` (a18, a23, a36).
2. **`FileInput` browses the wrong filesystem** (a33, a34). pysepal's FileInput
   already supports `sepal_client=`; without it the user sees the container's
   disk. This is a UI change, not just an IO change — budget for it.
3. **Reads the plan's grep pattern misses**: `pd.read_csv` (a9), `rasterio.open`
   (all of c7–c15), `zipfile` (a22, a36, c16), `hf_hub_download` (c18),
   `os.path.exists` probes (a13, a21, a35, a36). Anyone re-running the plan's
   one-liner will under-count; use this file instead.
4. **`gdal_env.py` keeps its own copy of the results-root arithmetic** (c3), so
   deleting `parameter/directory.py` will not break it — and must not be
   "fixed" by pointing it back at the user workspace.
5. **`attribute_<asset>.tif` is write-only** (a31): a decision, not a mechanical
   port.
6. **The shared `temp/` folder** created by `create_directory` (a4) is used only
   by `report_builder` (c4) — two concurrent reports overwrite each other's
   `output_image1.jpg` today. `ws.scratch_dir()` fixes that for free.
