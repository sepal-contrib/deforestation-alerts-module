"""Why a source or mask is out of date (the Download & harmonize Why line).

Compares an artifact's stored ``inputs`` with today's terms, rebuilt one by
one rather than through ``harmonized_inputs`` / ``mask_inputs``: those return
None while a mask is unaligned or the AOI raster is not current, and the Why
line must still name what changed. The first differing term wins, in a fixed
priority: grid, AOI, AOI raster, search window, user file, source settings,
an unaligned mask, the masks, downloaded again.
"""

from dataclasses import dataclass
from typing import Optional

from deforestation_alerts.engine.fingerprint import (
    AOI_KEY, aoi_raster_inputs, fingerprint, harm_key, is_file_source, mask_current, mask_key, mask_raw_inputs,
    mask_raw_key, period_masks, raw_alert_inputs, raw_key,
)
from deforestation_alerts.project.models import Period, Project

GEOGRAPHIC = frozenset({"EPSG:4326"})  # resolution in degrees: printed without a unit


@dataclass(frozen=True)
class Reason:
    """A ``steps.prepare.why_*`` key with its values, as (name, value) pairs."""

    key: str
    values: tuple = ()

    def kwargs(self) -> dict:
        return dict(self.values)


def grid_label(signature: str) -> str:
    """``EPSG:32719 · 30 m`` from a ``GridSpec.signature`` (``epsg|res|xmin|ymax|WxH``)."""
    epsg, res = signature.split("|")[:2]
    text = str(int(float(res))) if float(res).is_integer() else res
    return f"{epsg} · {text}" if epsg in GEOGRAPHIC else f"{epsg} · {text} m"


def source_reason(p: Project, period: Period, source: str) -> Optional[Reason]:
    """What changed since ``source`` was downloaded and harmonized in ``period``,
    or None when nothing did. A raw record with no harmonized one reads the raw
    side's change first (Run downloads again), else ``not_harmonized``."""
    raw, harm = _record(p, raw_key(period.id, source)), _record(p, harm_key(period.id, source))
    file_source = is_file_source(p.sources[source])
    if raw is None:
        return Reason("not_read" if file_source else "not_downloaded")
    today = raw_alert_inputs(p, source, period)
    raw_side = [
        _same("aoi", raw.inputs.get("aoi"), today["aoi"]),
        _window(raw.inputs.get("window"), today["window"]),
        *_definition(raw.inputs.get("source"), today["source"]),
        _same("source", raw.inputs.get("native"), today["native"]),
    ]
    unaligned, masks = _mask_terms(p, period)
    if harm is None:
        return _first(*raw_side, unaligned) or Reason("not_harmonized")
    stored = harm.inputs
    return _first(
        _grid(stored.get("grid"), p),
        raw_side[0],
        _same("aoi_raster", stored.get("aoi_raster"), _aoi_raster(p)) if file_source else None,
        *raw_side[1:],
        unaligned or _masks(stored.get("masks") or [], masks),
        _same("raw", stored.get("raw"), raw.content_id),
    )


def mask_reason(p: Project, name: str) -> Optional[Reason]:
    """What changed since mask ``name`` was downloaded (Earth Engine) and aligned,
    or None when nothing did. An Earth Engine mask with a download and no
    aligned record reads the download's change first, else ``not_aligned``."""
    m = p.masks[name]
    aligned = _record(p, mask_key(name))
    today = mask_raw_inputs(p, name)
    if m.origin == "gee":
        raw = _record(p, mask_raw_key(name))
        if raw is None:
            return Reason("not_downloaded")
        aoi = _same("aoi", raw.inputs.get("aoi"), today["aoi"])
        definition = [*_definition(raw.inputs.get("mask"), today["mask"]),
                      _same("source", raw.inputs.get("native"), today["native"])]
        if aligned is None:
            return _first(aoi, *definition) or Reason("not_aligned")
        return _first(_grid(aligned.inputs.get("grid"), p), aoi, *definition,
                      _same("raw", aligned.inputs.get("raw"), raw.content_id))
    if aligned is None:
        return Reason("not_aligned")
    stored = aligned.inputs
    return _first(
        _grid(stored.get("grid"), p),
        _same("aoi", stored.get("aoi"), today["aoi"]),
        _same("aoi_raster", stored.get("aoi_raster"), _aoi_raster(p)),
        *_definition(stored.get("mask"), today["mask"]),
    )


def _record(p: Project, key: str):
    """The artifact under ``key``; a slice-1 ``no_data`` record reads as none (``status``)."""
    art = p.artifacts.get(key)
    return None if art is None or art.no_data else art


def _first(*reasons) -> Optional[Reason]:
    return next((r for r in reasons if r is not None), None)


def _same(key: str, old, new) -> Optional[Reason]:
    return None if old == new else Reason(key)


def _grid(old: Optional[str], p: Project) -> Optional[Reason]:
    new = p.grid.signature if p.grid else None
    if old == new:
        return None
    return Reason("grid", (("old", grid_label(old) if old else ""), ("new", grid_label(new) if new else "")))


def _window(old, new) -> Optional[Reason]:
    if list(old or ()) == list(new):
        return None
    return Reason("window", (("old", " – ".join(old) if old else ""), ("new", " – ".join(new))))


def _definition(old: Optional[dict], new: dict) -> tuple:
    """``file`` when a source's or mask's definition differs only in its user
    file stamp, ``source`` when anything else in it differs: a (file, source)
    pair so each takes its own place in the priority."""
    if old == new:
        return None, None
    rest = {k: v for k, v in (old or {}).items() if k != "file"}
    if rest == {k: v for k, v in new.items() if k != "file"}:
        return Reason("file"), None
    return None, Reason("source")


def _aoi_raster(p: Project) -> Optional[str]:
    """The AOI raster's content id while it is of this AOI and grid, else None."""
    aoi = p.artifacts.get(AOI_KEY)
    return aoi.content_id if aoi is not None and aoi.fingerprint == fingerprint(aoi_raster_inputs(p)) else None


def _mask_terms(p: Project, period: Period) -> tuple[Optional[Reason], list]:
    """``mask_not_aligned`` naming the first mask on in ``period`` that is not
    aligned current (else None), and today's ``masks`` term of the aligned ones."""
    terms = []
    for name, mode in period_masks(p, period):
        if not mask_current(p, name):
            return Reason("mask_not_aligned", (("name", name),)), terms
        terms.append([name, mode, p.artifacts[mask_key(name)].content_id])
    return None, terms


def _masks(old: list, new: list) -> Optional[Reason]:
    """``mask`` naming it when one mask was added, removed, re-aligned or
    switched mode, ``masks`` when several were."""
    before, after = {m[0]: list(m) for m in old}, {m[0]: list(m) for m in new}
    changed = sorted(n for n in before.keys() | after.keys() if before.get(n) != after.get(n))
    if not changed:
        return None
    return Reason("mask", (("name", changed[0]),)) if len(changed) == 1 else Reason("masks")
