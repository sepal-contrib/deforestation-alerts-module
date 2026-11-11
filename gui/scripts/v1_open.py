"""Open hook of a version 1 recipe: resolve its AOI (Earth Engine for a GAUL
2015 code or an asset) and convert the folder once, before the project loads
(spec 2026-11-11 v1 recipe migration). Solara-free; runs on a worker thread."""

from pathlib import Path
from typing import Optional

from deforestation_alerts.project import io, paths
from deforestation_alerts.project.v1_migration import convert, is_v1_recipe
from gui.scripts.aoi_io import sidecar_request

_ADMIN = {"ADMIN0": 0, "ADMIN1": 1, "ADMIN2": 2}


def _filtered(column, value) -> bool:
    return column not in (None, "", "ALL") and value is not None


def _vector_fc(vector_json: Optional[dict]) -> dict:
    import geopandas as gpd

    vector_json = vector_json or {}
    path = Path(vector_json.get("pathname") or "")
    if not path.is_file():
        raise ValueError(f"the AOI file {path} no longer exists")
    gdf = gpd.read_file(path).to_crs("EPSG:4326")
    column, value = vector_json.get("column"), vector_json.get("value")
    if _filtered(column, value):
        gdf = gdf[gdf[column] == value]
    return gdf[["geometry"]].__geo_interface__  # geometry only: any attribute could fail to serialise


def aoi_collection(params: dict):
    """v1's AOI as an ``ee.FeatureCollection`` (an ADMIN0/1/2 GAUL 2015 code
    or an asset), or the stored GeoJSON FeatureCollection (drawn or a vector
    file on disk), which needs no Earth Engine."""
    method = params.get("aoi_method")
    if method in _ADMIN:
        import ee

        n = _ADMIN[method]
        # GAUL 2015 on purpose: v1 stored 2015 codes, which pygaul / GAUL 2024 reads as other places
        return ee.FeatureCollection(f"FAO/GAUL/2015/level{n}").filter(ee.Filter.eq(f"ADM{n}_CODE", int(params["aoi_admin"])))
    if method == "ASSET":
        import ee

        asset = params.get("aoi_asset_json") or {"pathname": params.get("aoi_asset_name")}
        fc = ee.FeatureCollection(asset["pathname"])
        column, value = asset.get("column"), asset.get("value")
        return fc.filter(ee.Filter.eq(column, value)) if _filtered(column, value) else fc
    if method == "SHAPE":
        return _vector_fc(params.get("aoi_vector_json"))
    return params.get("aoi_geo_json") or {"type": "FeatureCollection", "features": []}


def resolve_v1_aoi(params: dict) -> dict:
    """v1's AOI as a WGS84 GeoJSON FeatureCollection, an Earth Engine geometry
    simplified as the sidecar is. Raises ``ValueError`` when it has no feature."""
    fc = aoi_collection(params)
    if not isinstance(fc, dict):
        fc = sidecar_request(fc).getInfo()  # blocking: a worker thread
    if not fc.get("features"):
        raise ValueError(f"the {params.get('aoi_method')} area of interest has no feature")
    return fc


def ensure_converted(name: str, root: Optional[Path] = None, resolve=resolve_v1_aoi) -> bool:
    """Convert ``name``'s folder when it is an unconverted v1 recipe; returns
    whether it did. The manifest lock makes a second open wait, then find the project."""
    with io.manifest_lock(name, root):
        folder = paths.project_dir(name, root)
        if not is_v1_recipe(folder):
            return False
        convert(folder, resolve)
        return True
