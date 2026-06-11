"""Local raster → vector for alerts. Generalizes mosaics_helper.raster_to_gdf:
required cluster-size `sieve` (D11) before polygonizing, then `shapes`, then an
optional area filter. Block-wise reads + edge stitch handle national scale (D4)."""

import geopandas as gpd
import numpy as np
import rasterio
import rasterio.windows
from rasterio import features
from shapely.geometry import shape

from deforestation_alerts.scripts.geo_utils import (
    get_utm_proj_str_from_lat_lon,
    optimal_block_shape,
)


def _date_stats(date_arr, transform, geom, nodata):
    """min/max of the date band within a single polygon (ignoring nodata)."""
    mask = features.geometry_mask(
        [geom], out_shape=date_arr.shape, transform=transform, invert=True
    )
    vals = date_arr[mask]
    vals = vals[vals != nodata]
    if vals.size == 0:
        return None, None
    return float(vals.min()), float(vals.max())


def _records_from_arrays(alert, date, transform, *, min_cluster_size,
                         connectivity, nodata):
    """Sieve the alert mask, polygonize surviving clusters, return raw records."""
    # Exclude the nodata fill: the download unmasks masked pixels to `nodata`
    # (255) in both bands, and 255 > 0 would otherwise be read as alert code 255.
    valid = (alert > 0) & (alert != nodata)
    mask = valid.astype(np.uint8)
    cleaned = features.sieve(mask, size=int(min_cluster_size),
                             connectivity=connectivity)
    # `sieve` fills sub-threshold holes (e.g. a lone nodata pixel inside a cluster)
    # with the neighbouring class, so re-AND with `valid`: keep only pixels that
    # the sieve retained AND that are genuine alert codes. Without this, a filled
    # hole re-reads the original band's nodata (255) and becomes a phantom 1-px
    # code-255 alert with no date (NaN) — which blanked the Overview map.
    alert_clean = np.where(cleaned > 0, np.where(valid, alert, 0), 0).astype(
        alert.dtype
    )

    records = []
    px_area = abs(transform.a * transform.e)
    for geom_json, value in features.shapes(
        alert_clean, mask=(alert_clean > 0), transform=transform,
        connectivity=connectivity,
    ):
        geom = shape(geom_json)
        code = int(value)
        count = int(round(geom.area / px_area))  # polygon area / pixel area
        dmin, dmax = _date_stats(date, transform, geom, nodata)
        records.append(
            {"geometry": geom, "alert_type_unique": [code], "count": count,
             "alert_date_min": dmin, "alert_date_max": dmax}
        )
    return records


def _stitch(records):
    """Union same-code fragments that touch; aggregate count/date. One record per
    connected cluster (handles block-edge splits, D4)."""
    if not records:
        return records
    g = gpd.GeoDataFrame(records, geometry="geometry")
    g["code"] = g["alert_type_unique"].apply(lambda x: int(x[0]))

    out = []
    for code, grp in g.groupby("code"):
        grp = grp.reset_index(drop=True)
        merged = grp.geometry.union_all()
        parts = list(getattr(merged, "geoms", [merged]))
        for part in parts:
            members = grp[grp.geometry.intersects(part)]
            dmins = [d for d in members["alert_date_min"] if d is not None]
            dmaxs = [d for d in members["alert_date_max"] if d is not None]
            out.append({
                "geometry": part,
                "alert_type_unique": [code],
                "count": int(members["count"].sum()),
                "alert_date_min": min(dmins) if dmins else None,
                "alert_date_max": max(dmaxs) if dmaxs else None,
            })
    return out


def _finalize(records, src_crs, *, min_area_ha, output_epsg):
    """Build the gdf, compute area_ha in a metric CRS, apply the area filter."""
    cols = ["geometry", "alert_type_unique", "count",
            "alert_date_min", "alert_date_max", "area_ha"]
    if not records:
        return gpd.GeoDataFrame(columns=cols, geometry="geometry",
                                crs=f"EPSG:{output_epsg}")

    gdf = gpd.GeoDataFrame(records, geometry="geometry", crs=src_crs)
    gdf = gdf.to_crs(epsg=output_epsg)

    c = gdf.geometry.union_all().centroid
    utm = get_utm_proj_str_from_lat_lon(c.x, c.y)
    gdf["area_ha"] = gdf.geometry.to_crs(utm).area / 10_000.0

    if min_area_ha is not None:
        gdf = gdf[gdf["area_ha"] >= float(min_area_ha)].reset_index(drop=True)
    return gdf


def vectorize_alert_raster(
    raster_path, *, min_cluster_size, connectivity=8, min_area_ha=None,
    alert_band=1, date_band=2, output_epsg=4326, block_rows=None,
):
    """Vectorize a 2-band alert GeoTIFF (band1=alert code, band2=date) into a gdf
    of true pixel-cluster polygons. Cluster-size `sieve` is mandatory (D11).

    block_rows: if set (or auto via optimal_block_shape for huge rasters), read in
    row blocks and stitch edge-split clusters (D4 national scale).
    """
    with rasterio.open(raster_path) as src:
        height, width = src.height, src.width
        transform = src.transform
        nodata = src.nodata if src.nodata is not None else 255
        src_crs = src.crs

        if block_rows is None:
            block_rows, _ = optimal_block_shape(height, width, 2, ram_gb=4.0)

        if block_rows >= height:
            alert = src.read(alert_band)
            date = src.read(date_band)
            records = _records_from_arrays(
                alert, date, transform, min_cluster_size=min_cluster_size,
                connectivity=connectivity, nodata=nodata)
        else:
            records = []
            for row0 in range(0, height, block_rows):
                nrows = min(block_rows, height - row0)
                window = rasterio.windows.Window(0, row0, width, nrows)
                win_transform = src.window_transform(window)
                alert = src.read(alert_band, window=window)
                date = src.read(date_band, window=window)
                records += _records_from_arrays(
                    alert, date, win_transform,
                    min_cluster_size=min_cluster_size,
                    connectivity=connectivity, nodata=nodata)
            records = _stitch(records)

    return _finalize(records, src_crs, min_area_ha=min_area_ha,
                     output_epsg=output_epsg)
