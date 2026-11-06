"""The core modules load without the report's and the CNN's libraries: a
fresh interpreter imports each one and lists what came with it."""

import subprocess
import sys

import pytest

HEAVY = ("tensorflow", "keras", "cartopy", "docx", "matplotlib")


@pytest.mark.parametrize("module", [
    "deforestation_alerts.scripts.set_summary",
    "deforestation_alerts.scripts.alert_table",
    "deforestation_alerts.engine.integrate",
])
def test_a_core_module_loads_no_heavy_library(module):
    code = f"import sys, {module}; print(','.join(m for m in {HEAVY!r} if m in sys.modules))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""
