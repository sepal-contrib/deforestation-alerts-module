import uuid
from datetime import date, datetime

from deforestation_alerts.engine import fingerprint as fp
from deforestation_alerts.project import cleanup
from deforestation_alerts.project.models import AoiRecord, Artifact, Period, Project, add_predefined_source
from gui.scripts import aoi_io

JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
FEB = Period(id="2026-02-01_2026-02-28", start=date(2026, 2, 1), end=date(2026, 2, 28))


def _art(pdir, key, kind):
    rel = f"periods/{key.replace(':', '_')}.tif"
    (pdir / rel).parent.mkdir(parents=True, exist_ok=True)
    (pdir / rel).write_bytes(b"x")
    return Artifact(key=key, kind=kind, rel_path=rel, fingerprint="f", inputs={}, content_id=uuid.uuid4().hex,
                    created_at=datetime.now())


def _project(pdir):
    p = Project(name="c", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="d1", bounds=(0, 0, 1, 1))
    add_predefined_source(p, "GLAD-L")
    add_predefined_source(p, "RADD")
    p.periods = {JAN.id: JAN, FEB.id: FEB}
    arts = {}
    for period in (JAN, FEB):
        for s in ("GLAD-L", "RADD"):
            for key, kind in ((fp.raw_key(period.id, s), "raw_alert"), (fp.harm_key(period.id, s), "harmonized")):
                arts[key] = _art(pdir, key, kind)
    p.artifacts = arts
    return p


def test_deleting_a_source_leaves_none_of_its_files(tmp_path):
    p = _project(tmp_path)
    keys = cleanup.source_artifact_keys(p, "RADD")
    assert sorted(keys) == sorted([fp.raw_key(JAN.id, "RADD"), fp.harm_key(JAN.id, "RADD"),
                                   fp.raw_key(FEB.id, "RADD"), fp.harm_key(FEB.id, "RADD")])
    before = dict(p.artifacts)
    gone = cleanup.drop_artifacts(p, keys)
    assert p.artifacts is not before and all(k not in p.artifacts for k in keys)
    cleanup.delete_files(tmp_path, gone)
    assert all(not (tmp_path / r).exists() for r in gone)
    assert all((tmp_path / a.rel_path).exists() for a in p.artifacts.values())


def test_replacing_a_period_drops_only_that_period(tmp_path):
    p = _project(tmp_path)
    gone = cleanup.drop_artifacts(p, cleanup.period_artifact_keys(p, JAN.id))
    cleanup.delete_files(tmp_path, gone)
    assert len(p.artifacts) == 4 and all(FEB.id in k for k in p.artifacts)
    assert len(gone) == 4 and all(not (tmp_path / r).exists() for r in gone)


def test_an_aoi_change_leaves_no_dropped_file(tmp_path):
    p = _project(tmp_path)
    rec = AoiRecord(spec={}, name="b", method="DRAW", digest="d2", bounds=(1, 1, 2, 2))
    gone = aoi_io.apply_aoi(p, rec)
    cleanup.delete_files(tmp_path, gone)
    assert p.aoi == rec and p.grid is None and p.artifacts == {}
    assert not [f for f in tmp_path.rglob("*.tif")]


def test_missing_files_and_empty_paths_are_ignored(tmp_path):
    cleanup.delete_files(tmp_path, ["nope.tif", ""])
