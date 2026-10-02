from pathlib import Path

import pytest

from deforestation_alerts.scripts import local_files


def test_write_into_renames_on_success(tmp_path):
    dest = tmp_path / "sub" / "a.bin"
    with local_files.write_into(dest) as part:
        assert part.parent == dest.parent and part.name.startswith("a.bin.") and part.suffix == ".part"
        part.write_bytes(b"new")
    assert dest.read_bytes() == b"new"
    assert [p.name for p in dest.parent.iterdir()] == ["a.bin"]


def test_write_into_leaves_the_old_file_on_failure(tmp_path):
    dest = tmp_path / "a.bin"
    dest.write_bytes(b"old")
    with pytest.raises(RuntimeError):
        with local_files.write_into(dest) as part:
            part.write_bytes(b"half")
            raise RuntimeError("boom")
    assert dest.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["a.bin"]


def test_two_writers_never_share_a_part(tmp_path):
    dest = tmp_path / "a.bin"
    with local_files.write_into(dest) as p1, local_files.write_into(dest) as p2:
        assert p1 != p2
        p1.write_bytes(b"1")
        p2.write_bytes(b"2")
    assert dest.read_bytes() in (b"1", b"2")


def test_copy_into(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("x")
    out = local_files.copy_into(src, tmp_path / "d" / "dst.txt")
    assert out.read_text() == "x" and src.exists()


def test_scratch_dir_is_private_and_removed(monkeypatch, tmp_path):
    root = tmp_path / "scratch"  # not the host's tmp
    monkeypatch.setattr(local_files.gdal_env, "scratch_dir", lambda: root)
    with local_files.scratch_dir() as d1, local_files.scratch_dir() as d2:
        assert d1 != d2 and d1.is_dir() and d2.is_dir()
        assert d1.parent == root and d2.parent == root
        (d1 / "f").write_text("x")
    assert not d1.exists() and not d2.exists()


def test_home_relative(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    f = tmp_path / "module_results" / "x.zip"
    assert local_files.home_relative(f) == "module_results/x.zip"
    with pytest.raises(ValueError):
        local_files.home_relative(Path("/var/tmp/x.zip"))
