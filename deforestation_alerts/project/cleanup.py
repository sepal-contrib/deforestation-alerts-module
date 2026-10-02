"""Drop artifact records and delete their files (spec §5.2's one helper).

Callers drop the records inside ``AppState.update_project`` (the dict is
replaced, never mutated) and delete the returned files once the update has
returned True, outside the state lock.
"""

import shutil
from pathlib import Path
from typing import Iterable

from deforestation_alerts.engine.fingerprint import (
    combined_key, footprint_key, harm_key, mask_key, mask_raw_key, raw_key, store_key,
)
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import Project


def drop_artifacts(p: Project, keys: Iterable[str]) -> list[str]:
    keys = set(keys)
    gone = [a.rel_path for k, a in p.artifacts.items() if k in keys and a.rel_path]
    p.artifacts = {k: a for k, a in p.artifacts.items() if k not in keys}
    return gone


def delete_files(pdir, rel_paths: Iterable[str]) -> None:
    for rel in rel_paths:
        if not rel:
            continue
        path = Path(pdir) / rel
        path.unlink(missing_ok=True)
        path.with_name(path.name + ".ovr").unlink(missing_ok=True)


def source_artifact_keys(p: Project, name: str) -> list[str]:
    return [k for pid in p.periods for k in (raw_key(pid, name), harm_key(pid, name)) if k in p.artifacts]


def period_artifact_keys(p: Project, period_id: str) -> list[str]:
    prefixes = (f"raw:{period_id}:", f"harm:{period_id}:")
    return [k for k in p.artifacts if k.startswith(prefixes)]


def mask_artifact_keys(p: Project, name: str) -> list[str]:
    return [k for k in (mask_raw_key(name), mask_key(name)) if k in p.artifacts]


def set_artifact_keys(p: Project, set_ids) -> list[str]:
    """The recorded files of these sets: combined raster, footprint and store."""
    return [k for sid in sorted(set(set_ids)) for k in (combined_key(sid), footprint_key(sid), store_key(sid))
            if k in p.artifacts]


def delete_period_dir(pdir, period_id: str) -> None:
    """Remove the period's folder with everything in it: downloads, harmonized
    files and set folders, including the tiles, exports, reports and CNN
    composites Analysis wrote there, which no artifact records (spec §5.2).
    Never raises for a missing folder."""
    shutil.rmtree(paths.period_dir(pdir, period_id), ignore_errors=True)
