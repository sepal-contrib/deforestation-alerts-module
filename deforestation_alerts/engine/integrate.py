"""Combine harmonized sources, subtract earlier footprints, vectorize, and store
one alert set (spec §7.4). Entirely local; runs on a worker thread.

Every raster is read and written window by window: whole rows of 512-px tiles,
about ``WINDOW_PIXELS`` pixels at a time, so a national grid never sits in
memory (spec §11) and every tile is decompressed and written once.
"""

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
    Status, combined_key, earlier_sets, fingerprint, footprint_key, harm_key, harmonized_inputs,
    make_artifact, mask_key, set_inputs, status, store_key,
)
from deforestation_alerts.engine.grid import estimate_bytes
from deforestation_alerts.engine.raster_io import BLOCK, ensure_free_space, row_windows, write_grid_raster
from deforestation_alerts.engine.vectorize import vectorize_alerts
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import (
    AlertSet, AlertSetParams, Artifact, GridSpec, Period, Project, set_id as make_set_id,
)
from deforestation_alerts.scripts.alert_filter_helper import convert_to_geopandas
from deforestation_alerts.scripts.attribute_source import attribute_alerts_from_config
from deforestation_alerts.scripts.recipe_helper import save_gdf_to_parquet
from deforestation_alerts.scripts.workspace import Workspace

#: Pixels per window (the vectorizer's default block): about 80 MB per Int32
#: array, whatever the grid's width.
WINDOW_PIXELS = 20_000_000


def _windows(grid: GridSpec) -> list[Window]:
    """Full-width windows of whole 512-px tile rows, about ``WINDOW_PIXELS`` each."""
    rows = max(BLOCK, WINDOW_PIXELS // max(grid.width, 1) // BLOCK * BLOCK)
    return list(row_windows(grid, rows))


def _window_transform(grid: GridSpec, w: Window) -> Affine:
    return grid.affine @ Affine.translation(w.col_off, w.row_off)


def min_cluster_pixels(min_size_ha: float, resolution: float) -> int:
    """The minimum size in hectares as a pixel count, ``ceil(ha · 10⁴ / res²)``, at least 1."""
    return max(1, math.ceil(float(min_size_ha) * 10_000 / float(resolution) ** 2))


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

    ``inputs`` are ``(harmonized raster, digit)`` pairs on ``grid``. Band 1 is
    ``Σ alert · 10^digit``, band 2 the earliest date among the sources with an
    alert at the pixel, band 3 the display class: 1 if any source is confirmed
    there, 2 if all are probable. Pixels set in any of the ``footprints`` (0/1
    rasters of earlier sets) or outside ``area_geojson`` (WGS84) are 0 in every band.
    """
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
    workspace: Workspace


@dataclass(frozen=True)
class IntegrationResult:
    alert_set: AlertSet
    artifacts: dict[str, Artifact]  # keyed like project.artifacts
    alerts: gpd.GeoDataFrame  # EPSG:4326, as stored


def _staged(path: Path) -> Path:
    """The name a set file is built under until the whole run has succeeded."""
    return path.with_name(f"{path.stem}.staged{path.suffix}")


def run_integration(job: IntegrationJob, *, on_step: Optional[Callable[[str], None]] = None) -> IntegrationResult:
    """Build one alert set from the job's harmonized, current sources (spec §7.4).

    Writes the combined raster, the footprint and ``alert_db.parquet`` into the
    set's folder and returns the set and its artifacts; the caller records them.
    ``on_step`` gets a message key as each stage starts. Raises ``ValueError``
    when a source is not harmonized and current for the period, or an earlier
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
    for s in sources:
        st = status(p, pdir, harm_key(period.id, s), harmonized_inputs(p, s, period))
        if st is not Status.CURRENT:
            raise ValueError(f"{s} is not downloaded and harmonized for this period ({st.value})")
    inputs_fp = set_inputs(p, period, sources, job.params)
    sid = make_set_id(period.id, sources)
    sdir = paths.set_dir(pdir, period.id, sources)
    # A replaced set's old files stay until the swap, so the staged combined
    # raster, footprint and store all sit beside them; together they are well
    # under this bound.
    ensure_free_space(sdir, 3 * estimate_bytes(p.grid, bands=3))

    step("steps.integrate.step_combine")
    earlier = earlier_sets(p, period)
    fp_paths = []
    for s in earlier:
        art = p.artifacts.get(footprint_key(s.id))
        if art is None or not paths.resolve(pdir, art.rel_path).exists():
            raise ValueError(f"the footprint of set {s.id} is missing; re-run that set first")
        fp_paths.append(paths.resolve(pdir, art.rel_path))
    combined = paths.combined_path(sdir, sources, period.start, period.end)
    footprint = paths.footprint_path(sdir)
    store = paths.store_path(sdir)
    staged = {final: _staged(final) for final in (combined, footprint, store)}
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
            region = None
            if job.params.attribute_config.get("kind") == "gee":
                import ee  # only a GEE attribute raster needs an export region

                minx, miny, maxx, maxy = p.aoi.bounds
                region = ee.Geometry.Rectangle([minx, miny, maxx, maxy])
            alerts = attribute_alerts_from_config(alerts, job.params.attribute_config, region, job.workspace)
        save_gdf_to_parquet(alerts, job.workspace, str(staged[store]))

        step("steps.integrate.step_footprint")
        rasterize_footprint(vgdf, p.grid, staged[footprint])

        alert_set = AlertSet(
            id=sid,
            period_id=period.id,
            sources=sources,
            digit_map=digit_map,
            params=job.params,
            masks_used=[
                {"name": n, "mode": m.mode, "content_id": p.artifacts[mask_key(n)].content_id}
                for n, m in sorted(p.masks.items()) if m.enabled
            ],
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
    return IntegrationResult(alert_set=alert_set, artifacts=artifacts, alerts=alerts)
