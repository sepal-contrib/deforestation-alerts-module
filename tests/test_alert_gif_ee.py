"""Tier 2: a real Sentinel-2 frame through getThumbURL, outlined, assembled."""

import pytest

pytestmark = pytest.mark.ee


def test_two_real_frames_make_a_gif(gee, tmp_path):
    import ee
    from PIL import Image

    from deforestation_alerts.scripts import alert_gif

    region = ee.Geometry.Rectangle([-55.0, -10.0, -54.98, -9.98])
    coll = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(region)
            .filterDate("2024-06-01", "2024-09-01").sort("CLOUDY_PIXEL_PERCENTAGE").limit(2))
    lst = coll.toList(2)
    if coll.size().getInfo() < 2:
        pytest.skip("fewer than two Sentinel-2 scenes over the probe AOI")
    images = [ee.Image(lst.get(0)), ee.Image(lst.get(1))]
    vis = {"min": 0, "max": 3000, "bands": ["B4", "B3", "B2"]}
    outline = ee.Geometry.Rectangle([-54.995, -9.995, -54.985, -9.985])
    pngs = [alert_gif.fetch_frame(alert_gif.frame_image(img.clip(region), vis, outline if i else None), region)
            for i, img in enumerate(images)]
    assert all(p[:8] == b"\x89PNG\r\n\x1a\n" for p in pngs)
    dest = alert_gif.assemble_gif(pngs, ["Before", "After"], tmp_path / "x.gif")
    with Image.open(dest) as im:
        assert im.n_frames == 2
