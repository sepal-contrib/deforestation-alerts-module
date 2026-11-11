"""Version 1 recipe folders in the project list (v1 migration, task 4)."""

from datetime import datetime

from deforestation_alerts.project import io
from deforestation_alerts.project.models import Project
from deforestation_alerts.project.v1_migration import RECIPE_FILE, TABLE_FILE, convert
from tests._v1_helpers import write_recipe


def _by_name(root):
    return {i.name: i for i in io.list_projects(root)}


def test_v1_folder_is_listed_as_legacy(tmp_path):
    folder = write_recipe(tmp_path / "old", selected_alert_sources=["GLAD-L", "RADD"])
    info = _by_name(tmp_path)["old"]
    assert info.legacy and info.n_periods == 1 and info.n_sets == 1 and info.n_sources == 2
    assert info.aoi_name == "BRA_Acre"
    assert info.updated_at == datetime.fromtimestamp((folder / TABLE_FILE).stat().st_mtime)


def test_non_list_sources_count_none(tmp_path):
    write_recipe(tmp_path / "old", selected_alert_sources=5)
    assert _by_name(tmp_path)["old"].n_sources == 0


def test_recipe_without_table_is_not_listed(tmp_path):
    folder = write_recipe(tmp_path / "old")
    (folder / TABLE_FILE).unlink()
    assert "old" not in _by_name(tmp_path)


def test_converted_folder_is_a_normal_project(tmp_path):
    folder = write_recipe(tmp_path / "old")
    convert(folder, lambda params: {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}}]})
    assert not _by_name(tmp_path)["old"].legacy


def test_v2_projects_unchanged(tmp_path):
    io.save_project(Project(name="new", created_at=datetime.now(), updated_at=datetime.now()), tmp_path)
    write_recipe(tmp_path / "old")
    infos = _by_name(tmp_path)
    assert not infos["new"].legacy and infos["old"].legacy


def test_unreadable_recipe_json_is_skipped(tmp_path):
    folder = write_recipe(tmp_path / "old")
    (folder / RECIPE_FILE).write_text("{not json", encoding="utf-8")
    write_recipe(tmp_path / "list").joinpath(RECIPE_FILE).write_text("[]", encoding="utf-8")
    assert _by_name(tmp_path) == {}
