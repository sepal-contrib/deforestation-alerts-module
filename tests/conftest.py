"""Shared pytest fixtures for the deforestation-alerts test suite.

Phase 0 tests are pure: no Earth Engine calls, no UI rendering. We deliberately
do NOT call init_ee() here so the suite runs offline in CI without credentials.
"""

import pytest


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
