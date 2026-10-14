"""build_edit_tools sub-component (Task 9.3 / B2): draw-edit + CNN models.

Architecture layer: view (raw-widget builder, no ``@solara.component``). Ports
the OLD ``AnalysisTile`` ``card04`` interaction as ONE coherent unit, because the
polygon draw/edit controls and the two CNN models share the draw controls: a
model's prediction is *added to the drawing*, so *Add to map* opens the drawing
session itself when the reviewer ran a model without starting one
(``_begin_edition``).

WHY A RAW WIDGET (not a Solara component + ``solara.display``): MapApp renders the
Analysis dialog with ``v-if``, so it DESTROYS the dialog DOM on close and REBUILDS
it on reopen. Content captured via ``solara.display`` lives inside an ``Output``
whose view is NOT recreated on that rebuild, so it vanishes the 2nd time the dialog
is opened. A plain ipyvuetify widget tree is re-viewed from its model on reopen, so
it survives. ``build_edit_tools`` builds the body once and RETURNS it; the
parent hands it to MapApp as dialog content — same pattern as ``context`` /
``review_panel`` and ``recipe_tile.build_recipe_layout``.

What it returns is the *Refine polygon* section's BODY, not a card: the
section (``section.build_section``) owns the card, the title and the summary
badge, and the body keeps that badge reading what is drawn
(``wire_refine_badge``). The body is built once and survives a collapse —
the section only toggles its ``display`` — so the edit session's imperative
``.show()`` / ``.hide()`` / ``.disabled`` state is never rebuilt.

Ported OLD handlers:

* Edit (``toolBarEdition``): ``start_edition_function`` / ``save_edition_function``
  / ``stop_edition_function`` — switch the views' edit mode and seed or clear
  the drawing through ``widgets.panes`` (``set_edit_mode`` / ``set_drawing``;
  *Alert BB* and the *Defo Layer* hide themselves in edit mode), and persist
  the user-drawn polygon (``drawing_geojson``) to ``analysis.defo_layer``.
* CNN (``toolBarDL1`` / ``toolBarDL2``): ``run_dl_model_1`` / ``run_dl_model_2``
  (download both images + ``cnn_worker.apply_dl_model`` with a FILE-PATH model arg),
  ``verify_model*_output`` + ``enable_dl*`` (now ``_sync_dl``: the row shows
  the add/remove pair or the "found nothing" warning, from what the run found),
  ``add_model*_prediction`` / ``remove_model*_prediction`` (rewrite the
  drawing, ``widgets.panes.set_drawing``, from the prediction raster). The OLD
  ``clear_edition_function`` and its button are gone: the button was built and
  wired but never a child of the toolbar, so it has never been on screen.
* ``add_defo_layer`` — ``wire_defo_layer`` subscribes to ``analysis.defo_layer``
  and keeps one "Defo Layer" spec on every view.

Each run's prediction is vectorised by the worker (``vectorise``), not at
*Add* time: the row can then report what the model found
(``prediction_shapes`` / ``found_text``), *Add* is instant, and a prediction
with nothing in it is the ``dl_404`` warning instead of a "Found 0 patches"
row with nothing to add.

Threading (FIXED by the task contract):

* Each CNN model runs button-triggered via ``spawn_in_context`` (CPU/IO-bound;
  never auto-run) — same pattern as ``recipe_tile``'s load/save workers. The worker
  builds the image name, downloads both images, pulls the HF model (idempotent /
  cached), runs ``apply_dl_model`` -> the prediction ``.tif`` path, and then (still on
  the worker thread, in the success path) runs the OLD ``verify_model*_output`` /
  ``enable_dl*`` apply step with that path: stores it in
  ``analysis.prediction_files`` and makes *Add to map* live.
  The run button spins (``.loading``/``.disabled``) for the duration, the run is a
  tracked task in the notification pill (``notify_bridge.tracked_job``; the
  composite download drives its progress ring) and the outcome is a toast. Every
  run failure — download, inference or the verify/apply step — reaches the user as
  the friendly ``model_labels.dl_404`` text. ipywidgets trait
  sets from the worker thread are fine (recipe_tile does exactly this).
* add/remove prediction are main-thread click handlers reading the stored
  prediction path.
* The edit + DL toolbars + buttons are built once so the OLD ``.show()/.hide()/
  .disabled`` imperative calls port unchanged.
* ``wire_defo_layer`` is a ``defo_layer.subscribe`` registered once (it
  replaces the traitlets observer): it fires whenever a new layer is set.

Guards: everything no-ops safely with no alert loaded (smoke time): buttons render
but the CNN/edit click paths are not exercised, and the defo-layer observer is
registered but ``defo_layer`` stays ``None``. EE / raster / file failures are
caught and published as an error toast (or logged) so a failure never breaks render.

Feedback: the CNN run workers publish through ``notify_bridge.tracked_job`` +
``current_notifier``, not the ``use_notifications()`` hook: this module builds raw
ipyvuetify widgets and publishes from daemon worker threads, so there is no
component render to hold a hook. ``spawn_in_context`` carries the kernel context
``current_notifier`` resolves the bus from. ``tracked_job`` turns the worker
thread's app log lines into task milestones and, on a failure, marks the task
FAILED before it publishes the one error toast. The main-thread add-prediction
handler (no task) still uses the ``notify`` global for its error toast.

Props
-----
widgets : AnalysisWidgets
    Shared heavy widgets; the edit buttons switch ``widgets.panes``'s edit mode
    and read/write its drawing (``set_drawing`` / ``drawing_features`` /
    ``drawing_geojson`` / ``on_drawing_edit``), and the CNN reads the
    selected images + grid scratch slots. ``None`` (or a namespace without
    ``panes``) in state-only builds.
app_state : AppState
    Navigation + alerts source of truth (``actual_alert_id``).
analysis : AnalysisState
    ``defo_layer`` (the user-drawn polygon GeoDataFrame), the chosen
    ``before/after_image`` + ``before/after_info`` the CNN downloads,
    ``prediction_files`` (the CNN rasters by model number) and
    ``prediction_shapes`` (what each run found). Every edit the reviewer makes
    here ``touch``es it, so the footer's save-state line reads
    *Unsaved changes on this alert*.
app_tile_model : SetContext
    ``recipe_folder_path`` — the active set's folder the before/after composite
    and the CNN's prediction raster are written to.
set_badge : callable or None
    The section's ``set_badge(text, color=None)`` (already-translated text),
    kept reading what is drawn.
"""

