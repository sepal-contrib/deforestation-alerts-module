"""Visualization layers (Task 30): the helpers and builders of
``gui.scripts.vis_layers``, the modal's validation and form, and the Optional
layers step, rendered with its table, modal and confirmation swapped for
recorders (the pattern of tests/test_alerts_step.py)."""

import asyncio
import threading
from datetime import date, datetime
from types import SimpleNamespace

import ipyvuetify as v
import numpy as np
import pytest
import rasterio
import reacton
import solara
from affine import Affine
from pysepal.solara.notifications import NotificationProvider

from deforestation_alerts.project.models import (
    AoiRecord, FileVisLayer, GeeVisLayer, NicfiVisLayer, Period, Project,
)
from gui.i18n import t
from gui.scripts import vis_layers as vl
from gui.scripts.map_layers import MapLayers
from gui.store.state_manager import AppState
from gui.store.workflow_steps import STEPS, StepStatus, step_states
from gui.tile import layers_step
from gui.widget import vis_layer_modal as vlm


def _p():
    return Project(name="v", created_at=datetime.now(), updated_at=datetime.now())


@pytest.mark.parametrize("bounds, region", [((-60, -10, -55, -5), "americas"), ((20, -5, 25, 0), "africa"),
                                            ((100, -5, 110, 5), "asia")])
def test_nicfi_region_from_the_aoi_centroid(bounds, region):
    assert vl.nicfi_region(bounds) == region


def test_nicfi_month():
    p = _p()
    assert vl.nicfi_month(NicfiVisLayer(name="n", month="2025-07"), p, date(2026, 10, 1)) == "2025-07"
    assert vl.nicfi_month(NicfiVisLayer(name="n"), p, date(2026, 10, 1)) == "2026-09"
    per = Period(id="x", start=date(2026, 3, 1), end=date(2026, 4, 30))
    p.periods, p.active_period = {"x": per}, "x"
    assert vl.nicfi_month(NicfiVisLayer(name="n"), p, date(2026, 10, 1)) == "2026-04"


def test_ee_vis_params():
    assert vl.ee_vis_params(GeeVisLayer(name="a", asset="x", bands=["B4", "B3", "B2"], min=0, max=3000)) == {
        "bands": ["B4", "B3", "B2"], "min": 0.0, "max": 3000.0}
    assert vl.ee_vis_params(GeeVisLayer(name="a", asset="x", bands=["NDVI"], min=0, max=1, palette=["ff0000"])) == {
        "bands": ["NDVI"], "min": 0.0, "max": 1.0, "palette": ["ff0000"]}


def test_a_read_only_folder_is_refused(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    (ro / "x.tif").write_bytes(b"x")
    ro.chmod(0o555)
    try:
        assert not vl.folder_writable(ro / "x.tif")
        assert vl.folder_writable(tmp_path / "x.tif")
    finally:
        ro.chmod(0o755)


def test_names_are_unique_and_short():
    p = _p()
    p.vis_layers = {"S2": GeeVisLayer(name="S2", asset="x")}
    assert vl.validate_name("s2", p) == "steps.layers.name_duplicate"
    assert vl.validate_name("", p) == "steps.layers.name_empty"
    assert vl.validate_name("x" * 25, p) == "steps.layers.name_too_long"
    assert vl.validate_name("S2", p, current="S2") is None


def test_the_layers_step_is_third_and_counts_layers():
    assert [s.key for s in STEPS] == ["aoi", "alerts", "layers", "period", "prepare", "integrate", "analysis"]
    p = _p()
    assert dict(zip([s.key for s in STEPS], step_states(p)))["layers"] is StepStatus.LOCKED
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="d", bounds=(0, 0, 1, 1))
    p.vis_layers = {"t": FileVisLayer(name="t", path="/x.tif")}
    assert dict(zip([s.key for s in STEPS], step_states(p)))["layers"] is StepStatus.HAS_OUTPUTS


# ---------------------------------------------------------------- helpers shared below
PARAGUAY = (-55.5, -24.5, -55.4, -24.4)


