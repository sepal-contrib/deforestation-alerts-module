"""Which Download & harmonize rows need running for a period (spec §7.3)."""

from dataclasses import dataclass

from deforestation_alerts.engine.fingerprint import (
    Status, harm_key, harmonized_inputs, raw_alert_inputs, raw_key, status,
)
from deforestation_alerts.project.models import Period, Project


@dataclass(frozen=True)
class PrepareRow:
    source: str
    raw: Status
    harmonized: Status

    @property
    def needs_run(self) -> bool:
        if self.raw in (Status.MISSING, Status.STALE):
            return True
        return self.raw is Status.CURRENT and self.harmonized is not Status.CURRENT

    @property
    def ready(self) -> bool:
        return self.raw is Status.CURRENT and self.harmonized is Status.CURRENT


def plan_prepare(p: Project, pdir, period: Period) -> list[PrepareRow]:
    rows = []
    for name, src in sorted(p.sources.items(), key=lambda kv: kv[1].digit):
        raw = status(p, pdir, raw_key(period.id, name), raw_alert_inputs(p, name, period))
        if raw is Status.NO_DATA:
            harm = Status.NO_DATA
        else:
            harm = status(p, pdir, harm_key(period.id, name), harmonized_inputs(p, name, period))
        rows.append(PrepareRow(source=name, raw=raw, harmonized=harm))
    return rows


def ready_sources(p: Project, pdir, period: Period) -> list[str]:
    return [r.source for r in plan_prepare(p, pdir, period) if r.ready]
