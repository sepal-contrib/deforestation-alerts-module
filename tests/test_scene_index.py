# tests/test_scene_index.py
from datetime import date, datetime, timezone

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

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


RAW = {
    "scenes": {
        "source": ["Sentinel 2", "Sentinel 2", "Landsat"],
        "millis": [1775088000000, 1775088000000, 1775260800000],  # 2 Apr, 2 Apr, 4 Apr 2026 UTC
        "cloud": [12.5, 40.0, 3.25],
        "tile": ["21LYG", "21LYH", "LC08_227068"],
        "key": ["COPERNICUS/S2_HARMONIZED/A", "COPERNICUS/S2_HARMONIZED/B", "LC82270682026100LGN00"],
    },
    "tiles": {
        "tile": ["21LYG", "21LYH", "LC08_227068"],
        "gtype": ["Polygon", "Polygon", "Polygon"],
        "coords": [
            [[[-55.2, -10.2], [-55.0, -10.2], [-55.0, -10.0], [-55.2, -10.0], [-55.2, -10.2]]],
            [[[-55.0, -10.2], [-54.8, -10.2], [-54.8, -10.0], [-55.0, -10.0], [-55.0, -10.2]]],
            [[[-55.4, -10.4], [-54.6, -10.4], [-54.6, -9.8], [-55.4, -9.8], [-55.4, -10.4]]],
        ],
    },
}


def test_parse_scenes_pairs_each_array_into_rows():
    tables = scene_index.parse_scenes(RAW)
    assert list(tables.scenes.columns) == list(scene_index.SCENE_COLUMNS)
    assert len(tables.scenes) == 3
    row = tables.scenes.iloc[0]
    assert row["source"] == scene_index.SOURCE_S2
    assert row["date"] == pd.Timestamp("2026-04-02")
    assert row["cloud"] == pytest.approx(12.5)
    assert row["tile"] == "21LYG"
    assert row["key"] == "COPERNICUS/S2_HARMONIZED/A"


def test_parse_scenes_builds_tile_footprints_in_wgs84():
    tables = scene_index.parse_scenes(RAW)
    assert isinstance(tables.tiles, gpd.GeoDataFrame)
    assert tables.tiles.crs.to_epsg() == 4326
    assert len(tables.tiles) == 3
    got = tables.tiles.set_index("tile").loc["21LYG", "geometry"]
    assert got.equals(Polygon(RAW["tiles"]["coords"][0][0]))


def test_parse_scenes_handles_a_multipolygon_footprint():
    # A Sentinel-2 granule footprint is occasionally a MultiPolygon (a sliver
    # across the antimeridian, or a split orbit edge), so the parser reads the
    # geometry type the request reports instead of assuming Polygon.
    raw = {
        "scenes": {k: v[:1] for k, v in RAW["scenes"].items()},
        "tiles": {
            "tile": ["21LYG"],
            "gtype": ["MultiPolygon"],
            "coords": [[
                [[[-55.2, -10.2], [-55.1, -10.2], [-55.1, -10.1], [-55.2, -10.1], [-55.2, -10.2]]],
                [[[-55.0, -10.2], [-54.9, -10.2], [-54.9, -10.1], [-55.0, -10.1], [-55.0, -10.2]]],
            ]],
        },
    }
    geom = scene_index.parse_scenes(raw).tiles.iloc[0]["geometry"]
    assert geom.geom_type == "MultiPolygon"
    assert len(geom.geoms) == 2


def test_parse_scenes_rejects_arrays_of_different_lengths():
    bad = {"scenes": {**RAW["scenes"], "cloud": [1.0]}, "tiles": RAW["tiles"]}
    with pytest.raises(ValueError, match="length"):
        scene_index.parse_scenes(bad)


def test_parse_scenes_of_an_empty_aoi_window_keeps_the_columns():
    empty = {"scenes": {k: [] for k in RAW["scenes"]}, "tiles": {"tile": [], "coords": [], "gtype": []}}
    tables = scene_index.parse_scenes(empty)
    assert len(tables.scenes) == 0
    assert list(tables.scenes.columns) == list(scene_index.SCENE_COLUMNS)
    assert tables.tiles.crs.to_epsg() == 4326


def test_parse_planet_rows_carry_no_footprint_and_no_cloud():
    raw = {"key": ["projects/planet-nicfi/assets/basemaps/sr/2026-04"], "millis": [1775001600000]}  # 1 Apr 2026
    df = scene_index.parse_planet(raw)
    assert list(df.columns) == list(scene_index.SCENE_COLUMNS)
    assert df.iloc[0]["source"] == scene_index.SOURCE_PLANET
    assert df.iloc[0]["tile"] == ""
    assert pd.isna(df.iloc[0]["cloud"])


def test_empty_tables_are_usable_without_a_fetch():
    tables = scene_index.empty_tables()
    assert len(tables.scenes) == 0 and len(tables.tiles) == 0


#: the Earth-Engine-facing functions. Pinned so a rename cannot quietly empty
#: the assertion below, and so adding a seventh is a deliberate edit here.
EE_FUNCTIONS = ("_window", "_row", "_s2_rows", "_landsat_rows", "scene_request", "planet_request")


def test_scene_index_imports_earth_engine_only_inside_its_functions():
    """The constraint ``scene_query`` asserts for itself, asserted here too.

    ``scene_query`` imports ``scene_index`` at module scope, so a module-level
    ``import ee`` here would drag Earth Engine into the offline tier *through*
    ``scene_query`` -- and would leave
    ``test_scene_query_never_imports_earth_engine`` passing while meaning
    nothing. ``scene_query``'s own test is a source-text check; this one has to
    read the tree, because this module does contain the words ``import ee`` --
    six times, each inside a function.
    """
    import ast

    tree = ast.parse(open(scene_index.__file__).read())

    at_module_scope = [
        node for node in tree.body
        if (isinstance(node, ast.Import) and any(a.name.split(".")[0] == "ee" for a in node.names))
        or (isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "ee")
    ]
    assert at_module_scope == [], "scene_index must not import ee at module scope"

    # every function that names ``ee`` carries its own import, so a seventh
    # Earth Engine function is covered without editing this test. The
    # candidates are the module's own functions plus the methods of its
    # models -- ``async def`` and methods included, since either could
    # reach for ``ee`` -- but NOT functions nested inside those, which
    # legitimately inherit their parent's local import.
    defs = (ast.FunctionDef, ast.AsyncFunctionDef)
    candidates = [node for node in tree.body if isinstance(node, defs)]
    candidates += [child
                   for node in tree.body if isinstance(node, ast.ClassDef)
                   for child in node.body if isinstance(child, defs)]
    users = {node.name: node for node in candidates
             if any(isinstance(n, ast.Name) and n.id == "ee" for n in ast.walk(node))}
    assert set(EE_FUNCTIONS) <= set(users), f"expected {EE_FUNCTIONS} to use ee, found {sorted(users)}"
    for name, node in users.items():
        assert any(isinstance(n, ast.Import) and any(a.name == "ee" for a in n.names)
                   for n in ast.walk(node)), f"{name} uses ee with no function-local import"
