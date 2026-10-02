"""Measure one national-scale Download & harmonize + Integrate run (spec §14, slice 1 gate).

Usage (inside the SEPAL sandbox, app env active):
    python scripts/measure_prepare.py --level 1 --admin 2317 --start 2026-01-01 --end 2026-01-31 --res 10
    python scripts/measure_prepare.py --level 0 --admin 194 --start 2026-01-01 --end 2026-01-31 --res 10

``--level`` picks the GAUL admin level (0 = country, 1 = admin1; FAO/GAUL/2015/level{n},
ADM{n}_CODE). Paraguay is GAUL-2015 level0 code 194; Canindeyú is level1 code 2317.

Files go to ``--out``, by default ``<DATA_DIR>/_measure`` (``DATA_DIR`` is
``$DEFORESTATION_ALERTS_DATA_DIR``, else ``~/module_results/deforestation_alerts``):
the project disk, where the app itself writes its rasters (spec §11). ``/var/tmp``
is SEPAL's small local disk, kept for tippecanoe, so ``--out`` may not point into
it: at national 10 m the free-space gates, which ask for twice the compressed
size estimate (``raster_io.compressed_estimate``), want ~7 GB per Download &
harmonize row and ~13 GB for Integrate. Delete ``<out>`` when the numbers
are copied.

Prints, per source: wall time, raw + harmonized file size, the harmonize-phase
peak RSS (a VmRSS sampler around the actual reproject-and-mask call) and the
process's whole-run peak RSS. Prints the same whole-run peak for integration.
"""

import argparse
import resource
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ee  # noqa: E402

from deforestation_alerts.engine import catalogue, grid as gridmod  # noqa: E402
from deforestation_alerts.engine import runner  # noqa: E402
from deforestation_alerts.engine.integrate import IntegrationJob, run_integration  # noqa: E402
from deforestation_alerts.engine.runner import SourceJob, apply_artifacts, run_source_row  # noqa: E402
from deforestation_alerts.gdal_env import worker_threads  # noqa: E402
from deforestation_alerts.project import paths  # noqa: E402
from deforestation_alerts.project.models import (  # noqa: E402
    AlertSetParams, AoiRecord, Period, Project, add_predefined_source, period_id,
)


#: SEPAL's small local disk (gdal_env.local_scratch_dir): never for a national raster.
LOCAL_DISK = Path("/var/tmp")


def default_out() -> Path:
    return paths.data_dir() / "_measure"


def on_local_disk(out: Path) -> bool:
    out, local = Path(out).expanduser().resolve(), LOCAL_DISK.resolve()
    return out == local or local in out.parents


def peak_gb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6  # KiB -> GB on Linux


def _vmrss_gb() -> float:
    """This process's current resident set size, from /proc (Linux only)."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1e6  # KiB -> GB
    except OSError:
        pass
    return 0.0


def _watch_harmonize():
    """Replace ``runner.harmonize`` with a wrapper that samples VmRSS every
    ~0.2 s while the real harmonize call runs, and return a callable giving
    the peak seen during the most recent call (0.0 before any call).

    ``run_source_row`` calls the module-global name ``harmonize`` bound at
    import time in ``deforestation_alerts.engine.runner``; reassigning the
    attribute on the imported module changes what that lookup resolves to.
    """
    original = runner.harmonize
    peak = {"gb": 0.0}

    def wrapper(*args, **kwargs):
        peak["gb"] = 0.0
        stop = threading.Event()

        def poll():
            while not stop.is_set():
                peak["gb"] = max(peak["gb"], _vmrss_gb())
                stop.wait(0.2)

        t = threading.Thread(target=poll, daemon=True)
        t.start()
        try:
            return original(*args, **kwargs)
        finally:
            stop.set()
            t.join()

    runner.harmonize = wrapper
    return lambda: peak["gb"]


def integrate_into(out: Path, p: Project, per: Period, sources: list):
    """Run Integrate in ``out``: the set's files are written straight into it."""
    return run_integration(IntegrationJob(project=p, pdir=out, period=per, sources=sources,
                                          params=AlertSetParams()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, choices=(0, 1), default=1,
                     help="GAUL admin level: 0 (country, ADM0_CODE) or 1 (admin1, ADM1_CODE)")
    ap.add_argument("--admin", required=True, help="GAUL admin code at --level, e.g. 2317 (level 1) or 194 (level 0)")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--res", type=float, default=10)
    ap.add_argument("--sources", default="GLAD-L,RADD")
    ap.add_argument("--out", type=Path, default=None,
                    help="output folder on the project disk (default: <DATA_DIR>/_measure); not under /var/tmp")
    a = ap.parse_args()
    out = (a.out or default_out()).expanduser().resolve()
    if on_local_disk(out):
        ap.error(f"--out {out} is on the small local disk ({LOCAL_DISK}); use the project disk (spec §11)")

    from pysepal.scripts.utils import init_ee

    init_ee()
    fc = ee.FeatureCollection(f"FAO/GAUL/2015/level{a.level}").filter(
        ee.Filter.eq(f"ADM{a.level}_CODE", int(a.admin))
    )
    coords = fc.geometry().bounds().coordinates().getInfo()[0]
    xs, ys = [c[0] for c in coords], [c[1] for c in coords]
    bounds = (min(xs), min(ys), max(xs), max(ys))
    per = Period(id=period_id(date.fromisoformat(a.start), date.fromisoformat(a.end)),
                 start=date.fromisoformat(a.start), end=date.fromisoformat(a.end))
    p = Project(name="measure", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name=a.admin, method=f"ADMIN{a.level}", digest=f"admin:{a.admin}", bounds=bounds)
    p.grid = gridmod.build_grid(bounds, gridmod.auto_utm_epsg(bounds), a.res)
    p.periods[per.id] = per
    win_start, win_end = per.search_window(p.look_back_days)
    print(f"output {out}")
    print(f"grid {p.grid.width} x {p.grid.height} @ {a.res} m in {p.grid.epsg}, workers {worker_threads()}")
    print(f"search window {win_start.isoformat()} .. {win_end.isoformat()} (look_back_days={p.look_back_days})")
    sources = a.sources.split(",")
    for s in sources:
        add_predefined_source(p, s)
    harmonize_peak = _watch_harmonize()
    for s in sources:
        t0 = time.time()
        meta = catalogue.meta_request(s).getInfo()
        arts = run_source_row(SourceJob(project=p.model_copy(deep=True), pdir=out, period=per, source=s, meta=meta, aoi=fc))
        apply_artifacts(p, arts)
        sizes = {k: v.size_bytes / 1e6 for k, v in arts.items()}
        print(f"{s}: {time.time() - t0:.0f} s, files MB {sizes}, "
              f"harmonize peak {harmonize_peak():.2f} GB, peak RSS {peak_gb():.2f} GB")
    t0 = time.time()
    res = integrate_into(out, p, per, sources)
    print(f"integrate: {time.time() - t0:.0f} s, {res.alert_set.n_alerts} alerts, peak RSS {peak_gb():.2f} GB")


if __name__ == "__main__":
    main()
