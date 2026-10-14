"""Reviewed alerts (Confirmed / Need revision) of earlier periods near a point
(spec §9). Solara-free. The Analysis overlay calls ``near_reviews`` once per
alert, so each earlier set's reviewed polygons are loaded once, reprojected to
a metric CRS, and kept in a small LRU keyed by (set id, store generation).
"""

import json
import threading
from collections import OrderedDict
from typing import NamedTuple

import geopandas as gpd
import shapely
from pyproj import Transformer

from deforestation_alerts.engine.fingerprint import earlier_sets
from deforestation_alerts.project.review_status import LOSS_VERDICTS
from gui.scripts import set_store

_MAX_SETS = 8


class _Entry(NamedTuple):
    crs: object            # the metric CRS the distances are measured in
    metric: list           # shapely polygons in `crs`
    geoms: list            # the same polygons in EPSG:4326
    props: list            # per-feature properties


_cache: "OrderedDict[tuple, _Entry]" = OrderedDict()
_lock = threading.Lock()


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _has_polygon(g) -> bool:
    return g is not None and not (hasattr(g, "is_empty") and g.is_empty) and not (
        isinstance(g, float) and g != g)


def _build(p, s, gdf) -> _Entry:
    empty = _Entry(None, [], [], [])
    if gdf is None or len(gdf) == 0 or "alert_polygon" not in gdf.columns:
        return empty
    rows = gdf[gdf["status"].isin(LOSS_VERDICTS)]
    rows = rows[[_has_polygon(g) for g in rows["alert_polygon"]]]
    if rows.empty:
        return empty
    polys = gpd.GeoSeries(list(rows["alert_polygon"]), crs="EPSG:4326")
    crs = p.grid.epsg if getattr(p, "grid", None) is not None else polys.estimate_utm_crs()
    metric = polys.to_crs(crs)
    props = [
        {"status": str(r.status), "set_id": s.id, "alert_id": int(r.alert_id),
         "date_min": _num(r.alert_date_min), "date_max": _num(r.alert_date_max)}
        for r in rows.itertuples()
    ]
    return _Entry(crs, list(metric), list(polys), props)


def _num(v):
    try:
        return None if v != v else float(v)
    except (TypeError, ValueError):
        return None


def _entry(p, s, load) -> _Entry:
    key = (s.id, set_store.store_generation(set_store.store_path(p, s)),
           p.grid.epsg if getattr(p, "grid", None) is not None else None)
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    entry = _build(p, s, load(p, s))
    with _lock:
        _cache[key] = entry
        while len(_cache) > _MAX_SETS:
            _cache.popitem(last=False)
    return entry


def near_reviews(p, set_id: str, lon: float, lat: float, radius_m: float, *,
                 load=set_store.load_set_alerts) -> dict:
    """GeoJSON FeatureCollection of the reviewed alerts of sets from periods
    before ``set_id``'s whose polygon lies within ``radius_m`` of the point."""
    features = []
    cur = p.alert_sets.get(set_id)
    period = p.periods.get(cur.period_id) if cur is not None else None
    if period is not None:
        for s in earlier_sets(p, period):
            e = _entry(p, s, load)
            if not e.metric:
                continue
            # a Transformer is not thread-safe: one per call, never cached
            x, y = Transformer.from_crs("EPSG:4326", e.crs, always_xy=True).transform(lon, lat)
            pt = shapely.Point(x, y)
            near = shapely.dwithin(e.metric, pt, radius_m)
            for i in near.nonzero()[0]:
                features.append({"type": "Feature", "properties": dict(e.props[i]),
                                 "geometry": json.loads(shapely.to_geojson(e.geoms[i]))})
    return {"type": "FeatureCollection", "features": features}
