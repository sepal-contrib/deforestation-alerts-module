import pytest

from deforestation_alerts.scripts import mosaics_helper


def test_unknown_source_raises_a_clear_error(monkeypatch):
    monkeypatch.setattr(mosaics_helper, "scaleS2v2", lambda img: img)
    with pytest.raises(ValueError, match="Unsupported imagery source"):
        mosaics_helper.download_both_images(
            object(), object(), "/tmp/x.tif", "Sentinel 2", "MODIS", region=None
        )


def test_downloads_even_when_the_target_exists(monkeypatch, tmp_path):
    """The caller (paths.stage_alert_image) owns caching; a scratch path that
    exists is never a valid cache hit, so the download always runs."""
    calls = []

    class _Img:
        def addBands(self, other):
            return self

    monkeypatch.setattr(mosaics_helper, "scaleS2v2", lambda img: _Img())
    import deforestation_alerts.scripts.raster_export as rx

    monkeypatch.setattr(rx, "download_ee_image", lambda *a, **k: calls.append(a[1]))
    target = tmp_path / "a.tif"
    target.write_bytes(b"stale")
    mosaics_helper.download_both_images(
        object(), object(), str(target), "Sentinel 2", "Landsat", region=None
    )
    assert calls == [str(target)]


def test_the_composite_is_downloaded_at_the_finer_native_scale():
    assert mosaics_helper.composite_scale("Sentinel 2", "Landsat") == 10.0
    assert mosaics_helper.composite_scale("Landsat", "Landsat") == 15.0
    assert mosaics_helper.composite_scale("Planet NICFI", "Sentinel 2") == 4.77
    assert mosaics_helper.composite_scale("Sentinel 1 ascending", "Landsat") == 10.0


def test_a_mixed_pair_scales_each_side_by_its_own_source(monkeypatch):
    import deforestation_alerts.scripts.raster_export as rx

    scaled, scales = [], []

    class _Img:
        def addBands(self, other):
            return self

    monkeypatch.setattr(mosaics_helper, "scaleS2v2", lambda img: scaled.append(("s2", img)) or _Img())
    monkeypatch.setattr(mosaics_helper, "scaleS1", lambda img: scaled.append(("s1", img)) or _Img())
    monkeypatch.setattr(rx, "download_ee_image", lambda *a, **k: scales.append(k["scale"]))
    a, b = object(), object()
    mosaics_helper.download_both_images(a, b, "/tmp/x.tif", "Sentinel 2", "Sentinel 1 descending", None)
    assert scaled == [("s2", a), ("s1", b)]
    assert scales == [10.0]
