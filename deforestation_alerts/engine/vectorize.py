"""Alert raster -> polygons, one per alert cluster, correct across row blocks
(spec §7.4 step 5).

An alert is a **cluster**: a connected group of alert pixels, whatever source
or combination of sources flagged each one. A cluster where GLAD-L and RADD
touch is one alert listing both codes, reviewed as one; it is never split into
one alert per code.

Pass 1 labels the connected components of ``alert > 0`` block by block, joins
labels that touch across a block edge with a union-find, and totals each
component's pixel count; ``min_cluster_size`` keeps or drops whole clusters on
that count. Pass 2 recomputes the same labels, polygonizes the pieces of the
kept clusters, and collects per cluster its distinct codes and its date range.
A cluster that block edges cut into pieces (or that meets itself only in a
later block, like the arms of a U) is joined back by its pass-1 root.

Polygons are built in pixel coordinates, where every block edge is an exact
integer row, and moved onto the raster's grid once at the end. In world
coordinates two blocks can place their shared edge a last bit apart when the
pixel size is fractional, and the halves of a cluster would no longer touch.

Dates arrive as integer ``yyyyddd`` (spec D16) and leave as today's decimal
``year + doy/1000`` so everything downstream is unchanged.
"""

import geopandas as gpd
import numpy as np
import rasterio
import shapely
from affine import Affine
from rasterio import features
from rasterio.windows import Window
from scipy import ndimage
from shapely.geometry import shape

from deforestation_alerts.project.store_schema import VECTOR_COLUMNS

COLUMNS = list(VECTOR_COLUMNS)
_BIG = np.iinfo(np.int64).max


def yyyyddd_to_decimal(v: int) -> float:
    """``2026045`` (day 45 of 2026) -> ``2026.045``."""
    v = int(v)
    return v // 1000 + (v % 1000) / 1000.0


def _structure(connectivity: int):
    return np.ones((3, 3), dtype=bool) if connectivity == 8 else ndimage.generate_binary_structure(2, 1)


def _find(parent: np.ndarray, x: int) -> int:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = int(parent[x])
    return x


def _edge_pairs(prev_row: np.ndarray, cur_row: np.ndarray, connectivity: int) -> np.ndarray:
    shifts = (-1, 0, 1) if connectivity == 8 else (0,)
    pairs = []
    for dx in shifts:
        if dx == 0:
            a, b = prev_row, cur_row
        elif dx == 1:
            a, b = prev_row[:-1], cur_row[1:]
        else:
            a, b = prev_row[1:], cur_row[:-1]
        m = (a > 0) & (b > 0)
        if m.any():
            pairs.append(np.stack([a[m], b[m]], axis=1))
    return np.unique(np.concatenate(pairs), axis=0) if pairs else np.empty((0, 2), np.int64)


def _global(labels: np.ndarray, base: int) -> np.ndarray:
    """Block-local labels -> raster-wide ids (background stays 0)."""
    ids = labels.astype(np.int64)
    ids[ids > 0] += base
    return ids


def _valid(alert: np.ndarray, nodata) -> np.ndarray:
    v = alert > 0
    if nodata not in (None, 0):
        v &= alert != nodata
    return v