import json
import logging

import ipyvuetify as v
from pysepal import sepalwidgets as sw
from pysepal.solara.notifications import notify

from deforestation_alerts import gdal_env
from deforestation_alerts.scripts.cnn_worker import apply_dl_model
from deforestation_alerts.scripts.gee_progress import geedim_tile_progress
from deforestation_alerts.scripts.mosaics_helper import (
    calculate_total_area,
    convertir_formato3,
    filter_features_by_color,
    geojson_to_geodataframe,
    prediction_patches,
    simplify_and_extract_features,
)
from gui.i18n import format_number, t, tn
from gui.scripts.notify_bridge import (
    current_notifier,
    tile_progress_reporter,
    tracked_job,
)
from gui.scripts.solara_threads import spawn_in_context
from gui.store.project_writers import writer_name, writing
from gui.tile.analysis.pane_layers import geojson_spec
from gui.tile.analysis.paths import stage_alert_image
from gui.tile.analysis.state import area_crs, capture_alert, drawing_changed, min_patch_ha, touch
from gui.tile.analysis.section import TYPE_BODY, TYPE_LABEL

logger = logging.getLogger("SEPALUI.deforestation_alerts.analysis")

# OLD add_defo_layer / Defo Layer style.
_DEFO_LAYER_STYLE = {"color": "orange", "fillOpacity": "0"}
#: The reviewed polygon's spec id on the views.
DEFO_ID = "defo"

# HuggingFace model files (OLD pulled these in the worker startup); idempotent.
_MODEL1_REPO = "joseserafinig/forest_cover_change_cnn_m1"
_MODEL1_FILE = "Model1.h5"
_MODEL2_REPO = "joseserafinig/forest_cover_change_cnn_m2"
_MODEL2_FILE = "Model2.keras"

# CNN prediction probability cutoff (OLD passed 0.20 to verify_raster/raster_to_gdf)
# and polygon simplify tolerance in metres of the project's grid CRS (OLD
# passed 15, in EPSG:3857, to simplify_and_extract_features).
_PRED_THRESHOLD = 0.20
_SIMPLIFY_TOLERANCE = 15

#: the attribute the section body carries its named widgets on
_EDIT_SLOTS = "_defo_edit_slots"


def edit_widgets(body):
    """The section body's named widgets, by name (``start_btn`` / ``save_btn``
    / ``stop_btn``, and per model ``dl{n}_run`` / ``_add`` / ``_remove`` /
    ``_msg`` / ``_found``).

    Both model rows now carry the same *Run* label, so a walk of the tree by
    label can no longer tell model 1's button from model 2's -- same reason
    ``review_panel.verdict_widgets`` exists.
    """
    return getattr(body, _EDIT_SLOTS)


def vectorise(path, crs=None, min_size_ha=0.0):
    """``(features, n_patches, area_ha)`` of a prediction raster.

    Only the patches of at least ``min_size_ha`` count (the alert set's
    minimum size, ``state.min_patch_ha``), measured in ``crs`` (the project's
    grid CRS, ``state.area_crs``): the same rule and the same scale as the
    alerts Integrate made. A prediction whose patches are all smaller found
    nothing. The area is the patches' own, before the simplification.

    Ran at *Add* time before; the worker now does it inside its tracked job, so
    the row can report what was found and *Add* is instant. ``color`` is applied
    by the caller (``convertir_formato3``), which is what distinguishes the two
    models' features on the map.
    """
    patches = prediction_patches(path, _PRED_THRESHOLD, crs, min_size_ha)
    if len(patches) == 0:
        return [], 0, 0.0
    return (simplify_and_extract_features(patches, "geometry", _SIMPLIFY_TOLERANCE),
            int(len(patches)), float(patches["area_ha"].sum()))


def _record_prediction_shapes(analysis, model, n_patches, area_ha):
    """Remember what a run found, replacing the dict in one locked read-and-set
    so two runs finishing together cannot drop each other's numbers. An empty
    prediction records nothing and FORGETS what this model found before: the row
    shows the ``dl_404`` warning, and a stale "Found ..." line under it would
    describe a prediction that is no longer on offer."""
    with analysis._alert_lock:
        shapes = dict(analysis.prediction_shapes.value)
        if n_patches <= 0:
            shapes.pop(model, None)
        else:
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


