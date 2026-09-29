"""Filter Analyze download: region rule + progress/queued callbacks."""

from unittest.mock import MagicMock

import ee

from gui.tile import alerts_filter_tile as aft


class _Plan:
    def __init__(self, region, tmp_path):
        self.region = region
        self.out_tif = str(tmp_path / "a.tif")
        self.pixel_size = 10

        class _Raster:
            def select(self, bands):
                return self

        self.filtered_alert_raster = _Raster()


def test_download_alert_raster_passes_the_collection_not_its_geometry(
    monkeypatch, tmp_path
):
    seen = {}
    monkeypatch.setattr(
        aft, "download_ee_image", lambda img, out, **kw: seen.update(kw)
    )
    fc = MagicMock(spec=ee.FeatureCollection)
    aft._download_alert_raster(_Plan(fc, tmp_path))
    assert seen["region"] is fc


def test_download_alert_raster_routes_tiles_and_wait(monkeypatch, tmp_path):
    from deforestation_alerts.scripts import gee_progress

    def fake_download(img, out, **kw):
        gee_progress.notify_download_wait()

    monkeypatch.setattr(aft, "download_ee_image", fake_download)
    waits = []
    aft._download_alert_raster(
        _Plan(None, tmp_path), on_tiles=lambda d, t: None, on_wait=lambda: waits.append(1)
    )
    assert waits == [1]


def test_analyze_downloads_in_context_with_forwarded_logs():
    import inspect

    src = inspect.getsource(aft)
    body = src[src.index("async def _analyze("):src.index("def _start_analyze(")]
    assert "to_thread_in_context(" in body
    assert "forward_logs_to(" in src
    assert "tile_progress_reporter(" in body
    assert "analyze_status_queued" in body
