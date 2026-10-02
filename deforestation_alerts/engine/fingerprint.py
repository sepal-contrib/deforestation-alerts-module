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


def mask_file_missing(mask) -> Optional[str]:
    """A GeoTIFF mask's path when it is gone (spec §5.3 "file not found")."""
    return mask.ref if mask.origin == "file" and not os.path.exists(mask.ref) else None


def _mask_def(mask) -> dict:
    d = mask.model_dump(mode="json", exclude={"name", "native"})
    if mask.origin == "file":
        d["file"] = _file_stamp(mask.ref)
    return d


def mask_raw_inputs(p: Project, name: str) -> dict:
    """An Earth Engine mask's native download: no grid term (spec §5.3), so a
    grid change leaves it current and only re-aligns it."""
    m = p.masks[name]
    return {"stage": "mask_raw", "mask": _mask_def(m), "aoi": p.aoi.digest if p.aoi else None,
            "native": [m.native.crs, m.native.scale] if m.native else None}


def mask_inputs(p: Project, name: str) -> Optional[dict]:
    """The aligned mask on the grid. None while an Earth Engine mask's raw
    download, or a GeoTIFF mask's AOI raster of this AOI and grid (it clips
    the GeoTIFF at alignment), is not recorded."""
    m = p.masks[name]
    grid = p.grid.signature if p.grid else None
    if m.origin == "gee":
        raw = p.artifacts.get(mask_raw_key(name))
        return None if raw is None else {"stage": "mask", "raw": raw.content_id, "grid": grid}
    aoi = p.artifacts.get(AOI_KEY)
    if aoi is None or aoi.fingerprint != fingerprint(aoi_raster_inputs(p)):
        return None
    return {"stage": "mask", "mask": _mask_def(m), "aoi": p.aoi.digest if p.aoi else None,
            "aoi_raster": aoi.content_id, "grid": grid}


def mask_current(p: Project, name: str) -> bool:
    """The aligned mask's record matches its current inputs on the current
    grid. Fingerprint only, no disk access (``status`` adds the file check)."""
    art, expected = p.artifacts.get(mask_key(name)), mask_inputs(p, name)
    return (art is not None and not art.no_data and expected is not None
            and art.fingerprint == fingerprint(expected))


def mask_aligned_status(p: Project, pdir, name: str) -> Status:
    """The aligned mask's ``status`` (fingerprint and file on disk). A GeoTIFF
    mask also needs its AOI raster on disk and current: an aligned GeoTIFF
    mask whose AOI raster is gone reads STALE, since rebuilding the AOI raster
    gives it a new content id and the mask must be aligned again."""
    aligned = status(p, pdir, mask_key(name), mask_inputs(p, name))
    if (aligned is Status.CURRENT and p.masks[name].origin == "file"
            and status(p, pdir, AOI_KEY, aoi_raster_inputs(p)) is not Status.CURRENT):
        return Status.STALE
    return aligned


def period_masks(p: Project, period: Period) -> list:
    """The ``(name, mode)`` of the project masks on in ``period``, sorted. The
    period is read from ``p.periods`` (the project holds the per-period
    choices, spec §7.3); a period not in the project is read as given."""
    period = p.periods.get(period.id, period)
    return [(n, c.mode) for n, c in sorted(period.masks.items()) if c.on and n in p.masks]


def harmonized_inputs(p: Project, source: str, period: Period) -> Optional[dict]:
    """None (blocked) while the raw artifact is not recorded, while a mask on
    in the period is not aligned current on the current grid, or, for a
    GeoTIFF source, while the AOI raster of this AOI and grid is not recorded.

    Harmonizing aligns the raw file onto the grid, so the grid is an input here.
    The ``masks`` term holds only this period's choices (spec §5.3): changing
    one period's masks touches no other period. A GeoTIFF source is clipped to
    the AOI polygon by the AOI raster (an Earth Engine download is clipped by
    its region already).
    """
    return _harmonized(p, source, period, _mask_terms(p, period))


