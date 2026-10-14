"""``download_ee_image`` must never put the AOI's *computed* geometry in the request.

Earth Engine rejects a download request whose region (or clip area) is the
``.geometry()`` of a large table asset with "Description length exceeds
maximum" — the server inlines the materialised geometry into the request
description. Reproduced 2026-09-11 with a single-feature asset of ~1.1M
coordinates: every variant using ``fc.geometry()`` failed, while clipping with
the collection itself and exporting over ``geometry().bounds()`` succeeded and
produced the very same export grid.

The rule under test:

- the clip uses the region object as given (Geometry, Feature or
  FeatureCollection are all valid ``clip`` areas);
- the export region handed to geedim is the *bounds* of the region — a
  four-corner rectangle regardless of how heavy the AOI is.

This file also covers the single-flight lock around geedim's shared asyncio
runner: two overlapping downloads must take turns rather than crashing, a
download that has to wait must fire its queued-notice callback exactly once,
and a wedged download must surface as a visible ``TimeoutError`` rather than
hanging forever.
"""

import asyncio
import threading
import time
from unittest.mock import MagicMock

import ee
import geedim.utils  # noqa: F401 -- registers ee.Image.gd before any test below
# monkeypatches ee.Image; otherwise the accessor gets registered on whichever
# class ee.Image points to at that moment instead of the real one (order-dependent
# flake if this file runs standalone rather than after test_gee_progress.py).
import pytest

import deforestation_alerts.scripts.raster_export as export_module
from deforestation_alerts.scripts.raster_export import download_ee_image


class _Bounds:
    """Sentinel for ``<geometry>.bounds()``."""


class _FakeGeometry:
    """Stand-in for an ee.Geometry: has ``bounds`` but no ``geometry``."""

    def __init__(self):
        self.bounds_result = _Bounds()

    def bounds(self):
        return self.bounds_result


class _FakeCollection:
    """Stand-in for an ee.FeatureCollection / ee.Feature."""

    def __init__(self):
        self.geom = _FakeGeometry()

    def geometry(self):
        return self.geom


class _FakeAccessor:
    """Stand-in for the ``image.gd`` geedim accessor."""

    id = None  # no Earth Engine id: ``prefetch_stac`` has nothing to fetch

    def __init__(self, image):
        self._image = image

    def prepareForExport(self, **kwargs):
        self._image.calls.append(("prepareForExport", kwargs))
        return self._image  # geedim returns an ee.Image carrying ``.gd`` again

    def toGeoTIFF(self, **kwargs):
        self._image.calls.append(("toGeoTIFF", kwargs))


class _FakeImage:
    """Stand-in for an ee.Image recording clip/unmask and the geedim calls."""

    def __init__(self):
        self.calls = []
        self.gd = _FakeAccessor(self)

    def clip(self, area):
        self.calls.append(("clip", area))
        return self

    def unmask(self, value, sameFootprint):
        self.calls.append(("unmask", value))
        return self


@pytest.fixture()
def fake_ee(monkeypatch):
    """Make the helper's ``isinstance(image, ee.Image)`` guard accept the fake."""
    monkeypatch.setattr(export_module.ee, "Image", _FakeImage)


def _run(region):
    image = _FakeImage()
    download_ee_image(
        image,
        "out.tif",
        region=region,
        scale=30,
        crs="EPSG:4326",
        unmask_value=255,
        nodata_value=255,
    )
    return image.calls


def test_collection_region_clips_with_collection_and_exports_its_bounds(fake_ee):
    """Collection/Feature AOI: clip(fc), export region = fc.geometry().bounds()."""
    fc = _FakeCollection()
    calls = _run(fc)

    clip_areas = [area for name, area in calls if name == "clip"]
    assert clip_areas == [fc], "clip must use the collection, not its geometry"

    ((_, prepare_kwargs),) = [c for c in calls if c[0] == "prepareForExport"]
    assert prepare_kwargs["region"] is fc.geom.bounds_result


def test_geometry_region_exports_its_bounds(fake_ee):
    """A bare ee.Geometry AOI: clip(geom), export region = geom.bounds()."""
    geom = _FakeGeometry()
    calls = _run(geom)

    clip_areas = [area for name, area in calls if name == "clip"]
    assert clip_areas == [geom]

    ((_, prepare_kwargs),) = [c for c in calls if c[0] == "prepareForExport"]
    assert prepare_kwargs["region"] is geom.bounds_result


