"""Shared helpers: record raw and harmonized files for a period, as Download & harmonize leaves them."""

import numpy as np

from deforestation_alerts.engine import fingerprint as fp
from deforestation_alerts.engine import raster_io
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import GridSpec

GRID = GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=8900000, width=40, height=40)


def write(path, alert, dates):
    with raster_io.write_grid_raster(path, GRID, count=2) as dst:
        dst.write(alert.astype("int32"), 1)
        dst.write(dates.astype("int32"), 2)
    return path


def blank():
    return np.zeros((GRID.height, GRID.width), dtype="int64")


def prepare(p, pdir, period, arrays, day):
    """Record raw and harmonized artifacts per source, as Download & harmonize leaves them."""
    ws, we = period.search_window()
    for name, arr in arrays.items():
        dates = np.where(arr > 0, day, 0)
        raw = write(paths.raw_path(pdir, period.id, name, ws, we), arr, dates)
        p.artifacts[fp.raw_key(period.id, name)] = fp.make_artifact(
            fp.raw_key(period.id, name), "raw_alert", pdir, raw, fp.raw_alert_inputs(p, name, period))
        harm = write(paths.harmonized_path(pdir, period.id, name, ws, we), arr, dates)
        p.artifacts[fp.harm_key(period.id, name)] = fp.make_artifact(
            fp.harm_key(period.id, name), "harmonized", pdir, harm, fp.harmonized_inputs(p, name, period))
