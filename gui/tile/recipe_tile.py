"""Recipe tile as a Solara component (Phase 4 port of the old ipyvuetify class).

Architecture layer: view — imports solara + ipyvuetify/pysepal for UI only.

The OLD Recipe tile was the orchestration nexus: its two button handlers
imperatively called OTHER tiles' methods (``aoi_tile.process_alerts_silent``,
``alert_filter_tile.create_filtered_alert_raster``, ``*.initialize_layout`` ...)
and re-linked maps. That pattern does not translate to Solara components (there
are no long-lived tile objects to call into; components re-render from model
state). So this port is re-architected around the shared **models + services**:

* **New recipe** writes ``app_tile_model.recipe_name`` and (if alerts are loaded)
  resets every domain model + clears ``app_state.alerts_gdf``. The old code also
  re-init'd tiles and re-linked maps — unnecessary here: every ported tile
  re-renders from its model, so clearing the models is the whole job.
* **Load recipe** reads the recipe JSON, imports it into the models via their
  ``import_from_dictionary`` services, sets ``recipe_folder_path``, and (if the
  saved ``alert_db.csv`` exists) loads the alert gdf into ``app_state.alerts_gdf``
  so Overview shows the loaded alerts. The EE/analysis pipeline re-run the old
  load did (alert re-discovery, filtered-raster + planet-image reconstruction)
  is DEFERRED — it rebuilds EE/analysis-tile state and the analysis tile is not
  ported yet (Task 9). Cross-tile navigation is likewise deferred (we set
  ``app_state.current_page_view`` only; wiring it to MapApp.current_step is a
  separate navigation-foundation concern, same as in aoi_tile.py).

Heavy/file work runs in ``solara.lab.use_task(prefer_threaded=True)``; widgets
are memoized once (dependencies=[]) so the ``.bind()`` chain is stable — same
pattern as aoi_tile.py / aux_tile.py.
"""

import logging
import os

import ipyvuetify as v
import solara
from pysepal import sepalwidgets as sw

from deforestation_alerts.message import cm
from deforestation_alerts.parameter import directory
from deforestation_alerts.scripts.recipe_helper import (
    backfill_geometry,
    generate_recipe_string,
    load_gdf_from_csv,
)

logger = logging.getLogger("SEPALUI.deforestation_alerts.recipe")

# Geometry columns the OLD load decoded from alert_db.csv (Phase-3 `geometry`
# added; load_gdf_from_csv tolerates absent columns, backfill_geometry fills
# `geometry` from `bounding_box` for old recipes).
_ALERT_DB_GEOM_COLS = ["geometry", "bounding_box", "point", "alert_polygon"]