def _with_aoi(bounds=PARAGUAY):
    p = _p()
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="d", bounds=bounds)
    return p


def _tif(path, size=600, count=1, dtype="uint8"):
    with rasterio.open(path, "w", driver="GTiff", width=size, height=size, count=count, dtype=dtype,
                       crs="EPSG:4326", transform=Affine(0.0002, 0, -55.5, 0, -0.0002, -24.4)) as dst:
        dst.write(np.zeros((count, size, size), dtype))
    return path


class _FakeMap:
    def __init__(self):
        self.layers, self.removed, self.ee_calls = {}, [], []

    def add_layer(self, layer, key=""):
        self.layers[key] = layer

    def remove_layer(self, key, none_ok=False):
        self.removed.append(key)
        self.layers.pop(key, None)

    def add_ee_layer(self, img, vis, *, name, key, shown=True):
        self.ee_calls.append({"img": img, "vis": vis, "name": name, "key": key, "shown": shown})
        self.layers[key] = "ee"


class _Notifier:
    def __init__(self):
        self.errors = []

    def error(self, text):
        self.errors.append(text)


def _sync_spawn(monkeypatch):
    """Run MapLayers builds on the calling thread."""
    import gui.scripts.map_layers as ml

    def spawn(target, args=(), **kw):
        target(*args)
        return threading.current_thread()

    monkeypatch.setattr(ml, "spawn_in_context", spawn)


# ---------------------------------------------------------------- builders
def test_a_geotiff_layer_gets_its_nearest_ovr_and_is_drawn_with_its_colormap(tmp_path, monkeypatch):
    tif = _tif(tmp_path / "t.tif")
    calls = []
    monkeypatch.setattr(vl.raster_map, "add_raster_layer", lambda map_, path, **kw: calls.append((path, kw)))
    layer = FileVisLayer(name="t", path=str(tif), band=1, colormap="magma", min=0, max=45)
    vl.builder(_FakeMap(), layer, "vis:t", project=_with_aoi(), aoi_bounds=PARAGUAY, shown=False)()
    assert (tmp_path / "t.tif.ovr").exists()
    [(path, kw)] = calls
    assert path == str(tif) and kw["key"] == "vis:t" and kw["name"] == "t" and kw["band"] == 1
    assert kw["colormap"].name == "magma" and (kw["vmin"], kw["vmax"]) == (0, 45) and kw["visible"] is False


def test_a_geotiff_in_a_read_only_folder_is_refused_with_a_toast_naming_the_folder(tmp_path, monkeypatch):
    """Spec §9.1: the build fails with translated text; MapLayers shows it as
    the toast and leaves the key off."""
    _sync_spawn(monkeypatch)
    ro = tmp_path / "ro"
    ro.mkdir()
    tif = _tif(ro / "t.tif")
    ro.chmod(0o555)
    try:
        layer = FileVisLayer(name="t", path=str(tif))
        with pytest.raises(PermissionError) as exc:
            vl.builder(_FakeMap(), layer, "vis:t", project=_with_aoi(), aoi_bounds=PARAGUAY)()
        assert str(exc.value) == t("steps.layers.err_folder", folder=str(ro)) and str(ro) in str(exc.value)
        layers, notifier = MapLayers(_FakeMap()), _Notifier()
        layers.show("vis:t", vl.builder(layers.map_, layer, "vis:t", project=_with_aoi(), aoi_bounds=PARAGUAY),
                    notifier=notifier)
        assert notifier.errors == [str(exc.value)] and layers.on.value == frozenset()
        assert not (ro / "t.tif.ovr").exists()
    finally:
        ro.chmod(0o755)


def test_a_missing_geotiff_is_refused_naming_the_file(tmp_path):
    layer = FileVisLayer(name="t", path=str(tmp_path / "gone.tif"))
    with pytest.raises(FileNotFoundError) as exc:
        vl.builder(_FakeMap(), layer, "vis:t", project=_with_aoi(), aoi_bounds=PARAGUAY)()
    assert str(exc.value) == t("steps.layers.err_missing", path=str(tmp_path / "gone.tif"))


