"""Recipe tile — raw ipyvuetify widget handed straight to MapApp (Phase 4).

Architecture layer: view — ipyvuetify/pysepal for UI only.

WHY A RAW WIDGET (not a Solara component + ``solara.display``): MapApp renders a
``dialog`` tile's content with ``v-if``, so it DESTROYS the dialog DOM on close
and REBUILDS it on reopen. A widget captured via ``solara.display`` lives inside
an ``Output`` whose view is NOT recreated on that rebuild — so the cards vanish
the 2nd time the dialog is opened (the buttons, being reacton-managed, survived,
which is exactly the symptom that was reported). A plain ipyvuetify widget tree
is re-viewed from its model on reopen like any ipywidget, so it survives. ``Page``
builds this once via ``use_memo`` and hands it to MapApp as the dialog content —
same pattern as ``aux_tile.build_aux_layout``.

The async **load** that the earlier Solara port ran via ``solara.lab.use_task``
is here a daemon thread that toggles the Load button's spinner and surfaces
errors on an inline ``sw.Alert`` (the original ipyvuetify tile did the same).

The three handlers are re-architected around the shared **models + services**
(unchanged from the Solara port):

* **New recipe** (if alerts are loaded) resets every domain model + clears
  ``app_state.alerts_gdf`` (tiles re-render from their models), then calls
  ``start_new_recipe``: it sets ``recipe_name``, CREATES the recipe folder on
  disk, and sets ``recipe_folder_path`` (model + ``app_state``) so the recipe is
  persisted from the start — the Save card appears and export can write into it.
* **Load recipe** reads the recipe JSON, imports it into the models via their
  ``import_from_dictionary`` services, sets ``recipe_folder_path``, and (if the
  saved ``alert_db.csv`` exists) loads the alert gdf into ``app_state.alerts_gdf``
  so Overview shows the loaded alerts. The EE/analysis pipeline re-run the old
  load did is DEFERRED (Task 9); navigation sets ``app_state.current_page_view``,
  which the shell's ``_NavBridge`` translates into the ``MapApp`` step change.
* **Save recipe** persists every model to ``<recipe_folder>/recipe_parameters.json``
  via ``save_model_parameters_to_json``. It writes to an EXISTING recipe folder
  only, so the Save card stays hidden until ``app_state.recipe_folder_path`` is
  set (driven reactively via ``.subscribe``). This replaces the old header
  ``RecipeBtn``/``ShareBtn`` custom widget.
"""

import logging
import os
import threading

import ipyvuetify as v
from pysepal import sepalwidgets as sw

from deforestation_alerts.message import cm
from deforestation_alerts.parameter import directory
from deforestation_alerts.scripts.recipe_helper import (
    create_directory,
    generate_recipe_string,
    load_alert_store,
    save_model_parameters_to_json,
)

logger = logging.getLogger("SEPALUI.deforestation_alerts.recipe")


def start_new_recipe(app_tile_model, app_state, recipe_name):
    """Create a brand-new recipe: make its folder on disk, point the models at
    it, and signal navigation to the Aux tile.

    Creates ``<module_dir>/<recipe_name>/`` (plus the shared ``temp/``) via
    ``create_directory`` and writes the resulting absolute path to BOTH
    ``app_tile_model.recipe_folder_path`` (serialized into the recipe) and
    ``app_state.recipe_folder_path`` (drives Save-card visibility + the
    Overview/export ``alert_db.csv`` location). Mirrors what the load handler
    does for an EXISTING recipe, so a freshly created recipe is persisted on
    disk from the start instead of living only in memory.
    """
    app_tile_model.recipe_name = recipe_name
    folder = create_directory(recipe_name)
    app_tile_model.recipe_folder_path = folder
    app_state.recipe_folder_path.value = folder
    # Jump to the Aux (Optional files) tile; the shell's _NavBridge translates
    # this current_page_view write into the MapApp step change.
    app_state.current_page_view.value = "aux_tile"


