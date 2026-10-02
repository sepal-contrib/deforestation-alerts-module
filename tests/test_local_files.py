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


def test_download_url_defaults_to_the_sepal_route_relative_to_the_host(monkeypatch, tmp_path):
    """No configuration: a root-relative SEPAL route, so the browser's own host
    answers it -- sepal.io and test.sepal.io alike."""
    monkeypatch.delenv(local_files.DOWNLOAD_BASE_ENV, raising=False)
    monkeypatch.delenv(local_files.DOWNLOAD_ROOT_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    f = tmp_path / "module_results" / "deforestation_alerts" / "p" / "x.gpkg"
    assert local_files.download_url(f) == (
        "/api/sandbox/jupyter/files/module_results/deforestation_alerts/p/x.gpkg"
    )


def test_download_url_honours_a_configured_base_and_root(monkeypatch, tmp_path):
    """A local jupyter serves its own root at /files."""
    monkeypatch.setenv(local_files.DOWNLOAD_BASE_ENV, "/files")
    monkeypatch.setenv(local_files.DOWNLOAD_ROOT_ENV, str(tmp_path))
    assert local_files.download_url(tmp_path / "data" / "x.csv") == "/files/data/x.csv"


def test_download_url_keeps_an_absolute_base(monkeypatch, tmp_path):
    monkeypatch.setenv(local_files.DOWNLOAD_BASE_ENV, "https://test.sepal.io/api/sandbox/jupyter/files/")
    monkeypatch.setenv(local_files.DOWNLOAD_ROOT_ENV, str(tmp_path))
    assert local_files.download_url(tmp_path / "x.csv") == (
        "https://test.sepal.io/api/sandbox/jupyter/files/x.csv"
    )


def test_download_url_escapes_a_project_name_with_a_space(monkeypatch, tmp_path):
    monkeypatch.setenv(local_files.DOWNLOAD_ROOT_ENV, str(tmp_path))
    assert local_files.download_url(tmp_path / "Paraguay Norte" / "x.csv") == (
        "/api/sandbox/jupyter/files/Paraguay%20Norte/x.csv"
    )


def test_download_url_refuses_a_path_outside_the_served_root(monkeypatch, tmp_path):
    monkeypatch.setenv(local_files.DOWNLOAD_ROOT_ENV, str(tmp_path))
    with pytest.raises(ValueError):
        local_files.download_url(Path("/var/tmp/x.zip"))