def test_earth_engine_and_nicfi_builders(gee):
    map_ = _FakeMap()
    p = _with_aoi()
    p.periods, p.active_period = {"x": Period(id="x", start=date(2026, 3, 1), end=date(2026, 4, 30))}, "x"
    s2 = GeeVisLayer(name="ndvi", asset="COPERNICUS/S2_SR_HARMONIZED/20200101T135109_20200101T135546_T21JWN",
                     bands=["B8"], min=0, max=1, palette=["ffffff", "00ff00"])
    vl.builder(map_, s2, "vis:ndvi", project=p, aoi_bounds=PARAGUAY, shown=False)()
    vl.builder(map_, NicfiVisLayer(name="planet"), "vis:planet", project=p, aoi_bounds=PARAGUAY)()
    first, second = map_.ee_calls
    assert first["vis"] == {"bands": ["B8"], "min": 0.0, "max": 1.0, "palette": ["ffffff", "00ff00"]}
    assert (first["key"], first["name"], first["shown"]) == ("vis:ndvi", "ndvi", False)
    assert second["vis"] == vl.NICFI_VIS and (second["key"], second["shown"]) == ("vis:planet", True)
    serialized = second["img"].serialize()
    assert vl.NICFI_COLLECTIONS["americas"] in serialized and "2026-04-01" in serialized


def test_the_details_cell():
    assert vl.vis_layers_summary(GeeVisLayer(name="a", asset="x", bands=["B4", "B3", "B2"], min=0, max=3000)) == (
        "B4, B3, B2 · 0–3000")
    assert vl.vis_layers_summary(FileVisLayer(name="t", path="/x.tif", colormap="viridis", min=0, max=45)) == (
        "band 1 · viridis · 0–45")
    assert vl.vis_layers_summary(NicfiVisLayer(name="n", month="2025-07")) == "2025-07"
    assert vl.vis_layers_summary(NicfiVisLayer(name="n")) == t("steps.layers.nicfi_period_month")


# ---------------------------------------------------------------- validation
def _gee_answering(answer):
    calls = []

    async def get_info_async(obj):
        calls.append(obj)
        return answer(obj)

    return SimpleNamespace(get_info_async=get_info_async), calls


@pytest.fixture
def requests(monkeypatch):
    monkeypatch.setattr(vlm, "band_list_request", lambda ref, kind: ("BANDS", ref, kind))
    monkeypatch.setattr(vl, "nicfi_access_request", lambda region: ("NICFI", region))


def _check(gee_interface, project, **kw):
    return asyncio.run(vlm.validate_vis_layer(gee_interface, project, **kw))


def test_an_earth_engine_image_with_one_band_and_a_palette(requests):
    gee, calls = _gee_answering(lambda obj: ["B4", "B8"])
    out = _check(gee, _with_aoi(), kind="gee", name=" ndvi ", ref="users/x/img", asset_type="IMAGE", band="B8",
                 vmin="0", vmax="0.8", palette="#ffffff, 00ff00", show_in_analysis=True)
    assert out["layer"] == GeeVisLayer(name="ndvi", asset="users/x/img", bands=["B8"], min=0, max=0.8,
                                       palette=["ffffff", "00ff00"], show_in_analysis=True)
    assert calls == [("BANDS", "users/x/img", "IMAGE")]


def test_an_rgb_image_drops_the_palette_and_checks_every_band(requests):
    gee, _ = _gee_answering(lambda obj: ["B4", "B3", "B2"])
    out = _check(gee, _with_aoi(), kind="gee", name="rgb", ref="users/x/img", asset_type="IMAGE", rgb=True,
                 bands=("B4", "B3", "B2"), vmin="0", vmax="3000", palette="not used")
    assert out["layer"].bands == ["B4", "B3", "B2"] and out["layer"].palette == []
    gee, _ = _gee_answering(lambda obj: ["B4", "B3"])
    assert _check(gee, _with_aoi(), kind="gee", name="rgb", ref="users/x/img", asset_type="IMAGE", rgb=True,
                  bands=("B4", "B3", "B2"), vmin="0", vmax="3000") == {"error": "steps.layers.err_bands"}


