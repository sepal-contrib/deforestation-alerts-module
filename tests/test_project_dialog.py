from deforestation_alerts.project import io, paths
from deforestation_alerts.project.models import AoiRecord
from gui.store.project_writers import writing
from gui.tile import project_dialog as pd


def test_create_project_is_empty_and_named():
    p = pd.create_project("north")
    assert p.name == "north" and p.sources == {} and p.aoi is None


def test_duplicate_copies_the_aoi_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    p = pd.create_project("a")
    p.aoi = AoiRecord(spec={}, name="x", method="DRAW", digest="d", bounds=(0, 0, 1, 1),
                      geometry_file=paths.AOI_SIDECAR)
    io.save_project(p)
    (tmp_path / "a" / paths.AOI_SIDECAR).write_text('{"type": "FeatureCollection", "features": []}')
    q = pd.duplicate_with_sidecar(p, "b")
    assert q.name == "b" and q.aoi == p.aoi
    assert (tmp_path / "b" / paths.AOI_SIDECAR).exists()


def test_delete_is_blocked_while_writing():
    assert pd.delete_blocked("x") is None
    with writing("x"):
        assert pd.delete_blocked("x") == "steps.project.delete_busy"
