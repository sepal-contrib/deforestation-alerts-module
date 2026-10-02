"""Where and when each alert source has data for the project's AOI (spec §6.2, D19).

``coverage_request`` builds ONE unevaluated Earth Engine dictionary for the
three predefined sources and any custom ones; the caller evaluates it with
``await gee_interface.get_info_async(...)`` and ``parse_coverage`` turns it into
``SourceCoverage`` records, saved in the project so every gate, badge, table
and the runner agree. Everything after the request is pure Python.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Sequence

from deforestation_alerts.engine import catalogue
from deforestation_alerts.project.models import (
    PREDEFINED_KEYS, CustomSource, Project, RegionCoverage, SourceCoverage, mapped_bands,
)

ERR = 1000  # metres: simplify and intersection tolerance (spec §6.2)
FULL = 0.99  # a share at or above this reads "covers the AOI"

GLAD_L_FIRST = date(2018, 1, 1)
GLAD_S2_FIRST = date(2019, 1, 1)
RADD_FIRST = {"africa": date(2019, 1, 1), "asia": date(2020, 1, 1), "ca": date(2020, 1, 1), "sa": date(2020, 1, 1)}
RADD_FIRST_DEFAULT = date(2019, 1, 1)

COVERED, PARTIAL, NO_DATA, NOT_COVERED, UNCHECKED = "covered", "partial", "no_data", "not_covered", "unchecked"
BLOCKED = frozenset({NO_DATA, NOT_COVERED, UNCHECKED})

NOT_COVERED_KEYS = {
    "GLAD-L": "coverage.not_covered_glad_l",
    "RADD": "coverage.not_covered_radd",
    "GLAD-S2": "coverage.not_covered_glad_s2",
}


@dataclass(frozen=True)
class Note:
    """A message key with its values and a tone (info, warning, error, success)."""

    key: str
    args: tuple = ()
    tone: str = "warning"

    def kwargs(self) -> dict:
        return dict(self.args)


# ------------------------------------------------------------------ Earth Engine
def simplified_aoi(aoi_fc):
    """Each feature simplified to ``ERR`` first, then their union (spec §6.2)."""
    import ee

    return aoi_fc.map(lambda f: ee.Feature(f).simplify(ERR)).geometry(ERR).simplify(ERR)


def _entry(items, g, area):
    """``items``: an ee.List of {name, footprint, last?} -> {fraction, regions}."""
    import ee

    def region(d):
        d = ee.Dictionary(d)
        share = ee.Geometry(d.get("footprint")).intersection(g, ERR).area(ERR).divide(area)
        return d.remove(["footprint"]).set("fraction", share)

    union = ee.FeatureCollection(
        items.map(lambda d: ee.Feature(ee.Geometry(ee.Dictionary(d).get("footprint"))))
    ).geometry(ERR)
    fraction = ee.Algorithms.If(items.size().gt(0), union.intersection(g, ERR).area(ERR).divide(area), 0)
    return ee.Dictionary({"fraction": fraction, "regions": items.map(region)})


def _glad_l_items(bounds):
    import ee

    upd = ee.ImageCollection(catalogue.GLAD_L_UPD).filterBounds(bounds)
    regions = upd.aggregate_array("system:index").map(lambda s: ee.String(s).slice(6)).distinct()

    def item(r):
        latest = ee.Image(upd.filter(ee.Filter.stringEndsWith("system:index", ee.String("_").cat(r)))
                          .sort("system:time_start", False).first())
        return ee.Dictionary({"name": r, "footprint": latest.geometry(),
                              "last": ee.Date(latest.get("system:time_start")).format("yyyy-MM-dd")})

    return regions.map(item)


def _radd_items(bounds):
    import ee

    radd = (ee.ImageCollection(catalogue.RADD_COLLECTION).filterBounds(bounds)
            .filterMetadata("layer", "contains", "alert"))

    def item(geography):
        latest = ee.Image(radd.filter(ee.Filter.eq("geography", geography)).sort("system:time_end", False).first())
        return ee.Dictionary({"name": geography, "footprint": latest.geometry(),
                              "last": ee.Date(latest.get("system:time_end")).format("yyyy-MM-dd")})

    return radd.aggregate_array("geography").distinct().map(item)


def _glad_s2_items():
    import ee

    img = ee.Image(catalogue.GLAD_S2 + "/alert")
    return ee.List([ee.Dictionary({"name": "S2", "footprint": img.geometry(), "last": img.get("date")})])


def custom_footprint(src: CustomSource, bounds):
    """A custom source's footprint: an image's geometry, the union of the
    collection images that carry the mapped bands within ``bounds``, or a
    GeoTIFF's header bounds (planar, WGS84)."""
    import ee

    if src.asset_type == "IMAGE":
        return ee.Image(src.ref).geometry()
    if src.asset_type == "IMAGE_COLLECTION":
        col = ee.ImageCollection(src.ref).filterBounds(bounds)
        for band in mapped_bands(src):
            col = col.filter(ee.Filter.listContains("system:band_names", str(band)))
        return col.geometry(ERR)
    minx, miny, maxx, maxy = src.native.bounds
    return ee.Geometry.Rectangle([minx, miny, maxx, maxy], None, False)