@pytest.mark.parametrize("over, key", [
    ({"asset_type": "IMAGE_COLLECTION"}, "steps.layers.err_wrong_type"),
    ({"ref": None}, "steps.layers.err_incomplete"),
    ({"band": None}, "steps.layers.err_incomplete"),
    ({"rgb": True, "bands": ("B4", None, "B2")}, "steps.layers.err_incomplete"),
    ({"vmin": "1", "vmax": "1"}, "steps.layers.err_range"),
    ({"vmin": "", "vmax": "1"}, "steps.layers.err_range"),
    ({"palette": "ff0000, red"}, "steps.layers.err_palette"),
    ({"name": ""}, "steps.layers.name_empty"),
])
def test_an_incomplete_earth_engine_layer_is_refused_before_any_request(requests, over, key):
    gee, calls = _gee_answering(lambda obj: ["B4", "B3", "B2"])
    kw = dict(kind="gee", name="x", ref="users/x/img", asset_type="IMAGE", band="B4", vmin="0", vmax="1")
    kw.update(over)
    assert _check(gee, _with_aoi(), **kw) == {"error": key} and calls == []


def test_a_geotiff_layer(tmp_path, requests):
    tif = _tif(tmp_path / "h.tif", size=10, count=2)
    gee, calls = _gee_answering(lambda obj: None)
    out = _check(gee, _with_aoi(), kind="file", name="height", ref=str(tif), band=2, colormap="terrain",
                 vmin="0", vmax="45")
    assert out["layer"] == FileVisLayer(name="height", path=str(tif), band=2, colormap="terrain", min=0, max=45)
    assert calls == []
    assert _check(gee, _with_aoi(), kind="file", name="height", ref=str(tif), band=3, vmin="0", vmax="1") == {
        "error": "steps.alerts.err_missing_band"}
    (tmp_path / "bad.tif").write_text("not a tiff")
    assert _check(gee, _with_aoi(), kind="file", name="height", ref=str(tmp_path / "bad.tif"), band=1, vmin="0",
                  vmax="1") == {"error": "steps.alerts.err_unreadable"}


def test_a_geotiff_in_a_read_only_folder_is_refused_inline(tmp_path, requests):
    ro = tmp_path / "ro"
    ro.mkdir()
    tif = _tif(ro / "h.tif", size=10)
    ro.chmod(0o555)
    try:
        gee, _ = _gee_answering(lambda obj: None)
        out = _check(gee, _with_aoi(), kind="file", name="h", ref=str(tif), band=1, vmin="0", vmax="1")
        assert out == {"error": "steps.layers.err_folder", "args": {"folder": str(ro)}}
    finally:
        ro.chmod(0o755)


def test_nicfi_checks_the_access_of_the_aois_region(requests):
    gee, calls = _gee_answering(lambda obj: 1)
    out = _check(gee, _with_aoi((20, -5, 25, 0)), kind="nicfi", name="planet", month="", show_in_analysis=True)
    assert out["layer"] == NicfiVisLayer(name="planet", month=None, show_in_analysis=True)
    assert calls == [("NICFI", "africa")]
    out = _check(gee, _with_aoi(), kind="nicfi", name="planet", month="2025-07")
    assert out["layer"].month == "2025-07" and calls[-1] == ("NICFI", "americas")
    assert _check(gee, _with_aoi(), kind="nicfi", name="planet", month="2025-13") == {
        "error": "steps.layers.err_month"}


