"""Burn a vector alert source onto the project grid (spec §5.4).

The vector alert sources' 2-band Int32 contract: band 1 the alert code 1,
band 2 the ``yyyyddd`` date, 0 no data. The window-by-window burn itself is
``raster_io.burn``, shared with ``integrate`` and ``growth_links``. This module
imports nothing from ``integrate``, ``runner`` or ``gui``.
"""

import math
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import shapely
from rasterio import features
from rasterio.windows import Window

from deforestation_alerts.engine.raster_io import burn, tile_windows, window_transform, write_grid_raster
from deforestation_alerts.project.models import GridSpec

#: Pixels a polygon's pixel-centre check rasterizes at once (about 20 MB of uint8).
CHECK_PIXELS = 20_000_000


def rasterize_dated(gdf_grid: gpd.GeoDataFrame, grid: GridSpec, out_path) -> Path:
    """Write the 2-band Int32 alert contract of ``gdf_grid`` (grid CRS, a ``date``
    column, already in burn order: the last shape over a pixel gives its date)."""
    geoms = gdf_grid.geometry
    sindex = geoms.sindex if len(geoms) else None
    dates = gdf_grid["date"].to_numpy(dtype=np.int64)
    with write_grid_raster(Path(out_path), grid, count=2) as dst:
        for w in tile_windows(grid):
            date = burn(geoms, sindex, grid, w, values=dates, dtype="int32")
            dst.write((date > 0).astype(np.int32), 1, window=w)
            dst.write(date, 2, window=w)
    return Path(out_path)


def _covers_a_centre(geom, grid: GridSpec) -> bool:
    """Whether ``geom`` burns a pixel under the pixel-centre rule, checked in its
    own bounds snapped outward to the grid (clipped to it), row band by row band."""
    res = float(grid.resolution)
    minx, miny, maxx, maxy = geom.bounds
    col0 = max(0, math.floor((minx - grid.xmin) / res))
    col1 = min(grid.width, math.ceil((maxx - grid.xmin) / res))
    row0 = max(0, math.floor((grid.ymax - maxy) / res))
    row1 = min(grid.height, math.ceil((grid.ymax - miny) / res))
    width = col1 - col0
    if width <= 0 or row1 <= row0:
        return False
    # a national polygon spans the grid: bound the check's memory like a burn window
    step = max(1, CHECK_PIXELS // width)
    for r in range(row0, row1, step):
        w = Window(col0, r, width, min(step, row1 - r))
        if features.rasterize([(geom, 1)], out_shape=(int(w.height), width), transform=window_transform(grid, w),
                              fill=0, all_touched=False, dtype="uint8").any():
            return True
    return False


def burn_shapes(gdf: gpd.GeoDataFrame, grid: GridSpec, side_m: Optional[float]) -> gpd.GeoDataFrame:
    """The shapes to burn for the raw features ``gdf`` (EPSG:4326, ``geometry``
    and ``date``), in ``grid.epsg``, sorted by date descending (stable) so the
    earliest date is burned last and wins.

    A point becomes a ``side_m`` square centred on it (metric, in the grid's
    CRS), or stays a point when the side is below the grid resolution. A
    polygon that covers no pixel centre becomes its ``representative_point()``,
    so it still burns one pixel.
    """
    g = gdf.to_crs(grid.epsg)
    geoms = np.asarray(g.geometry.values, dtype=object)
    types = shapely.get_type_id(geoms)
    points = types == shapely.GeometryType.POINT
    if points.any() and side_m is not None and float(side_m) >= float(grid.resolution):
        h = float(side_m) / 2
        x, y = shapely.get_x(geoms[points]), shapely.get_y(geoms[points])
        geoms[points] = shapely.box(x - h, y - h, x + h, y + h)
    polys = np.flatnonzero(np.isin(types, (shapely.GeometryType.POLYGON, shapely.GeometryType.MULTIPOLYGON)))
    for i in polys:
        if not _covers_a_centre(geoms[i], grid):
            geoms[i] = geoms[i].representative_point()
    out = gpd.GeoDataFrame({"geometry": geoms, "date": g["date"].to_numpy()}, geometry="geometry", crs=grid.epsg)
    return out.sort_values("date", ascending=False, kind="stable").reset_index(drop=True)


def burn_vector_raw(raw_path, grid: GridSpec, out_path, *, side_m: Optional[float]) -> Path:
    """Burn a vector source's raw GeoParquet onto ``grid`` as the 2-band alert contract."""
    return rasterize_dated(burn_shapes(gpd.read_parquet(raw_path), grid, side_m), grid, out_path)
