"""An alert set as a PMTiles archive for Overview (spec §9.2).

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

from deforestation_alerts.gdal_env import local_scratch_dir

logger = logging.getLogger("SEPALUI.deforestation_alerts")

STATUS_CODES = {"Not reviewed": 0, "Confirmed": 1, "False Positive": 2, "Need revision": 3}
POLYGON_MIN_ZOOM = 11


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


def _write_layers(gdf, folder: Path) -> tuple[Path, Path]:
    gdf = gdf.to_crs(epsg=4326)
    polys, points = [], []
    for _, row in gdf.iterrows():
        props = _props(row)
        polys.append({"type": "Feature", "properties": props,
                      "geometry": row.geometry.__geo_interface__,
                      "tippecanoe": {"minzoom": POLYGON_MIN_ZOOM}})
        points.append({"type": "Feature", "properties": props,
                       "geometry": row.geometry.centroid.__geo_interface__})
    a, b = folder / "alerts.geojson", folder / "points.geojson"
    a.write_text(json.dumps({"type": "FeatureCollection", "features": polys}))
    b.write_text(json.dumps({"type": "FeatureCollection", "features": points}))
    return a, b


def alerts_to_pmtiles(alerts_gdf, out_path, *, guard: Optional[Callable[[], None]] = None) -> Path:
    """Build ``out_path`` from ``alerts_gdf``.

    ``guard``, when given, runs after tippecanoe and right before the built
    archive is moved onto ``out_path``: a caller that built from a store on
    disk checks there that the store is still the one it read. If it raises,
    nothing is moved into place, the staged copy is removed, and the
    exception propagates.
    """
    tippecanoe = resolve_tippecanoe()
    if tippecanoe is None:
        raise RuntimeError("tippecanoe not found on PATH or next to the interpreter")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=local_scratch_dir()) as td:
        polys, points = _write_layers(alerts_gdf, Path(td))
        built = Path(td) / out_path.name
        cmd = [
            tippecanoe, "-o", str(built), "-t", td, "-Z", "0", "-z", "14", "-r1",
            "--drop-densest-as-needed", "--force",
            "-L", json.dumps({"file": str(polys), "layer": "alerts"}),
            "-L", json.dumps({"file": str(points), "layer": "points"}),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
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
