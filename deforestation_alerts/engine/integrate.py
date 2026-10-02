"""Combine harmonized sources, subtract earlier footprints, vectorize, and store
one alert set (spec §7.4). Entirely local; runs on a worker thread.

Every raster is read and written window by window: whole rows of 512-px tiles,
about ``WINDOW_PIXELS`` pixels at a time but never less than one tile row, so a
national grid never sits in memory (spec §11) and every tile is decompressed
and written once. Above ~39,000 px wide (``WINDOW_PIXELS`` / 512) the one-row
floor wins and a window grows with the width: 512 x 88,236 px, 181 MB per Int32
array, for Paraguay at 10 m. ``combine`` holds ten window-sized Int32 arrays
at its peak (traced 2026-10-01 with two sources: 0.78 GB at 38,000 px wide,
1.8 GB at 88,236 px). The vectorizer reads its own blocks (vectorize.py).
Harmonize's band-by-band warp already takes 1.19 GB at 32,000 px with two
workers and also grows with the width (align.py), so at national scale it, not
this module, is expected to set a run's peak.
"""

import glob
import math
import os
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Sequence

import geopandas as gpd
import numpy as np
import rasterio
from affine import Affine
from pyproj import Transformer
from rasterio import features
from rasterio.windows import Window
from shapely.geometry import box, shape
from shapely.ops import transform as shp_transform

from deforestation_alerts.engine.fingerprint import (
    combined_key, earlier_sets, fingerprint, footprint_key, harm_key, make_artifact, set_inputs, store_key,
)
from deforestation_alerts.engine.grid import estimate_bytes
from deforestation_alerts.engine.plan import plan_prepare
from deforestation_alerts.engine.raster_io import (
    BLOCK, check_header, compressed_estimate, ensure_free_space, row_windows, write_grid_raster,
)
from deforestation_alerts.engine.vectorize import vectorize_alerts
from deforestation_alerts.errors import ProjectRuleError
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import (
    AlertSet, AlertSetParams, Artifact, GridSpec, Period, Project, set_id as make_set_id,
)
from deforestation_alerts.scripts.alert_store import save_gdf_to_parquet
from deforestation_alerts.scripts.alert_table import convert_to_geopandas
from deforestation_alerts.scripts.attribute_source import attribute_alerts_from_config, download_estimate

#: Pixels per window (the vectorizer's default block): about 80 MB per Int32
#: array, until the one-tile-row floor of ``_windows`` takes over above
#: ~39,000 px wide (see the module docstring).
WINDOW_PIXELS = 20_000_000


