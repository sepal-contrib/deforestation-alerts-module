"""The project grid: one CRS, pixel size and extent every raster shares."""

import math
from typing import Optional

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError

from deforestation_alerts.project.models import GridSpec, Project
from deforestation_alerts.scripts.geo_utils import get_utm_proj_str_from_lat_lon

NATIVE_RESOLUTION = {"GLAD-L": 30.0, "RADD": 10.0, "GLAD-S2": 10.0}
FALLBACK_RESOLUTION = 30.0


def normalise_epsg(text: str) -> str:
    text = (text or "").strip()
    return text if ":" in text else f"EPSG:{text}"


def auto_utm_epsg(bounds) -> str:
    minx, miny, maxx, maxy = bounds
    return get_utm_proj_str_from_lat_lon((minx + maxx) / 2, (miny + maxy) / 2)


def validate_projection(epsg: str, resolution) -> Optional[str]:
    """Blocking error key, or None. A geographic CRS is an error (spec §7.1):
    the minimum size in hectares becomes a pixel count, which needs metres.
    A projected CRS whose axis unit is not the metre (e.g. US survey feet)
    is rejected the same way, for the same reason (ruling #42)."""
    if not (epsg or "").strip():
        return "need_epsg"
    try:
        crs = CRS.from_user_input(normalise_epsg(epsg))
    except (CRSError, ValueError, TypeError):
        return "bad_epsg"
    if crs.is_geographic:
        return "geographic"
    axes = crs.axis_info
    if axes and axes[0].unit_name != "metre":
        return "geographic"
    try:
        metres = float(str(resolution).strip())
    except (TypeError, ValueError):
        return "bad_resolution"
    if metres <= 0 or not math.isfinite(metres):
        return "bad_resolution"
    return None


def build_grid(bounds, epsg: str, resolution: float) -> GridSpec:
    """AOI bounds (WGS84) -> the grid, snapped outward to the resolution."""
    code = normalise_epsg(epsg)
    res = float(resolution)
    t = Transformer.from_crs("EPSG:4326", code, always_xy=True)
    xmin, ymin, xmax, ymax = t.transform_bounds(*bounds, densify_pts=21)
    xmin = math.floor(xmin / res) * res
    ymin = math.floor(ymin / res) * res
    xmax = math.ceil(xmax / res) * res
    ymax = math.ceil(ymax / res) * res
    return GridSpec(
        epsg=code,
        resolution=res,
        xmin=xmin,
        ymax=ymax,
        width=int(round((xmax - xmin) / res)),
        height=int(round((ymax - ymin) / res)),
    )


def estimate_bytes(grid: GridSpec, bands: int = 2, itemsize: int = 4) -> int:
    """Uncompressed size of a ``bands``-band raster on ``grid`` (two Int32 bands
    by default). The free-space gates scale it with
    ``raster_io.compressed_estimate``; the grid form's size hint shows it as is."""
    return grid.width * grid.height * bands * itemsize


def default_resolution(project: Project) -> float:
    """Finest native resolution among the sources: the predefined table, a
    custom source's persisted ``native.scale``; 30 m when nothing is known."""
    known = []
    for s in project.sources.values():
        if s.kind == "predefined":
            known.append(NATIVE_RESOLUTION[s.catalogue_key])
        elif s.native is not None:
            known.append(float(s.native.scale))
    return min(known) if known else FALLBACK_RESOLUTION