def coverage_request(aoi_fc, predefined: Sequence[str] = (), custom: Sequence[CustomSource] = ()):
    """One unevaluated request for every named source over ``aoi_fc`` (an
    ``ee.FeatureCollection``). Evaluate it with ``get_info_async``."""
    import ee

    g = simplified_aoi(aoi_fc)
    bounds = g.bounds(ERR)
    area = g.area(ERR)
    builders = {"GLAD-L": lambda: _glad_l_items(bounds), "RADD": lambda: _radd_items(bounds),
                "GLAD-S2": _glad_s2_items}
    out = {}
    for key in predefined:
        out[key] = _entry(builders[key](), g, area)
    if "GLAD-L" in out:
        bands = ee.ImageCollection(catalogue.GLAD_L_UPD).aggregate_array("system:band_names").flatten().distinct()
        out["GLAD-L"] = ee.Dictionary(out["GLAD-L"]).set("bands", bands)
    for src in custom:
        items = ee.List([ee.Dictionary({"name": src.name, "footprint": custom_footprint(src, bounds)})])
        out[src.name] = _entry(items, g, area)
    return ee.Dictionary(out)


# ------------------------------------------------------------------ parsing
def _day(value) -> Optional[date]:
    return date.fromisoformat(str(value)[:10]) if value else None


def _first_day(source: str, region) -> Optional[date]:
    if source == "GLAD-L":
        return GLAD_L_FIRST
    if source == "RADD":
        return RADD_FIRST.get(str(region).lower(), RADD_FIRST_DEFAULT)
    if source == "GLAD-S2":
        return GLAD_S2_FIRST
    return None


def glad_l_gaps(upd_bands, last: Optional[date]) -> list:
    """Days between GLAD-L's first day and ``last`` that no collection holds:
    ``UpdResult`` holds the years its bands name (``conf26``), the yearly
    archives the others up to their cut (catalogue.GLAD_L_FINAL)."""
    if last is None:
        return []
    bands = set(upd_bands)
    held = []
    for year in range(GLAD_L_FIRST.year, last.year + 1):
        if f"conf{year % 100}" in bands:
            held.append((date(year, 1, 1), date(year, 12, 31)))
        elif year in catalogue.GLAD_L_FINAL:
            held.append((date(year, 1, 1), catalogue.GLAD_L_FINAL[year][1]))
    gaps, day = [], GLAD_L_FIRST
    for a, b in held:
        if a > day:
            gaps.append((day, min(a - timedelta(days=1), last)))
        day = max(day, b + timedelta(days=1))
        if day > last:
            return gaps
    if day <= last:
        gaps.append((day, last))
    return gaps


def parse_coverage(raw: dict, *, aoi_digest: str, today: date) -> dict:
    out = {}
    for name, entry in (raw or {}).items():
        predefined = name in PREDEFINED_KEYS
        regions = [
            RegionCoverage(
                name=str(r.get("name")),
                fraction=max(0.0, min(1.0, float(r.get("fraction") or 0.0))),
                first=_first_day(name, r.get("name")) if predefined else None,
                last=_day(r.get("last")) if predefined else None,
            )
            for r in (entry.get("regions") or [])
        ]
        gaps = []
        if name == "GLAD-L":
            lasts = [r.last for r in regions if r.last is not None]
            gaps = glad_l_gaps(entry.get("bands") or [], max(lasts) if lasts else None)
        out[name] = SourceCoverage(aoi_digest=aoi_digest, checked_on=today,
                                   fraction=max(0.0, min(1.0, float(entry.get("fraction") or 0.0))),
                                   regions=regions, gaps=gaps)
    return out


