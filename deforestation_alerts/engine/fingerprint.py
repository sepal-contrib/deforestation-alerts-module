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

from deforestation_alerts.project import paths
from deforestation_alerts.project.models import AlertSet, AlertSetParams, Artifact, Period, Project


class Status(str, Enum):
    MISSING = "missing"
    STALE = "stale"
    CURRENT = "current"
    NO_DATA = "no_data"


def fingerprint(inputs: dict) -> str:
    blob = json.dumps(inputs, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def raw_key(period_id: str, source: str) -> str:
    return f"raw:{period_id}:{source}"


def harm_key(period_id: str, source: str) -> str:
    return f"harm:{period_id}:{source}"


def mask_key(name: str) -> str:
    return f"mask:{name}"


def combined_key(set_id: str) -> str:
    return f"combined:{set_id}"


def footprint_key(set_id: str) -> str:
    return f"footprint:{set_id}"


def store_key(set_id: str) -> str:
    return f"store:{set_id}"


def pmtiles_key(set_id: str) -> str:
    return f"pmtiles:{set_id}"


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


def raw_alert_inputs(p: Project, source: str, period: Period) -> dict:
    win_start, win_end = period.search_window(p.look_back_days)
    return {
        "stage": "raw_alert",
        "source": _source_def(p.sources[source]),
        "window": [win_start.isoformat(), win_end.isoformat()],
        "aoi": p.aoi.digest if p.aoi else None,
        "grid": p.grid.signature if p.grid else None,
    }


def harmonized_inputs(p: Project, source: str, period: Period) -> Optional[dict]:
    """None while the raw artifact or an enabled mask's artifact is not recorded."""
    raw = p.artifacts.get(raw_key(period.id, source))
    if raw is None:
        return None
    masks = []
    for name in sorted(n for n, m in p.masks.items() if m.enabled):
        art = p.artifacts.get(mask_key(name))
        if art is None:
            return None
        masks.append([name, p.masks[name].mode, art.content_id])
    return {"stage": "harmonized", "raw": raw.content_id, "masks": masks}


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


def make_artifact(key, kind, pdir, path, inputs: dict, *, no_data: bool = False) -> Artifact:
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
        no_data=no_data,
        created_at=datetime.now(),
    )


def status(p: Project, pdir, key: str, expected: Optional[dict]) -> Status:
    art = p.artifacts.get(key)
    if art is None:
        return Status.MISSING
    if expected is None or art.fingerprint != fingerprint(expected):
        return Status.STALE
    if art.no_data:
        return Status.NO_DATA
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
