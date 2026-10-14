"""AoiStep._attach: digest first for Earth Engine AOIs, one install on the first
pick, and a sidecar that only lands in the project it was picked for."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import threading

import geopandas as gpd
import pytest
import reacton
from pysepal.solara.notifications import NotificationProvider
from pysepal.solara.notifications.bus import get_current_bus
from shapely.geometry import box

import gui.tile.aoi_step as aoi_step_mod
from deforestation_alerts.project import io, paths
from deforestation_alerts.project.models import AoiRecord, Project
from gui.i18n import t
from gui.scripts import aoi_io, map_layers
from gui.scripts.map_layers import MapLayers
from gui.store.project_writers import writing
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


class _Map:
    def add_layer(self, layer, key=""):
        pass

    def remove_layer(self, key, none_ok=False):
        pass


def _attach(state, gee, layers=None):
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
            aoi_step_mod.AoiStep(app_state=state, map_=SimpleNamespace(), gee_interface=gee,
                                 layers=layers if layers is not None else MapLayers(_Map()))
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


def _project_with_files(name="q"):
    """A project with a drawn AOI (its sidecar on disk), a grid, and a
    harmonized source and an aligned mask recorded, their files on disk."""
    from deforestation_alerts.project.models import Artifact, GridSpec

    p = Project(name=name, created_at=datetime.now(), updated_at=datetime.now())
    pdir = paths.project_dir(name)
    old = gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)], crs="EPSG:4326")
    aoi_io.write_sidecar(pdir, old)
    p.aoi = AoiRecord(spec={}, name="old", method="DRAW", digest=aoi_io.geometry_digest(old), bounds=(0, 0, 1, 1),
                      geometry_file=paths.AOI_SIDECAR)
    p.grid = GridSpec(epsg="EPSG:32721", resolution=10, xmin=0, ymax=0, width=10, height=10)
    for key, rel in (("harm:jan:GLAD-L", "periods/jan/harmonized/GLAD-L.tif"), ("mask:forest", "masks/forest.tif")):
        (pdir / rel).parent.mkdir(parents=True, exist_ok=True)
        (pdir / rel).write_bytes(b"x")
        p.artifacts[key] = Artifact(key=key, kind="harmonized", rel_path=rel, fingerprint="f", inputs={},
                                    content_id=f"c-{key}", created_at=datetime.now())
    return p, old


def _new_pick():
    new = gpd.GeoDataFrame(geometry=[box(5, 5, 6, 6)], crs="EPSG:4326")
    return _Result(gdf=new, admin=None, method="DRAW", name="new", feature_collection=None,
                   spec=SimpleNamespace(to_dict=lambda: {"method": "DRAW"}, asset_id=None, column=None))


def _toasts(kind):
    return [x.message for x in get_current_bus().toasts.value if x.type.value == kind]


@pytest.fixture
def sync_layers(monkeypatch):
    monkeypatch.setattr(map_layers, "spawn_in_context",
                        lambda target, args=(), **kw: target(*args) or threading.current_thread())
    return MapLayers(_Map())


def test_an_aoi_change_hides_the_layers_of_the_files_it_deletes_first(data_root, no_aoiview, sync_layers,
                                                                      monkeypatch):
    """Review B I1 (the Task 38 bug class): the harmonized (``src:``) and
    aligned (``mask:``) layers leave the map before their files are deleted;
    an Earth Engine preview (``src:`` without a content id) shows the old
    AOI's clip, so it goes too. A visualization layer stays."""
    p, _ = _project_with_files()
    state = AppState()
    state.load_project_state(p)
    layers = sync_layers
    layers.show("src:GLAD-L", lambda: None, content_id="c-harm:jan:GLAD-L")
    layers.show("mask:forest", lambda: None, content_id="c-mask:forest")
    layers.show("src:RADD", lambda: None)  # an Earth Engine preview
    layers.show("vis:ndvi", lambda: None)
    on_at_delete = []
    real_delete = aoi_step_mod.cleanup.delete_files
    monkeypatch.setattr(aoi_step_mod.cleanup, "delete_files",
                        lambda pdir, rels: (on_at_delete.append(set(layers.on.value)), real_delete(pdir, rels)))
    attach, rc = _attach(state, _gee([]), layers)
    try:
        asyncio.run(attach(_new_pick()))
        assert state.project.value.artifacts == {}
        assert on_at_delete == [{"vis:ndvi"}]  # hidden before the delete
        pdir = paths.project_dir("q")
        assert not (pdir / "periods/jan/harmonized/GLAD-L.tif").exists() and not (pdir / "masks/forest.tif").exists()
    finally:
        rc.close()


