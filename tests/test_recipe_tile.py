"""Recipe tile create flow: clicking "New recipe" must persist the recipe by
creating its folder on disk and wiring ``recipe_folder_path`` (domain model +
ephemeral app_state), so the Save card appears and the export/analysis flow can
write ``alert_db.csv`` into it.

Regression: the Phase-4 port left ``create_directory`` orphaned — the create
handler only set the name + navigated, so a new recipe was never written to
``module_results/deforestation_alerts/`` and ``recipe_folder_path`` stayed empty.

No GEE, no browser.
"""

import os

from deforestation_alerts.model.app_model import AppTileModel
from deforestation_alerts.parameter import directory
from gui.store.state_manager import AppState
from gui.tile.recipe_tile import start_new_recipe


def test_start_new_recipe_creates_folder_and_wires_path(tmp_path, monkeypatch):
    # Redirect module_dir so the test never touches the real ~/module_results.
    monkeypatch.setattr(directory, "module_dir", tmp_path)
    app_tile_model = AppTileModel()
    app_state = AppState()

    start_new_recipe(app_tile_model, app_state, "recipe_2026-06-11-120000")

    folder = tmp_path / "recipe_2026-06-11-120000"
    expected = os.path.abspath(folder)
    # the recipe folder is actually created on disk
    assert folder.is_dir()
    # both the domain model and the ephemeral app_state point at the new folder
    assert app_tile_model.recipe_folder_path == expected
    assert app_state.recipe_folder_path.value == expected
    # name recorded + navigation to the Aux step preserved
    assert app_tile_model.recipe_name == "recipe_2026-06-11-120000"
    assert app_state.current_page_view.value == "aux_tile"
