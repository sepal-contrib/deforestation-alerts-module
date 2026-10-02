"""AOI persistence (spec §6.1). Solara-free.

The project stores the pysepal ``AoiSpec`` (replayed by ``AoiView(spec=...)``
on load), the name, WGS84 bounds (the grid is built from them) and a digest
that changes only when the geometry does. AOIs with a local gdf also get an
``aoi.geojson`` sidecar; Earth Engine admin/asset AOIs are identified by their
code or asset id (plus the column filter, which picks another geometry).
"""

import hashlib
import json
import os
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

from deforestation_alerts.project import paths
from deforestation_alerts.project.models import AoiRecord

_DIGEST_GRID = 1e-6  # degrees: finer than any selection, coarser than GeoJSON noise


def geometry_digest(gdf) -> str:
    import shapely

    h = hashlib.sha256()
    for geom in gdf.to_crs(epsg=4326).geometry:
        h.update(shapely.to_wkb(shapely.set_precision(geom, _DIGEST_GRID)))
    return h.hexdigest()


def lazy_digest(result) -> Optional[str]:
    if getattr(result, "admin", None) and str(result.method).startswith("ADMIN"):
        return f"admin:{result.admin}"
    spec = getattr(result, "spec", None)
    asset = getattr(spec, "asset_id", None)
    if result.method == "ASSET" and asset:
        # A fresh unfiltered pick carries column "ALL"; a spec restored from
        # the manifest carries None (AoiSpec.to_dict drops it). Both are the
        # whole asset.
        column = getattr(spec, "column", None)
        if column in (None, "ALL"):
            return f"asset:{asset}"
        return f"asset:{asset}|{column}={getattr(spec, 'value', None)!r}"
    return None


def bounds_from_geojson(geom: dict) -> tuple:
    from shapely.geometry import shape

    return tuple(float(v) for v in shape(geom).bounds)


def build_record(result, gdf, bounds) -> AoiRecord:
    spec = result.spec.to_dict() if getattr(result, "spec", None) is not None else {}
    digest = geometry_digest(gdf) if gdf is not None else lazy_digest(result)
    return AoiRecord(
        spec=spec,
        name=str(result.name),
        method=str(result.method),
        digest=digest or "unknown",
        bounds=tuple(bounds),
        geometry_file=paths.AOI_SIDECAR if gdf is not None else None,
    )


def gdf_bounds(gdf) -> tuple:
    return tuple(float(v) for v in gdf.to_crs(epsg=4326).total_bounds)


def stage_sidecar(pdir: Path, gdf) -> Path:
    """Write the sidecar to a temporary name in ``pdir``; the caller moves it
    into place (``os.replace``) once the project accepted the AOI."""
    pdir = Path(pdir)
    pdir.mkdir(parents=True, exist_ok=True)
    tmp = pdir / f".{paths.AOI_SIDECAR}.{uuid.uuid4().hex}.tmp"
    try:
        gdf.to_crs(epsg=4326).to_file(tmp, driver="GeoJSON")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp


def write_sidecar(pdir: Path, gdf) -> None:
    staged = stage_sidecar(pdir, gdf)
    try:
        os.replace(staged, Path(pdir) / paths.AOI_SIDECAR)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def auto_project_name(aoi_name: str, existing: list, today: date) -> str:
    base = re.sub(r"[^A-Za-z0-9 _-]+", "_", str(aoi_name or "aoi")).strip(" _") or "aoi"
    name = f"{base}_{today:%Y%m%d}"
    taken = {e.casefold() for e in existing}
    candidate, n = name, 2
    while candidate.casefold() in taken:
        candidate, n = f"{name}_{n}", n + 1
    return candidate


def ee_aoi(result, pdir: Optional[Path]):
    """The ``ee.FeatureCollection`` downloads and previews clip to.

    The picker's object is used when there is one; an IMAGE or IMAGE_COLLECTION
    asset (or a bare geometry) is wrapped as a one-feature collection of its
    footprint. Without a selection, the ``aoi.geojson`` sidecar is the fallback,
    converted the way pysepal converts a picked gdf (planar edges).

    Raises:
        ValueError: neither exists; the message is the translated
            ``steps.aoi.no_geometry``, ready to toast.
    """
    import ee

    obj = getattr(result, "feature_collection", None) if result is not None else None
    if obj is not None:
        if isinstance(obj, ee.FeatureCollection):
            return obj
        geometry = obj if isinstance(obj, ee.Geometry) else obj.geometry()
        return ee.FeatureCollection([ee.Feature(geometry)])
    sidecar = Path(pdir) / paths.AOI_SIDECAR if pdir is not None else None
    if sidecar is not None and sidecar.exists():
        from pysepal.scripts.utils import geojson_to_ee

        return geojson_to_ee(json.loads(sidecar.read_text()))
    from gui.i18n import t  # imported here: the rest of the module stays Solara-free

    raise ValueError(t("steps.aoi.no_geometry"))
