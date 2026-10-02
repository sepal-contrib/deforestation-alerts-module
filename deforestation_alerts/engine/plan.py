"""Which Download & harmonize rows (sources and masks) need running for a period (spec §7.3)."""

from dataclasses import dataclass
from typing import Optional

from deforestation_alerts.engine.coverage import BLOCKED, COVERED, NO_DATA, NOT_COVERED, record_for, window_coverage
from deforestation_alerts.engine.fingerprint import (
    Status, harm_key, harmonized_inputs, mask_file_missing, mask_inputs, mask_key, mask_raw_inputs, mask_raw_key,
    raw_alert_inputs, raw_key, status, user_file_missing,
)
from deforestation_alerts.project.models import Period, Project


@dataclass(frozen=True)
class PrepareRow:
    source: str
    raw: Status
    harmonized: Status
    coverage: str = COVERED
    notes: tuple = ()  # coverage.Note, one per cause
    file_missing: Optional[str] = None  # a custom GeoTIFF that is gone
    stats: Optional[dict] = None  # the current harmonized file's stats

    @property
    def blocked(self) -> bool:
        """Not runnable now: not covered, no data for the window, no coverage
        record, or its user file is gone. Run all skips it; the runner refuses it."""
        return self.coverage in BLOCKED or self.file_missing is not None

    @property
    def needs_run(self) -> bool:
        if self.blocked:
            return False
        if self.raw in (Status.MISSING, Status.STALE):
            return True
        return self.raw is Status.CURRENT and self.harmonized is not Status.CURRENT

    @property
    def ready(self) -> bool:
        """Both files current and the source not known to miss this AOI or
        window. A row whose coverage record is merely missing (the check
        failed this session) keeps files it already built."""
        return (self.raw is Status.CURRENT and self.harmonized is Status.CURRENT
                and self.coverage not in (NOT_COVERED, NO_DATA) and self.file_missing is None)


@dataclass(frozen=True)
class MaskRow:
    name: str
    origin: str
    raw: Optional[Status]  # None for a GeoTIFF (its file is read where it is)
    aligned: Status
    on: bool
    mode: str
    file_missing: Optional[str] = None  # a GeoTIFF mask that is gone
    stats: Optional[dict] = None  # the current aligned file's stats ({"on_pixels": n})

    @property
    def ready(self) -> bool:
        return self.aligned is Status.CURRENT and self.raw in (None, Status.CURRENT)

    @property
    def needs_run(self) -> bool:
        """Run all runs a mask only when it is on in the period (spec §7.3)."""
        return self.on and self.file_missing is None and not self.ready

    @property
    def covers_none(self) -> bool:
        return self.ready and self.stats is not None and int(self.stats.get("on_pixels", 1)) == 0


def plan_masks(p: Project, pdir, period: Optional[Period]) -> list[MaskRow]:
    """One row per project mask, by name, with its on/mode in ``period`` (read
    from ``p.periods``: the project holds the per-period choices). ``aligned``
    is CURRENT only when the aligned file's fingerprint matches its inputs on
    the current grid and the file is on disk."""
    if period is not None:
        period = p.periods.get(period.id, period)
    rows = []
    for name, m in sorted(p.masks.items()):
        choice = period.masks.get(name) if period is not None else None
        missing = mask_file_missing(m)
        raw = None if m.origin == "file" else status(p, pdir, mask_raw_key(name), mask_raw_inputs(p, name))
        aligned = Status.MISSING if missing else status(p, pdir, mask_key(name), mask_inputs(p, name))
        art = p.artifacts.get(mask_key(name))
        rows.append(MaskRow(name=name, origin=m.origin, raw=raw, aligned=aligned,
                            on=bool(choice and choice.on), mode=choice.mode if choice else "keep",
                            file_missing=missing,
                            stats=dict(art.stats) if aligned is Status.CURRENT and art is not None else None))
    return rows


def plan_prepare(p: Project, pdir, period: Period) -> list[PrepareRow]:
    a, b = period.search_window(p.look_back_days)
    rows = []
    for name, src in sorted(p.sources.items(), key=lambda kv: kv[1].digit):
        state, notes = window_coverage(record_for(p, name), name, a, b)
        missing = user_file_missing(src)
        raw = Status.MISSING if missing else status(p, pdir, raw_key(period.id, name),
                                                    raw_alert_inputs(p, name, period))
        hkey = harm_key(period.id, name)
        harm = status(p, pdir, hkey, harmonized_inputs(p, name, period))
        art = p.artifacts.get(hkey)
        rows.append(PrepareRow(source=name, raw=raw, harmonized=harm, coverage=state, notes=notes,
                               file_missing=missing,
                               stats=dict(art.stats) if harm is Status.CURRENT and art is not None else None))
    return rows


def ready_sources(p: Project, pdir, period: Period) -> list[str]:
    return [r.source for r in plan_prepare(p, pdir, period) if r.ready]
