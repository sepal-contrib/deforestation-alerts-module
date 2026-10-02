"""Shared pytest fixtures for the deforestation-alerts test suite.

Phase 0 tests are pure: no Earth Engine calls, no UI rendering. We deliberately
do NOT call init_ee() here so the suite runs offline in CI without credentials.

The app reads and writes plain files on the local sandbox disk (spec D14).
The ``data_root`` fixture below points the data directory into ``tmp_path``
(with ``HOME`` pinned there too, so ``local_files.home_relative`` -- which
resolves against ``Path.home()`` -- stays inside it).
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
def data_root(monkeypatch, tmp_path):
    """The data directory of a test: ``<tmp>/module_results/deforestation_alerts``.

    ``HOME`` is pinned to ``tmp_path`` (download links are relative to it) and
    ``DEFORESTATION_ALERTS_DATA_DIR`` points at the directory, so
    ``paths.project_dir(name)`` lands inside it.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "module_results" / "deforestation_alerts"
    root.mkdir(parents=True)
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(root))
    return root


@pytest.fixture()
def data_files():
    """Callable: every file under a data directory. ``assert data_files(root) == []``."""

    def _data_files(root):
        root = Path(root)
        return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []

    return _data_files


@pytest.fixture()
def server_log():
    """The app logger's server-log handler on a private stream, removed after.

    Yields the stream: what a failure leaves in the server log (count
    ``"Traceback (most recent call last)"`` for one traceback per failure).
    """
    import io
    import logging

    from gui.scripts import notify_bridge

    logger = logging.getLogger("SEPALUI.deforestation_alerts")
    for h in list(logger.handlers):
        if isinstance(h, notify_bridge.ServerLogHandler):
            logger.removeHandler(h)
    stream = io.StringIO()
    handler = notify_bridge.install_server_log_handler(stream=stream)
    yield stream
    logger.removeHandler(handler)


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


@pytest.fixture()
def cover():
    """``cover(p, *sources, fraction=1.0)``: coverage records for ``p``'s AOI that
    cover every window (predefined: one region open from 2000 to 2100;
    custom: one undated region), as the coverage check would save them."""
    from datetime import date

    from deforestation_alerts.project.models import PREDEFINED_KEYS, RegionCoverage, SourceCoverage

    def _cover(p, *sources, fraction=1.0):
        recs = {}
        for s in sources:
            dated = s in PREDEFINED_KEYS
            recs[s] = SourceCoverage(
                aoi_digest=p.aoi.digest, checked_on=date.today(), fraction=fraction,
                regions=[RegionCoverage(name=s, fraction=fraction, first=date(2000, 1, 1) if dated else None,
                                        last=date(2100, 1, 1) if dated else None)])
        p.coverage = {**p.coverage, **recs}
        return p

    return _cover


@pytest.fixture()
def real_cover(gee):
    """``real_cover(p, aoi, *keys)``: ``p``'s coverage records for the AOI from
    Earth Engine (one request, as ``RemoteFactsLoader`` saves them). For the
    Tier-2 tests that build projects by hand: the runner refuses a row whose
    source has no coverage record (spec §7.2 "Coverage guard")."""
    from datetime import date

    def _cover(p, aoi, *keys):
        import ee

        from deforestation_alerts.engine import coverage

        fc = aoi if isinstance(aoi, ee.FeatureCollection) else ee.FeatureCollection([ee.Feature(aoi)])
        raw = ee.data.computeValue(coverage.coverage_request(fc, tuple(keys)))
        p.coverage = {**p.coverage, **coverage.parse_coverage(raw, aoi_digest=p.aoi.digest, today=date.today())}
        return p

    return _cover
