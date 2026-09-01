"""Recipe tile create flow: clicking "New recipe" must persist the recipe by
creating its folder in the user's SEPAL workspace and wiring
``recipe_folder_path`` (domain model + ephemeral app_state), so the Save card
appears and the export/analysis flow can write the alert store into it.

Regression: the Phase-4 port left ``create_directory`` orphaned — the create
handler only set the name + navigated, so a new recipe was never written and
``recipe_folder_path`` stayed empty.

Task 11 moved the folder from the CONTAINER's ``module_results`` to the user's
SEPAL workspace, so "created" is now asserted through the ``Workspace`` adapter
and the wired path is remote.

No GEE, no browser.
"""

from deforestation_alerts.model.app_model import AppTileModel
from gui.store.state_manager import AppState
from gui.tile.recipe_tile import start_new_recipe


def test_start_new_recipe_creates_folder_and_wires_path(ws):
    app_tile_model = AppTileModel()
    app_state = AppState()

    start_new_recipe(app_tile_model, app_state, "recipe_2026-06-11-120000", ws)

    expected = ws.results_dir("recipe_2026-06-11-120000")
    # the recipe folder is actually created — in the USER's workspace, not the
    # container's filesystem
    assert ws.exists(expected)
    assert ws.client.files.mkdir_calls == [expected]
    # both the domain model and the ephemeral app_state point at the new folder
    assert app_tile_model.recipe_folder_path == expected
    assert app_state.recipe_folder_path.value == expected
    # name recorded + navigation to the Aux step preserved
    assert app_tile_model.recipe_name == "recipe_2026-06-11-120000"
    assert app_state.current_page_view.value == "aux_tile"
