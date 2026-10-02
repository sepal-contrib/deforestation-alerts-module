"""Alert raster -> polygons, correct across row blocks (spec §7.4 step 5).

Pass 1 labels the connected components of ``alert > 0`` block by block, joins
labels that touch across a block edge with a union-find, and totals each
component's pixel count. Pass 2 recomputes the same labels, keeps the pixels of
components with at least ``min_cluster_size`` pixels, splits them into
single-code regions, polygonizes each region and takes its date range from its
own pixels. Same-code regions that a block edge cut apart are joined back as
the connected components of a touch graph, one row per region.

``min_cluster_size`` is then applied a second time, to the stitched regions
themselves: pass 1 gates whole components only as a cheap pre-filter (a region
never holds more pixels than its component, so that gate drops nothing a region
test would keep), but a component that clears the minimum can still split into
regions far below it — one 9-pixel GLAD-L region beside a 1-pixel RADD one.
The second test is what the user's minimum alert size actually means.

Polygons are built in pixel coordinates, where every block edge is an exact
integer row, and moved onto the raster's grid once at the end. In world
coordinates two blocks can place their shared edge a last bit apart when the
pixel size is fractional, and the halves of a region would no longer touch.

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
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from shapely.geometry import shape

COLUMNS = ["geometry", "alert_type_unique", "count", "alert_date_min", "alert_date_max", "area_ha"]
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
    """Polygons of the alert regions of at least ``min_cluster_size`` pixels.

    One row per connected single-code region, in the raster's CRS, with
    columns ``COLUMNS``; every returned row holds at least ``min_cluster_size``
    pixels. ``block_rows`` sets how many rows are read at a time
    (default: about 2e7 pixels, at least 256 rows); the result does not
    depend on it.
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
        comp_size = np.bincount(roots, weights=sizes, minlength=len(roots))
        keep_id = comp_size[roots] >= int(min_cluster_size)
        keep_id[0] = False

        # ---- pass 2: single-code regions of the kept components -------------
        records = []
        last = len(windows) - 1
        for i, (w, base) in enumerate(zip(windows, bases)):
            alert = src.read(alert_band, window=w)
            date = src.read(date_band, window=w).astype(np.int64)
            lab, _ = ndimage.label(_valid(alert, nodata), structure=structure)
            clean = np.where(keep_id[_global(lab, base)], alert, 0)
            date_lo = np.where(date > 0, date, _BIG)
            date_hi = np.where(date > 0, date, 0)
            to_pixels = Affine.translation(0, w.row_off)
            for code in np.unique(clean[clean > 0]):
                reg, m = ndimage.label(clean == code, structure=structure)
                idx = np.arange(1, m + 1)
                counts = np.bincount(reg.ravel(), minlength=m + 1)[1:]
                dmin = ndimage.minimum(date_lo, reg, idx)
                dmax = ndimage.maximum(date_hi, reg, idx)
                edge = set()
                if i > 0:
                    edge |= set(np.unique(reg[0][reg[0] > 0]).tolist())
                if i < last:
                    edge |= set(np.unique(reg[-1][reg[-1] > 0]).tolist())
                for geom, rid in features.shapes(reg, mask=reg > 0, transform=to_pixels,
                                                 connectivity=connectivity):
                    k = int(rid) - 1
                    records.append({
                        "geometry": shape(geom),
                        "code": int(code),
                        "count": int(counts[k]),
                        "dmin": None if dmin[k] >= _BIG else int(dmin[k]),
                        "dmax": None if dmax[k] <= 0 else int(dmax[k]),
                        "edge": int(rid) in edge,
                    })

    # The pass-1 gate kept whole components; a region is a subset of its own
    # component, so this is where the user's minimum is actually met. Measured
    # on the pixel count, which is exact: a region's polygon is axis-aligned,
    # so its area is ``count · resolution²``.
    records = [r for r in _stitch(records, connectivity) if r["count"] >= int(min_cluster_size)]
    if not records:
        return gpd.GeoDataFrame(columns=COLUMNS, geometry="geometry", crs=crs)
    gdf = gpd.GeoDataFrame(
        {
            "geometry": gpd.GeoSeries([r["geometry"] for r in records]).affine_transform(to_grid),
            "alert_type_unique": [[r["code"]] for r in records],
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


def _stitch(records, connectivity: int):
    """Join the same-code regions that a block edge cut apart.

    Only regions on an edge shared with another block take part. Two of them
    are one region when their polygons meet: anywhere with 8-connectivity, but
    only along a positive-length stretch of edge with 4-connectivity (a shared
    corner is a diagonal step, which 4-connectivity does not follow). Each
    connected group becomes one row: the union of its polygons, the summed pixel
    count and the min/max of its dates. Other records pass through unchanged.
    """
    out = [r for r in records if not r["edge"]]
    by_code = {}
    for r in records:
        if r["edge"]:
            by_code.setdefault(r["code"], []).append(r)
    for group in by_code.values():
        geoms = gpd.GeoSeries([r["geometry"] for r in group])
        left, right = geoms.sindex.query(geoms, predicate="intersects")
        pair = left < right
        left, right = left[pair], right[pair]
        if connectivity == 4 and len(left):
            shared = shapely.intersection(geoms.values[left], geoms.values[right])
            along = shapely.length(shared) > 0
            left, right = left[along], right[along]
        n = len(group)
        graph = coo_matrix((np.ones(len(left), dtype=np.int8), (left, right)), shape=(n, n))
        _, component = connected_components(graph, directed=False)
        members = {}
        for r, c in zip(group, component):
            members.setdefault(int(c), []).append(r)
        out.extend(_merge(rs) for rs in members.values())
    return out


def _merge(regions):
    """One record for the pieces of one region cut by block edges."""
    if len(regions) == 1:
        return regions[0]
    dmins = [r["dmin"] for r in regions if r["dmin"] is not None]
    dmaxs = [r["dmax"] for r in regions if r["dmax"] is not None]
    return {
        "geometry": shapely.union_all([r["geometry"] for r in regions]),
        "code": regions[0]["code"],
        "count": sum(r["count"] for r in regions),
        "dmin": min(dmins) if dmins else None,
        "dmax": max(dmaxs) if dmaxs else None,
        "edge": False,
    }
