"""All directories used in the application.

Outputs live under ``~/module_results/deforestation_alerts`` (the SEPAL-wide
convention), overridable via ``DEFORESTATION_ALERTS_DATA_DIR`` (absolute paths
only — a relative value would resolve against the CWD, which is read-only on
the platform). Importing this module also points GDAL's scratch-file allocator
at a writable directory (see gdal_env.py).
"""

import os
from pathlib import Path

from deforestation_alerts.gdal_env import configure_gdal_tmpdir

_override = os.environ.get("DEFORESTATION_ALERTS_DATA_DIR")
if _override:
    module_dir = Path(_override).expanduser()
    if not module_dir.is_absolute():
        raise ValueError(
            "DEFORESTATION_ALERTS_DATA_DIR must be an absolute path, got: "
            f"{_override!r}"
        )
else:
    module_dir = Path.home() / "module_results" / "deforestation_alerts"

module_dir.mkdir(parents=True, exist_ok=True)

configure_gdal_tmpdir()
