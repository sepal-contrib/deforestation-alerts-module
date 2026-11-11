"""GAUL 2024 admin boundaries, downloaded once per project AOI and joined to
alerts locally, so every alert carries its names without an Earth Engine call
on each save."""

import os
import threading
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely

from deforestation_alerts.project.paths import admin_boundaries_path

#: There is no official ``FAO/GAUL/2024``; this is the sat-io community copy.
GAUL_L2 = "projects/sat-io/open-datasets/FAO/GAUL/GAUL_2024_L2"
ADMIN_FIELDS = {"admin1": "gaul0_name", "admin2": "gaul1_name", "admin3": "gaul2_name"}
SIMPLIFY_M = 100
#: World cylindrical equal-area: overlap areas rank right at any latitude.
EQUAL_AREA = "EPSG:6933"

#: One lock per cache file, so one project's first fetch never blocks another's.
_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(path, threading.Lock())


def _frame(names: dict, geometry) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({k: pd.Series(v, dtype=object) for k, v in names.items()},
                            geometry=gpd.GeoSeries(geometry, crs="EPSG:4326"))


def fetch_boundaries(bounds: tuple[float, float, float, float]) -> gpd.GeoDataFrame:
    """The GAUL 2024 L2 areas touching ``bounds`` (WGS84), simplified server-side."""
    import ee

    fc = (ee.FeatureCollection(GAUL_L2).filterBounds(ee.Geometry.Rectangle(list(bounds)))
          .select(list(ADMIN_FIELDS.values())).map(lambda f: f.simplify(SIMPLIFY_M)))
    # computeFeatures pages through large results; getInfo would hit the 5000-feature cap
    raw = ee.data.computeFeatures({"expression": fc, "fileFormat": "GEOPANDAS_GEODATAFRAME"})
    if len(raw) == 0:  # from_features([]) has no geometry column
        return _frame({k: [] for k in ADMIN_FIELDS}, [])
    gdf = _frame({k: raw[v].fillna("").tolist() for k, v in ADMIN_FIELDS.items()}, list(raw.geometry))
    gdf["geometry"] = gdf.geometry.make_valid()  # simplification can self-intersect
    return gdf


def load_boundaries(pdir: Path, aoi, *, fetch=fetch_boundaries) -> gpd.GeoDataFrame:
    """The AOI's cached boundaries, fetched and written atomically on first use.
    Raises when the fetch fails; nothing is written then."""
    path = admin_boundaries_path(pdir, aoi.digest)
    with _lock_for(path):  # a second caller for this file waits, then reads what the first one wrote
        if path.is_file():
            return gpd.read_file(path)
        gdf = fetch(tuple(aoi.bounds))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.stem + ".part.gpkg")
        try:
            # explicit type: an empty frame (an all-ocean AOI) still writes a valid, typed layer
            gdf.to_file(tmp, driver="GPKG", geometry_type="Unknown")
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return gdf


def admin_names(geoms: gpd.GeoSeries, boundaries: gpd.GeoDataFrame) -> pd.DataFrame:
    """Per geometry, the distinct names of the areas it overlaps, comma-joined,
    the area holding most of it first; ``""`` where it overlaps none."""
    out = pd.DataFrame("", index=geoms.index, columns=list(ADMIN_FIELDS), dtype=object)
    if len(geoms) == 0 or len(boundaries) == 0:
        return out
    left = gpd.GeoDataFrame({"_i": range(len(geoms))}, geometry=geoms.to_crs(boundaries.crs).values)
    right = boundaries.reset_index(drop=True)
    pairs = gpd.sjoin(left, right[["geometry"]], predicate="intersects")
    if pairs.empty:
        return out
    a = left.geometry.values[pairs["_i"].to_numpy()]
    b = right.geometry.values[pairs["index_right"].to_numpy()]
    area = gpd.GeoSeries(a.intersection(b), crs=boundaries.crs).to_crs(EQUAL_AREA).area.to_numpy()
    zero = shapely.area(a) == 0
    # an edge shared with a neighbour is not overlap; a point or line keeps every touch
    keep = (area > 0) | zero
    pairs = pd.DataFrame({"_i": pairs["_i"].to_numpy()[keep], "_area": area[keep],
                          "_b": pairs["index_right"].to_numpy()[keep]})
    for col in ADMIN_FIELDS:
        pairs["_name"] = right[col].to_numpy()[pairs["_b"]]
        ranked = (pairs[pairs["_name"].astype(bool)].groupby(["_i", "_name"])["_area"].sum()
                  .reset_index().sort_values(["_i", "_area"], ascending=[True, False]))
        joined = ranked.groupby("_i")["_name"].agg(", ".join)
        out.iloc[joined.index.to_numpy(), out.columns.get_loc(col)] = joined.to_numpy()
    return out
