"""Mask rasters and the AOI raster on the project grid (spec §7.2).

Task 12 adds the AOI raster: the AOI polygon rasterized onto the grid, Int32
0/1, used to clip GeoTIFF sources at harmonize (and GeoTIFF masks at
alignment), as Earth Engine's download clips an Earth Engine raster. Built
window by window; never a whole-grid rasterize.
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
from affine import Affine
from rasterio import features
from shapely.geometry import box

from deforestation_alerts.engine.raster_io import tile_windows, write_grid_raster
from deforestation_alerts.project.models import GridSpec


def _window_transform(grid: GridSpec, w) -> Affine:
    return grid.affine @ Affine.translation(w.col_off, w.row_off)


def build_aoi_raster(sidecar_path, grid: GridSpec, out_path) -> Path:
    gdf = gpd.read_file(sidecar_path).to_crs(grid.epsg)
    geoms = list(gdf.geometry)
    sindex = gdf.sindex if len(gdf) else None
    with write_grid_raster(Path(out_path), grid, count=1) as dst:
        for w in tile_windows(grid):
            shape = (int(w.height), int(w.width))
            t = _window_transform(grid, w)
            arr = np.zeros(shape, np.int32)
            if sindex is not None:
                idx = sindex.query(box(t.c, t.f + t.e * shape[0], t.c + t.a * shape[1], t.f))
                if len(idx):
                    arr = features.rasterize(((geoms[i], 1) for i in idx), out_shape=shape, transform=t,
                                             fill=0, dtype="int32")
            dst.write(arr, 1, window=w)
    return Path(out_path)