def period_harmonized_inputs(p: Project, period: Period, sources=None) -> dict[str, Optional[dict]]:
    """``harmonized_inputs`` of each of ``sources`` (default: every project
    source) in ``period``, with the period's mask terms computed once rather
    than once per source (a render's badges read every period)."""
    terms = _mask_terms(p, period)
    names = p.sources if sources is None else [s for s in sources if s in p.sources]
    return {s: _harmonized(p, s, period, terms) for s in names}


_BLOCKED = object()  # a mask on in the period is not aligned current


def _mask_terms(p: Project, period: Period):
    """The harmonized inputs' ``masks`` term of ``period``, or ``_BLOCKED``."""
    masks = []
    for name, mode in period_masks(p, period):
        if not mask_current(p, name):
            return _BLOCKED  # blocked until the mask is aligned on the current grid (spec §5.3)
        masks.append([name, mode, p.artifacts[mask_key(name)].content_id])
    return masks


def _harmonized(p: Project, source: str, period: Period, masks) -> Optional[dict]:
    raw = p.artifacts.get(raw_key(period.id, source))
    if raw is None or masks is _BLOCKED:
        return None
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
    """STALE when the set's own inputs changed, or when an input of one of its
    sources' raw or harmonized files changed since (a mask switch, a grid, AOI
    or look-back change: spec §5.3, §14 slice 4 (a)), before any re-run;
    MISSING when its store is gone; else CURRENT.

    Only a fingerprint mismatch counts: a raw or harmonized file deleted from
    disk reads MISSING in its own row and is not a change of input (slice 1's
    ``test_reopened_project_with_deleted_files_offers_a_rerun`` keeps the set
    MISSING, not STALE). A moved user GeoTIFF reads "file not found" in its
    row, not out of date: a GeoTIFF source's raw comparison is skipped while
    its file is gone, and so is every harmonized comparison of the period while
    a GeoTIFF mask on in it is gone (its stamp would otherwise read as an input
    change). Manifest plus ``os.stat`` only: cheap enough for a render (Task
    34's badges read ``period_set_statuses``)."""
    period = p.periods.get(alert_set.period_id)
    if period is None:
        return Status.STALE
    return _set_status(p, pdir, alert_set, period, period_harmonized_inputs(p, period, alert_set.sources),
                       _mask_file_gone(p, period))


def period_set_statuses(p: Project, pdir, period: Period) -> dict[str, Status]:
    """``set_status`` of every set of ``period``, by set id, with the period's
    mask checks done once for all its sets and sources (Task 34's badges)."""
    period = p.periods.get(period.id, period)
    sets = [s for s in p.alert_sets.values() if s.period_id == period.id]
    if not sets:
        return {}
    harmonized, mask_gone = period_harmonized_inputs(p, period), _mask_file_gone(p, period)
    return {s.id: _set_status(p, pdir, s, period, harmonized, mask_gone) for s in sets}


def _mask_file_gone(p: Project, period: Period) -> bool:
    return any(mask_file_missing(p.masks[n]) for n, _ in period_masks(p, period))


def _set_status(p: Project, pdir, alert_set: AlertSet, period: Period, harmonized: dict,
                mask_gone: bool) -> Status:
    expected = set_inputs(p, period, alert_set.sources, alert_set.params)
    if expected is None or fingerprint(expected) != alert_set.fingerprint:
        return Status.STALE
    for s in alert_set.sources:
        src = p.sources.get(s)
        if src is None:
            return Status.STALE
        if (user_file_missing(src) is None
                and status(p, pdir, raw_key(period.id, s), raw_alert_inputs(p, s, period)) is Status.STALE):
            return Status.STALE
        if not mask_gone and status(p, pdir, harm_key(period.id, s), harmonized[s]) is Status.STALE:
            return Status.STALE
    if not (Path(pdir) / alert_set.store_rel_path).exists():
        return Status.MISSING
    return Status.CURRENT
