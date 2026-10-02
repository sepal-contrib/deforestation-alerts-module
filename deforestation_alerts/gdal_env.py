"""Point GDAL's temp-file allocator at a directory we know is writable.

``gdal.ComputeProximity()`` computes in Float32. When the destination band is a
different type (ours is ``GDT_UInt32``) GDAL allocates a full-size Float32
working band in a temporary GeoTIFF named by ``CPLGenerateTempFilename()``,
which resolves its directory from the ``CPL_TMPDIR`` / ``TMPDIR`` / ``TEMP``
config options and falls back to ``"."`` -- the process CWD -- when none is
set. On SEPAL the app runs with its CWD on the read-only shared module mount,
so the call dies with::

    Attempt to create new tiff file `./proximity_997_3' failed:
    ./proximity_997_3: Read-only file system

The same defect lives in third-party code we cannot patch, so the fix has to
be a process-wide GDAL config rather than a change to any one destination
dtype.
"""

import logging
import os
import tempfile
from pathlib import Path

from osgeo import gdal

logger = logging.getLogger("SEPALUI.deforestation_alerts")


def _is_usable(d: Path) -> bool:
    """Create ``d`` and prove we can write a file in it.

    A real write probe rather than ``os.access()``: the whole bug this module
    exists for was a directory that looked fine until GDAL created a file in it.
    """
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / f".probe_{os.getpid()}"
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


def _fallback_candidates() -> list:
    """Scratch directories to try when no explicit override is usable, best first."""
    out = []

    # ``tempfile.gettempdir()`` honours TMPDIR/TEMP/TMP and falls back to /tmp,
    # which on SEPAL is writable and wiped when the instance closes -- GDAL
    # unlinks its own scratch file as soon as it is done, so nothing
    # accumulates there. Its candidate list ends with the CWD, though, so a
    # machine where every system temp dir is unusable would hand us back the
    # read-only module mount: skip it in that case rather than reintroduce the
    # bug (or litter the checkout in dev, where the CWD *is* writable).
    system_tmp = Path(tempfile.gettempdir())
    if system_tmp.resolve() != Path.cwd().resolve():
        # The uid suffix keeps us off a directory of the same name created by
        # another user on a shared /tmp: that one would not be writable by us
        # and would reproduce the very bug this module exists to prevent.
        out.append(system_tmp / f"deforestation_alerts_gdal_{os.getuid()}")

    # Last resort: a ``module_results``-shaped directory under the PROCESS home.
    # NOTE (inventory c3): this is only GDAL's scratch, not user data. User
    # data lives in the project folder (``deforestation_alerts.project.paths``)
    # and is written there directly. This is the last copy of the arithmetic
    # the deleted ``parameter.directory`` module used, and it survived that
    # deletion on purpose; do not re-couple the two.
    # ``DEFORESTATION_ALERTS_DATA_DIR`` came back in Task 3 (see
    # ``deforestation_alerts.project.paths.data_dir()``), but it names the
    # user's *data* folder, not GDAL's scratch directory -- redirecting
    # scratch is what ``DEFORESTATION_ALERTS_GDAL_TMPDIR`` above is for, and a
    # second knob pointing at "the data dir" would read as a local-mode switch.
    out.append(Path.home() / "module_results" / "deforestation_alerts" / ".gdal_tmp")

    return out


def _candidates() -> list:
    """Scratch directories to try, best first."""
    out = []

    env = os.environ.get("DEFORESTATION_ALERTS_GDAL_TMPDIR")
    if env:
        out.append(Path(env))

    out.extend(_fallback_candidates())

    return out