def wire_refine_badge(analysis, set_badge, crs_of=lambda: None):
    """Keep a section badge reading the drawn polygon: *nothing drawn*, or the
    shape count and the area -- the same ``calculate_total_area`` in the same
    CRS (``crs_of()``, the project's ``state.area_crs``) the save stores as
    ``area_ha``, so the badge and the column can never disagree."""

    def _show(gdf=None):
        # Guarded like ``wire_defo_layer._on_layer``, the other
        # ``defo_layer`` subscriber, and for the same reason: this runs
        # INSIDE ``defo_layer.set()``, so anything raising here raises
        # through ``reset_alert`` and ``view_actual_alert`` -- out of the
        # badge and into navigation. Every production writer holds the
        # GeoDataFrame-or-``None`` invariant ``len`` and
        # ``calculate_total_area`` need (``state.reset_alert`` sets ``None``,
        # ``view_actual_alert`` a ``multipolygon_to_geodataframe``,
        # ``_save_edition`` a ``geojson_to_geodataframe``), so this never
        # fires today; a summary line is not worth betting the queue on it
        # staying that way (Ruling 32).
        try:
            gdf = analysis.defo_layer.value if gdf is None else gdf
            if gdf is None or len(gdf) == 0:
                set_badge(t("analysis_tile.sections.refine_empty"))
                return
            set_badge(t("analysis_tile.sections.refine_summary",
                        shapes=tn("analysis_tile.sections.shapes", len(gdf)),
                        area=format_number(calculate_total_area(gdf, crs_of()), 1)), color="primary")
        except Exception:
            logger.exception("Analysis: the refine badge could not be written")

    # The badge alone. The unsaved flag is NOT wired here: ``view_actual_alert``
    # seeds ``defo_layer`` with the alert's stored polygon when it opens, and a
    # ``touch`` on this subscription would mark every alert unsaved on sight
    # (Ruling 24). It hangs off the reviewer's own edits instead.
    analysis.defo_layer.subscribe(lambda gdf: _show(gdf))
    _show()
    return _show


class _EditionButton(v.Btn, sw.SepalWidget):
    """OLD ``EditionButtons``: small disabled-by-default icon button with .show/.hide."""

    def __init__(self, **kwargs):
        kwargs.setdefault("small", True)
        kwargs.setdefault("class_", "pa-1")
        kwargs.setdefault("disabled", True)
        super().__init__(**kwargs)


def _images_ready(alert) -> bool:
    """Whether the alert's before/after pair is picked AND drawn.

    The same guard as ``export._images_picked``, for the run that stages the
    composite: the infos are published at the click and each pane's ee.Image
    when its rebuild lands, so in between the pair's metadata and its pixels
    are of different scenes (``imagery.wire_panes``).
    """
    return all(v is not None for v in (alert.before_info, alert.after_info,
                                       alert.before_image, alert.after_image))


def _run_cnn_model(
    model_repo,
    model_file,
    suffix,
    widgets,
    analysis,
    app_state,
    app_tile_model,
    *,
    task=None,
    alert=None,
):
    """Body of the OLD ``run_dl_model_*`` (worker side), without widget mutation.

    Gets the before/after composite for the current alert (downloaded once
    into the active set's folder — see ``paths.stage_alert_image``), pulls the
    HF model (cached), runs ``apply_dl_model``, and returns the prediction
    ``.tif`` path. Runs on the daemon worker thread; the widget mutation
    (verify/enable, add/remove) happens separately, never inside this function.

    ``apply_dl_model`` writes the prediction raster next to the composite (same
    stem + ``_prediction<suffix>.tif``), so the composite and its prediction
    both stay in the set folder, on a first run and on a cache hit alike. The
    prediction path outlives this call: it is stashed in
    ``analysis.prediction_files`` and read again every time the user
    adds/removes the prediction overlay.

    ``task`` (the worker's ``TaskTracker``, optional) gets the composite
    download's geedim tile ticks as progress, and is handed back to
    indeterminate before the model pull + inference. A cache hit produces no
    ticks.

    ``alert`` (``state.capture_alert``, taken at the click) is the alert the
    composite is built for: its id, images and grid, read together. Taken
    here when not given.
    """
    from huggingface_hub import hf_hub_download

    alert = alert if alert is not None else capture_alert(analysis, widgets, app_state)

    on_tiles = tile_progress_reporter(task) if task is not None else None
    with geedim_tile_progress(on_tiles):
        image_local = stage_alert_image(
            app_tile_model.recipe_folder_path,
            alert.alert_id,
            alert.before_info,
            alert.after_info,
            alert.before_image,
            alert.after_image,
            alert.grid,
        )
    if task is not None:
        task.set_progress(None)  # model pull + inference: back to indeterminate

    # The CNN weights are a container-local cache, not user data — but they must
    # land in the one directory this app has proven writable, not a hard-coded
    # ``/tmp`` (inventory c18).
    model_path = hf_hub_download(
        repo_id=model_repo, filename=model_file, local_dir=str(gdal_env.scratch_dir())
    )
    return apply_dl_model(str(image_local), model_path, suffix)


def wire_defo_layer(widgets, analysis):
    """Show the reviewed polygon on every view whenever a new one is set (the
    old ``.observe("defo_dl_layer")``): one spec, hidden in edit mode, where
    the drawing shows the polygon instead. Returns the unsubscribe."""

    def _on_layer(gdf):
        panes = getattr(widgets, "panes", None)
        if panes is None:
            return  # no views (a state-only build)
        if gdf is None:
            # No reviewed polygon any more -- an *Apply* of an emptied drawing,
            # or ``reset_alert``. The layer comes off the views: left on, the
            # polygon stays drawn over a review that no longer has one.
            try:
                panes.remove(DEFO_ID)
            except Exception:
                logger.exception("Analysis: removing the Defo Layer failed")
            return
        try:
            panes.add(geojson_spec(DEFO_ID, "all", json.loads(gdf.to_json()), name="Defo Layer",
                                   group="alert", style=_DEFO_LAYER_STYLE, hide_while_editing=True))
        except Exception:
            logger.exception("Analysis: add_defo_layer failed")

    return analysis.defo_layer.subscribe(_on_layer)


