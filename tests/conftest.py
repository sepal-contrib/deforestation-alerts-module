"""Shared pytest fixtures for the deforestation-alerts test suite.

Phase 0 tests are pure: no Earth Engine calls, no UI rendering. We deliberately
do NOT call init_ee() here so the suite runs offline in CI without credentials.

``Workspace`` (``deforestation_alerts/scripts/workspace.py``) now reads and
writes plain files on the local sandbox disk (spec D14) -- there is no more
remote client to fake. The ``ws`` fixture below roots one in ``tmp_path`` (with
``HOME`` pinned there too, so ``home_relative`` -- which resolves against the
real ``Path.home()`` -- stays inside it).
"""

# Reacton's first ``t()`` call *during* a first render corrupts its internal
# widget map and surfaces later as a ``use_event`` KeyError (only reproduces in
# isolated single-test runs). Warm the translator once at collection time so
# every render test (``test_pipeline_header`` mounts ``rv.use_event`` rows) is
# covered. Must be import-time, not an autouse fixture: conftest is imported
# before any test module and its component imports.
from gui.i18n import t as _warm_t  # noqa: E402

_warm_t("common.cancel")

from pathlib import Path

import pytest


@pytest.fixture()
def ws(monkeypatch, tmp_path):
    """A local-disk ``Workspace`` rooted in ``tmp_path`` (user files under
    ``module_results/deforestation_alerts/``).

    ``HOME`` is pinned to ``tmp_path`` too: production ``home_relative``
    resolves against the real ``Path.home()``, so without this every call to
    it (recipe load, the overview/export "download" links) would raise for a
    root that sits outside the real user's home.
    """
    from deforestation_alerts.scripts.workspace import Workspace

    monkeypatch.setenv("HOME", str(tmp_path))
    return Workspace(
        root=tmp_path / "module_results" / "deforestation_alerts",
        scratch_parent=tmp_path / "scratch",
    )


@pytest.fixture()
def data_files():
    """Callable: every user file a ``Workspace`` holds (scratch excluded).

    Usage: ``assert data_files(ws) == []``. A fixture rather than a module
    import -- an installed ``tests`` package in site-packages shadows this
    directory, so ``from tests.conftest import ...`` resolves to the wrong
    module; fixtures are the only sharing channel.
    """

    def _data_files(workspace):
        root = Path(workspace.root)
        return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []

    return _data_files


@pytest.fixture()
def sample_wkt_csv(tmp_path):
    """Write a tiny CSV with a WKT geometry column and return its path.

    Mirrors the shape of the recipe `alert_db.csv` that `load_gdf_from_csv`
    reads: a geometry column stored as WKT strings plus a plain attribute column.
    """
    csv_path = tmp_path / "alert_db.csv"
    csv_path.write_text(
        "id,point\n"
        "0,POINT (1 2)\n"
        "1,\n"  # empty geometry -> should decode to None
    )
    return csv_path


@pytest.fixture(scope="session")
def gee():
    """Initialize Earth Engine once; skip the module if unauthenticated."""
    try:
        from pysepal.scripts.utils import init_ee
        init_ee()
        import ee
        ee.Number(1).getInfo()  # force a round-trip to confirm creds
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Earth Engine unavailable: {exc}")
    return True


@pytest.fixture
def tiny_aoi(gee):
    import ee
    return ee.Geometry.Rectangle([-55.10, -10.10, -55.00, -10.00])  # ~Amazon