def test_an_aoi_change_is_refused_while_a_job_writes_into_the_project(data_root, no_aoiview, sync_layers):
    """Review A I1: a Download & harmonize (or Integrate) job runs before any
    set locks the AOI; a change under it would delete the files it reads and
    let it write old-AOI files. The change is refused with a reason."""
    p, old = _project_with_files()
    state = AppState()
    state.load_project_state(p)
    sync_layers.show("src:GLAD-L", lambda: None, content_id="c-harm:jan:GLAD-L")
    attach, rc = _attach(state, _gee([]), sync_layers)
    try:
        with writing("q"):
            asyncio.run(attach(_new_pick()))
        q = state.project.value
        assert q.aoi.digest == aoi_io.geometry_digest(old) and set(q.artifacts) == {"harm:jan:GLAD-L", "mask:forest"}
        assert (paths.project_dir("q") / "masks/forest.tif").exists() and sync_layers.is_on("src:GLAD-L")
        side = gpd.read_file(paths.project_dir("q") / paths.AOI_SIDECAR)
        assert aoi_io.geometry_digest(side) == aoi_io.geometry_digest(old)
        assert _toasts("warning") == [t("steps.aoi.busy")] and _toasts("success") == []
    finally:
        rc.close()


def test_an_aoi_change_is_refused_when_a_job_starts_while_the_sidecar_is_staged(data_root, no_aoiview,
                                                                                sync_layers, monkeypatch):
    """The check runs again under the state lock: a job that set its mark
    after the first check still wins, and the staged sidecar is dropped."""
    p, old = _project_with_files()
    state = AppState()
    state.load_project_state(p)
    real_update = state.update_project

    def _job_started(name, fn):
        with writing(name):
            return real_update(name, fn)

    monkeypatch.setattr(state, "update_project", _job_started)
    attach, rc = _attach(state, _gee([]), sync_layers)
    try:
        asyncio.run(attach(_new_pick()))
        assert state.project.value.aoi.digest == aoi_io.geometry_digest(old)
        assert set(state.project.value.artifacts) == {"harm:jan:GLAD-L", "mask:forest"}
        assert (paths.project_dir("q") / "masks/forest.tif").exists()
        assert not list(paths.project_dir("q").glob(".aoi.geojson.*.tmp"))
        assert _toasts("warning") == [t("steps.aoi.busy")]
    finally:
        rc.close()


def test_the_aoi_bounds_are_measured_off_the_event_loop(data_root, no_aoiview, monkeypatch):
    """Review B M10: the reprojection of a national AOI's bounds is slow; it
    must not run on the event loop as an argument of ``to_thread``."""
    threads = []
    real_bounds = aoi_io.gdf_bounds
    monkeypatch.setattr(aoi_io, "gdf_bounds", lambda gdf: (threads.append(threading.current_thread()),
                                                           real_bounds(gdf))[1])
    state = AppState()
    attach, rc = _attach(state, _gee([]))
    try:
        asyncio.run(attach(_new_pick()))
        assert threads and threading.main_thread() not in threads
        assert state.project.value is not None
    finally:
        rc.close()
