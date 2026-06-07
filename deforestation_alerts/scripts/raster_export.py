"""Download an Earth Engine image to a local GeoTIFF using the geedim 2.0 `.gd`
accessor. Copied near-verbatim from spatial-risk-module
(srm/spatialrisk/gee/ee_raster_export.py)."""

from typing import Any, Optional, Union

import ee


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
    """Download `image` as a GeoTIFF at `filename`. Tiled automatically by geedim
    (`max_tile_size`/`max_tile_dim`), so national-scale exports stream safely (D4).

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

    if unmask_value is not None:
        if isinstance(region, (ee.Geometry, ee.FeatureCollection)):
            image = image.clip(region)
        image = image.unmask(unmask_value, sameFootprint=False)

    img = image.gd.prepareForExport(
        crs=crs, region=region, scale=scale, resampling=resampling, dtype=dtype
    )

    if nodata_value is None:
        img.gd.toGeoTIFF(file=filename, overwrite=overwrite, nodata=True, **kwargs)
    else:
        img.gd.toGeoTIFF(file=filename, overwrite=overwrite, nodata=nodata_value, **kwargs)

    print(f"File {filename}, downloaded")
