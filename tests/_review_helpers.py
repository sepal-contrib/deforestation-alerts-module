"""Shared stand-ins for the review Save tests."""


class AdminLookups:
    """Stands in for ``review_save.ee``: the FAO/GAUL lookup chain. Its first
    blocking ``getInfo`` runs ``during``, what the user does while it blocks."""

    def __init__(self, during=None):
        self.during = during
        self.calls = 0

    def __getattr__(self, name):  # FeatureCollection, String, filterBounds, iterate...
        return lambda *a, **k: self

    def getInfo(self):  # noqa: N802 - Earth Engine's name
        self.calls += 1
        during, self.during = self.during, None
        if during is not None:
            during()
        return {"admin1": "Brasil", "admin2": "Acre", "admin3": "Rio Branco"}


def stub_admin_lookups(monkeypatch, lookups=None) -> AdminLookups:
    """Put ``lookups`` (a fresh ``AdminLookups`` by default) in place of
    ``review_save.ee`` for the test, and return it."""
    lookups = lookups if lookups is not None else AdminLookups()
    monkeypatch.setattr("gui.tile.analysis.review_save.ee", lookups)
    return lookups