def build_recipe_layout(
    aux_model,
    aoi_date_model,
    alert_filter_model,
    selected_alerts_model,
    analyzed_alerts_model,
    app_tile_model,
    app_state,
    gee_interface=None,
):
    """Build the raw ipyvuetify recipe layout (New recipe + Load recipe).

    Returns the outermost ipyvuetify widget; ``Page`` hands it to MapApp as the
    Recipes dialog content. Built once (no re-render), so the ``.bind()`` chain
    is attached exactly once and entered/loaded values survive dialog reopen.

    Parameters
    ----------
    aux_model, aoi_date_model, alert_filter_model, selected_alerts_model,
    analyzed_alerts_model, app_tile_model : pysepal.model.Model
        Shared recipe domain models. New-recipe resets them; load-recipe imports
        the saved JSON into them.
    app_state : AppState
        Shared ephemeral reactive store (``alerts_gdf`` / ``recipe_folder_path``
        / ``current_page_view``).
    gee_interface : GEEInterface, optional
        Accepted for signature parity with the other tiles; the deferred EE
        pipeline re-run (Task 9) will use it.
    """
    # --- New-recipe input: pre-filled generated name, bound once to the model.
    new_recipe_input = sw.TextField(
        class_="pa-1 ma-1 d-flex align-center",
        v_model=generate_recipe_string(),
        hint=cm.recipe_tile.input_hint_set_recipe_name,
        persistent_hint=True,
    )
    app_tile_model.bind(new_recipe_input, "temporary_recipe_name")

    # --- Load-recipe input: choose the recipe JSON; .v_model is the chosen path.
    load_recipe_input = sw.FileInput(
        class_="pa-1 ma-1 d-flex align-center",
        large=True,
        extensions=[".json"],
        folder=directory.module_dir,
    )

    create_btn = sw.Btn(
        msg=cm.recipe_tile.button_new_recipe,
        color="primary",
        class_="ma-5",
    )
    load_btn = sw.Btn(
        msg=cm.recipe_tile.button_load_recipe,
        color="primary",
        class_="ma-5",
    )
    save_btn = sw.Btn(
        msg=cm.recipe_tile.button_save_recipe,
        color="primary",
        class_="ma-5",
    )
    # Inline error surface for the load (hidden until something fails).
    alert = sw.Alert().hide()
    # Inline success/error surface for the save (hidden until something happens).
    save_alert = sw.Alert().hide()

    new_card = v.Card(
        class_="pa-3 ma-5",
        hover=True,
        children=[
            v.CardTitle(
                class_="pa-1 ma-1", children=[cm.recipe_tile.card_title_new_recipe]
            ),
            v.CardSubtitle(
                class_="pa-1 ma-1",
                children=[cm.recipe_tile.card_subtitle_new_recipe],
            ),
            new_recipe_input,
            v.CardActions(class_="justify-end", children=[create_btn]),
        ],
    )
    load_card = v.Card(
        class_="pa-3 ma-5",
        hover=True,
        children=[
            v.CardTitle(
                class_="pa-1 ma-1", children=[cm.recipe_tile.card_title_load_recipe]
            ),
            v.CardSubtitle(
                class_="pa-1 ma-1",
                children=[cm.recipe_tile.card_subtitle_load_recipe],
            ),
            load_recipe_input,
            v.CardActions(class_="justify-end", children=[load_btn]),
            alert,
        ],
    )
    # Save card — written to the EXISTING recipe folder only, so it stays hidden
    # (display:none, not child-swap, so it survives dialog reopen) until a recipe
    # folder exists. Visibility is driven reactively below.
    save_card = v.Card(
        class_="pa-3 ma-5",
        style_="display:none;",
        hover=True,
        children=[
            v.CardTitle(
                class_="pa-1 ma-1", children=[cm.recipe_tile.card_title_save_recipe]
            ),
            v.CardSubtitle(
                class_="pa-1 ma-1",
                children=[cm.recipe_tile.card_subtitle_save_recipe],
            ),
            v.CardActions(class_="justify-end", children=[save_btn]),
            save_alert,
        ],
    )

    # Show the Save card only once a recipe folder exists (set by new-recipe
    # create and by load). Subscribed once — the layout is memoized for the
    # session, so the leaked cancel handle is fine.
    def _sync_save_visibility(folder):
        save_card.style_ = "" if folder else "display:none;"

    app_state.recipe_folder_path.subscribe(_sync_save_visibility)
    _sync_save_visibility(app_state.recipe_folder_path.value)

    # ------------------------------------------------------------------
    # New-recipe handler. Thin: if alerts are already loaded, reset every model
    # + clear the shared gdf (tiles re-render from model state); then start the
    # new recipe (create its folder, set the name, navigate to the Aux step).
    # ------------------------------------------------------------------
    def _on_create_click(*_):
        if app_state.alerts_gdf.value is not None:
            aux_model.reset_model()
            aoi_date_model.reset_model()
            alert_filter_model.reset_model()
            selected_alerts_model.reset_model()
            analyzed_alerts_model.reset_model()
            app_tile_model.reset_model()
            app_state.alerts_gdf.value = None

        start_new_recipe(app_tile_model, app_state, new_recipe_input.v_model)

    create_btn.on_event("click", _on_create_click)

    # ------------------------------------------------------------------
    # Load. File/IO + model round-trip + gdf load run on a daemon thread so the
    # UI stays responsive; the button shows a spinner and errors land on `alert`.
    # ------------------------------------------------------------------
    def _run_load():
        file_name = load_recipe_input.v_model
        if not file_name or not os.path.exists(file_name):
            raise FileNotFoundError(cm.recipe_tile.input_hint_load_recipe_name)

        import json

        with open(file_name, "r") as json_file:
            params = json.load(json_file)

        # Import the saved parameters into every model (service round-trip).
        aux_model.import_from_dictionary(params)
        aoi_date_model.import_from_dictionary(params)
        selected_alerts_model.import_from_dictionary(params)
        analyzed_alerts_model.import_from_dictionary(params)
        app_tile_model.import_from_dictionary(params)

        folder = os.path.dirname(os.path.abspath(file_name))
        app_tile_model.recipe_folder_path = folder
        # Expose the folder to the Overview download button (alert_db.csv lives
        # here). Kept on app_state so the view layer needn't read the domain model.
        app_state.recipe_folder_path.value = folder

        # Load the canonical alert store (parquet preferred, legacy CSV fallback)
        # so Overview renders the alerts. None when the recipe has no store yet.
        loaded = load_alert_store(folder)
        if loaded is not None:
            app_state.alerts_gdf.value = loaded

        # TODO(Task 9 – analysis tile): the OLD load also re-ran the EE pipeline
        # here (alert re-discovery, filtered alert raster, planet before/after
        # image dictionaries). Deferred until the analysis tile is wired.
        # Jump to the analysis tile; the shell's _NavBridge translates this
        # current_page_view write into the MapApp step change.
        app_state.current_page_view.value = "analysis_tile"

    def _on_load_click(*_):
        alert.reset()
        load_btn.loading = True
        load_btn.disabled = True

        def _worker():
            try:
                _run_load()
            except Exception as e:  # noqa: BLE001 — surfaced on the alert
                logger.exception("Recipe load failed")
                alert.add_msg(
                    f"{cm.recipe_tile.input_hint_load_recipe_name}: {e}", "error"
                )
            finally:
                load_btn.loading = False
                load_btn.disabled = False

        threading.Thread(target=_worker, daemon=True).start()

    load_btn.on_event("click", _on_load_click)

    # ------------------------------------------------------------------
    # Save. Persist every model to <recipe_folder>/recipe_parameters.json via
    # the shared service. Requires an existing recipe folder (the card is hidden
    # until one exists; the guard is a safety net). Runs on a daemon thread for
    # parity with load; the button spins and success/errors land on `save_alert`.
    # ------------------------------------------------------------------
    def _run_save():
        folder = app_state.recipe_folder_path.value
        if not folder or not os.path.isdir(folder):
            raise FileNotFoundError(cm.recipe_tile.input_hint_save_recipe_error)

        file_path = os.path.join(folder, "recipe_parameters.json")
        save_model_parameters_to_json(
            file_path,
            aux_model,
            aoi_date_model,
            alert_filter_model,
            selected_alerts_model,
            analyzed_alerts_model,
            app_tile_model,
        )

    def _on_save_click(*_):
        save_alert.reset()
        save_btn.loading = True
        save_btn.disabled = True

        def _worker():
            try:
                _run_save()
                save_alert.add_msg(cm.recipe_tile.alert_save_recipe_done, "success")
            except Exception as e:  # noqa: BLE001 — surfaced on the alert
                logger.exception("Recipe save failed")
                save_alert.add_msg(
                    f"{cm.recipe_tile.input_hint_save_recipe_error}: {e}", "error"
                )
            finally:
                save_btn.loading = False
                save_btn.disabled = False

        threading.Thread(target=_worker, daemon=True).start()

    save_btn.on_event("click", _on_save_click)

    # Layout: three cards, each containing its own action button (errors surface
    # on the inline alert inside the relevant card). The Save card is hidden
    # until a recipe folder exists (see _sync_save_visibility above).
    return v.Container(
        fluid=True,
        class_="pa-0",
        children=[new_card, load_card, save_card],
    )
