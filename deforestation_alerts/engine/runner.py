"""One Download & harmonize row, run on a worker thread (spec §7.3).

A source is downloaded in its native projection (the raw file) and harmonized
onto the project grid locally (user decision 2026-09-30). The raw file depends
on the source, its window and the AOI only, so a grid change re-harmonizes
from the raw file already on disk and never downloads.

The worker gets a deep copy of the project and returns the artifacts it wrote;
the UI applies them to the live project (``apply_artifacts``) only if that
project is still open. Nothing here touches Solara.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from deforestation_alerts.engine import catalogue
from deforestation_alerts.engine.download import estimate_native_bytes, export_native
from deforestation_alerts.engine.fingerprint import (
    Status, harm_key, harmonized_inputs, make_artifact, mask_key, raw_alert_inputs, raw_key, status,
)
from deforestation_alerts.engine.grid import estimate_bytes
from deforestation_alerts.engine.harmonize import harmonize
from deforestation_alerts.engine.plan import plan_prepare  # re-exported for callers and tests
from deforestation_alerts.engine.raster_io import ensure_free_space
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import Artifact, Period, Project

__all__ = ["SourceJob", "apply_artifacts", "plan_prepare", "run_source_row"]


@dataclass(frozen=True)
class SourceJob:
    project: Project  # a deep copy: the worker never touches the live project
    pdir: Path
    period: Period
    source: str
    meta: Optional[dict]  # catalogue.meta_request(key), evaluated
    aoi: Any  # ee.FeatureCollection
    force: bool = False


def _drop_superseded(p: Project, pdir: Path, key: str, new_path: Optional[Path]) -> None:
    """Delete the file ``key``'s record points at, unless it is ``new_path``."""
    old = p.artifacts.get(key)
    if old is None or not old.rel_path:
        return
    old_path = paths.resolve(pdir, old.rel_path)
    if new_path is None or old_path != Path(new_path):
        old_path.unlink(missing_ok=True)


def run_source_row(job: SourceJob, *, on_tiles=None, on_wait=None) -> dict[str, Artifact]:
    """Download (when the raw file is missing, out of date or ``force``d) and
    harmonize (when the harmonized file is not current) one source for one
    period. Returns the artifacts written, keyed like ``project.artifacts``."""
    p, pdir, period = job.project, Path(job.pdir), job.period
    if p.aoi is None or p.grid is None:
        raise ValueError("the project needs an AOI and a grid before a download")
    src = p.sources[job.source]
    if src.kind != "predefined":
        raise NotImplementedError("custom sources are added in slice 3")
    win_start, win_end = period.search_window(p.look_back_days)
    rkey, hkey = raw_key(period.id, job.source), harm_key(period.id, job.source)
    out: dict[str, Artifact] = {}

    raw_in = raw_alert_inputs(p, job.source, period)
    raw_state = status(p, pdir, rkey, raw_in)
    if raw_state is Status.NO_DATA and not job.force:
        return out
    if job.force or raw_state in (Status.MISSING, Status.STALE):
        if job.meta is None:
            raise ValueError(f"{job.source}: the catalogue metadata is not loaded yet")
        if not catalogue.covers(src.catalogue_key, job.meta, win_start, win_end):
            _drop_superseded(p, pdir, rkey, None)
            _drop_superseded(p, pdir, hkey, None)
            out[rkey] = make_artifact(rkey, "raw_alert", pdir, None, raw_in, no_data=True)
            return out
        raw_path = paths.raw_path(pdir, period.id, job.source, win_start, win_end)
        ensure_free_space(raw_path.parent, 2 * estimate_native_bytes(p.aoi.bounds, src.catalogue_key))
        image = catalogue.build_image(src.catalogue_key, win_start, win_end, job.aoi, job.meta)
        export_native(image, src.catalogue_key, job.aoi, raw_path, on_tiles=on_tiles, on_wait=on_wait)
        _drop_superseded(p, pdir, rkey, raw_path)
        out[rkey] = make_artifact(rkey, "raw_alert", pdir, raw_path, raw_in)
        p.artifacts[rkey] = out[rkey]  # so harmonized_inputs sees the new content_id

    harm_in = harmonized_inputs(p, job.source, period)
    if harm_in is None:
        raise RuntimeError(f"{job.source}: an enabled mask has not been downloaded yet")
    if rkey in out or status(p, pdir, hkey, harm_in) is not Status.CURRENT:
        harm_path = paths.harmonized_path(pdir, period.id, job.source, win_start, win_end)
        ensure_free_space(harm_path.parent, 2 * estimate_bytes(p.grid))
        masks = [
            (paths.resolve(pdir, p.artifacts[mask_key(name)].rel_path), m.mode)
            for name, m in sorted(p.masks.items())
            if m.enabled
        ]
        harmonize(paths.resolve(pdir, p.artifacts[rkey].rel_path), masks, harm_path, p.grid)
        _drop_superseded(p, pdir, hkey, harm_path)
        out[hkey] = make_artifact(hkey, "harmonized", pdir, harm_path, harm_in)
    return out


def apply_artifacts(project: Project, artifacts: dict[str, Artifact]) -> None:
    project.artifacts.update(artifacts)
