from pathlib import Path

import pytest

from deforestation_alerts.scripts.workspace import Workspace


@pytest.fixture
def local_ws(tmp_path):
    return Workspace(root=tmp_path / "data", scratch_parent=tmp_path / "scratch")


def test_results_dir_and_ensure_dir(local_ws, tmp_path):
    assert local_ws.results_dir("p", "sets") == str(tmp_path / "data" / "p" / "sets")
    with pytest.raises(ValueError):
        local_ws.results_dir("../escape")
    made = local_ws.ensure_dir("p", "x")
    assert Path(made).is_dir()


def test_write_read_round_trip_creates_parents(local_ws, tmp_path):
    target = tmp_path / "data" / "p" / "a" / "b.json"
    local_ws.write_json(str(target), {"k": 1})
    assert local_ws.read_json(str(target)) == {"k": 1}
    assert local_ws.exists(str(target))
    with pytest.raises(FileExistsError):
        local_ws.write_text(str(target), "x", overwrite=False)


def test_download_to_scratch_returns_the_file_itself(local_ws, tmp_path):
    f = tmp_path / "data" / "user.tif"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"123")
    staged = local_ws.download_to_scratch(str(f))
    assert staged == f
    local_ws.discard_scratch(staged)  # must NOT delete a user file
    assert f.exists()


def test_upload_copies_and_scratch_is_discardable(local_ws, tmp_path):
    local = local_ws.scratch_file(name="out.gpkg")
    local.write_bytes(b"abc")
    dest = tmp_path / "data" / "p" / "out.gpkg"
    assert local_ws.upload_from_scratch(local, str(dest)) == str(dest)
    assert dest.read_bytes() == b"abc"
    local_ws.discard_scratch(local)
    assert not local.exists()


def test_list_filters_extensions_and_hidden(local_ws, tmp_path):
    d = tmp_path / "data" / "p"
    d.mkdir(parents=True)
    for name in ("a.tif", "b.json", ".hidden.tif"):
        (d / name).write_text("x")
    assert local_ws.list(str(d), extensions=[".tif"]) == [str(d / "a.tif")]
    assert local_ws.list(str(tmp_path / "missing")) == []


def test_home_relative(local_ws):
    p = Path.home() / "module_results" / "x.zip"
    assert local_ws.home_relative(str(p)) == "module_results/x.zip"


def test_cleanup_scratch(local_ws):
    d = local_ws.scratch_dir()
    (d / "f").write_text("1")
    local_ws.cleanup_scratch()
    assert not d.exists()
