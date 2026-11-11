"""GAUL 2024 boundaries cached per project and joined to alerts locally."""

from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import pytest
from shapely.geometry import box

from deforestation_alerts.engine import admin_names as an
from deforestation_alerts.project.paths import admin_boundaries_path


def _boundaries():
    # Peru west of 0, Brasil east of 0
    return gpd.GeoDataFrame(
        {"admin1": ["Peru", "Brasil"], "admin2": ["Madre de Dios", "Acre"], "admin3": ["Tambopata", "Assis"]},
        geometry=[box(-1, 0, 0, 1), box(0, 0, 1, 1)], crs="EPSG:4326")


def _names(geoms, crs="EPSG:4326"):
    return an.admin_names(gpd.GeoSeries(geoms, crs=crs), _boundaries())


def test_inside_one_area():
    out = _names([box(-0.5, 0.2, -0.4, 0.3)])
    assert out.loc[0, "admin1"] == "Peru" and out.loc[0, "admin3"] == "Tambopata"


def test_across_a_border_lists_both_largest_first():
    out = _names([box(-0.03, 0.2, 0.07, 0.3)])  # 70 % east
    assert out.loc[0, "admin1"] == "Brasil, Peru"
    assert out.loc[0, "admin2"] == "Acre, Madre de Dios"


def test_projected_geometries_are_reprojected():
    # the same 70 % east box, in Web Mercator
    geoms = gpd.GeoSeries([box(-0.03, 0.2, 0.07, 0.3)], crs="EPSG:4326").to_crs(3857)
    out = an.admin_names(geoms, _boundaries())
    assert out.loc[0, "admin1"] == "Brasil, Peru"


def test_outside_every_area_is_empty():
    out = _names([box(5, 5, 6, 6)])
    assert out.loc[0].tolist() == ["", "", ""]


def test_index_follows_the_geometries():
    geoms = gpd.GeoSeries([box(-0.5, 0.2, -0.4, 0.3), box(5, 5, 6, 6)], index=[7, 3], crs="EPSG:4326")
    out = an.admin_names(geoms, _boundaries())
    assert list(out.index) == [7, 3] and list(out.columns) == ["admin1", "admin2", "admin3"]
    assert out.loc[7, "admin1"] == "Peru" and out.loc[3, "admin1"] == ""


def _aoi(digest="abc123"):
    return SimpleNamespace(digest=digest, bounds=(-1.0, 0.0, 1.0, 1.0))


def _counting_fetch(result=None):
    calls = []

    def fetch(bounds):
        calls.append(bounds)
        return _boundaries() if result is None else result
    return fetch, calls


def test_load_fetches_once_then_reads_the_cache(tmp_path):
    fetch, calls = _counting_fetch()
    first = an.load_boundaries(tmp_path, _aoi(), fetch=fetch)
    second = an.load_boundaries(tmp_path, _aoi(), fetch=fetch)
    assert len(calls) == 1
    assert admin_boundaries_path(tmp_path, "abc123").is_file()
    assert sorted(second["admin1"]) == sorted(first["admin1"]) == ["Brasil", "Peru"]
    assert second.crs.to_epsg() == 4326


def test_new_aoi_digest_refetches(tmp_path):
    fetch, calls = _counting_fetch()
    an.load_boundaries(tmp_path, _aoi("one"), fetch=fetch)
    an.load_boundaries(tmp_path, _aoi("two"), fetch=fetch)
    assert len(calls) == 2


def test_failed_fetch_writes_no_file(tmp_path):
    def fetch(bounds):
        raise RuntimeError("EE down")
    with pytest.raises(RuntimeError):
        an.load_boundaries(tmp_path, _aoi(), fetch=fetch)
    folder = admin_boundaries_path(tmp_path, "abc123").parent
    assert not folder.exists() or not any(folder.iterdir())


def test_failed_write_leaves_no_temp_file(tmp_path, monkeypatch):
    def broken_to_file(self, path, **kwargs):
        Path(path).write_bytes(b"half")
        raise OSError("disk full")
    monkeypatch.setattr(gpd.GeoDataFrame, "to_file", broken_to_file)
    fetch, _ = _counting_fetch()
    with pytest.raises(OSError):
        an.load_boundaries(tmp_path, _aoi(), fetch=fetch)
    assert list(admin_boundaries_path(tmp_path, "abc123").parent.iterdir()) == []


def test_empty_boundaries_are_cached_too(tmp_path):
    empty = gpd.GeoDataFrame({"admin1": [], "admin2": [], "admin3": []}, geometry=[], crs="EPSG:4326")
    fetch, calls = _counting_fetch(empty)
    an.load_boundaries(tmp_path, _aoi(), fetch=fetch)
    again = an.load_boundaries(tmp_path, _aoi(), fetch=fetch)
    assert len(calls) == 1 and len(again) == 0
    out = an.admin_names(gpd.GeoSeries([box(0, 0, 1, 1)], crs="EPSG:4326"), again)
    assert out.loc[0].tolist() == ["", "", ""]


def test_concurrent_loads_fetch_once(tmp_path):
    import threading
    import time

    calls = []

    def slow_fetch(bounds):
        calls.append(bounds)
        time.sleep(0.2)
        return _boundaries()
    threads = [threading.Thread(target=an.load_boundaries, args=(tmp_path, _aoi()), kwargs={"fetch": slow_fetch})
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1


def test_a_slow_fetch_does_not_block_another_project(tmp_path):
    import threading

    started, release = threading.Event(), threading.Event()

    def blocked_fetch(bounds):
        started.set()
        release.wait(5)
        return _boundaries()
    fetch, _ = _counting_fetch()
    an.load_boundaries(tmp_path / "b", _aoi("b"), fetch=fetch)  # B's file exists
    a = threading.Thread(target=an.load_boundaries, args=(tmp_path / "a", _aoi("a")),
                         kwargs={"fetch": blocked_fetch})
    a.start()
    try:
        assert started.wait(5)
        done = []
        b = threading.Thread(target=lambda: done.append(an.load_boundaries(tmp_path / "b", _aoi("b"))))
        b.start()
        b.join(2)
        assert done, "project B waited on project A's fetch"
    finally:
        release.set()
        a.join()


def test_path_helper():
    p = admin_boundaries_path(Path("/p"), "abc/def")
    assert p.parent == Path("/p/admin") and p.name.startswith("gaul2024_l2_") and p.suffix == ".gpkg"


def test_asset_digests_with_a_shared_prefix_get_different_paths():
    one, two = "asset:projects/ee-user/assets/aoi_one", "asset:projects/ee-user/assets/aoi_two"
    assert admin_boundaries_path(Path("/p"), one) != admin_boundaries_path(Path("/p"), two)
    assert admin_boundaries_path(Path("/p"), one) == admin_boundaries_path(Path("/p"), one)
