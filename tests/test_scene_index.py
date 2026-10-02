# tests/test_scene_index.py
from datetime import date, datetime, timezone

import pytest

from deforestation_alerts.project import paths
from deforestation_alerts.scripts import scene_index


def test_imagery_paths_sit_under_the_project_dir(tmp_path):
    pdir = tmp_path / "amazonia"
    assert paths.imagery_dir(pdir) == pdir / "imagery"
    assert paths.scenes_path(pdir) == pdir / "imagery" / "scenes.parquet"
    assert paths.tiles_path(pdir) == pdir / "imagery" / "tiles.parquet"
    assert paths.scene_manifest_path(pdir) == pdir / "imagery" / "index.json"


def test_row_id_is_stable_and_distinguishes_the_sources():
    d = date(2026, 8, 27)
    assert scene_index.row_id(scene_index.SOURCE_S2, d) == "s20260827"
    assert scene_index.row_id(scene_index.SOURCE_LANDSAT, d) == "l20260827"
    assert scene_index.row_id(scene_index.SOURCE_PLANET, d) == "p20260827"


def test_manifest_round_trips_through_json():
    m = scene_index.SceneManifest(
        aoi_digest="abc123",
        refreshed=datetime(2026, 10, 2, 9, 30, tzinfo=timezone.utc),
        covered=scene_index.Coverage(start=date(2026, 3, 3), end=date(2026, 10, 23)),
        counts={scene_index.SOURCE_S2: 47, scene_index.SOURCE_LANDSAT: 29},
    )
    again = scene_index.SceneManifest.model_validate_json(m.model_dump_json())
    assert again == m
    assert again.version == scene_index.INDEX_VERSION


def test_source_strings_are_the_ones_the_scalers_switch_on():
    from deforestation_alerts.scripts import mosaics_helper

    src = (mosaics_helper.__file__ and open(mosaics_helper.__file__).read())
    for name in (scene_index.SOURCE_S2, scene_index.SOURCE_LANDSAT, scene_index.SOURCE_PLANET):
        assert f'"{name}"' in src
