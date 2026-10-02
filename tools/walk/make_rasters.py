"""Synthetic GeoTIFFs for the gate walks, written under $HOME (the SEPAL folder)."""

from datetime import date
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine

RES = 0.0002  # degrees, about 22 m


def _profile(bbox, count, dtype):
    w, s, e, n = bbox
    width, height = int(round((e - w) / RES)), int(round((n - s) / RES))
    return dict(driver="GTiff", width=width, height=height, count=count, dtype=dtype, crs="EPSG:4326",
                transform=Affine(RES, 0, w, 0, -RES, n)), (height, width)


def ccdc_tif(path, bbox, *, alert_block=(0.3, 0.6), when=date(2026, 3, 14)) -> Path:
    """detection_count / first_detection_date (fractional year, start of day): a
    confirmed block (count 3) in the given fraction of rows and columns, a
    probable strip (count 1) under it, nothing elsewhere."""
    profile, (h, w) = _profile(bbox, 2, "float32")
    count = np.zeros((h, w), "float32")
    a, b = int(alert_block[0] * h), int(alert_block[1] * h)
    count[a:b, int(alert_block[0] * w):int(alert_block[1] * w)] = 3
    count[b:b + 5, int(alert_block[0] * w):int(alert_block[1] * w)] = 1
    diy = 366 if when.year % 4 == 0 else 365
    fdate = np.where(count > 0, when.year + (when.timetuple().tm_yday - 1) / diy, 0).astype("float32")
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(count, 1)
        dst.write(fdate, 2)
        dst.descriptions = ("detection_count", "first_detection_date")
    return Path(path)


def yymm_tif(path, bbox, months=(2603, 2604)) -> Path:
    """One band ``disturbance_date`` in yymm: one block per month."""
    profile, (h, w) = _profile(bbox, 1, "int16")
    band = np.zeros((h, w), "int16")
    for i, m in enumerate(months):
        band[int(0.2 * h):int(0.4 * h), int((0.2 + 0.3 * i) * w):int((0.4 + 0.3 * i) * w)] = m
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(band, 1)
        dst.descriptions = ("disturbance_date",)
    return Path(path)


def slope_tif(path, bbox) -> Path:
    """A float attribute raster (a west-east ramp) for the GeoTIFF attribute check."""
    profile, (h, w) = _profile(bbox, 1, "float32")
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(np.tile(np.linspace(0, 45, w, dtype="float32"), (h, 1)), 1)
        dst.descriptions = ("slope",)
    return Path(path)


def _asset_root(ee) -> str:
    """The account's asset root. On a Cloud project ``getAssetRoots()`` lists
    the root's children (its first entry can be an image collection, and an
    export there would add the image to it), so the root is the part of that
    id up to ``/assets``; a legacy ``users/<name>`` root is returned as is."""
    first = ee.data.getAssetRoots()[0]["id"]
    return first.split("/assets/", 1)[0] + "/assets" if "/assets/" in first else first


def ensure_yymm_asset(bbox, months=(2603, 2604), name="defo_walk_yymm") -> str:
    """A date-only Earth Engine image (one band ``disturbance_date`` in yymm, the
    layout of ``yymm_tif``) in the account's asset root, exported once and
    reused: done-when (b) must run the Earth Engine ``yymm`` conversion, which a
    GeoTIFF source never reaches (it goes through numpy)."""
    import time

    import ee
    from pysepal.scripts.utils import init_ee

    init_ee()
    asset = f"{_asset_root(ee)}/{name}"
    try:
        ee.data.getAsset(asset)
        return asset
    except ee.EEException:
        pass
    w, s, e, n = bbox
    lonlat = ee.Image.pixelLonLat()
    img = ee.Image(0)
    for i, month in enumerate(months):
        x0, x1 = w + (0.2 + 0.3 * i) * (e - w), w + (0.4 + 0.3 * i) * (e - w)
        y0, y1 = n - 0.4 * (n - s), n - 0.2 * (n - s)
        inside = (lonlat.select("longitude").gte(x0).And(lonlat.select("longitude").lt(x1))
                  .And(lonlat.select("latitude").gte(y0)).And(lonlat.select("latitude").lt(y1)))
        img = img.where(inside, month)
    region = ee.Geometry.Rectangle(list(bbox))
    task = ee.batch.Export.image.toAsset(image=img.selfMask().toInt16().rename("disturbance_date").clip(region),
                                         description=name, assetId=asset, region=region, scale=30,
                                         crs="EPSG:4326")
    task.start()
    t0 = time.time()
    while task.status()["state"] not in ("COMPLETED", "FAILED", "CANCELLED"):
        if time.time() - t0 > 1200:
            raise TimeoutError(f"the export of {asset} did not finish in 20 minutes")
        time.sleep(15)
    if task.status()["state"] != "COMPLETED":
        raise RuntimeError(f"the export of {asset} failed: {task.status()}")
    return asset
