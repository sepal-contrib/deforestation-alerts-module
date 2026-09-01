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

import pytest

from deforestation_alerts.model.app_model import AppTileModel
from gui.store.state_manager import AppState
from gui.tile.recipe_tile import normalize_picked_path, start_new_recipe


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


# ---------------------------------------------------------------------------
# The picker's path is the server's echo, not one we built (Task 11 fix round 1)
# ---------------------------------------------------------------------------
def test_normalize_picked_path_accepts_an_absolute_workspace_path(ws):
    picked = ws.results_dir("r1", "recipe_parameters.json")
    assert normalize_picked_path(ws, picked) == picked


def test_normalize_picked_path_reroots_a_home_relative_echo(ws):
    absolute = ws.results_dir("r1", "recipe_parameters.json")
    assert normalize_picked_path(ws, ws.home_relative(absolute)) == absolute


def test_normalize_picked_path_reroots_a_root_relative_echo(ws):
    assert normalize_picked_path(ws, "r1/recipe_parameters.json") == ws.results_dir(
        "r1", "recipe_parameters.json"
    )


def test_normalize_picked_path_rejects_paths_outside_the_workspace(ws):
    with pytest.raises(FileNotFoundError):
        normalize_picked_path(ws, "/etc/passwd")
    with pytest.raises(FileNotFoundError):
        normalize_picked_path(ws, "../../etc/passwd")
