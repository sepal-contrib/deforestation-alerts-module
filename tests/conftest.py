"""Shared pytest fixtures for the deforestation-alerts test suite.

Two tiers (spec §13). The default run is offline: no Earth Engine call, no
credentials, no network. The live Earth Engine tier is opt-in: every test that
uses the ``gee`` fixture (directly or through ``tiny_aoi`` / ``real_cover``)
is marked ``ee`` here, and an ``ee`` test is skipped unless
``DEFO_ALERTS_EE_TESTS=1`` is set or the run selects it with ``-m ee``.
A test that is not marked ``ee`` and still reaches Earth Engine fails (the
``_no_live_ee`` guard below), so a live call cannot slip into the offline tier.

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

import os
import re
from pathlib import Path

import pytest

#: Set to anything but "" or "0" to run the live Earth Engine tier.
EE_TESTS_ENV = "DEFO_ALERTS_EE_TESTS"


def ee_tier_selected(env: dict, markexpr: str) -> bool:
    """Whether the live EE tier runs: the env var is set, or ``-m`` asks for
    ``ee`` (``-m ee``, ``-m "ee and not slow"``; not ``-m "not ee"``)."""
    if env.get(EE_TESTS_ENV, "").strip() not in ("", "0"):
        return True
    return re.search(r"(?<!not )\bee\b", markexpr or "") is not None


@pytest.hookimpl(tryfirst=True)  # before ``-m`` deselects on the markers
def pytest_collection_modifyitems(config, items):
    run_ee = ee_tier_selected(os.environ, config.getoption("markexpr", ""))
    skip = pytest.mark.skip(reason=f"live Earth Engine test: set {EE_TESTS_ENV}=1 or run with -m ee")
    for item in items:
        if "gee" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.ee)
        if not run_ee and item.get_closest_marker("ee") is not None:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _no_live_ee(request, monkeypatch):
    """Outside the ``ee`` tier, any Earth Engine API call fails the test."""
    if request.node.get_closest_marker("ee") is not None:
        return
    import ee.data

    def _refuse(*args, **kwargs):
        raise RuntimeError(f"live Earth Engine call in {request.node.nodeid}, which is not marked `ee` "
                           "(use the `gee` fixture or @pytest.mark.ee)")

    monkeypatch.setattr(ee.data, "_execute_cloud_call", _refuse)


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


# ---------------------------------------------------------------------------
# Shared widget-tree helpers for the Analysis export tests.
#
# Plain functions rather than fixtures: the callers are module-level helpers
# (``test_analysis_download._export_harness``) as often as tests, and a fixture
# would have to be threaded through them. They live here because two test
# modules need them -- ``test_analysis_download`` drives the export section on
# its own and ``test_analysis_sections`` drives it through the assembled panel
# -- and a test module importing another test module's privates couples the two
# files' refactors together. Imports are function-local so conftest, which is
# imported before every test module, does not pull geopandas in at collection.
# ---------------------------------------------------------------------------
def _in(root):
    """Every widget of ``root``'s tree, root included."""
    yield root
    for child in getattr(root, "children", None) or []:
        if hasattr(child, "children") or hasattr(child, "msg"):
            yield from _in(child)


def _build_button(body):
    """The build row (``file_tile.BuildTile``) of an export section body: the
    file list's last row, *Build files* / *Rebuild files* / *Building files…*.

    Found by its class, not its label, which changes with the list's state.
    """
    for widget in _in(body):
        if "defo-build-tile" in (getattr(widget, "class_", "") or "").split():
            return widget
    raise AssertionError("no build tile in the export section body")


def _shown(root, target):
    """Whether ``target`` is in ``root``'s tree with nothing on its path out
    of the layout: ``display: none`` (a collapsed body, a hidden hint) or
    ``viz=False`` (``sw.SepalWidget.hide()``)."""
    def _path(node):
        if node is target:
            return [node]
        for child in getattr(node, "children", None) or []:
            if not isinstance(child, str):
                found = _path(child)
                if found:
                    return [node, *found]
        return None

    path = _path(root)
    if path is None:
        raise AssertionError(f"{target!r} is not in the tree")
    return not any("display: none" in (getattr(n, "style_", "") or "")
                   or getattr(n, "viz", True) is False for n in path)


def _alerts_gdf():
    """One alert row with every column a Save or a build reads."""
    import geopandas as gpd
    from shapely.geometry import box

    gdf = gpd.GeoDataFrame(
        {
            "admin1": ["Paraguay"], "admin2": ["Canindeyú"], "admin3": ["Curuguaty"],
            "alert_date_min": [2026.001], "alert_date_max": [2026.010],
            "before_img": ["S2_before"], "after_img": ["S2_after"],
            "alert_sources": ["GLAD-L"], "area_ha": [1.25],
            "alert_polygon": [None],
        },
        geometry=[box(0, 0, 1, 1)], crs="EPSG:4326",
    )
    gdf["bounding_box"] = gdf.geometry.envelope
    return gdf
