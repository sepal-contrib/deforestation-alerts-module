import asyncio
from datetime import datetime
from types import SimpleNamespace

import reacton
from pysepal.solara.notifications import NotificationProvider

from deforestation_alerts.engine.fingerprint import AOI_KEY
from deforestation_alerts.project import cleanup, paths
from deforestation_alerts.project.models import (
    AoiRecord, Artifact, BandMap, NativeGrid, Project, add_custom_source,
)
from gui.i18n import t
from gui.scripts import remote_facts as rf
from gui.store.state_manager import AppState


def _p(digest="d1"):
    p = Project(name="rf", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest=digest, bounds=(-55.1, -10.1, -55.0, -10.0),
                      geometry_file=paths.AOI_SIDECAR)
    return p


def _add_file(p):
    add_custom_source(p, name="tif", origin="file", ref="/x.tif", asset_type="FILE", format="radd",
                      date_encoding="yyddd", band_map=BandMap(alert=1, date=2),
                      native=NativeGrid(crs="EPSG:4326", scale=10.0, bounds=(-56, -11, -55, -10)))


def test_coverage_needed_once_per_session_for_predefined_and_when_stale_for_custom(cover):
    p = _p()
    _add_file(p)
    assert rf.coverage_needed(p, frozenset()) == (("GLAD-L", "RADD", "GLAD-S2"), ("tif",))
    refreshed = frozenset({("d1", "GLAD-L"), ("d1", "RADD"), ("d1", "GLAD-S2")})
    # another project with this AOI was refreshed: this one still has no records
    assert rf.coverage_needed(p, refreshed) == (("GLAD-L", "RADD", "GLAD-S2"), ("tif",))
    cover(p, "GLAD-L", "RADD", "GLAD-S2")
    assert rf.coverage_needed(p, frozenset()) == (("GLAD-L", "RADD", "GLAD-S2"), ("tif",))
    assert rf.coverage_needed(p, refreshed) == ((), ("tif",))
    cover(p, "tif")
    assert rf.coverage_needed(p, refreshed) == ((), ())
    p.aoi = p.aoi.model_copy(update={"digest": "d2"})  # another AOI: every record is stale
    assert rf.coverage_needed(p, refreshed) == (("GLAD-L", "RADD", "GLAD-S2"), ("tif",))
    assert rf.coverage_needed(None, frozenset()) == ((), ())


def test_the_aoi_geometry_comes_from_the_selection_of_this_aoi_or_the_sidecar():
    """The AOI step publishes a changed AOI before it sets ``aoi_result``: a
    selection of another AOI is never used."""
    admin = lambda code: SimpleNamespace(name="a", method="ADMIN1", admin=code, spec=None)  # noqa: E731
    p = _p()
    p.aoi = p.aoi.model_copy(update={"method": "ADMIN1", "digest": "admin:1", "geometry_file": None})
    assert rf.aoi_origin(p, None) is None
    assert rf.aoi_origin(p, admin("1")) == "result"
    assert rf.aoi_origin(p, admin("2")) is None  # the previous AOI's selection
    p.aoi = p.aoi.model_copy(update={"geometry_file": paths.AOI_SIDECAR})
    assert rf.aoi_origin(p, admin("2")) == "sidecar" and rf.aoi_origin(p, admin("1")) == "result"
    drawn = _p()  # a local AOI: its selection does not name it, the sidecar does
    assert rf.aoi_origin(drawn, SimpleNamespace(name="d", method="DRAW", admin=None, spec=None)) == "sidecar"
    assert rf.aoi_origin(None, None) is None


def test_the_sidecar_is_needed_only_for_an_earth_engine_aoi_with_file_inputs():
    p = _p()
    assert not rf.sidecar_needed(p)
    p.aoi = p.aoi.model_copy(update={"geometry_file": None, "method": "ADMIN1"})
    assert not rf.sidecar_needed(p)
    _add_file(p)
    assert rf.has_file_inputs(p) and rf.sidecar_needed(p)