def test_nicfi_without_access_is_refused_naming_the_region(requests):
    import ee

    def no_access(obj):
        raise ee.EEException("ImageCollection.load: ImageCollection asset 'projects/planet-nicfi/assets/"
                             "basemaps/asia' not found (does not exist or caller does not have access).")

    gee, _ = _gee_answering(no_access)
    out = _check(gee, _with_aoi((100, -5, 110, 5)), kind="nicfi", name="planet")
    assert out == {"error": "steps.layers.err_nicfi", "args": {"region": t("steps.layers.region_asia")}}
    gee, _ = _gee_answering(lambda obj: 0)
    assert _check(gee, _with_aoi(), kind="nicfi", name="planet")["error"] == "steps.layers.err_nicfi"

    def offline(obj):
        raise ee.EEException("Connection reset")

    gee, _ = _gee_answering(offline)
    with pytest.raises(ee.EEException):  # not a refusal: shown as it is
        _check(gee, _with_aoi(), kind="nicfi", name="planet")


# ---------------------------------------------------------------- the modal, rendered
@solara.component
def _StubRasterInput(gee_interface, origin, on_origin, asset, path, asset_types=("IMAGE", "IMAGE_COLLECTION"),
                     disabled=False, show_origin=True):
    """Stands in for pysepal's asset and file pickers: the test sets ``path``."""
    _StubRasterInput.seen = SimpleNamespace(origin=origin, asset=asset, path=path, asset_types=asset_types,
                                            show_origin=show_origin)


def _state(p):
    state = AppState()
    state.project.value = p
    return state


def _modal(state, open_, saved, editing=None):
    return vlm.VisLayerModal(open_=open_, app_state=state, gee_interface=object(),
                             on_save=lambda layer, previous: saved.append((layer, previous)), editing=editing)


async def _settle(cond, rounds=300):
    for _ in range(rounds):
        await asyncio.sleep(0.01)
        if cond():
            return True
    return False


def _field(rc, key, cls=v.TextField):
    return rc.find(cls, label=t(key)).widgets[0]


def _save_btn(rc):
    [btn] = [b for b in rc.find(v.Btn).widgets if t("common.save") in b.children]
    return btn


def _texts(rc):
    return " ".join(str(w.children) for w in rc.find(v.Html).widgets)


def test_a_geotiff_layer_is_saved_inside_the_task_and_the_modal_closes(tmp_path, monkeypatch):
    tif = _tif(tmp_path / "h.tif", size=10, count=2)
    monkeypatch.setattr(vlm, "RasterInput", _StubRasterInput)
    state, open_, saved = _state(_with_aoi()), solara.reactive(True), []

    async def main():
        box, rc = reacton.render(_modal(state, open_, saved), handle_error=False)
        try:
            _field(rc, "steps.layers.field_kind", v.Select).v_model = "file"
            _field(rc, "steps.layers.field_name").v_model = "height"
            assert _StubRasterInput.seen.origin == "file" and _StubRasterInput.seen.show_origin is False
            assert _StubRasterInput.seen.asset_types == ("IMAGE",)
            _StubRasterInput.seen.path.set(str(tif))
            assert await _settle(lambda: len(_field(rc, "steps.layers.field_band", v.Select).items) == 2)
            _field(rc, "steps.layers.field_band", v.Select).v_model = 2
            _field(rc, "steps.layers.field_colormap", v.Select).v_model = "terrain"
            _field(rc, "steps.layers.field_max").v_model = "45"
            _field(rc, "steps.layers.field_show_in_analysis", v.Checkbox).v_model = True
            _save_btn(rc).fire_event("click", None)
            assert await _settle(lambda: open_.value is False)
            assert saved == [(FileVisLayer(name="height", path=str(tif), band=2, colormap="terrain", min=0, max=45,
                                           show_in_analysis=True), None)]
        finally:
            rc.close()

    asyncio.run(main())


