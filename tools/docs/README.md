# Documentation tools

- `check_docs.py`: checks the user guide under `doc/` (links, images, translation drift, labels).
- `demo_project.py`: builds the demo project the guide's screenshots are taken from.
- `capture.py`: takes the guide's screenshots from a copy of that demo.
- `server_hooks/`: an opt-in start-up hook for the demo build's app server (see below).

`tests/test_docs.py::test_repo_doc_tree` runs the checker on the whole guide, labels included: every
bold label in `doc/<lang>.rst` must be the catalogue value (`deforestation_alerts/message/`) in all four
languages. So a change to a message string that the guide quotes must update the guide, in every
language, in the same change, or the test fails.

## The guide's format

The guide is one reStructuredText article per language, `doc/en.rst`, `doc/es.rst`, `doc/fr.rst` and
`doc/pt.rst`, written in [SEPALSTYLE](https://docs.sepal.io/en/latest/team/style_guide.html), the
style guide of SEPAL documentation. It is laid out for SEPAL's documentation build
([openforis/sepal-doc](https://github.com/openforis/sepal-doc)): its `_script/modules.py` downloads
the single file listed for the module in `docs/source/_data/modules/en.json` into `modules/dwn/`.
The app's entry there is `alert_module`, the page
[docs.sepal.io/en/latest/modules/dwn/alert_module.html](https://docs.sepal.io/en/latest/modules/dwn/alert_module.html)
that the app's rail **Documentation** link opens (`DOCS_URL` in `gui/solara_app.py`). Its `url` must
be `https://raw.githubusercontent.com/sepal-contrib/deforestation-alerts-module/release/doc/en.rst`:
SEPAL deploys the app from `release`, so the guide and its images are read from `release` too, and
a change reaches docs.sepal.io only once it is on `release` and sepal-doc has rebuilt. Hence:

- one file per language, no other pages: links between sections are explicit targets
  (`.. _defo-period-lookback:`) and references (`` `look-back <defo-period-lookback_>`_ ``), prefixed
  `defo-` because Sphinx labels are global across the whole SEPAL documentation;
- images are absolute URLs under `check_docs.RAW_BASE`
  (`https://raw.githubusercontent.com/sepal-contrib/deforestation-alerts-module/release/doc/`), the
  screenshots of each language under `doc/<lang>/img/`, the animations under `doc/anim/`;
- only standard docutils directives (`figure`, `list-table`, `note`, `tip`, `important`,
  `attention`), so the articles also render on GitHub; headings use `= - ^ " # +` in that order.

The translations are separate articles, readable on GitHub. docs.sepal.io translates with Sphinx
gettext instead: sepal-doc's `modules.py` fetches one `.po` catalogue per language from the `locale`
URL of the module's `en.json` entry (`.../release/doc/_locale`), at
`<locale>/<lang>/LC_MESSAGES/en.po`, into `docs/source/_locale/<lang>/LC_MESSAGES/modules/dwn/alert_module.po`.
`tools/docs/make_po.py` derives those catalogues from the articles, pairing each English message
with the translated one at the same position, so **after editing any article, regenerate them**:

    uvx --with sphinx --with polib python -m tools.docs.make_po

`tests/test_docs.py` fails when they are out of step (when Sphinx and polib are installed). A
translated paragraph with no English source (a note about that language only) follows a
`.. translation-only` comment. gettext keeps one translation per English message, so an English
sentence that two places translate differently (gender agreement, two UI labels) shows the first
translation on docs.sepal.io; `make_po` lists those. docs.sepal.io serves English, Spanish and French
(Portuguese once SEPAL adds it), and shows the English screenshots on every language.

A local check of the SEPAL build: copy `doc/en.rst` to `<tmp>/src/modules/dwn/deforestation_alerts.rst`
next to a minimal `conf.py` and an `index.rst` whose toctree lists it, then
`uvx --from sphinx sphinx-build -W -n -b html <tmp>/src <tmp>/out`. When docutils is installed,
`check_docs` also reports every docutils warning.

## Keeping the guide in step with main

`doc/SOURCE_COMMIT` holds the main commit the guide (text and screenshots) describes. To see what
changed in the app since, and so what the guide may need:

```
git log --oneline $(cat doc/SOURCE_COMMIT)..main
git diff --stat $(cat doc/SOURCE_COMMIT) main -- gui deforestation_alerts
git diff $(cat doc/SOURCE_COMMIT) main -- deforestation_alerts/message/en   # changed labels
```

After updating the guide (and retaking the shots it needs), write the new commit there:
`git rev-parse main > doc/SOURCE_COMMIT`. `tests/test_docs.py` checks that it names a commit of the
branch's history.

## The demo project

`madre_de_dios`, a project built through the app's own UI (Playwright on a local Voila server,
with the `tools/walk/walklib.py` helpers), so it is exactly what a user gets.

| Stage | What the build does | How |
| --- | --- | --- |
| Project | Project → New project → `madre_de_dios` | UI |
| AOI | Admin level 1 → Peru → Madre de Dios → Select AOI | UI |
| Alerts | GLAD-L, RADD, GLAD-S2 (predefined) | UI |
| Optional layers | Earth Engine image `COPERNICUS/S2_SR_HARMONIZED/20260927T144729_20260927T145044_T19LDF` (Sentinel-2, 27 Sep 2026, tile 19LDF east of Puerto Maldonado), RGB true colour B4/B3/B2, Min 0, Max 3000, named `Sentinel-2 2026-09-27`, **Show in Analysis** on. The build account has no Planet NICFI access, so no NICFI layer. | UI |
| Period | 2026-09-01 → 2026-09-30, look-back 90 days typed in (a new period starts at 0; search window 2026-06-03 → 2026-09-30) | UI |
| Grid | UTM zone (EPSG:32719), 10 m | UI |
| Mask | `forest_2000`: `UMD/hansen/global_forest_change_2025_v1_13`, band `treecover2000`, Keep | UI |
| Download & harmonize | Run 3 pending (the mask, then the three sources) | UI |
| Integrate & vectorize | Minimum alert size 1 ha, Bigger alerts first, **Limit the number of alerts** on → Maximum number of alerts 48; Include probable alerts left on | UI |
| Reviews | Alerts 1 (True alert, Mining) and 2 (False positive) before the split | UI |
| Evaluation | Review mode **Multiple evaluators** checked (a new project has none) → Split alerts… → Names: Ana, Luis (reviewed alerts stay unassigned) | UI |
| Reviews | Alerts 3 (True alert, Agriculture expansion), 4 (Needs review, Logging), 5 (True alert, Mining) | UI |
| Packages | Generate packages; Ana's package copied to `_fixtures/` | UI |
| Return | Ana's package imported, 3 alerts reviewed in her copy, Generate reviewed copy; the reviewed copy copied to `_fixtures/` and **not** merged into the demo | UI |

The questionnaire is the default one (Loss driver only).

Result of the last build (2026-10-20, from scratch, `--ee-project` set to an unrestricted project):

- Alert set `2026-09-01_2026-09-30__glad-l-glad-s2-radd`: **48 alerts** (the cap), 5 reviewed
  (alerts 1–5: True alert ×3, False positive ×1, Needs review ×1, each true/revision one with a
  Loss driver answer).
- Split between Ana (23) and Luis (23); the 2 alerts reviewed before the split stay unassigned. Both
  packages generated (the split is marked packaged), no return merged.
- `_fixtures/`: `…__e01-ana__review.zip` (Ana's package), `…__e01-ana__reviewed.zip` (her copy,
  with no review of her own: the build's Return stage counted the 5 reviews the package carries as
  hers and stopped at once; fixed since in `new_copy_reviews`, the demo was not rebuilt) and `review_copy/madre-de-dios__e01-ana/` (that copy's project folder).
- Grid 41425 × 38561 px. Timings: AOI to mask about 1.5 min, Download & harmonize 14 min,
  Integrate & vectorize 3.6 min, reviews, split, packages and return about 2 min.
- The project has an active set with reviews, so re-running Integrate & vectorize in a copy shows
  the Replace confirmation.

### Building it

```
PATH=<the defo-alerts env>/bin:$PATH \
  python -m tools.docs.demo_project --data-dir ~/defo_docs_demo --port 8931 \
  [--ee-project my-ee-project] [--ee-max-requests 4]
```

- `--data-dir` is the app's data directory (`DEFORESTATION_ALERTS_DATA_DIR`); keep it under `$HOME`.
  It gets `madre_de_dios/` (the demo), `_fixtures/` (the review package, the reviewed copy and the
  review copy project they were made in) and `_build/` (report, a screenshot per stage, server log).
  Never commit it.
- Every stage is skipped when its output exists, so after a failure just run the same command again.
- Earth Engine runs with your own credentials. Download & harmonize is the long part (hours on a Restricted
  Mode project, see `--ee-project`; 14 min on an unrestricted one).
- `--ee-project NAME`: the Earth Engine (Cloud) project for the build's server only, instead of the
  `project` in `~/.config/earthengine/credentials` (that file is not changed). The builder hands the
  server an `EARTHENGINE_TOKEN` (the same credentials naming NAME, read by eeclient) and
  `DEFO_DOCS_EE_PROJECT`, which `server_hooks/sitecustomize.py` uses to force the project on every
  `ee.Initialize` (pysepal's `init_ee` passes the file's project explicitly). Use it when your default
  project is in *Restricted Mode* (the non-commercial tier's reduced quota): large downloads there
  fail with "Too Many Requests: Exceeded Earth Engine concurrency limit".
- `--ee-max-requests N`: the fallback when no unrestricted project is at hand. Caps geedim's
  concurrent tile requests at N (without the option geedim keeps its own limit, 32) and retries
  those errors, through the same hook. Very
  slow (more than 40 min for GLAD-L alone at N=4), and N=4 still hit the limit now and then.
- Both options put `server_hooks/` on the server's `PYTHONPATH`; only the build's server is affected
  and the app itself never loads it.

### How the screenshots use it

`capture.py` copies the built project into its own data directory and works on the copy, so
the demo is never changed by a capture. Shots that need more than the demo holds (a second period, an
added question, a merged return, the Replace confirmation) make that change in the copy. The
Evaluation shots of the evaluator's side use `_fixtures/`: import the `*__review.zip` package (or open
the parked review copy project), and preview the `*__reviewed.zip` return in **Merge reviewed copy…**.

## Capturing screenshots

```
PATH=<the defo-alerts env>/bin:$PATH \
  python -m tools.docs.capture --demo ~/defo_docs_demo --lang en --port 8931 \
  [--only analysis-screen,analysis-console] [--data-dir ~/defo_docs_capture] [--ee-project my-ee-project] \
  [--raw-cache ~/defo_docs_raw_cache]
```

- Every `.. shot: <id> | …` comment in `doc/en.rst` has a function in `capture.py`'s `SHOTS`
  (a test checks both ways). Each writes `doc/<lang>/img/<id>.png`: a 1440×900 window (taller only
  when a panel or dialog would scroll), light theme, optimized under 600 KB.
- `--demo` is only read. The copy goes to `--data-dir` (default `~/defo_docs_capture`, keep it under
  `$HOME` for the file pickers), which is wiped at the start of the next run; a folder the script did
  not make is refused. The server log, a `report.json` and a traceback plus screenshot per failed
  shot go to `<data-dir>/_capture/`.
- The shots run in `SHOTS` order on one server, each building on the state the earlier ones left:
  the second project `Madre de Dios 2026` (New project, AOI, GLAD-L) for the AOI and Alerts dialogs,
  the evaluator's reviewed copy and its merge, the added question, four more reviews on the demo's
  September set, a later period (New period, 2026-10-01 → `LATER_END`, 2026-10-20, downloaded and
  vectorized: about 20 min, a few with the raw cache below; then 16 of its alerts reviewed) for the
  Overview, and
  last the Split the alerts dialog filled in and cancelled (on the later set, which has no split;
  run alone, the September set's split is removed first). The second period must come after
  September: an earlier one would change the inputs of the September set and mark it out of date.
  All of it goes through the UI. The use case's shots (`use-case-*`) sit among them where their state
  is: Ana's copy after its reviews, a reviewed September alert and the September alerts on the map
  after the guide's alert (so the dots carry review colours), the October Overview and map last. Every Overview
  setup first gives the September set the full run's reviews (`september_state`), so the comparison
  with September reads the same in a full run and with `--only`. `--only` runs the setups its shots
  need, so any subset works. The
  typed values (the added question, its options, the NICFI layer's name) are in `--lang` (`TYPED`).
- The later period's source data come from a cache, so every language gets the same October set
  (its alerts, areas and Overview figures) whatever Earth Engine serves on the day: the sources keep
  publishing and revising alerts, so a new download of the same window differs. The server hook
  `server_hooks/sitecustomize.py` (`DEFO_DOCS_RAW_CACHE`, set from `--raw-cache`, default
  `~/defo_docs_raw_cache`, outside the wiped `--data-dir`) serves each raw download
  (`raw_<source>_<start>_<end>.tif`, named by source and search window) from the cache when it holds
  that file, and adds a new download to it otherwise. Only the download is skipped: harmonizing,
  integrating and the project's records go through the app as usual. The en shots' October set came
  from the raws downloaded on 2026-10-20 (window 2026-07-03 → 2026-10-20); keep those three files to
  reproduce it. An empty cache downloads them afresh, and the October figures may then change, so
  the use case's text must be checked again. The demo's own September raws are in the demo and are
  copied, not downloaded.
- Earth Engine map tiles go through the script (`ee_tile_route`), which asks again when Earth Engine
  answers "Too Many Requests" (the browser would show a gap and the *Imagery failed to load* badge).
  `maps_ready` still fails a shot whose Analysis maps show that badge after a few reloads.
- A failed shot is logged and the run goes on; the exit code is 1 when one failed.
- The labels the script clicks come from the app's catalogue in `--lang`, and every shot checks the
  pipeline header is in that language (`assert_language`), so a language that did not switch stops
  the shot instead of writing an English picture.
- Ports: 8931 by default; the app needs no other.
