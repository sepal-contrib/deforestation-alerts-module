"""Download an Earth Engine image to a local GeoTIFF using the geedim 2.0 `.gd`
accessor. Copied near-verbatim from spatial-risk-module
(srm/spatialrisk/gee/ee_raster_export.py).

Concurrency: geedim drives every tile fetch through ONE process-wide asyncio
runner (``geedim.utils.AsyncRunner`` is a ``@singleton`` holding a single
``Runner``), so two overlapping exports raise ``RuntimeError: This event loop is
already running``. Every geedim export in this app goes through
``download_ee_image``, so the serialization lock lives here — one lock covering
the alert-raster export, the attribute-raster materialization
(``attribute_source``) and the analysis mosaics (``mosaics_helper``), including
the ones started from ``spawn_in_context`` daemon threads.
"""

import logging
import threading
from typing import Any, Optional, Union

import ee

from deforestation_alerts.scripts.gee_progress import notify_download_wait

logger = logging.getLogger("SEPALUI.deforestation_alerts.raster_export")

# Serializes the geedim export section (see the module docstring).
_DOWNLOAD_LOCK = threading.Lock()

# How long a caller waits for an in-flight export before giving up. It has to
# cover a legitimately slow neighbour: geedim tiles national-scale exports, which
# can run for many minutes on a slow SEPAL link. 30 minutes is comfortably above
# that while still turning a wedged export into a visible error rather than a
# session that hangs forever — the caller (a use_task) surfaces the raised
# TimeoutError, and cancelling an export does NOT stop its thread, so an
# unbounded wait could otherwise strand a second user indefinitely.
_DOWNLOAD_LOCK_TIMEOUT_S = 30 * 60


def export_bounds(region: Any) -> Any:
    """The export region for geedim: the *bounds* of ``region``, never its geometry.

    Putting the computed ``.geometry()`` of a table asset in a download request
    makes Earth Engine materialise it into the request description; for a
    large AOI (a single-feature asset of ~1.1M coordinates reproduced this,
    2026-09-11) the server answers "Description length exceeds maximum" on
    every tile. The export grid only depends on the bounding rectangle anyway
    — ``bounds()`` yields the identical transform and shape — while the
    footprint itself is applied by ``clip``, which accepts the collection.
    """
    if region is None:
        return None
    geom = region.geometry() if hasattr(region, "geometry") else region
    return geom.bounds()


def download_ee_image(
    image: ee.Image,
    filename: str,
    region: Optional[Union[ee.Geometry, ee.FeatureCollection]] = None,
    crs: Optional[str] = None,
    crs_transform: Optional[list] = None,
    scale: Optional[float] = None,
    resampling: str = "near",
    dtype: Optional[str] = None,
    overwrite: bool = True,
    num_threads: Optional[int] = None,
    max_tile_size: Optional[int] = None,
    max_tile_dim: Optional[int] = None,
    shape: Optional[tuple] = None,
    scale_offset: bool = False,
    unmask_value: Optional[int] = None,
    nodata_value: Optional[int] = None,
    **kwargs: Any,
) -> None:
    """Download `image` as a GeoTIFF at `filename`. Tiling (so national-scale
    exports stream safely, D4) is controlled by `max_tile_size` (MB) /
    `max_tile_dim` (px); `num_threads` maps to geedim's `max_requests`.

    The export region handed to geedim is always the *bounds* of `region` — the
    footprint itself is applied by the clip (see `export_bounds`).

    Set `unmask_value` to treat zeros as data (the alert raster uses 0 = no-alert,
    so pass e.g. 255 to distinguish masked-out from genuine zero). `nodata_value`
    sets the GeoTIFF NoData tag.
    """
    try:
        import geedim as gd  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise ImportError("Install geedim>=2.0 ('pip install geedim').") from exc

    if not isinstance(image, ee.Image):  # pragma: no cover
        raise ValueError("image must be an ee.Image.")

    # Clip with the region object as given (Geometry, Feature or
    # FeatureCollection) -- never ``region.geometry()``, see ``export_bounds``.
    if unmask_value is not None:
        if region is not None:
            image = image.clip(region)
        image = image.unmask(unmask_value, sameFootprint=False)

    prepare_opts = {"crs_transform": crs_transform, "shape": shape}
    prepare_opts = {k: v for k, v in prepare_opts.items() if v is not None}
    tiff_opts = {
        "max_tile_size": max_tile_size,
        "max_tile_dim": max_tile_dim,
        "max_requests": num_threads,
    }
    tiff_opts = {k: v for k, v in tiff_opts.items() if v is not None}

    # One export at a time, process-wide (see the module docstring). A
    # download that has to wait says so once before blocking.
    if not _DOWNLOAD_LOCK.acquire(blocking=False):
        notify_download_wait()
        if not _DOWNLOAD_LOCK.acquire(timeout=_DOWNLOAD_LOCK_TIMEOUT_S):
            raise TimeoutError(
                f"Timed out after {_DOWNLOAD_LOCK_TIMEOUT_S}s waiting for another "
                f"Earth Engine export to finish; {filename} was not downloaded."
            )
    try:
        img = image.gd.prepareForExport(
            crs=crs,
            region=export_bounds(region),
            scale=scale,
            resampling=resampling,
            dtype=dtype,
            scale_offset=scale_offset,
            **prepare_opts,
        )
        nodata = True if nodata_value is None else nodata_value
        img.gd.toGeoTIFF(
            file=filename, overwrite=overwrite, nodata=nodata, **tiff_opts, **kwargs
        )
    finally:
        _DOWNLOAD_LOCK.release()

    # DEBUG, not INFO: INFO lines on a tracked job's thread become user-visible
    # task milestones (gui/scripts/notify_bridge.py); this one would expose a
    # container scratch path and count as an extra, unplanned step.
    logger.debug("Downloaded %s", filename)