def _windows(grid: GridSpec) -> list[Window]:
    """Full-width windows of whole 512-px tile rows, about ``WINDOW_PIXELS`` each."""
    rows = max(BLOCK, WINDOW_PIXELS // max(grid.width, 1) // BLOCK * BLOCK)
    return list(row_windows(grid, rows))


def _window_transform(grid: GridSpec, w: Window) -> Affine:
    return grid.affine @ Affine.translation(w.col_off, w.row_off)


def min_cluster_pixels(min_size_ha: float, resolution: float) -> int:
    """The minimum size in hectares as a pixel count, ``ceil(ha · 10⁴ / res²)``, at least 1.

    The quotient is rounded to 9 decimals before ``ceil``: float noise such as
    0.07 ha at 10 m giving 7.000000000000001 must not ask for an extra pixel,
    or alerts exactly at the user's minimum would be dropped.
    """
    return max(1, math.ceil(round(float(min_size_ha) * 10_000 / float(resolution) ** 2, 9)))


def _area_in_grid(area_geojson: dict, grid: GridSpec) -> list:
    """A drawn GeoJSON geometry, Feature or FeatureCollection (WGS84) -> shapely geometries in the grid's CRS."""
    geo = area_geojson.get("geometry", area_geojson)
    if geo.get("type") == "FeatureCollection":
        geoms = [shape(f["geometry"]) for f in geo["features"]]
    else:
        geoms = [shape(geo)]
    t = Transformer.from_crs("EPSG:4326", grid.epsg, always_xy=True)
    return [shp_transform(t.transform, g) for g in geoms]


def combine(inputs: Sequence[tuple[Path, int]], grid: GridSpec, out_path, *,
            footprints: Sequence[Path] = (), area_geojson: Optional[dict] = None) -> int:
    """Write the combined 3-band raster (spec §7.4 steps 1-4); return its alert pixel count.

    ``inputs`` are ``(harmonized raster, digit)`` pairs on ``grid`` (checked:
    an input or footprint off the grid raises ``GridMismatchError``). Band 1 is
    ``Σ alert · 10^digit``, band 2 the earliest date among the sources with an
    alert at the pixel, band 3 the display class: 1 if any source is confirmed
    there, 2 if all are probable. Pixels set in any of the ``footprints`` (0/1
    rasters of earlier sets) or outside ``area_geojson`` (WGS84) are 0 in every band.
    """
    # The window reads below assume every input shares the grid's pixels.
    for path in [p for p, _ in inputs] + list(footprints):
        check_header(path, grid)
    area = _area_in_grid(area_geojson, grid) if area_geojson else None
    total = 0
    with ExitStack() as stack:
        srcs = [(stack.enter_context(rasterio.open(p)), 10 ** int(d)) for p, d in inputs]
        fps = [stack.enter_context(rasterio.open(p)) for p in footprints]
        with write_grid_raster(Path(out_path), grid, count=3) as dst:
            for w in _windows(grid):
                shp = (int(w.height), int(w.width))
                # Int32 is exact: 9 digits of at most 2 sum to 222222222 < 2^31 (spec D16).
                code = np.zeros(shp, np.int32)
                date = np.zeros(shp, np.int32)
                confirmed = np.zeros(shp, bool)
                for src, mult in srcs:
                    a = src.read(1, window=w).astype(np.int32, copy=False)
                    d = src.read(2, window=w).astype(np.int32, copy=False)
                    hit = a > 0
                    code += np.where(hit, a * mult, 0).astype(np.int32, copy=False)
                    earlier = hit & (d > 0) & ((date == 0) | (d < date))
                    date[earlier] = d[earlier]
                    confirmed |= a == 1
                keep = code > 0
                for f in fps:
                    keep &= f.read(1, window=w) == 0
                if area is not None:
                    keep &= features.geometry_mask(
                        area, out_shape=shp, transform=_window_transform(grid, w), invert=True
                    )
                code[~keep] = 0
                date[~keep] = 0
                display = np.where(keep, np.where(confirmed, 1, 2), 0).astype(np.int32)
                dst.write(code, 1, window=w)
                dst.write(date, 2, window=w)
                dst.write(display, 3, window=w)
                total += int(keep.sum())
    return total


def rasterize_footprint(gdf_grid: gpd.GeoDataFrame, grid: GridSpec, out_path) -> Path:
    """Burn the polygons (in the grid's CRS) into an Int32 0/1 raster on ``grid``."""
    geoms = gdf_grid.geometry
    sindex = geoms.sindex if len(geoms) else None
    with write_grid_raster(Path(out_path), grid, count=1) as dst:
        for w in _windows(grid):
            shp = (int(w.height), int(w.width))
            t = _window_transform(grid, w)
            arr = np.zeros(shp, np.int32)
            if sindex is not None:
                idx = sindex.query(box(t.c, t.f + t.e * shp[0], t.c + t.a * shp[1], t.f))
                if len(idx):
                    arr = features.rasterize(
                        ((g, 1) for g in geoms.iloc[idx]), out_shape=shp, transform=t, fill=0, dtype="int32"
                    )
            dst.write(arr, 1, window=w)
    return Path(out_path)


_SORTS = {
    1: ("area_ha", False),         # bigger first
    2: ("area_ha", True),          # smaller first
    3: ("alert_date_max", False),  # recent first
    4: ("alert_date_min", True),   # older first
}


def sort_and_cut(gdf, sorting: int, max_alerts: int):
    """Sort by the Filter step's numeric code (1..4) and keep the first ``max_alerts`` (0 = all)."""
    col, ascending = _SORTS.get(int(sorting), _SORTS[1])
    out = gdf.sort_values(col, ascending=ascending, kind="stable", na_position="last")
    if int(max_alerts) > 0:
        out = out.head(int(max_alerts))
    return out.reset_index(drop=True)


@dataclass(frozen=True)
class IntegrationJob:
    project: Project  # a deep copy: the worker never touches the live project
    pdir: Path
    period: Period
    sources: list[str]
    params: AlertSetParams


@dataclass(frozen=True)
class IntegrationResult:
    alert_set: AlertSet
    artifacts: dict[str, Artifact]  # keyed like project.artifacts
    alerts: gpd.GeoDataFrame  # EPSG:4326, as stored
    # per chosen source {"alert_pixels", "alert_pixels_unmasked"} from its
    # harmonized file's stats; {} for a file harmonized before stats existed
    outcomes: dict[str, dict]


def _staged(path: Path) -> Path:
    """The name a set file is built under until the whole run has succeeded."""
    return path.with_name(f"{path.stem}.staged{path.suffix}")


def _sweep_staged(staged: dict[Path, Path]) -> None:
    """Delete a killed run's staged files and their half-written ``.part``
    siblings (``<staged>.part``, ``<staged>.<uuid>.part``)."""
    for part in staged.values():
        part.unlink(missing_ok=True)
        if part.parent.is_dir():
            for half in part.parent.glob(glob.escape(part.name) + "*.part"):
                half.unlink(missing_ok=True)


def _attribute_region(p: Project):
    """The export region of an Earth Engine attribute raster: the AOI's bounds."""
    import ee  # only a GEE attribute raster needs it

    minx, miny, maxx, maxy = p.aoi.bounds
    return ee.Geometry.Rectangle([minx, miny, maxx, maxy])


def _masks_applied(p: Project, period: Period, sources) -> list:
    """The masks the chosen sources' harmonized files were built with (spec §5.1)."""
    seen = {}
    for s in sources:
        for name, mode, content_id in p.artifacts[harm_key(period.id, s)].inputs.get("masks") or []:
            seen[name] = {"name": name, "mode": mode, "content_id": content_id}
    return [seen[n] for n in sorted(seen)]


def _outcomes(p: Project, period: Period, sources) -> dict[str, dict]:
    """Each source's alert pixels in the AOI, with and without the masks, as
    counted while its harmonized file was written ({} when not counted)."""
    outcomes = {}
    for s in sources:
        st = p.artifacts[harm_key(period.id, s)].stats or {}
        outcomes[s] = ({"alert_pixels": int(st["alert_pixels"]),
                        "alert_pixels_unmasked": int(st["alert_pixels_unmasked"])}
                       if "alert_pixels" in st else {})
    return outcomes


def run_integration(job: IntegrationJob, *, on_step: Optional[Callable[[str], None]] = None) -> IntegrationResult:
    """Build one alert set from the job's harmonized, current sources (spec §7.4).

    Writes the combined raster, the footprint and ``alert_db.parquet`` into the
    set's folder and returns the set and its artifacts; the caller records them.
    ``on_step`` gets a message key as each stage starts. Raises ``ValueError``
    when no source is chosen or one is chosen twice, when the drawn area mode
    has no drawing, when a source's download or harmonized file is not current
    for the period (its Download & harmonize row is not ready), or an earlier
    set's footprint is missing.

    The three files are built under staged names and moved onto the set's own
    names only once every step has succeeded. A run that fails, including one
    that replaces a recorded set, deletes what it staged and leaves the set's
    files as they were, still matching the manifest's content ids (spec §5.3,
    §12).
    """
    step = on_step or (lambda key: None)
    p, pdir, period = job.project, Path(job.pdir), job.period
    sources = sorted(job.sources, key=str.casefold)
    if not job.sources or len(set(job.sources)) != len(job.sources):
        raise ProjectRuleError("choose at least one source, each once", key="errors.no_sources")
    if job.params.area_mode == "drawn" and not job.params.area_geojson:
        raise ProjectRuleError("the drawn area mode needs a drawn polygon", key="errors.no_drawing")
    # Both stages must be current: a harmonized file whose raw download is out
    # of date (say, for an AOI changed since) holds the old inputs' alerts.
    rows = {row.source: row for row in plan_prepare(p, pdir, period)}
    for s in sources:
        row = rows.get(s)
        if row is None:
            raise ProjectRuleError(f"{s} is not a source of this project", key="errors.not_a_source", source=s)
        if not row.ready:
            raise ProjectRuleError(f"{s} is not downloaded and harmonized for this period "
                                   f"(download {row.raw.value}, harmonized {row.harmonized.value})",
                                   key="errors.not_ready", source=s)
    inputs_fp = set_inputs(p, period, sources, job.params)
    sid = make_set_id(period.id, sources)
    sdir = paths.set_dir(pdir, period.id, sources)
    combined = paths.combined_path(sdir, sources, period.start, period.end)
    footprint = paths.footprint_path(sdir)
    store = paths.store_path(sdir)
    staged = {final: _staged(final) for final in (combined, footprint, store)}
    # A run killed outright (SIGKILL) leaves its staged files behind; swept
    # before the gate so they do not count against it. The set's folder has
    # one writer (the ``integrate:{project}`` in-flight key).
    _sweep_staged(staged)
    # A run stages the combined raster (3 bands) and the footprint (1 band);
    # the parquet store is small next to them. A replaced set's old files stay
    # beside the staged ones until the swap, hence twice their compressed
    # estimate. An Earth Engine attribute raster is downloaded first, twice its
    # compressed estimate too (spec §7.4 step 0).
    staged_bytes = 2 * compressed_estimate(estimate_bytes(p.grid, bands=3 + 1))
    attr_bytes = 2 * compressed_estimate(download_estimate(job.params.attribute_config, p.aoi.bounds))
    ensure_free_space(sdir, staged_bytes + attr_bytes)

    step("steps.integrate.step_combine")
    earlier = earlier_sets(p, period)
    fp_paths = []
    for s in earlier:
        art = p.artifacts.get(footprint_key(s.id))
        if art is None or not paths.resolve(pdir, art.rel_path).exists():
            raise ProjectRuleError(f"the footprint of set {s.id} is missing; re-run that set first",
                                   key="errors.footprint_missing", set_id=s.id)
        fp_paths.append(paths.resolve(pdir, art.rel_path))
    harm_paths = [
        (paths.resolve(pdir, p.artifacts[harm_key(period.id, s)].rel_path), p.sources[s].digit)
        for s in sources
    ]
    digit_map = {p.sources[s].digit: s for s in sources}
    try:
        combine(harm_paths, p.grid, staged[combined], footprints=fp_paths,
                area_geojson=job.params.area_geojson if job.params.area_mode == "drawn" else None)

        step("steps.integrate.step_vectorize")
        vgdf = vectorize_alerts(
            staged[combined], min_cluster_size=min_cluster_pixels(job.params.min_size_ha, p.grid.resolution)
        )
        vgdf = sort_and_cut(vgdf, job.params.sorting, job.params.max_alerts)

        # The alerts, attribute statistics included, are built before the
        # footprint: the attribute raster (an Earth Engine export for "gee") is
        # the step most likely to fail.
        step("steps.integrate.step_store")
        alerts = convert_to_geopandas(vgdf.to_crs(epsg=4326), digit_map=digit_map)
        alerts["alert_id"] = range(len(alerts))
        alerts["set_id"] = sid
        if job.params.attribute_config:
            region = _attribute_region(p) if job.params.attribute_config.get("kind") == "gee" else None
            alerts = attribute_alerts_from_config(alerts, job.params.attribute_config, region)
        save_gdf_to_parquet(alerts, str(staged[store]))

        step("steps.integrate.step_footprint")
        rasterize_footprint(vgdf, p.grid, staged[footprint])

        alert_set = AlertSet(
            id=sid,
            period_id=period.id,
            sources=sources,
            digit_map=digit_map,
            params=job.params,
            masks_used=_masks_applied(p, period, sources),
            footprint_from=[s.id for s in earlier],
            store_rel_path=paths.rel(pdir, store),
            combined_rel_path=paths.rel(pdir, combined),
            footprint_rel_path=paths.rel(pdir, footprint),
            n_alerts=len(alerts),
            n_reviewed=0,
            last_alert_index=0,
            fingerprint=fingerprint(inputs_fp),
            created_at=datetime.now(),
        )
        # A replaced set's tiles show its previous alerts: they are superseded
        # (spec §5.2), and the caller builds the new ones from the new store.
        # Dropped before the swap, so a failure here still leaves the recorded
        # set's own files as they were (its tiles are rebuilt on demand).
        paths.pmtiles_path(sdir).unlink(missing_ok=True)
        for final, part in staged.items():
            os.replace(part, final)
    except BaseException:
        for part in staged.values():
            part.unlink(missing_ok=True)
        raise

    # New content ids and sizes, taken from the files now in place (spec §5.3).
    artifacts = {
        combined_key(sid): make_artifact(combined_key(sid), "combined", pdir, combined, inputs_fp),
        footprint_key(sid): make_artifact(footprint_key(sid), "footprint", pdir, footprint, inputs_fp),
        store_key(sid): make_artifact(store_key(sid), "store", pdir, store, inputs_fp),
    }
    return IntegrationResult(alert_set=alert_set, artifacts=artifacts, alerts=alerts,
                             outcomes=_outcomes(p, period, sources))
