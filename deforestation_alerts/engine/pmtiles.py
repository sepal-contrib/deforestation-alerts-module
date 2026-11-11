"""An alert set as a PMTiles archive for Overview (spec §9.2), and a vector
alert source's features read for a period, for the Alerts step's map.

Two layers: ``alerts`` (polygons, from zoom 11) and ``points`` (centroids at
every zoom; ``-r1`` stops tippecanoe's default rate-dropping of points).
``status_code`` is an int: protomaps-leaflet filters compare with strict ===.
The archive is assembled on local disk (tippecanoe seeks constantly; on SEPAL's
NFS it is ~60x slower) and copied into place through a ``.part`` rename. The
``.part`` name is unique per call: Integrate and Overview can build the same
set's tiles at the same time, and two builders sharing one staging name would
copy over each other's half-written file.
"""

import json
import logging
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from deforestation_alerts.errors import KeyedError
from deforestation_alerts.gdal_env import local_scratch_dir
from deforestation_alerts.project.review_status import STATUS_CODES

logger = logging.getLogger("SEPALUI.deforestation_alerts")

POLYGON_MIN_ZOOM = 11

#: Seconds a tippecanoe run may take (a country of alerts takes minutes): a
#: hung one fails the tile build, which callers report as a tile failure.
TIPPECANOE_TIMEOUT_S = 30 * 60


class TippecanoeMissing(KeyedError, RuntimeError):
    """tippecanoe is neither on the PATH nor next to the interpreter."""

    key = "errors.no_tippecanoe"


def resolve_tippecanoe() -> Optional[str]:
    """PATH first, then next to the interpreter (SEPAL kernels omit the env's bin/)."""
    found = shutil.which("tippecanoe")
    if found:
        return found
    sibling = Path(sys.executable).parent / "tippecanoe"
    return str(sibling) if sibling.exists() else None


def tippecanoe_available() -> bool:
    return resolve_tippecanoe() is not None


def _props(row) -> dict:
    date_min = row.get("alert_date_min")
    return {
        "alert_id": int(row["alert_id"]),
        "status_code": STATUS_CODES.get(row.get("status"), 0),
        "area_ha": float(row["area_ha"]) if row.get("area_ha") is not None else 0.0,
        "date_min": None if date_min is None or date_min != date_min else float(date_min),
        "set_id": str(row.get("set_id", "")),
    }


def _features_file(folder: Path, name: str, features: list) -> Path:
    path = folder / f"{name}.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return path


def _write_layers(gdf, folder: Path) -> dict:
    gdf = gdf.to_crs(epsg=4326)
    polys, points = [], []
    for _, row in gdf.iterrows():
        props = _props(row)
        polys.append({"type": "Feature", "properties": props,
                      "geometry": row.geometry.__geo_interface__,
                      "tippecanoe": {"minzoom": POLYGON_MIN_ZOOM}})
        points.append({"type": "Feature", "properties": props,
                       "geometry": row.geometry.centroid.__geo_interface__})
    return {"alerts": _features_file(folder, "alerts", polys), "points": _features_file(folder, "points", points)}


def alerts_to_pmtiles(alerts_gdf, out_path, *, guard: Optional[Callable[[], None]] = None) -> Path:
    """Build ``out_path`` from ``alerts_gdf``.

    ``guard``, when given, runs after tippecanoe and right before the built
    archive is moved onto ``out_path``: a caller that built from a store on
    disk checks there that the store is still the one it read. If it raises,
    nothing is moved into place, the staged copy is removed, and the
    exception propagates.
    """
    return _build(lambda folder: _write_layers(alerts_gdf, folder), out_path, guard)


def vector_shapes(raw, side_m: Optional[float]):
    """The shapes a vector source burns, in EPSG:4326: a point becomes its
    ``side_m`` square (in the local UTM zone), a polygon stays as drawn."""
    import geopandas as gpd
    import shapely

    if raw.empty or not side_m:
        return raw
    utm = raw.to_crs(raw.estimate_utm_crs())
    geoms = np.asarray(utm.geometry.values, dtype=object)
    points = shapely.get_type_id(geoms) == shapely.GeometryType.POINT
    h = float(side_m) / 2
    x, y = shapely.get_x(geoms[points]), shapely.get_y(geoms[points])
    geoms[points] = shapely.box(x - h, y - h, x + h, y + h)
    return gpd.GeoDataFrame({"date": raw["date"].to_numpy()}, geometry=geoms, crs=utm.crs).to_crs(epsg=4326)


def vector_source_to_pmtiles(raw_path, out_path, *, side_m: Optional[float]) -> Path:
    """Build ``out_path`` from a vector source's raw GeoParquet (``date``,
    EPSG:4326): ``shapes`` (polygons and point squares, from zoom 11) and
    ``points`` (a point per feature at every zoom)."""
    import geopandas as gpd

    raw = gpd.read_parquet(raw_path)

    def layers(folder):
        shapes = vector_shapes(raw, side_m)
        dates = [int(d) for d in raw["date"]]
        return {
            "shapes": _features_file(folder, "shapes", [
                {"type": "Feature", "properties": {"date": d}, "geometry": g.__geo_interface__,
                 "tippecanoe": {"minzoom": POLYGON_MIN_ZOOM}} for d, g in zip(dates, shapes.geometry)]),
            "points": _features_file(folder, "points", [
                {"type": "Feature", "properties": {"date": d}, "geometry": g.__geo_interface__}
                for d, g in zip(dates, raw.geometry.representative_point())]),
        }

    return _build(layers, out_path)


def _build(write_layers: Callable[[Path], dict], out_path, guard: Optional[Callable[[], None]] = None) -> Path:
    """Run tippecanoe over the layers ``write_layers(folder)`` writes (layer
    name -> GeoJSON file) on local disk and copy the archive onto ``out_path``
    (``guard``: ``alerts_to_pmtiles``)."""
    tippecanoe = resolve_tippecanoe()
    if tippecanoe is None:
        raise TippecanoeMissing("tippecanoe not found on PATH or next to the interpreter")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=local_scratch_dir()) as td:
        built = Path(td) / out_path.name
        cmd = [tippecanoe, "-o", str(built), "-t", td, "-Z", "0", "-z", "14", "-r1",
               "--drop-densest-as-needed", "--force"]
        for layer, path in write_layers(Path(td)).items():
            cmd += ["-L", json.dumps({"file": str(path), "layer": layer})]
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=TIPPECANOE_TIMEOUT_S)
        # Staged beside the target under a name of its own (two builders of
        # one set must not share it), created with the umask's default mode:
        # mkstemp made it 0600, which the rename carried onto alerts.pmtiles.
        partial = out_path.with_name(f"{out_path.name}.{uuid.uuid4().hex}.part")
        with open(partial, "xb"):
            pass
        try:
            shutil.copyfile(built, partial)
            if guard is not None:
                guard()
            partial.replace(out_path)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    return out_path
