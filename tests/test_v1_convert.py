"""A version 1 recipe folder converted into a locked version 2 project, in place (v1 migration, task 3)."""

import hashlib
import os

import pytest

from deforestation_alerts.project import io, paths
from deforestation_alerts.project.review_copy import read_only
from deforestation_alerts.project.v1_migration import (
    CONVERTING,
    RECIPE_FILE,
    TABLE_FILE,
    V1MigrationError,
    convert,
    is_v1_recipe,
    parse_vis,
)
from deforestation_alerts.scripts.alert_store import load_gdf_from_parquet
from gui.i18n import t
from tests._v1_helpers import write_recipe

AOI_FC = {"type": "FeatureCollection", "features": [{
    "type": "Feature", "properties": {},
    "geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]]},
}]}
V1_FILES = sorted([RECIPE_FILE, TABLE_FILE])


def _resolve(params):
    return AOI_FC


def _convert(tmp_path, rows=None, **params):
    folder = write_recipe(tmp_path / "recipe_2026-09-29-223039", rows=rows, **params)
    p = convert(folder, _resolve)
    return p, folder


def _sha1(path):
    return hashlib.sha1(path.read_bytes()).hexdigest()


def test_converted_project_loads(tmp_path):
    _, folder = _convert(tmp_path)
    p = io.load_project(folder.name, folder.parent)
    assert p.migrated_from.version == 1 and p.migrated_from.n_alerts == 4 and p.migrated_from.n_reviewed == 3
    assert read_only(p) and p.multi_evaluator is False and p.locked


def test_period_and_set(tmp_path):
    p, _ = _convert(tmp_path)
    assert p.active_period == "2026-08-01_2026-08-31"
    s = p.alert_sets[p.active_set]
    assert s.sources == ["GLAD-L"] and s.digit_map == {0: "GLAD-L"}
    assert s.n_alerts == 4 and s.n_reviewed == 3
    assert s.fingerprint.startswith("v1:") and s.params.min_size_ha == 0.5 and s.params.area_mode == "aoi"
    assert p.periods[p.active_period].sources == ["GLAD-L"] and p.sources["GLAD-L"].digit == 0


def test_store_is_where_the_set_says(tmp_path):
    p, folder = _convert(tmp_path)
    s = p.alert_sets[p.active_set]
    gdf = load_gdf_from_parquet(paths.resolve(folder, s.store_rel_path))
    assert gdf["set_id"].eq(s.id).all() and list(gdf["alert_id"]) == [0, 1, 2, 3]
    assert gdf["detected_area_ha"].iloc[0] == pytest.approx(100 * 0.0784)


def test_aoi_sidecar_written(tmp_path):
    p, folder = _convert(tmp_path)
    assert p.aoi.geometry_file == "aoi.geojson" and (folder / "aoi.geojson").is_file()
    assert p.aoi.name == "BRA_Acre" and p.aoi.method == "ADMIN1" and p.aoi.bounds == (0.0, 0.0, 1.0, 1.0)
    assert len(p.aoi.digest) == 40


def test_v1_files_untouched(tmp_path):
    folder = write_recipe(tmp_path / "r")
    before = (_sha1(folder / RECIPE_FILE), _sha1(folder / TABLE_FILE))
    convert(folder, _resolve)
    assert (_sha1(folder / RECIPE_FILE), _sha1(folder / TABLE_FILE)) == before


@pytest.mark.parametrize("actual, kept", [(2, 2), (99, 3)])
def test_last_alert_kept(tmp_path, actual, kept):
    p, _ = _convert(tmp_path, actual_alert_id=actual)
    assert p.alert_sets[p.active_set].last_alert_index == kept


def test_resolver_failure_leaves_nothing(tmp_path):
    folder = write_recipe(tmp_path / "r")

    def boom(params):
        raise RuntimeError("no Earth Engine")

    with pytest.raises(V1MigrationError) as exc:
        convert(folder, boom)
    assert exc.value.key == "migrated.err_aoi" and exc.value.values == {"error": "no Earth Engine"}
    assert sorted(os.listdir(folder)) == V1_FILES
    assert is_v1_recipe(folder)