def _windows(height: int, width: int, block_rows):
    rows = block_rows or max(256, int(2e7 // max(width, 1)))
    return [Window(0, r, width, min(rows, height - r)) for r in range(0, height, rows)]


def vectorize_alerts(raster_path, *, min_cluster_size: int, connectivity: int = 8,
                     alert_band: int = 1, date_band: int = 2,
                     block_rows: int | None = None) -> gpd.GeoDataFrame:
    """Polygons of the alert clusters of at least ``min_cluster_size`` pixels.

    One row per connected cluster of alert pixels, in the raster's CRS, with
    columns ``COLUMNS``: ``alert_type_unique`` lists the cluster's distinct
    codes (sorted), ``count`` its pixels and the dates span all of them.
    ``block_rows`` sets how many rows are read at a time (default: about 2e7
    pixels, at least 256 rows); the result does not depend on it.
    """
    structure = _structure(connectivity)
    with rasterio.open(raster_path) as src:
        crs, nodata, t = src.crs, src.nodata, src.transform
        to_grid = [t.a, t.b, t.d, t.e, t.c, t.f]  # pixel -> CRS, in shapely's matrix order
        windows = _windows(src.height, src.width, block_rows)

        # ---- pass 1: component sizes across blocks --------------------------
        parent = np.zeros(1, dtype=np.int64)
        sizes = np.zeros(1, dtype=np.int64)
        bases, prev_last = [], None
        for w in windows:
            alert = src.read(alert_band, window=w)
            lab, n = ndimage.label(_valid(alert, nodata), structure=structure)
            base = len(parent) - 1
            bases.append(base)
            parent = np.concatenate([parent, np.arange(base + 1, base + n + 1, dtype=np.int64)])
            sizes = np.concatenate([sizes, np.bincount(lab.ravel(), minlength=n + 1)[1:]])
            if prev_last is not None:
                for a, b in _edge_pairs(prev_last, _global(lab[0], base), connectivity):
                    ra, rb = _find(parent, int(a)), _find(parent, int(b))
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
            prev_last = _global(lab[-1], base)

        roots = parent.copy()
        while True:
            nxt = roots[roots]
            if np.array_equal(nxt, roots):
                break
            roots = nxt
        comp_size = np.bincount(roots, weights=sizes, minlength=len(roots)).astype(np.int64)
        keep_id = comp_size[roots] >= int(min_cluster_size)
        keep_id[0] = False

        # ---- pass 2: polygons, codes and dates of the kept clusters ---------
        clusters = {}  # pass-1 root -> {"pieces", "codes", "dmin", "dmax"}
        for w, base in zip(windows, bases):
            alert = src.read(alert_band, window=w)
            lab, n = ndimage.label(_valid(alert, nodata), structure=structure)
            if n == 0:
                continue
            keep = keep_id[_global(lab, base)]
            if not keep.any():
                continue
            idx = np.arange(1, n + 1)
            root_of = roots[base + idx]  # local label k -> root_of[k - 1]
            date = src.read(date_band, window=w).astype(np.int64)
            dmin = ndimage.minimum(np.where(date > 0, date, _BIG), lab, idx)
            dmax = ndimage.maximum(np.where(date > 0, date, 0), lab, idx)
            for k in np.unique(lab[keep]):
                c = clusters.setdefault(int(root_of[k - 1]),
                                        {"pieces": [], "codes": set(), "dmin": _BIG, "dmax": 0})
                c["dmin"] = min(c["dmin"], int(dmin[k - 1]))
                c["dmax"] = max(c["dmax"], int(dmax[k - 1]))
            pairs = np.unique(np.stack([lab[keep], alert[keep].astype(np.int64)]), axis=1)
            for k, code in pairs.T:
                clusters[int(root_of[k - 1])]["codes"].add(int(code))
            to_pixels = Affine.translation(0, w.row_off)
            for geom, k in features.shapes(lab, mask=keep, transform=to_pixels, connectivity=connectivity):
                clusters[int(root_of[int(k) - 1])]["pieces"].append(shape(geom))

    records = [
        {
            "geometry": c["pieces"][0] if len(c["pieces"]) == 1 else shapely.union_all(c["pieces"]),
            "codes": sorted(c["codes"]),
            "count": int(comp_size[root]),
            "dmin": None if c["dmin"] >= _BIG else c["dmin"],
            "dmax": None if c["dmax"] <= 0 else c["dmax"],
        }
        for root, c in clusters.items()
    ]
    if not records:
        return gpd.GeoDataFrame(columns=COLUMNS, geometry="geometry", crs=crs)
    gdf = gpd.GeoDataFrame(
        {
            "geometry": gpd.GeoSeries([r["geometry"] for r in records]).affine_transform(to_grid),
            "alert_type_unique": [r["codes"] for r in records],
            "count": [r["count"] for r in records],
            "alert_date_min": [None if r["dmin"] is None else yyyyddd_to_decimal(r["dmin"]) for r in records],
            "alert_date_max": [None if r["dmax"] is None else yyyyddd_to_decimal(r["dmax"]) for r in records],
        },
        geometry="geometry",
        crs=crs,
    )
    if crs is not None and crs.is_projected:
        gdf["area_ha"] = gdf.geometry.area / 10_000.0
    else:  # the grid form refuses geographic CRSs; keep a correct fallback anyway
        utm = gdf.estimate_utm_crs()
        gdf["area_ha"] = gdf.geometry.to_crs(utm).area / 10_000.0
    return gdf[COLUMNS]