def test_no_region_leaves_export_unbounded(fake_ee):
    """Without a region nothing is clipped and geedim gets region=None."""
    calls = _run(None)
    assert not [c for c in calls if c[0] == "clip"]
    ((_, prepare_kwargs),) = [c for c in calls if c[0] == "prepareForExport"]
    assert prepare_kwargs["region"] is None


def test_the_stac_entry_is_fetched_before_the_export_and_retried(fake_ee, monkeypatch):
    """geedim fetches the STAC entry inside ``toGeoTIFF`` with no retry; the
    helper fetches it first, where a transient error is retried (spec §12)."""
    tries = []

    def flaky_prefetch(img):
        tries.append(1)
        img.calls.append(("prefetch_stac", None))
        if len(tries) == 1:
            raise OSError("Temporary failure in name resolution")

    real = export_module.with_retries
    monkeypatch.setattr(export_module, "prefetch_stac", flaky_prefetch)
    monkeypatch.setattr(export_module, "with_retries", lambda fn: real(fn, sleep=lambda s: None))
    names = [name for name, _ in _run(None)]
    assert names == ["unmask", "prepareForExport", "prefetch_stac", "prefetch_stac", "toGeoTIFF"]


def _fake_image():
    """An ``ee.Image`` stand-in whose geedim export blocks on the shared runner."""
    from geedim.utils import AsyncRunner

    image = MagicMock(spec=ee.Image)
    prepared = MagicMock()
    prepared.gd.id = None  # no STAC entry to prefetch

    def to_geotiff(**_kw):
        AsyncRunner().run(asyncio.sleep(0.3))

    prepared.gd.toGeoTIFF.side_effect = to_geotiff
    image.gd.prepareForExport.return_value = prepared
    return image


def test_two_threads_downloading_at_once_both_succeed(tmp_path):
    """Two downloads on two threads take turns instead of the second one crashing."""
    from geedim.utils import AsyncRunner

    # The app's first download creates the singleton; only a *second* download
    # started while another runs hits the shared loop. (Two threads creating
    # it simultaneously get two runners — geedim's ``singleton`` has no lock —
    # which would mask the bug.)
    AsyncRunner().run(asyncio.sleep(0))
    errors = []

    def worker(i):
        try:
            download_ee_image(_fake_image(), str(tmp_path / f"{i}.tif"))
        except Exception as exc:  # the assertion below reads the list
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert errors == []


def test_wait_notice_fires_only_for_the_download_that_queues(tmp_path):
    """The thread stuck behind the lock reports it; the running one stays silent."""
    from deforestation_alerts.scripts import raster_export
    from deforestation_alerts.scripts.gee_progress import geedim_download_wait

    notices = []
    first_in_geedim = threading.Event()

    def make_image(name):
        image = MagicMock(spec=ee.Image)
        prepared = MagicMock()
        prepared.gd.id = None  # no STAC entry to prefetch

        def to_geotiff(**_kw):
            first_in_geedim.set()
            time.sleep(0.3)

        prepared.gd.toGeoTIFF.side_effect = to_geotiff
        image.gd.prepareForExport.return_value = prepared
        return image

    def worker(name):
        with geedim_download_wait(lambda: notices.append(name)):
            raster_export.download_ee_image(
                make_image(name), str(tmp_path / f"{name}.tif")
            )

    first = threading.Thread(target=worker, args=("first",))
    first.start()
    assert first_in_geedim.wait(5)
    second = threading.Thread(target=worker, args=("second",))
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    assert notices == ["second"]


