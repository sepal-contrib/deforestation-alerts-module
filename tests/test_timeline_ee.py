"""Tier 2: the Landsat rebuild against real Earth Engine. Skips without credentials."""

import pytest

pytestmark = pytest.mark.ee


def _geom():
    import ee

    return ee.Geometry.Rectangle([-55.0, -10.0, -54.9, -9.9])


def _a_real_landsat_scene_id(geom):
    """One ``LANDSAT_SCENE_ID`` the surface-reflectance product really holds
    over ``geom`` -- what the cached index's Landsat keys are (spec §5.1)."""
    import ee

    from gui.tile.analysis import imagery

    coll = ee.ImageCollection(imagery.LANDSAT_SR_COLLECTIONS[0])
    for cid in imagery.LANDSAT_SR_COLLECTIONS[1:]:
        coll = coll.merge(ee.ImageCollection(cid))
    ids = (coll.filterBounds(geom).filterDate("2024-01-01", "2024-04-01")
           .aggregate_array("LANDSAT_SCENE_ID").getInfo())
    if not ids:
        pytest.skip("no Landsat scene over the test AOI in the probed window")
    return ids[0]


def test_a_landsat_scene_with_its_toa_twin_is_pan_sharpened(gee):
    """The happy path, which only Earth Engine can confirm: both halves of the
    acquisition resolve and ``SFIM_pan_sharpen`` yields the seven harmonized
    bands ``vis1`` / ``vis2`` ask for by name."""
    from gui.tile.analysis import imagery

    geom = _geom()
    img = imagery._landsat_pair([_a_real_landsat_scene_id(geom)], geom)
    assert sorted(img.bandNames().getInfo()) == ["B1", "B2", "B3", "B4", "B6", "B7", "B8"]


def test_a_landsat_scene_with_no_toa_match_still_builds_an_image(gee, monkeypatch):
    """Spec §9: "Landsat TOA match missing → fall back to the unsharpened SR
    image". T1_TOA is not a perfect mirror of T1_L2 -- a scene can be in one and
    not the other. Pan-sharpening against an empty mosaic would otherwise take
    down the pane for a scene the reviewer can see on the timeline.

    The missing twin is produced by looking the scene up in Landsat 7's TOA
    collection, which exists and can never hold an ``LC08``/``LC09`` scene id:
    a fabricated id would miss the *surface* product too, which is a different
    (and impossible) case, since every Landsat key in the index comes from
    ``LANDSAT_SR_COLLECTIONS`` in the first place.
    """
    from gui.tile.analysis import imagery

    geom = _geom()
    scene_id = _a_real_landsat_scene_id(geom)
    monkeypatch.setattr(imagery, "LANDSAT_TOA_COLLECTIONS", ("LANDSAT/LE07/C02/T1_TOA",))
    img = imagery._landsat_pair([scene_id], geom)
    assert img is not None
    assert img.bandNames().size().getInfo() > 0  # the SR fallback, not a raise