def scratch_dir() -> Path:
    """Return the app's writable scratch directory, creating it if needed.

    An explicit ``DEFORESTATION_ALERTS_GDAL_TMPDIR`` wins when it is genuinely
    writable, then the system temp dir, then the module's output root. Raises
    ``RuntimeError`` if none of them can hold a file, because every caller's
    alternative is writing into the CWD.
    """
    tried = _candidates()
    for candidate in tried:
        if _is_usable(candidate):
            return candidate
    raise RuntimeError(
        "No writable GDAL scratch directory found; set "
        "DEFORESTATION_ALERTS_GDAL_TMPDIR. Tried: "
        + ", ".join(str(c) for c in tried)
    )


def configure_gdal_tmpdir() -> Path:
    """Point GDAL's temp-file allocator at :func:`scratch_dir`. Idempotent.

    Sets both the environment variable -- inherited by spawned children -- and
    the GDAL config option, which covers the library already initialised in
    this process.

    Returns the configured directory. Raises ``RuntimeError`` if no candidate
    directory is writable: fail fast rather than silently letting GDAL fall
    back to the (possibly read-only) working directory.
    """
    d = scratch_dir()
    os.environ["CPL_TMPDIR"] = str(d)
    gdal.SetConfigOption("CPL_TMPDIR", str(d))
    return d


LOCAL_SCRATCH_ENV = "DEFORESTATION_ALERTS_LOCAL_SCRATCH"
NUM_THREADS_ENV = "DEFORESTATION_ALERTS_NUM_THREADS"

#: The container overlay is the only local disk on SEPAL; /var/tmp lives there.
_LOCAL_SCRATCH_ROOTS = (Path("/var/tmp"),)


def local_scratch_dir() -> Path:
    """A scratch directory on local disk for small, seek-heavy writers.

    tippecanoe (PMTiles) and sqlite rewrite their output constantly, and on
    SEPAL both the home and /tmp are NFS: a PMTiles build took 57-70 s there and
    1 s on /var/tmp (spatial_risk, SEPAL c8, 2026-09-21). Build such files here,
    then move the result into place. Rasters go straight to NFS.
    """
    env = os.environ.get(LOCAL_SCRATCH_ENV)
    if env and _is_usable(Path(env)):
        return Path(env)
    for root in _LOCAL_SCRATCH_ROOTS:
        candidate = Path(root) / f"deforestation_alerts_{os.getuid()}"
        if _is_usable(candidate):
            return candidate
    return scratch_dir()


def keep_gdal_sidecars_visible() -> None:
    """Let GDAL see ``.ovr`` sidecars after the map's tile server starts.

    localtileserver's app setup does
    ``os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")``,
    process-wide, which hides every external overview pyramid: each tile then
    decodes the whole raster (spatial_risk measured ~50 s vs ~1 s a tile).
    Set outright, in the environment (rasterio's wheel bundles its own libgdal,
    which only reads the environment).
    """
    os.environ["GDAL_DISABLE_READDIR_ON_OPEN"] = "FALSE"


def _available_cores() -> int:
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:  # pragma: no cover - not Linux
        return os.cpu_count() or 1


def worker_threads() -> int:
    """Threads for a raster job: half the cores, at least 1 (override:
    DEFORESTATION_ALERTS_NUM_THREADS). Half leaves room for the Solara server
    and a concurrent job on the same instance (spatial_risk ``worker_threads``).
    A value that is not a whole number is ignored with a warning: the app must
    start whatever the operator wrote."""
    override = os.environ.get(NUM_THREADS_ENV, "").strip()
    try:
        threads = int(override) if override else _available_cores() // 2
    except ValueError:
        logger.warning("%s=%r is not a whole number; using half the cores", NUM_THREADS_ENV, override)
        threads = _available_cores() // 2
    return max(1, threads)


def pin_gdal_num_threads() -> None:
    """Give GDAL half the cores app-wide (override: DEFORESTATION_ALERTS_NUM_THREADS).

    localtileserver's setdefault("GDAL_NUM_THREADS", "ALL_CPUS") otherwise makes
    every raster write run on one thread before the first map layer and on all
    cores after it.
    """
    os.environ["GDAL_NUM_THREADS"] = str(worker_threads())
