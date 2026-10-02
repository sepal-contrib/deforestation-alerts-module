"""AoiStep._attach: digest first for Earth Engine AOIs, one install on the first
pick, and a sidecar that only lands in the project it was picked for."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import geopandas as gpd
import pytest
import reacton
from pysepal.solara.notifications import NotificationProvider
from shapely.geometry import box

import gui.tile.aoi_step as aoi_step_mod
from deforestation_alerts.project import io, paths
from deforestation_alerts.project.models import AoiRecord, Project
from gui.scripts import aoi_io
from gui.store.state_manager import AppState


class _Result(SimpleNamespace):
    async def get_gdf_async(self):
        return self.gdf


def _gee(calls):
    async def get_info_async(obj):
        calls.append(obj)
        return {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    return SimpleNamespace(get_info_async=get_info_async)


def _admin_result():
    fc = SimpleNamespace(geometry=lambda: SimpleNamespace(bounds=lambda: "BOUNDS"))
    return _Result(gdf=None, admin="2317", method="ADMIN1", name="Canindeyu", feature_collection=fc,
                   spec=SimpleNamespace(to_dict=lambda: {"method": "ADMIN1"}, asset_id=None, column=None))


def _attach(state, gee):
    """The step's ``_attach`` coroutine, captured from a real render."""
    captured = {}
    real_use_task = aoi_step_mod.solara.lab.use_task

    def spy(fn, **kw):
        captured["fn"] = fn
        return real_use_task(fn, **kw)

    aoi_step_mod.solara.lab.use_task = spy
    try:
        @reacton.component
        def Page():
            NotificationProvider()
            aoi_step_mod.AoiStep(app_state=state, map_=SimpleNamespace(), gee_interface=gee)
        _, rc = reacton.render(Page(), handle_error=False)
    finally:
        aoi_step_mod.solara.lab.use_task = real_use_task
    return captured["fn"], rc


@pytest.fixture
def no_aoiview(monkeypatch):
    monkeypatch.setattr(aoi_step_mod, "AoiView", lambda **kw: None)


def test_a_restored_admin_aoi_makes_no_bounds_request(data_root, no_aoiview):
    state = AppState()
    p = Project(name="p", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="Canindeyu", method="ADMIN1", digest="admin:2317", bounds=(0, 0, 1, 1))
    state.load_project_state(p)
    calls = []
    attach, rc = _attach(state, _gee(calls))
    try:
        result = _admin_result()
        asyncio.run(attach(result))
        assert calls == [] and state.aoi_result.value is result
    finally:
        rc.close()


def test_the_first_pick_installs_one_project_with_its_aoi(data_root, no_aoiview):
    """M29: the project used to be installed without its AOI and given it in a
    second publish; the AOI column remounted in between. Every published
    project must carry the AOI."""
    from gui.store.autosave import install_autosave

    state = AppState()
    install_autosave(state)  # the shell saves every install, as here
    publishes = []  # read at publish time: the live project is mutated afterwards
    state.project.subscribe(lambda v: publishes.append(v is not None and v.aoi is not None))
    attach, rc = _attach(state, _gee([]))
    try:
        gdf = gpd.GeoDataFrame(geometry=[box(-55.1, -10.1, -55.0, -10.0)], crs="EPSG:4326")
        result = _Result(gdf=gdf, admin=None, method="DRAW", name="box", feature_collection=None,
                         spec=SimpleNamespace(to_dict=lambda: {"method": "DRAW"}, asset_id=None, column=None))
        asyncio.run(attach(result))
        p = state.project.value
        assert p is not None and p.aoi is not None and p.aoi.digest == aoi_io.geometry_digest(gdf)
        assert publishes == [True]  # one install, the AOI already in it
        assert (paths.project_dir(p.name) / paths.AOI_SIDECAR).exists()
        assert io.load_project(p.name).aoi.digest == p.aoi.digest
    finally:
        rc.close()


