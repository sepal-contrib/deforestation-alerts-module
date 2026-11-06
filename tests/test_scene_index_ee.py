# tests/test_scene_index_ee.py
"""Tier 2: the one scene-index request against real Earth Engine. Skips without credentials."""

from datetime import date

import pandas as pd
import pytest

from deforestation_alerts.scripts import scene_index

START, END = date(2025, 6, 1), date(2025, 8, 31)

#: The AOI of ``tests/conftest.py``'s ``tiny_aoi``, pinned here because that
#: fixture is function-scoped and a module-scoped one cannot depend on it: the
#: three-month index is fetched once and read by every test below.
AOI_RECT = [-55.10, -10.10, -55.00, -10.00]  # ~Amazon

#: A monthly collection every account can read, standing in for the
#: licence-gated NICFI basemaps where only the request's shape is at stake.
MONTHLY_COLLECTION = "MODIS/061/MOD13A3"

#: The surface-reflectance / TOA pairs Task 12's rebuild searches, pinned here
#: rather than derived from ``LANDSAT_COLLECTIONS`` so the test names the ids
#: it needs: pan-sharpening wants both members of one satellite's pair.
LANDSAT_PAIRS = (("LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC08/C02/T1_TOA"),
                 ("LANDSAT/LC09/C02/T1_L2", "LANDSAT/LC09/C02/T1_TOA"))

#: ``LANDSAT_SCENE_ID``, e.g. ``LC80440342013330LGN00``: sensor, satellite,
#: path, row, year, day of year, ground station, version.
SCENE_ID = r"L[A-Z]\d{14}[A-Z]{3}\d{2}"


@pytest.fixture(scope="module")
def tiny_aoi(gee):
    import ee

    return ee.Geometry.Rectangle(AOI_RECT)


@pytest.fixture(scope="module")
def tables(tiny_aoi):
    return scene_index.parse_scenes(scene_index.scene_request(tiny_aoi, START, END).getInfo())


def test_a_scene_acquired_on_the_last_day_of_the_window_is_included(tiny_aoi, gee):
    """``filterDate``'s end is exclusive, so an inclusive ``end`` has to ask for
    the day after. A one-day window is the cheapest way to catch a regression:
    it returns nothing at all if the off-by-one comes back."""
    import ee

    # The same cloud prefilter ``scene_request`` applies: a granule it would
    # drop is no use as the day to ask for.
    day = (ee.ImageCollection(scene_index.S2_COLLECTION).filterBounds(tiny_aoi)
           .filterDate(START.isoformat(), END.isoformat())
           .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", scene_index.MAX_CLOUD))
           .first().date().format("YYYY-MM-dd").getInfo())
    one = date.fromisoformat(day)
    tables = scene_index.parse_scenes(scene_index.scene_request(tiny_aoi, one, one).getInfo())
    assert len(tables.scenes) > 0
    assert (tables.scenes["date"] == pd.Timestamp(one)).all()


def test_the_whole_index_arrives_in_one_round_trip(tables):
    assert len(tables.scenes) > 0
    assert set(tables.scenes["source"]) <= {scene_index.SOURCE_S2, scene_index.SOURCE_LANDSAT}
    assert tables.scenes["date"].between(pd.Timestamp(START), pd.Timestamp(END)).all()
    assert tables.scenes["cloud"].between(0, 90).all()  # the < 90 prefilter the retired imagery_service applied
    assert (tables.scenes["key"] != "").all()


def test_the_parallel_arrays_come_back_paired(tables):
    """``source``, ``key`` and ``tile`` travel as three separate arrays, so a
    row only means anything if all three are aggregated in one order: an
    S2 row has to carry an S2 asset id and that granule's own index as its
    tile, a Landsat row a
    ``LANDSAT_SCENE_ID`` -- which is not an asset id -- and a WRS-2 path/row."""
    s2 = tables.scenes[tables.scenes["source"] == scene_index.SOURCE_S2]
    assert len(s2) > 0
    assert s2["key"].str.startswith(scene_index.S2_COLLECTION + "/").all()
    assert (s2["key"] == scene_index.S2_COLLECTION + "/" + s2["tile"]).all()

    # Asserted, not skipped: the AOI and the window are pinned constants and
    # three months over them always hold Landsat 8/9 passes, so an empty
    # Landsat side means ``_landsat_rows`` regressed -- which would otherwise
    # skip every Landsat test and leave the suite green.
    ls = tables.scenes[tables.scenes["source"] == scene_index.SOURCE_LANDSAT]
    assert not ls.empty, "the pinned AOI and window must contain Landsat scenes"
    assert ls["key"].str.fullmatch(SCENE_ID).all()
    assert ls["tile"].str.fullmatch(r"\d{3}_\d{3}").all()


def test_every_scene_row_names_a_tile_that_the_tiles_table_defines(tables):
    assert set(tables.scenes["tile"]) <= set(tables.tiles["tile"])
    assert tables.tiles["tile"].is_unique