@solara.component
def RecipeTile(
    aux_model,
    aoi_date_model,
    alert_filter_model,
    selected_alerts_model,
    analyzed_alerts_model,
    app_tile_model,
    app_state,
    gee_interface,
):
    """Solara port of the old RecipeTile ipyvuetify widget.

    Parameters
    ----------
    aux_model, aoi_date_model, alert_filter_model, selected_alerts_model,
    analyzed_alerts_model, app_tile_model : pysepal.model.Model
        The shared recipe domain models. New-recipe resets them; load-recipe
        imports the saved JSON into them.
    app_state : AppState
        Shared ephemeral reactive store. The load task writes
        ``alerts_gdf`` / ``recipe_folder_path`` and both handlers set
        ``current_page_view`` (navigation wiring deferred).
    gee_interface : GEEInterface
        Session GEE interface (accepted for signature parity with the other
        tiles; the deferred EE pipeline re-run will use it — see TODOs below).
    """

    # ------------------------------------------------------------------
    # Memoized widgets. New-recipe TextField is pre-filled with a generated
    # name and bound once to app_tile_model.temporary_recipe_name; the load
    # FileInput exposes the chosen path via .v_model. Built inside use_memo so
    # the .bind() chain is attached exactly once and entered values survive
    # re-renders (same rationale as aux_tile.py).
    # ------------------------------------------------------------------
    new_recipe_input = solara.use_memo(
        lambda: _build_new_recipe_input(app_tile_model), dependencies=[]
    )
    load_recipe_input = solara.use_memo(
        lambda: sw.FileInput(
            class_="pa-1 ma-1 d-flex align-center",
            large=True,
            extensions=[".json"],
            folder=directory.module_dir,
        ),
        dependencies=[],
    )

    # ------------------------------------------------------------------
    # New-recipe handler. Thin: if alerts are already loaded, reset every model
    # + clear the shared gdf (the old code also re-init'd tiles + re-linked maps
    # — not applicable in Solara, components re-render from model state); then
    # set the recipe name and signal navigation to the Aux step.
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

        app_tile_model.recipe_name = new_recipe_input.v_model
        # TODO(navigation foundation): wiring app_state.current_page_view ->
        # MapApp.current_step (so the step panel actually switches) is a separate
        # cross-tile navigation concern not yet wired in this app (same deferral
        # as aoi_tile.py). Set the intent only.
        app_state.current_page_view.value = "aux_tile"

    # ------------------------------------------------------------------
    # Load task. dependencies=None => never auto-runs; fires only when the click
    # handler calls load_task(). Reads the JSON, imports it across the models,
    # and (if present) loads the saved alert gdf so Overview shows it. The EE
    # pipeline re-run + navigation are deferred (see module docstring).
    # ------------------------------------------------------------------
    def _run_load():
        file_name = load_recipe_input.v_model
        if not file_name or not os.path.exists(file_name):
            # Surfaced via solara.Error (raise_error=False keeps the task alive).
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

        # If the saved alert DB exists, load it so Overview renders the alerts.
        alert_db = os.path.join(folder, "alert_db.csv")
        if os.path.exists(alert_db):
            app_state.alerts_gdf.value = backfill_geometry(
                load_gdf_from_csv(alert_db, _ALERT_DB_GEOM_COLS)
            )

        # TODO(Task 9 – analysis tile): the OLD load also re-ran the EE pipeline
        # here — alert re-discovery (aoi_tile.process_alerts_silent), the
        # filtered alert raster (alert_filter_tile.create_filtered_alert_raster)
        # and the planet before/after image dictionaries
        # (create_planet_images_dictionary). Those reconstruct EE + analysis-tile
        # state, and the analysis tile is not ported yet, so they are deferred to
        # Task 9. The model round-trip + loading the saved alert gdf above is the
        # scope of this step.
        # TODO(navigation foundation): the OLD load navigated to the analysis
        # tile. Set the intent only (same deferral as _on_create_click / aoi_tile).
        app_state.current_page_view.value = "analysis_tile"
        return True

    load_task = solara.lab.use_task(
        _run_load, dependencies=None, prefer_threaded=True, raise_error=False
    )

    def _on_load_click(*_):
        load_task()

    # ------------------------------------------------------------------
    # Layout: two cards (New recipe / Load recipe), memoized builder.
    # ------------------------------------------------------------------
    cards = solara.use_memo(
        lambda: _build_cards(new_recipe_input, load_recipe_input),
        dependencies=[],
    )
    new_card, load_card = cards

    with solara.Column(style="padding: 0;"):
        with solara.Div():
            solara.display(new_card)
            solara.Button(
                label=cm.recipe_tile.button_new_recipe,
                on_click=_on_create_click,
                color="primary",
                large=True,
                class_="ma-5",
            )
        with solara.Div():
            solara.display(load_card)
            solara.Button(
                label=cm.recipe_tile.button_load_recipe,
                on_click=_on_load_click,
                loading=load_task.pending,
                disabled=load_task.pending,
                color="primary",
                large=True,
                class_="ma-5",
            )
            if load_task.error:
                solara.Error(
                    f"{cm.recipe_tile.input_hint_load_recipe_name}: "
                    f"{load_task.exception}",
                    class_="ma-5",
                )


def _build_new_recipe_input(app_tile_model):
    """Build the pre-filled new-recipe TextField and bind it once.

    Pre-fills a generated ``recipe_YYYY-MM-DD-HHMMSS`` name (same as the old
    tile) and binds it to ``app_tile_model.temporary_recipe_name``.
    """
    field = sw.TextField(
        class_="pa-1 ma-1 d-flex align-center",
        v_model=generate_recipe_string(),
        hint=cm.recipe_tile.input_hint_set_recipe_name,
        persistent_hint=True,
    )
    app_tile_model.bind(field, "temporary_recipe_name")
    return field


def _build_cards(new_recipe_input, load_recipe_input):
    """Build the two v.Card layouts once (inside use_memo).

    Recipe is a form tile (no map); the step panel's opaque background covers the
    fullscreen main map (z-index rule in solara_app.py), so no backdrop is needed.
    """
    new_card = v.Card(
        class_="pa-3 ma-5",
        hover=True,
        children=[
            v.CardTitle(
                class_="pa-1 ma-1", children=[cm.recipe_tile.card_title_new_recipe]
            ),
            new_recipe_input,
        ],
    )
    load_card = v.Card(
        class_="pa-3 ma-5",
        hover=True,
        children=[
            v.CardTitle(
                class_="pa-1 ma-1", children=[cm.recipe_tile.card_title_load_recipe]
            ),
            load_recipe_input,
        ],
    )
    return new_card, load_card