def test_late_failure_removes_written_files(tmp_path, monkeypatch):
    folder = write_recipe(tmp_path / "r")

    def fail(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(io, "save_project", fail)
    with pytest.raises(V1MigrationError):
        convert(folder, _resolve)
    assert sorted(os.listdir(folder)) == V1_FILES


def test_reload_failure_leaves_nothing(tmp_path, monkeypatch):
    folder = write_recipe(tmp_path / "r")

    def fail(*a, **k):
        raise ValueError("manifest does not validate")

    monkeypatch.setattr(io, "load_project", fail)
    with pytest.raises(V1MigrationError):
        convert(folder, _resolve)
    assert sorted(os.listdir(folder)) == V1_FILES and is_v1_recipe(folder)


def test_existing_aoi_file_is_refused(tmp_path):
    folder = write_recipe(tmp_path / "r")
    (folder / "aoi.geojson").write_text("mine", encoding="utf-8")
    before = _sha1(folder / "aoi.geojson")
    with pytest.raises(V1MigrationError) as exc:
        convert(folder, _resolve)
    assert exc.value.key == "migrated.err_leftover" and exc.value.values == {"files": "aoi.geojson"}
    assert _sha1(folder / "aoi.geojson") == before
    assert sorted(os.listdir(folder)) == sorted([*V1_FILES, "aoi.geojson"])
    text = t("migrated.err_leftover", files="aoi.geojson")
    assert "aoi.geojson" in text and "was not changed" in text


def test_interrupted_conversion_converts_again(tmp_path):
    p, _ = _convert(tmp_path / "a")
    rel = p.alert_sets[p.active_set].store_rel_path
    folder = write_recipe(tmp_path / "r")
    # what a kernel killed after the store and the sidecar, before the manifest, leaves
    (folder / rel).parent.mkdir(parents=True)
    (folder / rel).write_bytes(b"half")
    (folder / "aoi.geojson").write_text("half", encoding="utf-8")
    (folder / CONVERTING).touch()
    assert is_v1_recipe(folder)
    p = convert(folder, _resolve)
    assert p.migrated_from is not None and not (folder / CONVERTING).exists()
    assert len(load_gdf_from_parquet(folder / rel)) == 4 and (folder / "aoi.geojson").read_text() != "half"


def test_no_marker_left(tmp_path, monkeypatch):
    _, folder = _convert(tmp_path / "ok")
    assert not (folder / CONVERTING).exists()
    folder = write_recipe(tmp_path / "r")
    monkeypatch.setattr(io, "save_project", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(V1MigrationError):
        convert(folder, _resolve)
    assert sorted(os.listdir(folder)) == V1_FILES


def test_existing_store_is_refused(tmp_path):
    p, _ = _convert(tmp_path / "a")
    rel = p.alert_sets[p.active_set].store_rel_path
    folder = write_recipe(tmp_path / "r")
    store = folder / rel
    store.parent.mkdir(parents=True)
    store.write_bytes(b"mine")
    with pytest.raises(V1MigrationError) as exc:
        convert(folder, _resolve)
    assert exc.value.key == "migrated.err_leftover" and store.read_bytes() == b"mine"
    assert not (folder / "aoi.geojson").exists() and not (folder / paths.MANIFEST).exists()


def test_late_failure_keeps_an_existing_periods_folder(tmp_path, monkeypatch):
    folder = write_recipe(tmp_path / "r")
    (folder / "periods").mkdir()
    monkeypatch.setattr(io, "save_project", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(V1MigrationError):
        convert(folder, _resolve)
    assert sorted(os.listdir(folder)) == sorted([*V1_FILES, "periods"]) and os.listdir(folder / "periods") == []


def test_unreadable_table_and_params(tmp_path):
    folder = write_recipe(tmp_path / "r")
    (folder / TABLE_FILE).write_text("", encoding="utf-8")
    with pytest.raises(V1MigrationError) as exc:
        convert(folder, _resolve)
    assert exc.value.key == "migrated.err_table"
    folder = write_recipe(tmp_path / "r2", start_date="not a date")
    with pytest.raises(V1MigrationError) as exc:
        convert(folder, _resolve)
    assert exc.value.key == "migrated.err_params"
    assert sorted(os.listdir(folder)) == V1_FILES


def test_stored_folder_path_is_ignored(tmp_path):
    folder = write_recipe(tmp_path / "r")
    params = (folder / RECIPE_FILE).read_text(encoding="utf-8").replace(str(folder), "/nowhere/x")
    (folder / RECIPE_FILE).write_text(params, encoding="utf-8")
    p = convert(folder, _resolve)
    assert (folder / paths.MANIFEST).is_file() and p.name == "r"
    assert not os.path.exists("/nowhere/x")


def test_empty_table_converts(tmp_path):
    p, folder = _convert(tmp_path, rows=[])
    assert p.alert_sets[p.active_set].n_alerts == 0 and p.alert_sets[p.active_set].last_alert_index == 0
    assert io.load_project(folder.name, folder.parent).migrated_from.n_alerts == 0


def test_drawn_selection_area(tmp_path):
    fc = {"type": "FeatureCollection", "features": AOI_FC["features"]}
    p, _ = _convert(tmp_path, alert_selection_polygons=fc, alert_sorting_method=3, max_number_alerts=10)
    params = p.alert_sets[p.active_set].params
    assert params.area_mode == "drawn" and params.area_geojson == fc
    assert params.sorting == 3 and params.max_alerts == 10


def test_mask_aux_ccdc_imported(tmp_path):
    p, _ = _convert(tmp_path, mask_layer="users/a/mask", aux_layer="users/a/aux",
                    aux_layer_vis="{'min': 0, 'max': 3, 'palette': ['red','blue']}", ccdc_layer="users/a/ccdc",
                    selected_alert_sources=["GLAD-L", "CCDC"])
    s = p.alert_sets[p.active_set]
    period = p.periods[p.active_period]
    assert "v1 mask" in p.masks and "v1 mask" in p.vis_layers and p.masks["v1 mask"].ref == "users/a/mask"
    assert period.masks["v1 mask"].mode == "keep"
    assert s.masks_used == [{"name": "v1 mask", "mode": "keep", "content_id": "v1"}]
    assert p.vis_layers["v1 layer"].max == 3 and p.vis_layers["v1 layer"].palette == ["red", "blue"]
    assert p.sources["CCDC"].format == "ccdc" and p.sources["CCDC"].band_map.date == "first_detection_date"
    assert s.digit_map[3] == "CCDC" and s.sources == ["CCDC", "GLAD-L"]
    assert period.digits == {"CCDC": 3} and set(period.sources) == {"GLAD-L", "CCDC"}


def test_ccdc_without_layer_is_noted(tmp_path):
    p, _ = _convert(tmp_path, selected_alert_sources=["GLAD-L", "CCDC"])
    s = p.alert_sets[p.active_set]
    assert s.digit_map == {0: "GLAD-L", 3: "CCDC"} and "CCDC" not in p.sources
    assert any("CCDC" in n for n in p.migrated_from.notes)


def test_bad_layer_vis_falls_back_to_defaults(tmp_path):
    p, _ = _convert(tmp_path, aux_layer="users/a/aux", aux_layer_vis="{'bands': 'B4'}")
    layer = p.vis_layers["v1 layer"]
    assert layer.asset == "users/a/aux" and layer.bands == [] and (layer.min, layer.max) == (0.0, 1.0)
    notes = [n for n in p.migrated_from.notes if "v1 layer" in n]
    assert len(notes) == 1 and "default display" in notes[0] and "not imported" not in notes[0]


def test_unreadable_vis_text_uses_defaults():
    assert parse_vis("true color") == {}
    assert parse_vis(None) == {}
    assert parse_vis("'min': 1, 'gamma': 2") == {"min": 1}


def test_report_template_kept_when_it_exists(tmp_path):
    tpl = tmp_path / "t.docx"
    tpl.write_bytes(b"x")
    p, _ = _convert(tmp_path, custom_report_template=str(tpl))
    assert p.report_template == str(tpl)
    p, _ = _convert(tmp_path / "b", custom_report_template=str(tmp_path / "gone.docx"))
    assert p.report_template is None


def test_malformed_codes_cell_converts(tmp_path):
    p, folder = _convert(tmp_path, rows=[{}, {"alert_type_unique": "[1,"}])
    assert p.alert_sets[p.active_set].n_alerts == 2
    assert any("alert 1" in n and "alert_type_unique" in n for n in p.migrated_from.notes)


def test_is_v1_recipe(tmp_path):
    folder = write_recipe(tmp_path / "r")
    assert is_v1_recipe(folder)
    convert(folder, _resolve)
    assert not is_v1_recipe(folder)
    other = write_recipe(tmp_path / "s")
    (other / TABLE_FILE).unlink()
    assert not is_v1_recipe(other)


def test_converting_a_project_is_refused(tmp_path):
    folder = write_recipe(tmp_path / "r")
    convert(folder, _resolve)
    manifest = _sha1(folder / paths.MANIFEST)
    with pytest.raises(V1MigrationError):
        convert(folder, _resolve)
    assert _sha1(folder / paths.MANIFEST) == manifest