def test_a_refused_aoi_change_leaves_the_old_sidecar(data_root, no_aoiview, monkeypatch):
    state = AppState()
    p = Project(name="q", created_at=datetime.now(), updated_at=datetime.now())
    old = gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)], crs="EPSG:4326")
    aoi_io.write_sidecar(paths.project_dir("q"), old)
    p.aoi = AoiRecord(spec={}, name="old", method="DRAW", digest=aoi_io.geometry_digest(old), bounds=(0, 0, 1, 1),
                      geometry_file=paths.AOI_SIDECAR)
    state.load_project_state(p)
    monkeypatch.setattr(state, "update_project", lambda name, fn: False)  # switched meanwhile
    attach, rc = _attach(state, _gee([]))
    try:
        new = gpd.GeoDataFrame(geometry=[box(5, 5, 6, 6)], crs="EPSG:4326")
        result = _Result(gdf=new, admin=None, method="DRAW", name="new", feature_collection=None,
                         spec=SimpleNamespace(to_dict=lambda: {"method": "DRAW"}, asset_id=None, column=None))
        asyncio.run(attach(result))
        side = gpd.read_file(paths.project_dir("q") / paths.AOI_SIDECAR)
        assert aoi_io.geometry_digest(side) == aoi_io.geometry_digest(old)
        assert not list(paths.project_dir("q").glob(".aoi.geojson.*.tmp"))
    finally:
        rc.close()


def test_an_accepted_aoi_change_moves_the_staged_sidecar_into_place(data_root, no_aoiview):
    from deforestation_alerts.project.models import Artifact, GridSpec

    state = AppState()
    p = Project(name="q", created_at=datetime.now(), updated_at=datetime.now())
    old = gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)], crs="EPSG:4326")
    aoi_io.write_sidecar(paths.project_dir("q"), old)
    p.aoi = AoiRecord(spec={}, name="old", method="DRAW", digest=aoi_io.geometry_digest(old), bounds=(0, 0, 1, 1),
                      geometry_file=paths.AOI_SIDECAR)
    p.grid = GridSpec(epsg="EPSG:32721", resolution=10, xmin=0, ymax=0, width=10, height=10)
    p.artifacts = {"k": Artifact(key="k", kind="raw_alert", rel_path="r.tif", fingerprint="f", inputs={},
                                 content_id="c", created_at=datetime.now())}
    state.load_project_state(p)
    attach, rc = _attach(state, _gee([]))
    try:
        new = gpd.GeoDataFrame(geometry=[box(5, 5, 6, 6)], crs="EPSG:4326")
        result = _Result(gdf=new, admin=None, method="DRAW", name="new", feature_collection=None,
                         spec=SimpleNamespace(to_dict=lambda: {"method": "DRAW"}, asset_id=None, column=None))
        asyncio.run(attach(result))
        side = gpd.read_file(paths.project_dir("q") / paths.AOI_SIDECAR)
        assert aoi_io.geometry_digest(side) == aoi_io.geometry_digest(new)
        q = state.project.value
        assert q.aoi.digest == aoi_io.geometry_digest(new)
        assert q.grid is None and q.artifacts == {}
        assert state.aoi_result.value is result
        assert not list(paths.project_dir("q").glob(".aoi.geojson.*.tmp"))
    finally:
        rc.close()


def test_the_first_pick_keeps_the_picker_mounted(data_root, monkeypatch):
    """M29: the install of the project created from the pick on screen must not
    remount the picker (its unmount clears the outline and the remount
    refetches the AOI); installing any other project still remounts it."""
    import solara

    mounts = []

    @solara.component
    def _View(**kw):
        solara.use_effect(lambda: mounts.append(1), [])

    monkeypatch.setattr(aoi_step_mod, "AoiView", _View)
    state = AppState()
    attach, rc = _attach(state, _gee([]))
    try:
        assert mounts == [1]
        gdf = gpd.GeoDataFrame(geometry=[box(-55.1, -10.1, -55.0, -10.0)], crs="EPSG:4326")
        result = _Result(gdf=gdf, admin=None, method="DRAW", name="box", feature_collection=None,
                         spec=SimpleNamespace(to_dict=lambda: {"method": "DRAW"}, asset_id=None, column=None))
        asyncio.run(attach(result))
        assert state.project.value is not None and mounts == [1]
        state.load_project_state(Project(name="other", created_at=datetime.now(), updated_at=datetime.now()))
        assert mounts == [1, 1]
    finally:
        rc.close()
