"""One Download & harmonize row, run on a worker thread (spec §7.3).

The coverage guard comes first (spec §7.2): a row whose source does not cover
the AOI, has no data for the window, or has no coverage record for this AOI is
refused (``CoverageRefused``) before anything else, with no Earth Engine call
and nothing recorded. Coverage is never an artifact status, so a refusal never
sticks: the next coverage check decides again.

A source is downloaded in its native projection (the raw file) and harmonized
onto the project grid locally (user decision 2026-09-30). A predefined source
and a custom Earth Engine source download with ``crs + scale + region`` (the
custom one from its persisted ``native``); a custom GeoTIFF is normalized
locally from the user's file and, at harmonize, clipped to the AOI polygon by
the AOI raster (``masks/aoi.tif``, rebuilt first whenever the AOI or the grid
changed). The raw file depends on the source, its window and the AOI only, so
a grid change re-harmonizes from the raw file already on disk and never
downloads. The harmonize stats are recorded on the harmonized artifact.

The worker gets a deep copy of the project and returns the artifacts it wrote;
the UI applies them to the live project (``apply_artifacts``) only if that
project is still open. A harmonize that fails after a new download raises
``HarmonizeFailed``, which carries the raw artifact so the caller records it
the same way: the next run then harmonizes from the file on disk instead of
downloading again. Nothing here touches Solara.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from deforestation_alerts.engine import catalogue, normalize
from deforestation_alerts.engine.coverage import BLOCKED, record_for, window_coverage
from deforestation_alerts.engine.download import estimate_native_bytes, export_native
from deforestation_alerts.engine.fingerprint import (
    AOI_KEY, Status, aoi_raster_inputs, harm_key, harmonized_inputs, is_file_source, make_artifact, mask_file_missing,
    mask_aligned_status, mask_inputs, mask_key, mask_raw_inputs, mask_raw_key, period_masks, raw_alert_inputs, raw_key,
    status, user_file_missing,
)
from deforestation_alerts.engine.grid import estimate_bytes
from deforestation_alerts.engine.harmonize import harmonize
from deforestation_alerts.engine.masks import (
    align_mask, build_aoi_raster, build_mask_image, export_mask_native, mask_raw_estimate,
)
from deforestation_alerts.engine.plan import plan_prepare  # re-exported for callers and tests
from deforestation_alerts.engine.raster_io import compressed_estimate, ensure_free_space
from deforestation_alerts.errors import KeyedError, ProjectRuleError, UserFileMissing
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import Artifact, Period, Project

__all__ = [
    "AoiGeometryMissing", "CoverageRefused", "HarmonizeFailed", "MaskFileMissing", "MaskJob", "MaskNativeUnknown",
    "MaskNotReady",
    "SourceJob",
    "apply_artifacts", "ensure_aoi_raster", "plan_prepare", "run_mask_row", "run_source_row",
]


class CoverageRefused(RuntimeError):
    """The row's source does not cover the AOI, has no data for the window, or
    has no coverage record (spec §7.2 "Coverage guard"). Raised before
    anything else: no Earth Engine call, nothing recorded."""

    def __init__(self, source: str, state: str, notes: tuple):
        super().__init__(f"{source}: {state}")
        self.source, self.state, self.notes = source, state, notes


class AoiGeometryMissing(KeyedError, RuntimeError):
    """A GeoTIFF row needs ``aoi.geojson`` for the AOI raster, and it is not
    written yet (an admin or asset AOI's geometry is being fetched)."""

    key = "steps.prepare.aoi_geometry_missing"


class MaskNotReady(KeyedError, RuntimeError):
    """A mask on in the period is not aligned on the current grid; run it first."""

    key = "steps.prepare.mask_not_ready"


class MaskFileMissing(KeyedError, FileNotFoundError):
    """A GeoTIFF mask on in the period is gone from disk (spec §5.3 "file not
    found"): Run all cannot align it; the user moves it back or edits the mask."""

    key = "steps.prepare.mask_file_missing"

    def __init__(self, name: str, path: str):
        super().__init__(f"{name}: file not found: {path}", name=name, path=path)


class MaskNativeUnknown(KeyedError, ValueError):
    """An Earth Engine mask without its persisted native grid (crs, scale):
    it cannot be downloaded natively; the user edits the mask."""

    key = "steps.prepare.mask_native_unknown"

    def __init__(self, name: str):
        super().__init__(f"{name}: its native grid is unknown; edit the mask", name=name)


class HarmonizeFailed(KeyedError, RuntimeError):
    """Harmonizing (or a mask's alignment) failed after this run downloaded a
    new raw file (or rebuilt the AOI raster).

    ``artifacts`` holds those records (keyed like ``project.artifacts``); the
    caller applies them like a returned result, so the download is not lost. The
    message is the cause's (its text, and its message key and values when it
    has them), which stays chained as ``__cause__``.
    """

    def __init__(self, cause: BaseException, artifacts: dict[str, Artifact]):
        super().__init__(str(cause) or cause.__class__.__name__, key=getattr(cause, "key", None) or None,
                         **(getattr(cause, "values", None) or {}))
        self.artifacts = artifacts


@dataclass(frozen=True)
class SourceJob:
    project: Project  # a deep copy: the worker never touches the live project
    pdir: Path
    period: Period
    source: str
    meta: Optional[dict]  # GLAD-L's evaluated catalogue.meta_request; None for the other sources
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


def _harmonize_space(p: Project) -> int:
    """Free bytes a harmonize needs: twice the harmonized file's compressed
    estimate (its ``.part`` and the old file it replaces)."""
    return 2 * compressed_estimate(estimate_bytes(p.grid))


def _require_masks_ready(p: Project, pdir: Path, period: Period, source: str) -> None:
    """Every mask on in the period (as ``p.periods`` holds it) is aligned
    current on the current grid, its aligned file (and a GeoTIFF mask's AOI
    raster) on disk: harmonize reads them all, so a mask left on an old grid
    or deleted from disk fails here, before any download (spec §5.3). A
    GeoTIFF mask whose user file is gone raises ``MaskFileMissing`` (Run all
    cannot align it); any other mask not ready raises ``MaskNotReady``."""
    on = [name for name, _ in period_masks(p, period)]
    for name in on:
        missing = mask_file_missing(p.masks[name])
        if missing:
            raise MaskFileMissing(name, missing)
    if any(mask_aligned_status(p, pdir, name) is not Status.CURRENT for name in on):
        raise MaskNotReady(f"{source}: a mask on in this period is not aligned on the current grid; "
                           "run the masks first")


def _raw_estimate(p: Project, src) -> int:
    if src.kind == "predefined":
        return estimate_native_bytes(p.aoi.bounds, src.catalogue_key)
    if src.origin == "gee":
        return estimate_native_bytes(p.aoi.bounds, (src.native.crs, src.native.scale))
    return normalize.estimate_bytes(src, p.aoi.bounds)


def ensure_aoi_raster(p: Project, pdir: Path) -> Optional[Artifact]:
    """Build ``masks/aoi.tif`` when it is not current for this AOI and grid;
    returns its new artifact, or None when it was current."""
    inputs = aoi_raster_inputs(p)
    if status(p, pdir, AOI_KEY, inputs) is Status.CURRENT:
        return None
    sidecar = Path(pdir) / paths.AOI_SIDECAR
    if p.aoi.geometry_file is None or not sidecar.exists():
        raise AoiGeometryMissing("the AOI's geometry is not on disk yet; try again in a moment")
    out = paths.aoi_raster_path(pdir)
    ensure_free_space(out.parent, 2 * compressed_estimate(estimate_bytes(p.grid, bands=1)))
    _drop_superseded(p, Path(pdir), AOI_KEY, out)
    build_aoi_raster(sidecar, p.grid, out)
    return make_artifact(AOI_KEY, "aoi_mask", pdir, out, inputs)


def run_source_row(job: SourceJob, *, on_tiles=None, on_wait=None) -> dict[str, Artifact]:
    """Download (when the raw file is missing, out of date or ``force``d) and
    harmonize (when the harmonized file is not current) one source for one
    period. Returns the artifacts written, keyed like ``project.artifacts``.

    The row's coverage is read first: a blocked row raises ``CoverageRefused``,
    a custom source without its native grid ``ValueError``, a custom GeoTIFF
    that is gone ``FileNotFoundError``, and a GLAD-L download without its
    catalogue metadata ``ValueError``, before anything else. Everything the
    harmonize step needs (the masks, the free space, a GeoTIFF's AOI raster)
    is checked or built before a download starts, so a row that cannot finish
    fails before hours of downloading. Each free-space
    check asks for twice the file's compressed size estimate
    (``raster_io.compressed_estimate``): the ``.part`` and the old file it
    replaces. If harmonizing still fails after a download or an AOI-raster
    build, ``HarmonizeFailed`` carries the new artifacts.
    """
    p, pdir, period = job.project, Path(job.pdir), job.period
    if p.aoi is None or p.grid is None:
        raise ProjectRuleError("the project needs an AOI and a grid before a download", key="errors.no_aoi_or_grid")
    src = p.sources[job.source]
    win_start, win_end = period.search_window(p.look_back_days)
    state, notes = window_coverage(record_for(p, job.source), job.source, win_start, win_end)
    if state in BLOCKED:
        raise CoverageRefused(job.source, state, notes)
    if src.kind == "custom" and src.native is None:
        raise ProjectRuleError(f"{job.source}: its native grid is unknown; edit the source",
                               key="errors.source_native_unknown", source=job.source)
    missing = user_file_missing(src)
    if missing:
        raise UserFileMissing(f"file not found: {missing}", path=str(missing))
    file_source = is_file_source(src)
    rkey, hkey = raw_key(period.id, job.source), harm_key(period.id, job.source)
    harm_path = paths.harmonized_path(pdir, period.id, job.source, win_start, win_end)
    out: dict[str, Artifact] = {}

    raw_in = raw_alert_inputs(p, job.source, period)
    downloading = job.force or status(p, pdir, rkey, raw_in) in (Status.MISSING, Status.STALE)
    # Before the AOI raster and the free-space gates, so a NotEnoughSpaceError cannot hide it.
    if downloading and src.kind == "predefined" and src.catalogue_key == "GLAD-L" and job.meta is None:
        raise ProjectRuleError(
            "GLAD-L: the catalogue metadata (its band list) is not loaded yet; try again in a moment",
            key="errors.bands_not_loaded", source="GLAD-L")
    # Every precondition of the harmonize is checked before a download (spec §7.3).
    _require_masks_ready(p, pdir, period, job.source)
    if file_source:
        aoi_art = ensure_aoi_raster(p, pdir)
        if aoi_art is not None:
            out[AOI_KEY] = p.artifacts[AOI_KEY] = aoi_art
    if downloading:
        raw_path = paths.raw_path(pdir, period.id, job.source, win_start, win_end)
        ensure_free_space(raw_path.parent, 2 * compressed_estimate(_raw_estimate(p, src)))
        # A download is always followed by a harmonize: check its room now too.
        ensure_free_space(harm_path.parent, _harmonize_space(p))
        if src.kind == "predefined":
            image = catalogue.build_image(src.catalogue_key, win_start, win_end, job.aoi, job.meta or {})
            export_native(image, src.catalogue_key, job.aoi, raw_path, on_tiles=on_tiles, on_wait=on_wait)
        elif src.origin == "gee":
            image = catalogue.build_custom_image(src, win_start, win_end, job.aoi)
            export_native(image, (src.native.crs, src.native.scale), job.aoi, raw_path,
                          on_tiles=on_tiles, on_wait=on_wait)
        else:
            normalize.normalize_file(src, p.aoi.bounds, win_start, win_end, raw_path)
        _drop_superseded(p, pdir, rkey, raw_path)
        # recorded in the job's copy too, so harmonized_inputs sees the new content_id
        out[rkey] = p.artifacts[rkey] = make_artifact(rkey, "raw_alert", pdir, raw_path, raw_in)

    try:
        harm_in = harmonized_inputs(p, job.source, period)
        # Defensive: the mask check above (aligned masks, a GeoTIFF mask's AOI
        # raster on disk) and the AOI-raster build leave every input recorded.
        if harm_in is None:
            raise RuntimeError(f"{job.source}: its harmonize inputs are not recorded")
        if rkey in out or status(p, pdir, hkey, harm_in) is not Status.CURRENT:
            ensure_free_space(harm_path.parent, _harmonize_space(p))
            masks = [
                (paths.resolve(pdir, p.artifacts[mask_key(n)].rel_path), mode)
                for n, mode, _ in harm_in["masks"]
            ]
            # only a GeoTIFF is clipped here; its AOI raster was (re)built above
            aoi_raster = paths.resolve(pdir, p.artifacts[AOI_KEY].rel_path) if file_source else None
            stats = harmonize(paths.resolve(pdir, p.artifacts[rkey].rel_path), masks, harm_path, p.grid,
                              aoi_raster=aoi_raster)
            _drop_superseded(p, pdir, hkey, harm_path)
            out[hkey] = make_artifact(hkey, "harmonized", pdir, harm_path, harm_in, stats=stats)
    except Exception as exc:
        if rkey in out or AOI_KEY in out:
            raise HarmonizeFailed(exc, dict(out)) from exc
        raise
    return out


@dataclass(frozen=True)
class MaskJob:
    project: Project  # a deep copy: the worker never touches the live project
    pdir: Path
    name: str
    aoi: Any  # ee.FeatureCollection; only an Earth Engine download needs it
    force: bool = False


def run_mask_row(job: MaskJob, *, on_tiles=None, on_wait=None) -> dict[str, Artifact]:
    """Download (an Earth Engine mask, natively, as uint8 0/1, when its raw
    file is missing, out of date or ``force``d) and align a mask onto the
    grid (when the aligned file is not current). Returns the artifacts
    written, keyed like ``project.artifacts``.

    A GeoTIFF mask is read where it is and clipped at alignment by the AOI
    raster, rebuilt first when the AOI or the grid changed. The alignment's
    free space is checked before a download (spec §7.3); each check asks for
    twice the compressed estimate. If the alignment fails after a download or
    an AOI-raster build, ``HarmonizeFailed`` carries the new artifacts. A
    GeoTIFF mask gone from disk raises ``MaskFileMissing`` and an Earth Engine
    mask without its native grid ``MaskNativeUnknown`` (both keyed, so the row
    reads them translated), before anything else.
    """
    p, pdir, name = job.project, Path(job.pdir), job.name
    if p.aoi is None or p.grid is None:
        raise ProjectRuleError("the project needs an AOI and a grid before a mask runs", key="errors.no_aoi_or_grid")
    m = p.masks[name]
    missing = mask_file_missing(m)
    if missing:
        raise MaskFileMissing(name, missing)
    if m.origin == "gee" and m.native is None:
        raise MaskNativeUnknown(name)
    mpath = paths.mask_path(pdir, name)
    align_bytes = 2 * compressed_estimate(estimate_bytes(p.grid, bands=1))
    out: dict[str, Artifact] = {}
    if m.origin == "gee":
        rkey, raw_in = mask_raw_key(name), mask_raw_inputs(p, name)
        if job.force or status(p, pdir, rkey, raw_in) in (Status.MISSING, Status.STALE):
            raw_path = paths.mask_raw_path(pdir, name)
            ensure_free_space(raw_path.parent, 2 * compressed_estimate(mask_raw_estimate(m, p.aoi.bounds)))
            # A download is always followed by an alignment: check its room now too.
            ensure_free_space(mpath.parent, align_bytes)
            export_mask_native(build_mask_image(m, job.aoi), m.native, job.aoi, raw_path,
                               on_tiles=on_tiles, on_wait=on_wait)
            _drop_superseded(p, pdir, rkey, raw_path)
            # recorded in the job's copy too, so mask_inputs sees the new content_id
            out[rkey] = p.artifacts[rkey] = make_artifact(rkey, "raw_mask", pdir, raw_path, raw_in)
        src_path, band, aoi_raster = paths.resolve(pdir, p.artifacts[rkey].rel_path), 1, None
    else:
        aoi_art = ensure_aoi_raster(p, pdir)
        if aoi_art is not None:
            out[AOI_KEY] = p.artifacts[AOI_KEY] = aoi_art
        src_path, band = m.ref, int(m.band)
        aoi_raster = paths.resolve(pdir, p.artifacts[AOI_KEY].rel_path)
    try:
        key, expected = mask_key(name), mask_inputs(p, name)
        if out or job.force or status(p, pdir, key, expected) is not Status.CURRENT:
            ensure_free_space(mpath.parent, align_bytes)
            stats = align_mask(src_path, p.grid, mpath, band=band, aoi_raster=aoi_raster)
            _drop_superseded(p, pdir, key, mpath)
            out[key] = make_artifact(key, "mask", pdir, mpath, expected, stats=stats)
    except Exception as exc:
        if out:
            raise HarmonizeFailed(exc, dict(out)) from exc
        raise
    return out


def apply_artifacts(project: Project, artifacts: dict[str, Artifact]) -> None:
    """Record ``artifacts`` in ``project``. The dict is replaced, not
    mutated: the app publishes shallow copies of the project that share it,
    and a render iterating it on another thread must not see it change."""
    project.artifacts = {**project.artifacts, **artifacts}
