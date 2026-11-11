"""Shared stand-ins for the review Save tests."""

import geopandas as gpd
from shapely.geometry import box


def boundaries(*names, boxes=None) -> gpd.GeoDataFrame:
    """GAUL-like boundaries: one area per ``(admin1, admin2, admin3)`` in
    ``names``, each over its box in ``boxes`` (the whole world by default)."""
    boxes = boxes or [box(-180, -90, 180, 90)] * len(names)
    return gpd.GeoDataFrame({f"admin{i + 1}": [n[i] for n in names] for i in range(3)},
                            geometry=list(boxes), crs="EPSG:4326")


class AdminLookups:
    """Stands in for ``review_save.load_boundaries``: one area named
    Brasil / Acre / Rio Branco over the whole world. Its first call runs
    ``during``, what the user does while the fetch blocks; ``calls`` counts."""

    def __init__(self, during=None):
        self.during = during
        self.calls = 0

    def __call__(self, pdir, aoi):
        self.calls += 1
        during, self.during = self.during, None
        if during is not None:
            during()
        return boundaries(("Brasil", "Acre", "Rio Branco"))


def stub_admin_lookups(monkeypatch, lookups=None) -> AdminLookups:
    """Put ``lookups`` (a fresh ``AdminLookups`` by default) in place of
    ``review_save.load_boundaries`` for the test, and return it."""
    lookups = lookups if lookups is not None else AdminLookups()
    monkeypatch.setattr("gui.tile.analysis.review_save.load_boundaries", lookups)
    return lookups