def test_an_edit_opens_with_the_layers_values(monkeypatch):
    monkeypatch.setattr(vlm, "RasterInput", _StubRasterInput)
    p = _with_aoi()
    p.vis_layers = {"rgb": GeeVisLayer(name="rgb", asset="users/x/img", bands=["B4", "B3", "B2"], min=0, max=3000)}
    state, open_, saved = _state(p), solara.reactive(True), []

    async def main():
        box, rc = reacton.render(_modal(state, open_, saved, editing="rgb"), handle_error=False)
        try:
            assert t("steps.layers.edit_title", name="rgb") in _texts(rc)
            assert _field(rc, "steps.layers.field_name").v_model == "rgb"
            assert _field(rc, "steps.layers.field_rgb", v.Switch).v_model is True
            assert [_field(rc, f"steps.layers.field_{c}", v.Select).v_model for c in ("red", "green", "blue")] == [
                "B4", "B3", "B2"]
            assert _field(rc, "steps.layers.field_max").v_model == "3000"
            assert _StubRasterInput.seen.asset.value["asset_id"] == "users/x/img"
            assert not rc.find(v.TextField, label=t("steps.layers.field_palette")).widgets  # RGB: no palette
        finally:
            rc.close()

    asyncio.run(main())


def test_a_refused_layer_shows_translated_text_inline_and_can_be_sent_again(monkeypatch):
    asked = []

    async def fake_validate(gee_interface, project, **kw):
        asked.append(kw)
        return {"error": "steps.layers.err_folder", "args": {"folder": "/home/me/ro"}}

    monkeypatch.setattr(vlm, "RasterInput", _StubRasterInput)
    monkeypatch.setattr(vlm, "validate_vis_layer", fake_validate)
    state, open_, saved = _state(_with_aoi()), solara.reactive(True), []

    async def main():
        box, rc = reacton.render(_modal(state, open_, saved), handle_error=False)
        try:
            _field(rc, "steps.layers.field_name").v_model = "h"
            _save_btn(rc).fire_event("click", None)
            shown = t("steps.layers.err_folder", folder="/home/me/ro")
            assert await _settle(lambda: shown in _texts(rc))
            assert open_.value is True and saved == []
            _save_btn(rc).fire_event("click", None)  # re-armed
            assert await _settle(lambda: len(asked) == 2)
        finally:
            rc.close()

    asyncio.run(main())


def test_what_on_save_raises_is_shown_inline(monkeypatch):
    async def fake_validate(gee_interface, project, **kw):
        return {"layer": NicfiVisLayer(name=kw["name"])}

    def on_save(layer, previous):
        raise ValueError(t("steps.layers.name_duplicate"))

    monkeypatch.setattr(vlm, "validate_vis_layer", fake_validate)
    state, open_ = _state(_with_aoi()), solara.reactive(True)

    async def main():
        box, rc = reacton.render(vlm.VisLayerModal(open_=open_, app_state=state, gee_interface=object(),
                                                   on_save=on_save), handle_error=False)
        try:
            _field(rc, "steps.layers.field_kind", v.Select).v_model = "nicfi"
            assert t("steps.layers.region", region=t("steps.layers.region_americas")) in _texts(rc)
            _field(rc, "steps.layers.field_name").v_model = "planet"
            _save_btn(rc).fire_event("click", None)
            assert await _settle(lambda: t("steps.layers.name_duplicate") in _texts(rc))
            assert open_.value is True
        finally:
            rc.close()

    asyncio.run(main())


def _hanging(monkeypatch):
    asked, done, release = [], [], asyncio.Event()

    async def fake_validate(gee_interface, project, **kw):
        asked.append(kw)
        await release.wait()
        done.append(kw)
        return {"layer": NicfiVisLayer(name=kw["name"])}

    monkeypatch.setattr(vlm, "RasterInput", _StubRasterInput)
    monkeypatch.setattr(vlm, "validate_vis_layer", fake_validate)
    return asked, done, release