def test_the_tile_footprints_are_real_footprints_not_bounding_boxes(tables):
    # A Sentinel-2 granule is a square, a Landsat scene a slanted
    # parallelogram: its footprint must be strictly smaller than its envelope.
    ls = tables.tiles.merge(
        tables.scenes[tables.scenes["source"] == scene_index.SOURCE_LANDSAT][["tile"]].drop_duplicates(),
        on="tile",
    )
    if ls.empty:
        pytest.skip("no Landsat scene over the test AOI in this window")
    # Web Mercator: a ratio of two areas, so the projection only has to keep
    # the envelope axis-aligned (it does) -- and ``area`` on lon/lat warns.
    ls = ls.to_crs("EPSG:3857")
    worst = (ls.geometry.area / ls.geometry.envelope.area).min()
    assert worst < 0.95


#: A box on the swath edge in Canindeyú, Paraguay: MGRS tile 21KYP is imaged
#: there by relative orbit 124 (the whole granule) and by relative orbit 24 (a
#: slanted strip ending ~0.2 deg west of the box). Two weeks of August 2026
#: hold passes of both.
EDGE_RECT = [-54.69, -24.44, -54.66, -24.42]
EDGE_START, EDGE_END = date(2026, 8, 1), date(2026, 8, 15)


def test_every_sentinel2_granule_draws_inside_the_footprint_its_tile_records(gee):
    """A tile's footprint has to hold *every* granule filed under it.

    ``distinct("tile")`` keeps the footprint of whichever granule it meets
    first. Keyed by MGRS tile alone, the edge box's 21KYP got orbit 24's
    partial strip whenever that pass came first, and every alert east of the
    strip lost all its Sentinel-2 rows -- 49 of 101 alerts of one Canindeyú
    project. Tile plus relative orbit is not enough either: a granule at the
    end of a datastrip (2026-08-14's 21JYN) is partial within its orbit.
    """
    import ee

    tables = scene_index.parse_scenes(scene_index.scene_request(
        ee.Geometry.Rectangle(EDGE_RECT), EDGE_START, EDGE_END).getInfo())
    s2 = tables.scenes[tables.scenes["source"] == scene_index.SOURCE_S2]
    assert s2["key"].str.endswith("_T21KYP").sum() > 0
    footprints = dict(zip(tables.tiles["tile"], tables.tiles.geometry))
    granules = ee.List([ee.Image(k).geometry() for k in s2["key"]]).getInfo()
    from shapely.geometry import shape

    for tile, key, geo in zip(s2["tile"], s2["key"], granules):
        granule = shape(geo)
        missed = granule.difference(footprints[tile]).area / granule.area
        assert missed < 0.01, f"{key} lies {missed:.0%} outside the footprint of {tile}"


def test_the_sentinel2_keys_are_loadable_asset_ids(tables, gee):
    import ee

    key = tables.scenes[tables.scenes["source"] == scene_index.SOURCE_S2]["key"].iloc[0]
    assert ee.Image(key).bandNames().size().getInfo() > 0


def test_the_landsat_keys_resolve_in_both_the_l2_and_toa_collections(tables, gee):
    """Why the key is a ``LANDSAT_SCENE_ID`` and not an asset id: the rebuild
    has to find the *same* scene in one satellite's surface-reflectance **and**
    TOA collection to pan-sharpen it. Both members of the pair that holds the
    key are therefore required -- "it resolves somewhere" is not enough."""
    import ee

    hit = tables.scenes[tables.scenes["source"] == scene_index.SOURCE_LANDSAT]
    if hit.empty:
        pytest.skip("no Landsat scene over the test AOI in this window")
    key = hit["key"].iloc[0]
    counts = ee.Dictionary({
        cid: ee.ImageCollection(cid).filter(ee.Filter.eq("LANDSAT_SCENE_ID", key)).size()
        for pair in LANDSAT_PAIRS for cid in pair
    }).getInfo()
    found = [pair for pair in LANDSAT_PAIRS if counts[pair[0]]]
    assert found, f"{key} is in neither L2 collection the rebuild searches: {counts}"
    for l2, toa in found:
        assert counts[toa], f"{key} is in {l2} but not in {toa}, which the pan-sharpen needs"


def test_planet_request_returns_one_row_per_month_of_the_window(gee):
    """The window starts at the first of ``start``'s month -- a reviewer asking
    from mid-June still gets June's basemap -- and ``filterDate``'s exclusive
    end still has to reach ``end``'s month. The NICFI basemaps are
    licence-gated (the test below skips without them), so the graph is proven
    on a monthly collection every account can read.
    """
    raw = scene_index.planet_request(MONTHLY_COLLECTION, date(2025, 6, 15), END).getInfo()
    df = scene_index.parse_planet(raw)
    assert list(df["date"]) == [pd.Timestamp("2025-06-01"), pd.Timestamp("2025-07-01"),
                                pd.Timestamp("2025-08-01")]
    assert df["key"].str.startswith(MONTHLY_COLLECTION + "/").all()


def test_planet_months_come_back_as_one_row_each(gee):
    import ee

    from gui.scripts import vis_layers

    cid = vis_layers.NICFI_COLLECTIONS["americas"]
    try:
        ee.ImageCollection(cid).limit(1).size().getInfo()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no NICFI access: {exc}")
    df = scene_index.parse_planet(scene_index.planet_request(cid, START, END).getInfo())
    assert len(df) == 3  # June, July, August
    assert (df["source"] == scene_index.SOURCE_PLANET).all()
    assert df["date"].dt.day.eq(1).all()