# ------------------------------------------------------------------ decisions
def record_for(p: Project, source: str) -> Optional[SourceCoverage]:
    cov = p.coverage.get(source) if p is not None else None
    if cov is None or p.aoi is None or cov.aoi_digest != p.aoi.digest:
        return None
    return cov


def pct_text(fraction: float) -> str:
    n = round(float(fraction) * 100)
    return "<1" if n == 0 and fraction > 0 else str(n)


def _covering(cov: SourceCoverage) -> list:
    return [r for r in cov.regions if r.fraction > 0]


def _dated(cov: SourceCoverage) -> list:
    return [r for r in _covering(cov) if r.first is not None and r.last is not None]


def _available(region: RegionCoverage, gaps, a: date, b: date) -> bool:
    """Does [a, b] hold a day of ``region``'s [first, last] outside the gaps?"""
    day, hi = max(a, region.first), min(b, region.last)
    for g0, g1 in sorted(gaps):
        if day > hi:
            return False
        if g0 <= day <= g1:
            day = g1 + timedelta(days=1)
    return day <= hi


def _src(source):
    return (("source", source),)


def window_coverage(cov: Optional[SourceCoverage], source: str, a: date, b: date) -> tuple:
    """``(state, notes)`` for the inclusive window [a, b] (spec §6.2)."""
    if cov is None:
        return UNCHECKED, (Note("coverage.unchecked", tone="error"),)
    if cov.fraction <= 0:
        key = NOT_COVERED_KEYS.get(source, "coverage.not_covered")
        return NOT_COVERED, (Note(key, _src(source), "error"),)
    dated = _dated(cov)
    if dated and not any(_available(r, cov.gaps, a, b) for r in dated):
        first, last = min(r.first for r in dated), max(r.last for r in dated)
        if b < first:
            note = Note("coverage.no_data_starts", _src(source) + (("first", first.isoformat()),), "error")
        elif a > last:
            note = Note("coverage.no_data_until", _src(source) + (("last", last.isoformat()),), "error")
        else:
            gap = next((g for g in sorted(cov.gaps) if g[0] <= b and g[1] >= a), None)
            note = (Note("coverage.no_data_gap", _src(source) + (("start", gap[0].isoformat()),
                                                                 ("end", gap[1].isoformat())), "error")
                    if gap else Note("coverage.no_data_window", _src(source), "error"))
        return NO_DATA, (note,)
    notes = []
    if cov.fraction < FULL:
        notes.append(Note("coverage.partial_share", _src(source) + (("pct", pct_text(cov.fraction)),)))
    if dated:
        latest_first = max(r.first for r in dated)
        earliest_last = min(r.last for r in dated)
        if a < latest_first:
            notes.append(Note("coverage.partial_starts", _src(source) + (("first", latest_first.isoformat()),)))
        if b > earliest_last:
            notes.append(Note("coverage.partial_until", _src(source) + (("last", earliest_last.isoformat()),)))
        for g0, g1 in sorted(cov.gaps):
            if g0 <= b and g1 >= a:
                notes.append(Note("coverage.partial_gap", _src(source) + (("start", g0.isoformat()),
                                                                          ("end", g1.isoformat()))))
    return (PARTIAL if notes else COVERED), tuple(notes)


def chip_note(cov: Optional[SourceCoverage]) -> Note:
    if cov is None:
        return Note("coverage.chip_unchecked", tone="error")
    if cov.fraction >= FULL:
        return Note("coverage.chip_full", tone="success")
    if cov.fraction > 0:
        return Note("coverage.chip_partial", (("pct", pct_text(cov.fraction)),), "warning")
    return Note("coverage.chip_none", tone="error")


def covering_dates(cov: Optional[SourceCoverage]) -> list:
    """``(region, first, last)`` for the dated regions that meet the AOI."""
    return [] if cov is None else [(r.name, r.first, r.last) for r in _dated(cov)]


def data_window(cov: Optional[SourceCoverage]) -> Optional[tuple]:
    """The span a preview shows without a period: first to last day of the covering regions."""
    dated = covering_dates(cov)
    return (min(d[1] for d in dated), max(d[2] for d in dated)) if dated else None
