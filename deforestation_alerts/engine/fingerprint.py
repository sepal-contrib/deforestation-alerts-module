"""What each artifact was built from, and whether it is still current (spec §5.3).

Downstream fingerprints use their inputs' ``content_id`` (a fresh uuid on every
write), not the inputs' own fingerprints: a forced re-download of a live
catalogue produces new pixels from identical inputs, and everything built from
it must go out of date. The earlier sets a set's footprint depends on are
recomputed every time, so a set added later for an earlier period is noticed.
"""

import hashlib
import json
import os
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from deforestation_alerts.engine import catalogue
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import AlertSet, AlertSetParams, Artifact, Period, Project


class Status(str, Enum):
    MISSING = "missing"
    STALE = "stale"
    CURRENT = "current"


AOI_KEY = "aoi_mask"


def fingerprint(inputs: dict) -> str:
    blob = json.dumps(inputs, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def raw_key(period_id: str, source: str) -> str:
    return f"raw:{period_id}:{source}"


def harm_key(period_id: str, source: str) -> str:
    return f"harm:{period_id}:{source}"


def mask_key(name: str) -> str:
    return f"mask:{name}"


def mask_raw_key(name: str) -> str:
    return f"mask_raw:{name}"


def combined_key(set_id: str) -> str:
    return f"combined:{set_id}"


def footprint_key(set_id: str) -> str:
    return f"footprint:{set_id}"


def store_key(set_id: str) -> str:
    return f"store:{set_id}"


def _file_stamp(path: str) -> dict:
    try:
        st = os.stat(path)
    except OSError:
        return {"missing": True}
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _source_def(src) -> dict:
    d = src.model_dump(mode="json", exclude={"name", "digit"})
    if d.get("kind") == "custom" and d.get("origin") == "file":
        d["file"] = _file_stamp(d["ref"])
    return d


def is_file_source(src) -> bool:
    return getattr(src, "kind", "") == "custom" and src.origin == "file"


def user_file_missing(src) -> Optional[str]:
    """The user's GeoTIFF path when it is gone (spec §5.3 "file not found")."""
    return src.ref if is_file_source(src) and not os.path.exists(src.ref) else None


def _native(src) -> Optional[list]:
    """The projection a source is downloaded or read in (part of the raw fingerprint)."""
    if src.kind == "predefined":
        crs, scale = catalogue.NATIVE[src.catalogue_key]
        return [crs, scale]
    if src.native is None:
        return None
    if src.origin == "file":
        return [src.native.crs, list(src.native.transform or ())]
    return [src.native.crs, src.native.scale]


def aoi_raster_inputs(p: Project) -> dict:
    """The AOI raster (``masks/aoi.tif``) burns the AOI polygon onto the grid."""
    return {"stage": "aoi_mask", "aoi": p.aoi.digest if p.aoi else None,
            "grid": p.grid.signature if p.grid else None}


def raw_alert_inputs(p: Project, source: str, period: Period) -> dict:
    """The raw download is in the source's native projection, not on the grid
    (user decision 2026-09-30), so a grid change never makes it out of date."""
    win_start, win_end = period.search_window(p.look_back_days)
    src = p.sources[source]
    return {
        "stage": "raw_alert",
        "source": _source_def(src),
        "window": [win_start.isoformat(), win_end.isoformat()],
        "aoi": p.aoi.digest if p.aoi else None,
        "native": _native(src),
    }


def harmonized_inputs(p: Project, source: str, period: Period) -> Optional[dict]:
    """None while the raw artifact or an "on" mask's artifact is not recorded,
    or, for a GeoTIFF source, while the AOI raster of this AOI and grid is not.

    Harmonizing aligns the raw file onto the grid, so the grid is an input here.
    A GeoTIFF source is clipped to the AOI polygon by the AOI raster (an Earth
    Engine download is clipped by its region already).
    """
    raw = p.artifacts.get(raw_key(period.id, source))
    if raw is None:
        return None
    masks = []
    for name, choice in sorted(period.masks.items()):
        if not (choice.on and name in p.masks):
            continue
        art = p.artifacts.get(mask_key(name))
        if art is None:
            return None
        masks.append([name, choice.mode, art.content_id])
    inputs = {"stage": "harmonized", "raw": raw.content_id, "grid": p.grid.signature if p.grid else None,
              "masks": masks}
    if is_file_source(p.sources[source]):
        aoi = p.artifacts.get(AOI_KEY)
        if aoi is None or aoi.fingerprint != fingerprint(aoi_raster_inputs(p)):
            return None  # the GeoTIFF is clipped by the AOI raster of this AOI and grid
        inputs["aoi_raster"] = aoi.content_id
    return inputs


def earlier_sets(p: Project, period: Period) -> list[AlertSet]:
    out = []
    for s in p.alert_sets.values():
        other = p.periods.get(s.period_id)
        if other is not None and other.start < period.start:
            out.append(s)
    return sorted(out, key=lambda s: s.id)


def set_inputs(p: Project, period: Period, sources: list[str], params: AlertSetParams) -> Optional[dict]:
    harmonized = []
    for source in sorted(sources, key=str.casefold):
        art = p.artifacts.get(harm_key(period.id, source))
        if art is None:
            return None
        harmonized.append([source, art.content_id])
    footprints = []
    for s in earlier_sets(p, period):
        art = p.artifacts.get(footprint_key(s.id))
        footprints.append([s.id, art.content_id if art else None])
    return {
        "stage": "set",
        "harmonized": harmonized,
        "footprints": footprints,
        "params": params.model_dump(mode="json"),
    }


def make_artifact(key, kind, pdir, path, inputs: dict, *, stats: Optional[dict] = None) -> Artifact:
    rel = paths.rel(pdir, path) if path is not None else ""
    size = Path(path).stat().st_size if path is not None and Path(path).exists() else 0
    return Artifact(
        key=key,
        kind=kind,
        rel_path=rel,
        fingerprint=fingerprint(inputs),
        inputs=inputs,
        content_id=uuid.uuid4().hex,
        size_bytes=size,
        stats=dict(stats or {}),
        created_at=datetime.now(),
    )


def status(p: Project, pdir, key: str, expected: Optional[dict]) -> Status:
    """MISSING (no record, a slice-1 ``no_data`` record, no file), STALE, or CURRENT.
    Coverage is not an artifact status: see ``coverage.window_coverage``."""
    art = p.artifacts.get(key)
    if art is None or art.no_data or not art.rel_path:
        return Status.MISSING
    if expected is None or art.fingerprint != fingerprint(expected):
        return Status.STALE
    if not (Path(pdir) / art.rel_path).exists():
        return Status.MISSING
    return Status.CURRENT


def set_status(p: Project, pdir, alert_set: AlertSet) -> Status:
    period = p.periods.get(alert_set.period_id)
    if period is None:
        return Status.STALE
    expected = set_inputs(p, period, alert_set.sources, alert_set.params)
    if expected is None or fingerprint(expected) != alert_set.fingerprint:
        return Status.STALE
    if not (Path(pdir) / alert_set.store_rel_path).exists():
        return Status.MISSING
    return Status.CURRENT