def _render(state, gee):
    @reacton.component
    def Page():
        NotificationProvider()
        rf.RemoteFactsLoader(app_state=state, gee_interface=gee)

    return reacton.render(Page(), handle_error=False)


def test_the_loader_saves_the_coverage_of_the_aoi(data_root, monkeypatch):
    raw = {k: {"fraction": 1.0, "regions": [{"name": k, "fraction": 1.0, "last": "2026-09-30"}]}
           for k in ("GLAD-L", "RADD", "GLAD-S2")}
    raw["GLAD-L"]["bands"] = ["conf25", "conf26"]
    asked = []

    async def get_info_async(obj):
        asked.append(obj)
        return {"bands": ["conf26"]} if obj == "META" else raw

    monkeypatch.setattr(rf, "coverage_request", lambda aoi, pre, custom: ("COV", pre, tuple(custom)))
    monkeypatch.setattr(rf.catalogue, "meta_request", lambda key: "META")
    monkeypatch.setattr(rf.aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    state = AppState()
    state.load_project_state(_p())

    async def main():
        box, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            for _ in range(200):  # the tasks run on this loop
                await asyncio.sleep(0.01)
                if state.project.value.coverage and state.catalogue_meta.value:
                    break
            p = state.project.value
            assert set(p.coverage) == {"GLAD-L", "RADD", "GLAD-S2"} and p.coverage["RADD"].aoi_digest == "d1"
            assert state.coverage_refreshed.value == {("d1", k) for k in ("GLAD-L", "RADD", "GLAD-S2")}
            assert state.catalogue_meta.value == {"GLAD-L": {"bands": ["conf26"]}}
            assert state.coverage_error.value is None and state.coverage_pending.value is False
        finally:
            rc.close()

    asyncio.run(main())  # rendered inside the loop, as tests/test_project_dialog.py does


def test_the_loader_fetches_the_sidecar_of_an_earth_engine_aoi_again_after_an_aoi_change(data_root, monkeypatch):
    """Spec §13: an admin AOI with a GeoTIFF input gets its ``aoi.geojson``
    from Earth Engine, staged then moved into place, no ``.tmp`` left; after
    an AOI change to another admin AOI it is fetched again."""
    from gui.scripts import aoi_io

    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {
        "type": "Polygon", "coordinates": [[[-55.1, -10.1], [-55.0, -10.1], [-55.0, -10.0], [-55.1, -10.0],
                                            [-55.1, -10.1]]]}}]}
    raw = {k: {"fraction": 1.0, "regions": [{"name": k, "fraction": 1.0}]} for k in ("GLAD-L", "RADD", "GLAD-S2", "tif")}
    sidecar_asks = []

    async def get_info_async(obj):
        if obj == "SIDE":
            sidecar_asks.append(obj)
            return fc
        return {"bands": ["conf26"]} if obj == "META" else raw

    monkeypatch.setattr(rf, "coverage_request", lambda aoi, pre, custom: ("COV", pre, tuple(c.name for c in custom)))
    monkeypatch.setattr(rf.catalogue, "meta_request", lambda key: "META")
    monkeypatch.setattr(rf.aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    monkeypatch.setattr(rf.aoi_io, "sidecar_request", lambda aoi: "SIDE")
    p = _p()
    p.aoi = p.aoi.model_copy(update={"method": "ADMIN1", "geometry_file": None, "digest": "admin:1"})
    _add_file(p)
    pdir = paths.project_dir(p.name)
    state = AppState()
    state.load_project_state(p)
    state.aoi_result.set(SimpleNamespace(name="admin", method="ADMIN1", admin="1", spec=None))

    async def settle(cond, rounds=300):
        for _ in range(rounds):
            await asyncio.sleep(0.01)
            if cond():
                return True
        return False

    def has_sidecar():
        return state.project.value.aoi.geometry_file == paths.AOI_SIDECAR

    async def main():
        box, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await settle(has_sidecar)
            assert (pdir / paths.AOI_SIDECAR).exists() and not list(pdir.glob(f".{paths.AOI_SIDECAR}.*.tmp"))
            assert await settle(lambda: set(state.project.value.coverage) == {"GLAD-L", "RADD", "GLAD-S2", "tif"})
            # the AOI raster and a row's raw file of this AOI, as a prepare run leaves them
            olds = {AOI_KEY: "masks/aoi.tif", "raw:jan:tif": "periods/jan/raw/tif.tif"}
            state.update_project(p.name, lambda q: setattr(q, "artifacts", {**q.artifacts, **{
                k: Artifact(key=k, kind="aoi_mask" if k == AOI_KEY else "raw_alert", rel_path=rel, fingerprint="f",
                            inputs={}, content_id=k, created_at=datetime.now()) for k, rel in olds.items()}}))
            for rel in olds.values():
                (pdir / rel).parent.mkdir(parents=True, exist_ok=True)
                (pdir / rel).write_bytes(b"x")
            new = p.aoi.model_copy(update={"digest": "admin:2", "geometry_file": None})
            gone = []

            def _change(q):  # the AOI step's change to another admin AOI (Tasks 5 and 8)
                gone.extend(aoi_io.apply_aoi(q, new))
                (pdir / paths.AOI_SIDECAR).unlink(missing_ok=True)

            state.update_project(p.name, _change)
            cleanup.delete_files(pdir, gone)  # as the AOI step does once the change is published
            assert not has_sidecar() and state.project.value.artifacts == {}
            assert not any((pdir / rel).exists() for rel in olds.values())
            # the AOI step sets the new selection only after the change: the
            # previous AOI's selection must not be fetched as this AOI's
            # sidecar, nor checked as its coverage
            assert not await settle(has_sidecar, rounds=30) and len(sidecar_asks) == 1
            assert {c.aoi_digest for c in state.project.value.coverage.values()} == {"admin:1"}
            state.aoi_result.set(SimpleNamespace(name="admin", method="ADMIN1", admin="2", spec=None))
            assert await settle(has_sidecar)
            assert len(sidecar_asks) == 2 and (pdir / paths.AOI_SIDECAR).exists()
            assert not list(pdir.glob(f".{paths.AOI_SIDECAR}.*.tmp"))
            assert await settle(lambda: {c.aoi_digest for c in state.project.value.coverage.values()} == {"admin:2"})
        finally:
            rc.close()

    asyncio.run(main())


def test_a_failed_check_is_reported_and_check_again_retries_it(data_root, monkeypatch):
    """Spec §6.2 "Failure": the error is kept for the rows and toasted; the
    "Check again" counter runs the check again."""
    from pysepal.solara.notifications.bus import get_current_bus

    calls = []

    async def get_info_async(obj):
        calls.append(obj)
        if obj == "META":
            return {"bands": ["conf26"]}
        if len(calls) <= 2:
            raise RuntimeError("Earth Engine is down")
        return {k: {"fraction": 1.0, "regions": [{"name": k, "fraction": 1.0}]} for k in ("GLAD-L", "RADD", "GLAD-S2")}

    monkeypatch.setattr(rf, "coverage_request", lambda aoi, pre, custom: ("COV", pre))
    monkeypatch.setattr(rf.catalogue, "meta_request", lambda key: "META")
    monkeypatch.setattr(rf.aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    state = AppState()
    state.load_project_state(_p())

    async def settle(cond):
        for _ in range(200):
            await asyncio.sleep(0.01)
            if cond():
                return True
        return False

    async def main():
        box, rc = _render(state, SimpleNamespace(get_info_async=get_info_async))
        try:
            assert await settle(lambda: state.coverage_error.value is not None)
            assert state.coverage_error.value == "Earth Engine is down" and state.coverage_pending.value is False
            assert state.project.value.coverage == {}
            toasts = [x.message for x in get_current_bus().toasts.value if x.type.value == "error"]
            assert toasts == [t("coverage.check_failed", error="Earth Engine is down")]
            state.coverage_retry.set(state.coverage_retry.value + 1)
            assert await settle(lambda: set(state.project.value.coverage) == {"GLAD-L", "RADD", "GLAD-S2"})
            assert state.coverage_error.value is None
        finally:
            rc.close()

    asyncio.run(main())
