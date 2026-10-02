"""Seed a project folder for a browser walk: a drawn rectangular AOI, optional
predefined sources and one period, saved like the app saves it.

    python -m tools.walk.seed_project NAME --bbox W S E N [--source GLAD-L ...]
        [--period 2026-06-01 2026-06-30] [--look-back 90]

Writes ``$DEFORESTATION_ALERTS_DATA_DIR/NAME`` (``paths.data_dir()``). Keep that
directory under ``$HOME``: Analysis and Overview downloads are linked
relative to it.
"""

import argparse
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Sequence

import geopandas as gpd
from pysepal.solara.components.aoi.aoi_spec import AoiSpec
from shapely.geometry import box, mapping

from deforestation_alerts.project import io, paths
from deforestation_alerts.project.models import AoiRecord, Period, Project, add_predefined_source, period_id
from gui.scripts import aoi_io


def seed(name: str, bbox, sources: Sequence[str] = (), period: Optional[tuple] = None,
         look_back: int = 90, root: Optional[Path] = None) -> Project:
    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "properties": {}, "geometry": mapping(box(*bbox))}]}
    gdf = gpd.GeoDataFrame.from_features(fc["features"], crs="EPSG:4326")
    now = datetime.now()
    p = Project(name=name, created_at=now, updated_at=now, look_back_days=int(look_back))
    p.aoi = AoiRecord(
        spec=AoiSpec(method="DRAW", geo_json=fc, name=name).to_dict(),
        name=name,
        method="DRAW",
        digest=aoi_io.geometry_digest(gdf),
        bounds=tuple(float(v) for v in gdf.total_bounds),
        geometry_file=paths.AOI_SIDECAR,
    )
    for key in sources:
        add_predefined_source(p, key)
    if period:
        a, b = (date.fromisoformat(str(x)) for x in period)
        pid = period_id(a, b)
        p.periods = {pid: Period(id=pid, start=a, end=b)}
        p.active_period = pid
    aoi_io.write_sidecar(paths.project_dir(name, root), gdf)
    io.save_project(p, root)
    return p


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("name")
    ap.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("W", "S", "E", "N"))
    ap.add_argument("--source", action="append", default=[])
    ap.add_argument("--period", nargs=2, metavar=("START", "END"))
    ap.add_argument("--look-back", type=int, default=90)
    args = ap.parse_args(argv)
    p = seed(args.name, tuple(args.bbox), args.source, tuple(args.period) if args.period else None,
             args.look_back)
    print(paths.project_dir(p.name))


if __name__ == "__main__":
    main()