def build_edit_tools(
    widgets,
    app_state,
    analysis,
    app_tile_model,
    set_badge=None,
):
    """Build and return the *Refine polygon* section's BODY (OLD ``card04``).

    The section owns the card and the title, so what comes back is a plain
    ``v.Html`` div: the three edit buttons over one row per CNN model.

    ``set_badge`` is the section's own ``set_badge`` (already-translated text
    in, see ``section.build_section``): given one, the body keeps it reading
    what is drawn.

    The before/after composite the CNN runs on is a file of the active set's
    folder (see ``paths.stage_alert_image``).
    """
    # What each run found, by ``(alert token, model)``: the features *Add*
    # puts on the map, vectorised once by the worker. Per build (one session)
    # and cleared on every alert change, so no alert's shapes reach the next.
    features_cache = {}

    # ------------------------------------------------------------------
    # Widgets (created once so the OLD .show()/.hide()/.disabled imperative
    # toggles port unchanged). Bundled in a small namespace so the handlers below
    # can mutate them in place.
    # ------------------------------------------------------------------
    def _build_widgets():
        ns = {}

        # --- the edit buttons (start / apply / stop) ---
        # Labelled and 44px, like the footer's Save buttons: an icon-only button
        # whose meaning lived in a hover tooltip was unreadable on a touch
        # screen, and there is room for the words in the panel.
        def _edit_button(label, **kwargs):
            return v.Btn(
                class_="flex-grow-1", style_="height: 44px; text-transform: none;",
                children=[label], **kwargs,
            )

        # One filled button in the section, the one that commits: *Save
        # drawing*. *Start* and *Stop* are outlined, like the panel's other
        # secondary actions, so the footer's *Save & next* stays the one
        # strong green on screen when no drawing is open.
        ns["start_btn"] = _edit_button(
            t("analysis_tile.edition_labels.start_edit_button"), color="primary", outlined=True)
        ns["save_btn"] = _edit_button(
            t("analysis_tile.edition_labels.apply_edit_button"), color="primary", disabled=True)
        ns["stop_btn"] = _edit_button(
            t("analysis_tile.edition_labels.stop_edit_button"), outlined=True, disabled=True)

        # --- the "discard the drawing?" confirmation of *Stop Drawing* ---
        # Raw, like ``widgets.info_button``'s: the panel never renders through
        # reacton, so the Solara ``ConfirmDialog`` cannot be used here. It
        # sits in the edit row (a dialog draws nothing in place).
        # The dialog is detached to the app root, outside the screen's type
        # rules, so its buttons carry the sentence case inline.
        _dialog_btn = "text-transform: none; letter-spacing: normal; font-size: 14px;"
        ns["stop_keep"] = v.Btn(text=True, style_=_dialog_btn,
                                children=[t("analysis_tile.edition_labels.stop_keep")])
        ns["stop_save"] = v.Btn(outlined=True, color="primary", style_=_dialog_btn,
                                children=[t("analysis_tile.edition_labels.stop_save")])
        ns["stop_discard"] = v.Btn(color="error", depressed=True, style_=_dialog_btn,
                                   children=[t("analysis_tile.edition_labels.stop_discard")])
        ns["stop_dialog"] = v.Dialog(v_model=False, max_width="480px", children=[
            v.Card(children=[
                v.CardTitle(style_="word-break: normal; font-size: 16px; font-weight: 600;",
                            children=[t("analysis_tile.edition_labels.stop_title")]),
                v.CardText(style_="font-size: 14px;",
                           children=[t("analysis_tile.edition_labels.stop_message")]),
                v.CardActions(class_="flex-wrap justify-end pa-4 pt-0",
                              children=[ns["stop_keep"], ns["stop_save"], ns["stop_discard"]]),
            ]),
        ])
        ns["edit_row"] = v.Html(
            tag="div", class_="d-flex", style_="gap: 8px;",
            children=[ns["start_btn"], ns["save_btn"], ns["stop_btn"], ns["stop_dialog"]],
        )

        # --- one row per CNN model: its name + Run, the add/remove pair
        # (shown in edit mode only) and what the run found ---
        for n, model_name in ((1, t("analysis_tile.model_labels.dl_button1")),
                              (2, t("analysis_tile.model_labels.dl_button2"))):
            ns[f"dl{n}_run"] = v.Btn(
                color="primary", small=True, outlined=True,
                children=[t("analysis_tile.model_labels.run")],
            )
            ns[f"dl{n}_add"] = _EditionButton(
                color="primary", outlined=True,
                children=[t("analysis_tile.model_labels.dl_button_add")],
            )
            ns[f"dl{n}_remove"] = _EditionButton(
                color="error", outlined=True,
                children=[t("analysis_tile.model_labels.dl_button_remove")],
            )
            # The one button that keeps its icon and its tooltip: it IS the
            # "found nothing" warning, and it carries no state of its own.
            ns[f"dl{n}_msg"] = _EditionButton(
                color="error", disabled=False,
                children=[v.Icon(color="white", children=["fa-solid fa-triangle-exclamation"])],
            ).hide()
            tt_msg = sw.Tooltip(ns[f"dl{n}_msg"], tooltip=t("analysis_tile.model_labels.dl_404"),
                                top=True, open_delay=100, close_delay=100)
            # Always on screen, unlike OLD's hidden ``toolBarDL{n}``: OLD kept
            # *Run* inside that same toolbar, so a model could not be run
            # outside drawing mode and a gated pair could stand nobody up. With
            # *Run* in the row, the pair is shown from the start and says what
            # it offers through ``disabled`` alone (``_sync_dl``).
            name_span = v.Html(tag="span", class_=TYPE_BODY, style_="flex: 1 1 auto;", children=[model_name])
            ns[f"dl{n}_found"] = v.Html(tag="div", class_=TYPE_LABEL, children=[])
            ns[f"dl{n}_row"] = v.Html(tag="div", class_="mt-3", children=[
                v.Html(tag="div", class_="d-flex align-center flex-wrap", style_="gap: 8px;", children=[
                    sw.Tooltip(name_span, tooltip=t(f"analysis_tile.model_labels.dl_hint{n}"),
                               top=True, open_delay=100),
                    ns[f"dl{n}_run"], ns[f"dl{n}_add"], ns[f"dl{n}_remove"], tt_msg,
                ]),
                ns[f"dl{n}_found"],
            ])

        return ns

    ns = _build_widgets()

    # ------------------------------------------------------------------
    # enable_dl1 / enable_dl2 (OLD) — one function, and one small state per
    # model it renders the row from:
    #
    # * ``found``: ``None`` nothing run for this alert yet, ``True`` a
    #   prediction with something in it, ``False`` a run that found nothing
    #   (the warning takes the pair's place, as it did in OLD).
    # * ``added``: whether that prediction is in the drawing right now.
    #
    # Seeded from ``prediction_shapes``, which outlives this build: the panel
    # is rebuilt every time the Analysis dialog reopens, and the alert's
    # prediction is still on offer when it comes back.
    # ------------------------------------------------------------------
    # Whether the drawing session is open: *Add to map* has to know, because
    # the prediction goes into the draw layer. OLD read that off the toolbar it
    # hid; this flag replaces it.
    editing = {"on": False}

    dl_state = {
        n: {"found": True if analysis.prediction_shapes.value.get(n) else None,
            "added": False}
        for n in (1, 2)
    }

    def _sync_dl(model):
        state = dl_state[model]
        add, remove = ns[f"dl{model}_add"], ns[f"dl{model}_remove"]
        nothing_found = state["found"] is False
        # A run that found nothing takes *Add*'s place with the warning, as in
        # OLD.
        if nothing_found:
            ns[f"dl{model}_msg"].show()
            add.hide()
        else:
            ns[f"dl{model}_msg"].hide()
            add.show()
        # *Add* is live for a prediction that is not in the drawing yet — it
        # opens the drawing session itself if there is none.
        add.disabled = not state["found"] or state["added"]
        # *Remove* keeps its place whenever the drawing still holds this
        # model's shapes, warning or not: a later run that found nothing (or
        # found something new) must not strand the ones already drawn with no
        # button that takes them off.
        if state["added"] or not nothing_found:
            remove.show()
        else:
            remove.hide()
        remove.disabled = not state["added"]

    def _set_found(model, found):
        """What a finished run leaves the row offering. ``added`` is left
        alone: a rerun does not touch the drawing, so whatever a previous one
        put there is still in it and still removable."""
        dl_state[model]["found"] = found
        _sync_dl(model)

    # ------------------------------------------------------------------
    # verify_model*_output (OLD) — applied at the END of the worker's success
    # path with the worker's computed prediction path (replaces the OLD
    # model*_prediction_time observer / the use_task-value effect).
    # ------------------------------------------------------------------
    # Both run INSIDE the worker's tracked job and simply raise on failure: a
    # verify failure then fails the task and publishes the one dl_404 error toast
    # through tracked_job, exactly like a failed download/inference.
    def _record_prediction(model, pred_path):
        # The two runs are separate workers and can finish together: replace
        # the dict in one locked read-and-set, never mutate it in place, so
        # neither run drops the other's path.
        files = analysis.prediction_files
        with files.lock:
            files.set({**files.value, model: pred_path})

    def _show_found(model):
        text = found_text(analysis, model)
        ns[f"dl{model}_found"].children = [text] if text else []

    # A run belongs to the alert it started on (``token``, captured at the
    # click): one that ends after the user moved on must not record its
    # raster or enable "add" for the alert now on screen, whose review Save
    # would store the other alert's polygons.
    #
    # ``found`` is ``vectorise``'s ``(features, n_patches, area_ha)``. Returns
    # ``(applied, valid)``: whether this was still the current alert's run, and
    # whether the prediction holds anything to add -- an empty one is the
    # ``dl_404`` warning, not a "Found 0 patches" row.
    def _apply_model(model, pred_path, token, found):
        if not analysis.is_current(token):
            return False, False
        features, n_patches, area_ha = found
        valid = n_patches > 0

        def _apply():
            _record_prediction(model, pred_path)
            features_cache[(token, model)] = features
            _record_prediction_shapes(analysis, model, n_patches, area_ha)
            _set_found(model, valid)
            _show_found(model)

        return analysis.apply_if_current(token, _apply), valid

    # ------------------------------------------------------------------
    # An alert change (``AnalysisState.reset_alert``) puts the card back as it
    # was built: out of edit mode, nothing drawn, the add/remove buttons
    # disabled. The drawn features and an enabled "add" belonged to the
    # previous alert.
    # ------------------------------------------------------------------
    def _reset_for_new_alert(_token=None):
        try:
            features_cache.clear()
            editing["on"] = False
            for n in (1, 2):
                # Nothing run for THIS alert yet and nothing of the
                # previous one's drawn: the pair back on screen and dead, the
                # previous alert's warning gone.
                dl_state[n].update(found=None, added=False)
                _sync_dl(n)
                # Set empty, not from ``found_text``: this runs on the token
                # bump, which ``reset_alert`` does BEFORE it clears
                # ``prediction_shapes``.
                ns[f"dl{n}_found"].children = []
            for name in ("save_btn", "stop_btn"):
                ns[name].disabled = True
            ns["start_btn"].disabled = False
            panes = getattr(widgets, "panes", None)
            if panes is not None:
                panes.set_edit_mode(False)
                panes.set_drawing([])
        except Exception:
            logger.exception("Analysis: resetting the edit tools failed")

    # ------------------------------------------------------------------
    # add/remove prediction (OLD) — main-thread click handlers rewriting the
    # drawing through ``widgets.panes.set_drawing``: one write, every view
    # shows it, and it is never heard back as the reviewer's edit.
    # ------------------------------------------------------------------
    def _add_model_prediction(model, color_name):
        path = analysis.prediction_files.value.get(model)
        if not path:
            return
        try:
            # The prediction goes INTO the drawing, and *Start Drawing* seeds
            # the drawing from the stored polygon -- so a prediction added
            # before the session opened would be replaced by that seeding.
            # *Add* therefore opens the session itself.
            if not editing["on"]:
                _begin_edition()
            # The run already vectorised it; only a panel reopened on a
            # prediction this build never saw has to do it again.
            features = features_cache.get((analysis.alert_token.value, model))
            if features is None:
                p = app_state.project.value
                features, _n, _area = vectorise(path, area_crs(p), min_patch_ha(p))
            orig_features = widgets.panes.drawing_features()
            new_features = convertir_formato3(features, color_name)
            widgets.panes.set_drawing(orig_features + new_features)
            dl_state[model]["added"] = True
            _sync_dl(model)
            drawing_changed(analysis)  # unsaved, and not applied yet (spec §4.4)
        except Exception:
            logger.exception("Analysis: add prediction failed")
            notify(t("analysis_tile.model_labels.dl_404"), type_="error")

    def _remove_model_prediction(model, color_name):
        try:
            widgets.panes.set_drawing(
                filter_features_by_color(widgets.panes.drawing_features(), color_name))
            dl_state[model]["added"] = False
            _sync_dl(model)
            drawing_changed(analysis)  # unsaved, and not applied yet (spec §4.4)
        except Exception:
            logger.exception("Analysis: remove prediction failed")

    # ------------------------------------------------------------------
    # Edit handlers (start / save / stop / clear) — OLD *_edition_function.
    # ------------------------------------------------------------------
    def _begin_edition():
        """Open the drawing session: edit mode on every view, the drawing
        seeded with the alert's stored polygon, and the three edit buttons set
        for it.

        ``_start_edition``'s body, split out because *Add to map* opens the
        session too (there is no drawing to add a prediction to without one).
        """
        # *Alert BB* and the *Defo Layer* are ``hide_while_editing``: the views
        # hide them in edit mode, and the drawing shows the polygon instead.
        widgets.panes.set_edit_mode(True)
        defo_layer = analysis.defo_layer.value
        widgets.panes.set_drawing(
            convertir_formato3(defo_layer.__geo_interface__["features"], "blue")
            if defo_layer is not None else []
        )

        editing["on"] = True
        # Seeded from ``defo_layer``: the drawing and the reviewed polygon say
        # the same thing, so there is nothing to apply yet.
        analysis.drawing_unapplied.set(False)
        # The drawing was just seeded, so no prediction is in it.
        for n in (1, 2):
            dl_state[n]["added"] = False
            _sync_dl(n)

        ns["start_btn"].disabled = True
        ns["save_btn"].disabled = False
        ns["stop_btn"].disabled = False

    def _start_edition(widget, event, data):
        widget.loading = True
        widget.disabled = True
        try:
            _begin_edition()
        except Exception:
            logger.exception("Analysis: start edition failed")
        finally:
            widget.loading = False

    def _save_edition(widget, event, data):
        widget.loading = True
        widget.disabled = True
        try:
            _apply_drawing()
        except Exception:
            logger.exception("Analysis: save edition failed")
        finally:
            widget.loading = False
            widget.disabled = False

    def _apply_drawing():
        """*Save drawing*'s write: the drawing becomes the reviewed polygon."""
        drawn = widgets.panes.drawing_geojson()
        if len(drawn["features"]) > 0:
            analysis.defo_layer.set(geojson_to_geodataframe(drawn).set_crs(
                epsg="4326", allow_override=True, inplace=True))
            touch(analysis)  # the reviewed polygon changed (spec §4.4)
        elif analysis.defo_layer.value is not None:
            # An emptied drawing applies as "no polygon at all". It applied
            # to NOTHING before, so a reviewer who deleted every shape to
            # correct a false positive could never clear the stored polygon
            # the save refuses one for -- and that refusal
            # (``questionarie.false_positive_drawn``) is what tells them to
            # delete it. ``wire_defo_layer`` takes the layer off the views.
            analysis.defo_layer.set(None)
            touch(analysis)
        # Applied: the drawing and the reviewed polygon agree again, so the
        # review Save stops refusing (``export._save_attributes``).
        analysis.drawing_unapplied.set(False)

    def _stop_edition(widget, event, data):
        """*Stop Drawing*: asks first when the drawing holds edits *Save
        drawing* has not stored (``drawing_unapplied``), which stopping would
        throw away; stops at once otherwise -- a drawing still as it was
        seeded loses nothing."""
        if analysis.drawing_unapplied.value:
            ns["stop_dialog"].v_model = True
            return
        _end_edition()

    def _stop_choice(choice):
        ns["stop_dialog"].v_model = False
        if choice == "keep":
            return
        try:
            if choice == "save":
                _apply_drawing()
        except Exception:
            logger.exception("Analysis: save edition failed")
            return  # nothing stored, so the drawing is not thrown away either
        _end_edition()

    def _end_edition():
        widget = ns["stop_btn"]
        widget.loading = True
        widget.disabled = True
        try:
            # Out of edit mode (*Alert BB* and the *Defo Layer* come back) and
            # the drawing cleared.
            widgets.panes.set_edit_mode(False)
            widgets.panes.set_drawing([])

            # Stopping cleared the drawing, so nothing is added any more --
            # but what a run found stays on offer: *Add to map* opens a new
            # drawing session itself.
            editing["on"] = False
            # The drawing is gone, so nothing is waiting to be applied. What
            # was not applied is discarded with it, as it always has been.
            analysis.drawing_unapplied.set(False)
            for n in (1, 2):
                dl_state[n]["added"] = False
                _sync_dl(n)

            ns["save_btn"].disabled = True
            ns["start_btn"].disabled = False
        except Exception:
            logger.exception("Analysis: stop edition failed")
        finally:
            widget.loading = False

    # ------------------------------------------------------------------
    # CNN run handlers: button-triggered daemon threads (CPU/IO-bound; never
    # auto-run). The worker runs _run_cnn_model -> prediction path, then on the
    # success path applies the OLD verify/enable step with that path. The run
    # button spins for the duration; errors are published by tracked_job (FAILED
    # task + one dl_404 error toast), which also logs the raw traceback.
    # ------------------------------------------------------------------
    def _dl_404(_exc):
        # Every CNN run failure (composite download, HF model pull, inference,
        # verify) has always reached the user as this one friendly message; the
        # raw exception stays in the log.
        return t("analysis_tile.model_labels.dl_404")

    def _on_dl1_run(*_):
        # The previous run's prediction is not what this row offers any more
        # (``added`` is left alone: whatever it put in the drawing is still
        # there, and *Remove* still takes it off).
        dl_state[1]["found"] = None
        _sync_dl(1)
        ns["dl1_run"].loading = True
        ns["dl1_run"].disabled = True
        # The alert this run is for, with its images and grid, read here on
        # the UI thread; the worker starts only while it is still current.
        alert = capture_alert(analysis, widgets, app_state)
        token = alert.token  # the alert this run is for
        # Writer tracking (spec §12): the composite lands in the set's folder,
        # so the project is marked for the run and a delete is refused
        # meanwhile. The name is read now, at the click: the run keeps marking
        # the project it writes into after the user switched to another one.
        project = writer_name(app_state)

        def _worker():
            # No ``total_steps`` — the run is one download+inference phase; the
            # composite download drives the ring, the rest is indeterminate.
            notifier = current_notifier()
            try:
                with tracked_job(
                    notifier,
                    t("analysis_tile.model_labels.dl_task_title"),
                    error_format=_dl_404,
                ) as task:
                    task.step(t("analysis_tile.model_labels.dl_step_run"))
                    applied = valid = False
                    # Nothing to run on while a pane is still rebuilding its
                    # pick: a composite staged now would carry one pick's name
                    # over the other's pixels (``_images_ready``). Its own
                    # reason, so the refusal does not read as "you moved on".
                    refused = None if _images_ready(alert) else t(
                        "analysis_tile.model_labels.dl_no_images")
                    if refused is None and analysis.is_current(token):
                        with writing(project):
                            pred_path = _run_cnn_model(
                                _MODEL1_REPO, _MODEL1_FILE, "_m1",
                                widgets, analysis, app_state, app_tile_model,
                                task=task, alert=alert,
                            )
                            # Vectorised here, inside the job and inside the
                            # writer guard that keeps the set's folder alive:
                            # the row can then say what was found and *Add* is
                            # instant. A failure fails the run like any other.
                            found = vectorise(pred_path, alert.area_crs, alert.min_patch_ha)
                        applied, valid = _apply_model(1, pred_path, token, found)
                    if not applied:
                        # Another alert is on screen (its result is dropped),
                        # or the pair was not ready to run on. A warning,
                        # recorded on the task before the toast.
                        reason = refused or t("analysis_tile.model_labels.dl_stale")
                        task.fail(reason)
                        notifier.warning(reason)
            except Exception:  # noqa: BLE001 — tracked_job toasted and logged it (traceback included)
                pass
            else:
                if applied and valid:
                    notifier.success(t("analysis_tile.model_labels.dl_done"))
                elif applied:
                    # The run worked; the model simply found no forest loss.
                    # Nothing is recorded and nothing is offered to add, so the
                    # reviewer's own drawing is left exactly as it was.
                    notifier.warning(t("analysis_tile.model_labels.dl_404"))
            finally:
                ns["dl1_run"].loading = False
                ns["dl1_run"].disabled = False

        spawn_in_context(_worker)

    def _on_dl2_run(*_):
        # The previous run's prediction is not what this row offers any more
        # (``added`` is left alone: whatever it put in the drawing is still
        # there, and *Remove* still takes it off).
        dl_state[2]["found"] = None
        _sync_dl(2)
        ns["dl2_run"].loading = True
        ns["dl2_run"].disabled = True
        # The alert this run is for, with its images and grid, read here on
        # the UI thread; the worker starts only while it is still current.
        alert = capture_alert(analysis, widgets, app_state)
        token = alert.token  # the alert this run is for
        project = writer_name(app_state)  # as in _on_dl1_run

        def _worker():
            # Same tracked_job shape as _on_dl1_run above.
            notifier = current_notifier()
            try:
                with tracked_job(
                    notifier,
                    t("analysis_tile.model_labels.dl_task_title"),
                    error_format=_dl_404,
                ) as task:
                    task.step(t("analysis_tile.model_labels.dl_step_run"))
                    applied = valid = False
                    # Nothing to run on while a pane is still rebuilding its
                    # pick: a composite staged now would carry one pick's name
                    # over the other's pixels (``_images_ready``). Its own
                    # reason, so the refusal does not read as "you moved on".
                    refused = None if _images_ready(alert) else t(
                        "analysis_tile.model_labels.dl_no_images")
                    if refused is None and analysis.is_current(token):
                        with writing(project):
                            pred_path = _run_cnn_model(
                                _MODEL2_REPO, _MODEL2_FILE, "_m2",
                                widgets, analysis, app_state, app_tile_model,
                                task=task, alert=alert,
                            )
                            # Vectorised here, inside the job and inside the
                            # writer guard that keeps the set's folder alive:
                            # the row can then say what was found and *Add* is
                            # instant. A failure fails the run like any other.
                            found = vectorise(pred_path, alert.area_crs, alert.min_patch_ha)
                        applied, valid = _apply_model(2, pred_path, token, found)
                    if not applied:
                        # Another alert is on screen (its result is dropped),
                        # or the pair was not ready to run on. A warning,
                        # recorded on the task before the toast.
                        reason = refused or t("analysis_tile.model_labels.dl_stale")
                        task.fail(reason)
                        notifier.warning(reason)
            except Exception:  # noqa: BLE001 — tracked_job toasted and logged it (traceback included)
                pass
            else:
                if applied and valid:
                    notifier.success(t("analysis_tile.model_labels.dl_done"))
                elif applied:
                    # The run worked; the model simply found no forest loss.
                    # Nothing is recorded and nothing is offered to add, so the
                    # reviewer's own drawing is left exactly as it was.
                    notifier.warning(t("analysis_tile.model_labels.dl_404"))
            finally:
                ns["dl2_run"].loading = False
                ns["dl2_run"].disabled = False

        spawn_in_context(_worker)

    # ------------------------------------------------------------------
    # Wire all click handlers once (the widgets are built once per build).
    # ------------------------------------------------------------------
    def _register_handlers():
        ns["start_btn"].on_event("click", _start_edition)
        ns["save_btn"].on_event("click", _save_edition)
        ns["stop_btn"].on_event("click", _stop_edition)
        for choice in ("keep", "save", "discard"):
            ns[f"stop_{choice}"].on_event("click", lambda *_, c=choice: _stop_choice(c))

        for n, (run, color_name) in enumerate(((_on_dl1_run, "lime"),
                                               (_on_dl2_run, "purple")), start=1):
            ns[f"dl{n}_run"].on_event("click", run)
            ns[f"dl{n}_add"].on_event(
                "click", lambda *_, n=n, c=color_name: _add_model_prediction(n, c))
            ns[f"dl{n}_remove"].on_event(
                "click", lambda *_, n=n, c=color_name: _remove_model_prediction(n, c))

        # Spec §4.4: the reviewer's work on the drawing marks the alert
        # unsaved. ``on_drawing_edit`` hears only the edits the reviewer
        # finished in the browser -- never ``set_drawing`` (seeding, adding a
        # prediction), and never ``analysis.defo_layer``, which
        # ``view_actual_alert`` seeds on every opened alert (a subscription
        # there would mark every freshly opened alert unsaved; Ruling 24).
        # *Apply* still touches too, where it writes ``defo_layer``.
        panes = getattr(widgets, "panes", None)
        if panes is not None:
            panes.on_drawing_edit(lambda: drawing_changed(analysis))

    _register_handlers()

    # MapApp rebuilds this panel every time the Analysis dialog reopens, while
    # the alert's prediction lives on in ``AnalysisState``: both rows are drawn
    # from it, so a reopened panel still reports what the run found and still
    # offers to add it (``_add_model_prediction`` re-vectorises the raster for
    # the features this build never saw).
    for _model in (1, 2):
        _sync_dl(_model)
        _show_found(_model)

    analysis.alert_token.subscribe(_reset_for_new_alert)

    def _on_predictions(files):
        """Follow ``prediction_files`` for a model's prediction DISAPPEARING.

        An imagery change drops them (``state.imagery_changed``): a prediction
        is a function of the composite it ran on, so once the reviewer picks
        another pair the row must say nothing was run for this one rather than
        keep offering a raster of imagery they are no longer looking at. The
        run's own publish is left to ``_apply_model``, which has the numbers
        the "Found ..." line needs.

        ``added`` is deliberately untouched: what a previous prediction put in
        the drawing is the reviewer's own work by then, and *Remove from map*
        (which filters the drawing by colour, not by raster) still takes it
        off. The "Found ..." line is cleared here rather than read back from
        ``prediction_shapes``, which ``reset_alert`` clears AFTER the files.
        """
        try:
            for n in (1, 2):
                if files.get(n):
                    continue
                dl_state[n]["found"] = None
                _sync_dl(n)
                ns[f"dl{n}_found"].children = []
        except Exception:
            logger.exception("Analysis: syncing the model rows failed")

    analysis.prediction_files.subscribe(_on_predictions)

    # add_defo_layer (OLD): the "Defo Layer" lands on the views whenever a new
    # reviewed polygon is set. Subscribed once (the layout is built once).
    wire_defo_layer(widgets, analysis)

    if set_badge is not None:
        wire_refine_badge(analysis, set_badge, lambda: area_crs(app_state.project.value))

    # ------------------------------------------------------------------
    # Layout: the section's body -- the edit buttons over one row per model.
    # The card and the title belong to the section; the OLD inline alert is
    # gone (failures are toasts, see _on_dl*_run).
    # ------------------------------------------------------------------
    body = v.Html(tag="div", children=[ns["edit_row"], ns["dl1_row"], ns["dl2_row"]])
    setattr(body, _EDIT_SLOTS, ns)
    return body
