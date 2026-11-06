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
