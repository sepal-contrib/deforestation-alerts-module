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


def test_a_landsat_scene_with_its_toa_twin_is_pan_sharpened(gee, monkeypatch):
    """The happy path, which only Earth Engine can confirm: both halves of the
    acquisition resolve, and the image really is sharpened.

    Band names cannot witness that. ``harmonizeL8ToS2_scaled`` renames
    ``SR_B1..SR_B7`` the same way on both sides of the ``ee.Algorithms.If``
    and ``SFIM_pan_sharpen`` preserves the names, so the two branches are
    name-identical and only the pixels tell them apart. So: build the same
    scene twice, once with its real TOA twin and once with the twin made
    unreachable, and require the pixels to differ. An ``If`` wired to the
    wrong branch, or a pan-sharpen that silently became a pass-through,
    collapses that difference to zero.
    """
    import ee

    from gui.tile.analysis import imagery

    geom = _geom()
    scene_id = _a_real_landsat_scene_id(geom)
    sharpened = imagery._landsat_pair([scene_id], geom)
    assert sorted(sharpened.bandNames().getInfo()) == ["B1", "B2", "B3", "B4", "B6", "B7", "B8"]

    monkeypatch.setattr(imagery, "LANDSAT_TOA_COLLECTIONS", ("LANDSAT/LE07/C02/T1_TOA",))
    plain = imagery._landsat_pair([scene_id], geom)
    spread = sharpened.subtract(plain).abs().reduceRegion(
        ee.Reducer.max(), geom, 30, bestEffort=True).getInfo()
    values = [v for v in spread.values() if v is not None]
    assert values, f"no overlap to compare over: {spread}"
    assert max(values) > 0


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


def test_the_pan_sharpened_landsat_keeps_the_scene_grid_and_is_not_one_flat_pixel(gee):
    """The regression behind "Landsat shows nothing": a flat olive square.

    ``_landsat_pair`` mosaics its granules before pan-sharpening, and an Earth
    Engine composite has no fixed projection -- ``.projection()`` on one is the
    default WGS84 at *one degree* (~111 km) per pixel. ``SFIM_pan_sharpen``
    reads both projections to size its kernel and ends on
    ``.reproject(pan.projection())``, so with composites it pins the result to
    a 111 km grid. A whole alert grid then falls inside a single pixel and the
    pane draws one uniform colour -- which is what the reviewer sees.

    Two assertions, because either alone is weak: the output must still be on
    the scene's metre-scale grid, and the pixels over the grid must actually
    vary. ``test_a_landsat_scene_with_its_toa_twin_is_pan_sharpened`` does not
    catch this -- a flat image still differs from the unsharpened one.
    """
    import ee

    from gui.tile.analysis import imagery

    geom = _geom()
    scene_id = _a_real_landsat_scene_id(geom)
    img = imagery._landsat_pair([scene_id], geom)

    scale = img.projection().nominalScale().getInfo()
    assert scale < 100, f"output is on the 1-degree default grid, not the scene's: {scale} m/px"

    stats = img.select(["B4", "B3", "B2"]).reduceRegion(
        ee.Reducer.stdDev(), geom, 30, bestEffort=True).getInfo()
    spreads = [v for v in stats.values() if v is not None]
    assert spreads, f"no pixels to measure over: {stats}"
    assert min(spreads) > 0, f"the image is one flat colour over the grid: {stats}"
