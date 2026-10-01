import json
from datetime import date, datetime

import pytest

from deforestation_alerts.project import io, paths
from deforestation_alerts.project.models import (
    AlertSet, AlertSetParams, GridSpec, Period, Project, add_predefined_source,
)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


def _project(name="demo"):
    now = datetime(2026, 9, 30, 12)
    p = Project(name=name, created_at=now, updated_at=now)
    add_predefined_source(p, "GLAD-L")
    return p


def test_data_dir_from_env(root):
    assert paths.data_dir() == root


def test_data_dir_default_is_absolute(monkeypatch):
    monkeypatch.delenv("DEFORESTATION_ALERTS_DATA_DIR", raising=False)
    d = paths.data_dir()
    assert d.is_absolute() and d.parts[-2:] == ("module_results", "deforestation_alerts")


def test_save_then_load_round_trip(root):
    p = _project()
    manifest = io.save_project(p)
    assert manifest == root / "demo" / "project.json"
    assert not list((root / "demo").glob("*.tmp"))  # atomic write left nothing behind
    back = io.load_project("demo")
    assert back.sources.keys() == {"GLAD-L"}
    assert back.updated_at >= p.created_at


def test_load_rejects_future_schema(root):
    io.save_project(_project())
    m = root / "demo" / "project.json"
    data = json.loads(m.read_text())
    data["schema_version"] = 99
    m.write_text(json.dumps(data))
    with pytest.raises(io.ProjectLoadError, match="schema"):
        io.load_project("demo")


def test_load_rejects_garbage(root):
    (root / "bad").mkdir(parents=True)
    (root / "bad" / "project.json").write_text("{not json")
    with pytest.raises(io.ProjectLoadError):
        io.load_project("bad")


def test_a_folder_renamed_on_disk_reopens_under_its_new_name(root):
    """The folder name wins over the manifest's: saving after the reopen must
    write into the renamed folder, never re-create the old one (a ghost)."""
    io.save_project(_project("paraguay"))
    (root / "paraguay").rename(root / "paraguay_2026")
    p = io.load_project("paraguay_2026")
    assert p.name == "paraguay_2026"
    io.save_project(p)
    assert sorted(d.name for d in root.iterdir()) == ["paraguay_2026"]
    saved = json.loads((root / "paraguay_2026" / "project.json").read_text())
    assert saved["name"] == "paraguay_2026"
    assert io.load_project("paraguay_2026").sources.keys() == {"GLAD-L"}


def test_a_renamed_folder_never_takes_over_the_project_now_holding_its_old_name(root):
    """Rename ``paraguay`` to free the name, create a new ``paraguay``, then
    open and save the renamed one: the new project's manifest stays as it was."""
    io.save_project(_project("paraguay"))
    (root / "paraguay").rename(root / "paraguay_2026")
    newer = _project("paraguay")
    newer.sources.clear()
    io.save_project(newer)
    before = (root / "paraguay" / "project.json").read_bytes()
    io.save_project(io.load_project("paraguay_2026"))
    assert (root / "paraguay" / "project.json").read_bytes() == before
    assert io.load_project("paraguay").sources == {}
    assert io.load_project("paraguay_2026").sources.keys() == {"GLAD-L"}


def test_list_skips_folders_without_manifest(root):
    io.save_project(_project("a"))
    io.save_project(_project("b"))
    (root / "stray").mkdir()
    infos = io.list_projects()
    assert [i.name for i in infos] == ["a", "b"]
    assert infos[0].n_sources == 1 and infos[0].n_sets == 0


def test_delete_removes_folder(root):
    io.save_project(_project())
    (root / "demo" / "periods").mkdir()
    (root / "demo" / "periods" / "x.tif").write_bytes(b"1")
    io.delete_project("demo")
    assert not (root / "demo").exists()


@pytest.mark.parametrize("name,err", [("", "empty"), ("a/b", "chars"), ("dup", "exists"), ("ok name-1", None)])
def test_validate_project_name(name, err):
    assert io.validate_project_name(name, ["dup"]) == err


def test_duplicate_keeps_setup_drops_results():
    p = _project()
    p.grid = GridSpec(epsg="EPSG:32721", resolution=10, xmin=0, ymax=0, width=1, height=1)
    p.periods["x"] = Period(id="x", start=date(2026, 1, 1), end=date(2026, 1, 31))
    p.alert_sets["s"] = AlertSet(id="s", period_id="x", sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                                 params=AlertSetParams(), store_rel_path="a", combined_rel_path="c",
                                 footprint_rel_path="f", fingerprint="f", created_at=datetime(2026, 9, 30))
    q = io.duplicate_project(p, "copy")
    assert q.name == "copy" and q.grid == p.grid and q.sources == p.sources
    assert q.periods == {} and q.alert_sets == {} and q.artifacts == {} and q.locked is False


def test_paths_encode_source_and_dates(tmp_path):
    raw = paths.raw_path(tmp_path, "2026-02-01_2026-02-28", "GLAD-L", date(2025, 11, 3), date(2026, 2, 28))
    assert raw == tmp_path / "periods" / "2026-02-01_2026-02-28" / "raw" / "raw_glad-l_2025-11-03_2026-02-28.tif"
    sd = paths.set_dir(tmp_path, "2026-02-01_2026-02-28", ["RADD", "GLAD-L"])
    assert sd == tmp_path / "periods" / "2026-02-01_2026-02-28" / "sets" / "glad-l-radd"
    assert paths.combined_path(sd, ["RADD", "GLAD-L"], date(2026, 2, 1), date(2026, 2, 28)).name == \
        "filtered_alerts_glad-l-radd_2026-02-01_2026-02-28.tif"
    assert paths.rel(tmp_path, raw) == "periods/2026-02-01_2026-02-28/raw/raw_glad-l_2025-11-03_2026-02-28.tif"
    assert paths.resolve(tmp_path, paths.rel(tmp_path, raw)) == raw