def test_lock_timeout_still_raises(tmp_path, monkeypatch):
    """A download still waiting after the timeout becomes a visible
    TimeoutError. Another session's national download can legitimately hold
    the lock that long, so the user reads what happened and what to do."""
    from unittest.mock import MagicMock

    import ee

    from deforestation_alerts.errors import KeyedError
    from deforestation_alerts.scripts import raster_export
    from gui.i18n import t
    from gui.scripts.error_text import error_text

    monkeypatch.setattr(raster_export, "_DOWNLOAD_LOCK_TIMEOUT_S", 0.05)
    assert raster_export._DOWNLOAD_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(TimeoutError) as waited:
            raster_export.download_ee_image(
                MagicMock(spec=ee.Image), str(tmp_path / "x.tif")
            )
    finally:
        raster_export._DOWNLOAD_LOCK.release()
    assert isinstance(waited.value, KeyedError) and waited.value.key == "errors.download_busy"
    assert waited.value.values == {"minutes": 0, "file": "x.tif"}
    assert error_text(waited.value) == t("errors.download_busy", minutes=0, file="x.tif")
    assert "x.tif" in error_text(waited.value)


def test_export_options_reach_geedim(tmp_path):
    """Options the signature accepts are forwarded, not silently dropped."""
    from unittest.mock import MagicMock

    import ee

    from deforestation_alerts.scripts.raster_export import download_ee_image

    image = MagicMock(spec=ee.Image)
    prepared = MagicMock()
    prepared.gd.id = None  # no STAC entry to prefetch
    image.gd.prepareForExport.return_value = prepared
    download_ee_image(
        image,
        str(tmp_path / "x.tif"),
        crs="EPSG:3857",
        crs_transform=[1, 0, 0, 0, -1, 0],
        shape=(10, 20),
        scale_offset=True,
        max_tile_size=8,
        max_tile_dim=2000,
        num_threads=4,
    )
    prep = image.gd.prepareForExport.call_args.kwargs
    assert prep["crs_transform"] == [1, 0, 0, 0, -1, 0]
    assert prep["shape"] == (10, 20)
    assert prep["scale_offset"] is True
    geo = prepared.gd.toGeoTIFF.call_args.kwargs
    assert geo["max_tile_size"] == 8
    assert geo["max_tile_dim"] == 2000
    assert geo["max_requests"] == 4


def _local_disk_full():
    import errno

    return OSError(errno.ENOSPC, "No space left on device")


@pytest.mark.parametrize("make, tries", [
    (lambda: FileNotFoundError("/x.tif"), 1),
    (lambda: PermissionError("/x.tif"), 1),
    (_local_disk_full, 1),
    (lambda: __import__("socket").gaierror("Temporary failure in name resolution"), 3),
    (lambda: __import__("requests").exceptions.ConnectionError("reset"), 3),
    (lambda: TimeoutError("read timed out"), 3),
], ids=["not-found", "permission", "disk-full", "dns", "requests-connection", "timeout"])
def test_a_local_file_error_is_not_retried_a_network_one_is(make, tries):
    """Review A M5: every OSError used to be retried, a missing file or a
    full disk too; a network error (geedim's requests, aiohttp, DNS) still is."""
    from deforestation_alerts.scripts import raster_export as rx

    calls = []

    def fail():
        calls.append(1)
        raise make()

    with pytest.raises(OSError):
        rx.with_retries(fail, sleep=lambda s: None)
    assert len(calls) == tries


def test_with_retries_retries_transient_errors_only():
    from deforestation_alerts.scripts import raster_export as rx

    sleeps, calls = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise OSError("Temporary failure in name resolution")
        return "ok"

    assert rx.with_retries(flaky, sleep=sleeps.append) == "ok" and len(calls) == 3 and sleeps == [2.0, 4.0]

    def broken():
        raise ValueError("not transient")

    with pytest.raises(ValueError):
        rx.with_retries(broken, sleep=sleeps.append)

    def always():
        raise OSError("down")

    with pytest.raises(OSError):
        rx.with_retries(always, sleep=lambda s: None)


@pytest.mark.parametrize("status, tries", [(404, 1), (403, 1), (503, 3), (429, 3)])
def test_an_http_answer_below_500_is_not_retried(status, tries):
    from types import SimpleNamespace

    import aiohttp

    from deforestation_alerts.scripts import raster_export as rx

    calls = []
    request = SimpleNamespace(real_url="https://earthengine-stac.storage.googleapis.com/catalog/catalog.json")

    def answer():
        calls.append(1)
        raise aiohttp.ClientResponseError(request_info=request, history=(), status=status, message="x")

    with pytest.raises(aiohttp.ClientResponseError):
        rx.with_retries(answer, sleep=lambda s: None)
    assert len(calls) == tries
