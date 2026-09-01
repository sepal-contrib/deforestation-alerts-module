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
    # NOTE (inventory c3): this is the app's SCRATCH root, not the user's
    # workspace. In the container it resolves to a container path, which is
    # exactly right here and exactly wrong for user data -- that goes through
    # ``scripts/workspace.Workspace`` and the SepalClient. This is the last copy
    # of the arithmetic the deleted ``parameter.directory`` module used, and it
    # survived that deletion on purpose; do not re-couple the two. The old
    # ``DEFORESTATION_ALERTS_DATA_DIR`` override went with it -- redirecting
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