@pytest.mark.parametrize("close", ["esc", "cancel"])
def test_closing_the_dialog_during_a_check_saves_nothing(monkeypatch, close):
    asked, done, release = _hanging(monkeypatch)
    state, open_, saved = _state(_with_aoi()), solara.reactive(True), []

    async def main():
        box, rc = reacton.render(_modal(state, open_, saved), handle_error=False)
        try:
            _field(rc, "steps.layers.field_name").v_model = "late"
            _save_btn(rc).fire_event("click", None)
            assert await _settle(lambda: len(asked) == 1)
            assert _field(rc, "steps.layers.field_name").disabled is True  # frozen while checked
            assert _field(rc, "steps.layers.field_kind", v.Select).disabled is True
            if close == "esc":
                rc.find(v.Dialog).widgets[0].fire_event("keydown.esc", None)
            else:
                [btn] = [b for b in rc.find(v.Btn).widgets if t("common.cancel") in b.children
                         and not any(isinstance(c, v.ProgressCircular) for c in b.children)]
                btn.fire_event("click", None)
            assert open_.value is False
            release.set()
            await asyncio.sleep(0.1)
            assert saved == [] and done == []  # cancelled at its await
        finally:
            rc.close()

    asyncio.run(main())


def test_an_aoi_change_during_the_check_refuses_inline(monkeypatch):
    asked, done, release = _hanging(monkeypatch)
    state, open_, saved = _state(_with_aoi()), solara.reactive(True), []

    async def main():
        box, rc = reacton.render(_modal(state, open_, saved), handle_error=False)
        try:
            _field(rc, "steps.layers.field_name").v_model = "moved"
            _save_btn(rc).fire_event("click", None)
            assert await _settle(lambda: len(asked) == 1)
            state.update_project("v", lambda live: setattr(live, "aoi", live.aoi.model_copy(update={"digest": "d2"})))
            release.set()
            assert await _settle(lambda: t("steps.layers.err_changed") in _texts(rc))
            assert saved == [] and open_.value is True
        finally:
            rc.close()

    asyncio.run(main())


# ---------------------------------------------------------------- the step, rendered
def _stub_children(monkeypatch):
    seen, modal, confirm = {}, {}, {}
    monkeypatch.setattr(layers_step, "ProductTable", lambda **kw: seen.update(kw))
    monkeypatch.setattr(layers_step, "VisLayerModal", lambda **kw: modal.update(kw))
    monkeypatch.setattr(layers_step, "ConfirmDialog", lambda **kw: confirm.update(kw))
    return seen, modal, confirm


@solara.component
def _Harness(app_state, map_, layers):
    NotificationProvider()  # LayersStep's use_notifications() needs a provider rendered first
    layers_step.LayersStep(app_state=app_state, map_=map_, gee_interface=object(), layers=layers)


def _render_step(state, map_, layers):
    return reacton.render(_Harness(app_state=state, map_=map_, layers=layers), handle_error=False)


def _action(seen, name, kind):
    row = next(r for r in seen["rows"] if r["key"] == name)
    return next(a for a in row["actions"] if a["kind"] == kind)


def _layers_project():
    p = _with_aoi()
    p.periods, p.active_period = {"x": Period(id="x", start=date(2026, 3, 1), end=date(2026, 4, 30))}, "x"
    p.vis_layers = {
        "S2": GeeVisLayer(name="S2", asset="x", bands=["B4", "B3", "B2"], min=0, max=3000, show_in_analysis=True),
        "height": FileVisLayer(name="height", path="/x.tif", colormap="terrain", min=0, max=45),
        "planet": NicfiVisLayer(name="planet"),
    }
    return p


def test_the_step_is_locked_without_an_aoi(monkeypatch):
    seen, modal, _ = _stub_children(monkeypatch)
    state = _state(_p())
    box, rc = _render_step(state, _FakeMap(), MapLayers(_FakeMap()))
    try:
        assert seen == {} and t("workflow.lock_layers") in str([w.children for w in rc.find(v.Alert).widgets])
    finally:
        rc.close()


