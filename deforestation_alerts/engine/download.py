"""Download an Earth Engine alert image in its source's native projection (spec §7.2).

User decision 2026-09-30: sources are downloaded natively and aligned onto the
project grid locally when harmonizing, following spatial_risk
(``gee_var.to_local``): ``download_ee_image(crs=<native crs>, scale=<native
scale>, region=aoi)``. ``download_ee_image`` clips to the AOI, exports over its
bounds (``_export_bounds``) and holds the process-wide geedim lock.

Earth Engine snaps an EPSG:4326 export at a given scale outward to whole
multiples of the pixel size, which is the native lattice of all three
predefined sources (checked 2026-09-30, and by tests/test_engine_prepare_ee.py
for GLAD-L), so the download resamples nothing. The raw file is off the project
grid by design and is not header-checked against it; geedim's *average*
overviews on it are harmless, as raw files are never displayed.
"""

import math
from pathlib import Path

import rasterio
from pyproj import Transformer
from rasterio.crs import CRS

from deforestation_alerts.engine import catalogue
from deforestation_alerts.scripts.gee_progress import geedim_download_wait, geedim_tile_progress
from deforestation_alerts.scripts.raster_export import download_ee_image

#: Earth Engine's metres per degree of a geographic CRS (a degree at the equator,
#: 2 * pi * 6378137 / 360): how it turns a scale in metres into degrees.
METRES_PER_DEGREE = 111319.49079327357


class RawRasterError(RuntimeError):
    """A download is not the 2-band Int32 raster in the native CRS that was asked for."""


def _native(key_or_native) -> tuple[str, float]:
    """A catalogue key's native (crs, scale in metres), or such a pair as given."""
    if isinstance(key_or_native, str):
        return catalogue.NATIVE[key_or_native]
    crs, scale = key_or_native
    return crs, float(scale)


def native_pixel_size(key_or_native) -> float:
    """The native pixel size in the native CRS's units (degrees for EPSG:4326)."""
    crs, scale = _native(key_or_native)
    return scale / METRES_PER_DEGREE if CRS.from_user_input(crs).is_geographic else scale


def estimate_native_bytes(bounds, key_or_native) -> int:
    """Uncompressed size of a raw download of the WGS84 ``bounds``: two Int32 bands.
    The download's free-space gate scales it with ``raster_io.compressed_estimate``."""
    crs, _ = _native(key_or_native)
    minx, miny, maxx, maxy = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform_bounds(*bounds)
    size = native_pixel_size(key_or_native)
    return math.ceil((maxx - minx) / size) * math.ceil((maxy - miny) / size) * 2 * 4


def _check_raw(path: Path, crs: str) -> None:
    with rasterio.open(path) as src:
        same_crs = src.crs is not None and src.crs == CRS.from_user_input(crs)
        dtypes = src.dtypes
    if not (same_crs and dtypes == ("int32", "int32")):
        raise RawRasterError(
            f"{path} is not a 2-band Int32 raster in {crs} (crs={same_crs}, dtypes={dtypes})"
        )


def export_native(image, key_or_native, aoi, out_path, *, on_tiles=None, on_wait=None) -> Path:
    """Download ``image``'s ``alert`` and ``date`` bands over ``aoi`` to ``out_path``
    in the native projection of ``key_or_native`` (a catalogue key or a (crs,
    scale in metres) pair). Written as ``<out_path>.part`` and renamed once
    checked; on any failure nothing is left behind. Run it on a worker thread.
    """
    crs, scale = _native(key_or_native)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part = out_path.with_name(out_path.name + ".part")
    part.unlink(missing_ok=True)
    try:
        with geedim_tile_progress(on_tiles), geedim_download_wait(on_wait):
            download_ee_image(
                image.select(["alert", "date"]).toInt32(),
                str(part),
                region=aoi,
                crs=crs,
                scale=scale,
                dtype="int32",
                unmask_value=0,
                nodata_value=0,
            )
        _check_raw(part, crs)
        part.replace(out_path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return out_path
