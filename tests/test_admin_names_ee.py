"""Tier 2 (live Earth Engine): GAUL 2024 L2 pages down for a small box. Skips without credentials."""

import pytest


@pytest.mark.ee
def test_fetch_small_bounds(gee):
    from deforestation_alerts.engine import admin_names as an

    gdf = an.fetch_boundaries((-68.0, -10.1, -67.9, -10.0))  # Acre, Brazil
    assert len(gdf) > 0 and gdf.crs.to_epsg() == 4326
    assert list(gdf.columns) == ["admin1", "admin2", "admin3", "geometry"]
    assert all(gdf["admin1"].str.len() > 0) and all(gdf["admin3"].str.len() > 0)
    assert "Brazil" in set(gdf["admin1"]) or "Brasil" in set(gdf["admin1"])
