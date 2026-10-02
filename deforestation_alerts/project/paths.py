"""Project folder layout. Every path is absolute: on SEPAL the process CWD is
the read-only module mount, so nothing may be relative to it."""

import os
from datetime import date
from pathlib import Path
from typing import Optional

from deforestation_alerts.project.models import sources_slug
from deforestation_alerts.scripts.naming import alert_artifact_name, safe_token

DATA_DIR_ENV = "DEFORESTATION_ALERTS_DATA_DIR"
MANIFEST = "project.json"
AOI_SIDECAR = "aoi.geojson"


def data_dir() -> Path:
    env = os.environ.get(DATA_DIR_ENV)
    base = Path(env) if env else Path.home() / "module_results" / "deforestation_alerts"
    return base.expanduser().resolve()


def project_dir(name: str, root: Optional[Path] = None) -> Path:
    return (Path(root) if root else data_dir()) / name


def _sorted(sources):
    return sorted(sources, key=str.casefold)


def mask_path(pdir: Path, mask_name: str) -> Path:
    return Path(pdir) / "masks" / f"mask_{safe_token(mask_name)}.tif"


def aoi_raster_path(pdir: Path) -> Path:
    return Path(pdir) / "masks" / "aoi.tif"


def mask_raw_path(pdir: Path, mask_name: str) -> Path:
    return Path(pdir) / "masks" / "raw" / f"mask_{safe_token(mask_name)}.tif"


def _period(pdir: Path, period_id: str) -> Path:
    return Path(pdir) / "periods" / period_id


def raw_path(pdir, period_id, source, win_start: date, win_end: date) -> Path:
    name = alert_artifact_name("raw", [source], win_start.isoformat(), win_end.isoformat(), "tif")
    return _period(pdir, period_id) / "raw" / name


def harmonized_path(pdir, period_id, source, win_start: date, win_end: date) -> Path:
    name = alert_artifact_name("harmonized", [source], win_start.isoformat(), win_end.isoformat(), "tif")
    return _period(pdir, period_id) / "harmonized" / name


def set_dir(pdir, period_id, sources) -> Path:
    return _period(pdir, period_id) / "sets" / sources_slug(sources)


def combined_path(set_directory, sources, start: date, end: date) -> Path:
    return Path(set_directory) / alert_artifact_name(
        "filtered_alerts", _sorted(sources), start.isoformat(), end.isoformat(), "tif"
    )


def footprint_path(set_directory) -> Path:
    return Path(set_directory) / "footprint.tif"


def store_path(set_directory) -> Path:
    return Path(set_directory) / "alert_db.parquet"


def pmtiles_path(set_directory) -> Path:
    return Path(set_directory) / "alerts.pmtiles"


def rel(pdir, path) -> str:
    return Path(path).relative_to(Path(pdir)).as_posix()


def resolve(pdir, rel_path: str) -> Path:
    return Path(pdir) / rel_path