def test_rows_show_kind_details_and_the_analysis_chip(monkeypatch):
    seen, _, _ = _stub_children(monkeypatch)
    state = _state(_layers_project())
    box, rc = _render_step(state, _FakeMap(), MapLayers(_FakeMap()))
    try:
        assert [r["key"] for r in seen["rows"]] == ["height", "planet", "S2"]  # by name, case-folded
        s2 = next(r for r in seen["rows"] if r["key"] == "S2")
        assert [c.get("value") for c in s2["cells"]] == ["S2", t("steps.layers.kind_gee"), "B4, B3, B2 · 0–3000",
                                                         t("steps.layers.in_analysis")]
        assert [a["kind"] for a in s2["actions"]] == ["map_toggle", "edit", "delete"]
        planet = next(r for r in seen["rows"] if r["key"] == "planet")
        assert planet["cells"][2]["value"] == t("steps.layers.nicfi_period_month") and planet["cells"][3] == {
            "value": ""}
    finally:
        rc.close()


def test_the_map_toggle_shows_the_layer_with_its_legend_then_hides_it(monkeypatch):
    _sync_spawn(monkeypatch)
    seen, _, _ = _stub_children(monkeypatch)
    built = []

    def fake_builder(map_, layer, key, *, project, aoi_bounds, shown=True):
        built.append((layer.name, key, aoi_bounds, shown))
        return lambda: map_.add_layer("x", key=key)

    monkeypatch.setattr(layers_step.vis_layers, "builder", fake_builder)
    state, map_ = _state(_layers_project()), _FakeMap()
    layers = MapLayers(map_)
    box, rc = _render_step(state, map_, layers)
    try:
        _action(seen, "planet", "map_toggle")["on_click"]()
        assert built == [("planet", "vis:planet", PARAGUAY, True)] and layers.on.value == {"vis:planet"}
        [legend] = layers.legends.value
        assert legend.layer_id == "vis:planet" and dict(legend.spec.labels[0].args) == {"month": "2026-04"}
        assert _action(seen, "planet", "map_toggle")["is_on"] is True
        _action(seen, "planet", "map_toggle")["on_click"]()
        assert layers.on.value == frozenset() and "vis:planet" in map_.removed and layers.legends.value == ()
    finally:
        rc.close()


def test_save_adds_renames_and_drops_the_old_layer_from_the_map(monkeypatch):
    _sync_spawn(monkeypatch)
    seen, modal, _ = _stub_children(monkeypatch)
    state, map_ = _state(_layers_project()), _FakeMap()
    layers = MapLayers(map_)
    box, rc = _render_step(state, map_, layers)
    try:
        new = NicfiVisLayer(name="mosaic", month="2025-01")
        modal["on_save"](new, None)
        assert state.project.value.vis_layers["mosaic"] == new
        layers.show("vis:height", lambda: map_.add_layer("x", key="vis:height"))
        _action(seen, "height", "edit")["on_click"]()
        assert modal["editing"] == "height" and modal["open_"].value is True
        renamed = FileVisLayer(name="relief", path="/x.tif", colormap="gray")
        modal["on_save"](renamed, "height")
        live = state.project.value.vis_layers
        assert "height" not in live and live["relief"] == renamed
        assert layers.on.value == frozenset() and "vis:height" in map_.removed
        with pytest.raises(ValueError, match=t("steps.layers.name_duplicate")):  # taken meanwhile
            modal["on_save"](NicfiVisLayer(name="s2"), None)
        assert set(state.project.value.vis_layers) == {"S2", "planet", "mosaic", "relief"}
    finally:
        rc.close()


def test_delete_hides_the_layer_and_removes_it(monkeypatch):
    _sync_spawn(monkeypatch)
    seen, _, confirm = _stub_children(monkeypatch)
    state, map_ = _state(_layers_project()), _FakeMap()
    layers = MapLayers(map_)
    box, rc = _render_step(state, map_, layers)
    try:
        layers.show("vis:S2", lambda: map_.add_layer("x", key="vis:S2"))
        _action(seen, "S2", "delete")["on_click"]()
        assert confirm["open"] is True and confirm["title"] == t("steps.layers.delete_title", name="S2")
        confirm["on_confirm"]()
        assert "S2" not in state.project.value.vis_layers and layers.on.value == frozenset()
        assert "vis:S2" in map_.removed and confirm["open"] is False
    finally:
        rc.close()
