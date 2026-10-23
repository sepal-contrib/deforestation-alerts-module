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
import reacton.ipyvuetify as rv
import solara
from affine import Affine
from pysepal.solara.notifications import NotificationProvider

from deforestation_alerts.project.models import (
    AoiRecord, FileVisLayer, GeeVisLayer, Period, Project, VisClass,
)
from gui.i18n import t
from gui.scripts import vis_layers as vl
from gui.scripts import vis_palettes
from gui.scripts.error_text import error_text
from gui.scripts.map_layers import MapLayers
from gui.store.state_manager import AppState
from gui.store.workflow_steps import STEPS, StepStatus, step_states
from gui.tile import layers_step
from gui.widget import vis_layer_modal as vlm
from gui.widget.enter_key import ENTER_DEFAULT


def _p():
    return Project(name="v", created_at=datetime.now(), updated_at=datetime.now())


@pytest.mark.parametrize("bounds, region", [((-60, -10, -55, -5), "americas"), ((20, -5, 25, 0), "africa"),
                                            ((100, -5, 110, 5), "asia")])
def test_nicfi_region_from_the_aoi_centroid(bounds, region):
    assert vl.nicfi_region(bounds) == region


def test_a_saved_nicfi_layer_is_dropped_when_the_project_loads():
    """Planet NICFI was an optional layer kind; it now comes with the Earth
    Engine account, as the timeline's Planet collection."""
    p = _p()
    p.vis_layers = {"s2": GeeVisLayer(name="s2", asset="x", bands=["B4"])}
    data = p.model_dump(mode="json")
    data["vis_layers"]["planet"] = {"kind": "nicfi", "name": "planet", "month": None, "show_in_analysis": True}
    assert set(Project.model_validate(data).vis_layers) == {"s2"}


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
    assert [s.key for s in STEPS] == ["aoi", "alerts", "layers", "period", "prepare", "integrate", "questionnaire",
                                      "evaluators"]
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
    """Spec §9.1: the build fails with a keyed error; MapLayers shows it
    translated as the toast and leaves the key off."""
    _sync_spawn(monkeypatch)
    ro = tmp_path / "ro"
    ro.mkdir()
    tif = _tif(ro / "t.tif")
    ro.chmod(0o555)
    try:
        layer = FileVisLayer(name="t", path=str(tif))
        with pytest.raises(PermissionError) as exc:
            vl.builder(_FakeMap(), layer, "vis:t", project=_with_aoi(), aoi_bounds=PARAGUAY)()
        assert exc.value.key == "steps.layers.err_folder" and exc.value.values == {"folder": str(ro)}
        shown = t("steps.layers.err_folder", folder=str(ro))
        layers, notifier = MapLayers(_FakeMap()), _Notifier()
        layers.show("vis:t", vl.builder(layers.map_, layer, "vis:t", project=_with_aoi(), aoi_bounds=PARAGUAY),
                    notifier=notifier)
        assert notifier.errors == [shown] and str(ro) in shown and layers.on.value == frozenset()
        assert not (ro / "t.tif.ovr").exists()
    finally:
        ro.chmod(0o755)


def test_a_missing_geotiff_is_refused_naming_the_file(tmp_path):
    layer = FileVisLayer(name="t", path=str(tmp_path / "gone.tif"))
    with pytest.raises(FileNotFoundError) as exc:
        vl.builder(_FakeMap(), layer, "vis:t", project=_with_aoi(), aoi_bounds=PARAGUAY)()
    assert exc.value.key == "steps.layers.err_missing" and exc.value.values == {"path": str(tmp_path / "gone.tif")}
    assert error_text(exc.value) == t("steps.layers.err_missing", path=str(tmp_path / "gone.tif"))


def test_the_earth_engine_builder(gee):
    map_ = _FakeMap()
    p = _with_aoi()
    p.periods, p.active_period = {"x": Period(id="x", start=date(2026, 3, 1), end=date(2026, 4, 30))}, "x"
    s2 = GeeVisLayer(name="ndvi", asset="COPERNICUS/S2_SR_HARMONIZED/20200101T135109_20200101T135546_T21JWN",
                     bands=["B8"], min=0, max=1, palette=["ffffff", "00ff00"])
    vl.builder(map_, s2, "vis:ndvi", project=p, aoi_bounds=PARAGUAY, shown=False)()
    [first] = map_.ee_calls
    assert first["vis"] == {"bands": ["B8"], "min": 0.0, "max": 1.0, "palette": ["ffffff", "00ff00"]}
    assert (first["key"], first["name"], first["shown"]) == ("vis:ndvi", "ndvi", False)


def test_the_details_cell():
    assert vl.vis_layers_summary(GeeVisLayer(name="a", asset="x", bands=["B8", "B4", "B3"], min=0, max=3000)) == (
        "R/G/B: B8, B4, B3 · 0–3000")
    ramp = GeeVisLayer(name="a", asset="x", bands=["NDVI"], palette=vis_palettes.ramp_palette("viridis"))
    assert vl.vis_layers_summary(ramp) == f"{t('steps.layers.ramp_viridis')} · 0–1"
    assert vl.vis_layers_summary(GeeVisLayer(name="a", asset="x", bands=["NDVI"], palette=["ff0000"])) == (
        "NDVI · 0–1")  # a palette saved before the ramps: no ramp label
    assert vl.vis_layers_summary(FileVisLayer(name="t", path="/x.tif", colormap="viridis", min=0, max=45)) == (
        "band 1 · viridis · 0–45")  # saved before the ramps: its colormap name
    assert vl.vis_layers_summary(FileVisLayer(name="t", path="/x.tif", band=2, min=0, max=45,
                                              palette=vis_palettes.ramp_palette("greens", True))) == (
        f"band 2 · {t('steps.layers.ramp_greens')} · 0–45")
    assert vl.vis_layers_summary(FileVisLayer(name="t", path="/x.tif", bands=[4, 3, 2], min=0, max=3000)) == (
        "R/G/B: 4, 3, 2 · 0–3000")


def test_a_geotiff_rgb_layer_is_drawn_with_three_bands_and_no_colormap(tmp_path, monkeypatch):
    tif = _tif(tmp_path / "t.tif", count=4)
    calls = []
    monkeypatch.setattr(vl.raster_map, "add_raster_layer", lambda map_, path, **kw: calls.append((path, kw)))
    layer = FileVisLayer(name="t", path=str(tif), bands=[4, 3, 2], min=0, max=3000)
    vl.builder(_FakeMap(), layer, "vis:t", project=_with_aoi(), aoi_bounds=PARAGUAY)()
    [(path, kw)] = calls
    assert kw["band"] == [4, 3, 2] and kw["colormap"] is None and (kw["vmin"], kw["vmax"]) == (0, 3000)


def test_a_geotiff_layer_with_a_palette_is_drawn_with_that_ramp_and_an_old_one_with_its_colormap(tmp_path,
                                                                                                monkeypatch):
    from matplotlib import colormaps
    from matplotlib.colors import LinearSegmentedColormap

    tif = _tif(tmp_path / "t.tif")
    calls = []
    monkeypatch.setattr(vl.raster_map, "add_raster_layer", lambda map_, path, **kw: calls.append(kw))
    palette = vis_palettes.ramp_palette("heat", True)
    vl.builder(_FakeMap(), FileVisLayer(name="t", path=str(tif), palette=palette), "vis:t", project=_with_aoi(),
               aoi_bounds=PARAGUAY)()
    vl.builder(_FakeMap(), FileVisLayer(name="t", path=str(tif), colormap="Greens"), "vis:t", project=_with_aoi(),
               aoi_bounds=PARAGUAY)()
    ramp_kw, legacy_kw = calls
    assert isinstance(ramp_kw["colormap"], LinearSegmentedColormap) and ramp_kw["band"] == 1
    assert ramp_kw["colormap"](0.0) == vis_palettes.ramp_colormap(palette)(0.0)
    assert legacy_kw["colormap"].name == colormaps["Greens"].name == "Greens"


def test_the_raster_map_takes_one_band_or_several(monkeypatch):
    import sys
    import types

    from gui.scripts import raster_map

    seen = []
    fake = types.ModuleType("localtileserver")
    fake.TileClient = lambda path: object()
    fake.get_leaflet_tile_layer = lambda client, **kw: seen.append(kw) or "layer"
    monkeypatch.setitem(sys.modules, "localtileserver", fake)
    map_ = _FakeMap()
    raster_map.add_raster_layer(map_, "/x.tif", key="a", name="a", band=[4, 3, 2], colormap="cmap")
    raster_map.add_raster_layer(map_, "/x.tif", key="b", name="b", band=2, colormap="cmap")
    assert seen[0]["indexes"] == [4, 3, 2] and seen[0]["colormap"] is None
    assert seen[1]["indexes"] == [2] and seen[1]["colormap"] == "cmap"


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


def _check(gee_interface, project, **kw):
    return asyncio.run(vlm.validate_vis_layer(gee_interface, project, **kw))


def test_an_earth_engine_image_with_one_band_and_a_ramp(requests):
    gee, calls = _gee_answering(lambda obj: ["B4", "B8"])
    out = _check(gee, _with_aoi(), kind="gee", name=" ndvi ", ref="users/x/img", asset_type="IMAGE", band="B8",
                 vmin="0", vmax="0.8", ramp="viridis", invert=True, show_in_analysis=True)
    assert out["layer"] == GeeVisLayer(name="ndvi", asset="users/x/img", bands=["B8"], min=0, max=0.8,
                                       palette=vis_palettes.ramp_palette("viridis", True), style="continuous",
                                       show_in_analysis=True)
    assert calls == [("BANDS", "users/x/img", "IMAGE")]


def test_an_rgb_image_drops_the_palette_and_checks_every_band(requests):
    gee, _ = _gee_answering(lambda obj: ["B4", "B3", "B2"])
    out = _check(gee, _with_aoi(), kind="gee", name="rgb", ref="users/x/img", asset_type="IMAGE", style="rgb",
                 bands=("B4", "B3", "B2"), vmin="0", vmax="3000", ramp="viridis")
    assert out["layer"].bands == ["B4", "B3", "B2"] and out["layer"].palette == []
    assert out["layer"].style == "rgb"
    gee, _ = _gee_answering(lambda obj: ["B4", "B3"])
    assert _check(gee, _with_aoi(), kind="gee", name="rgb", ref="users/x/img", asset_type="IMAGE", style="rgb",
                  bands=("B4", "B3", "B2"), vmin="0", vmax="3000") == {"error": "steps.layers.err_bands"}


@pytest.mark.parametrize("over, key", [
    ({"asset_type": "IMAGE_COLLECTION"}, "steps.layers.err_wrong_type"),
    ({"ref": None}, "steps.layers.err_incomplete"),
    ({"band": None}, "steps.layers.err_incomplete"),
    ({"style": "rgb", "bands": ("B4", None, "B2")}, "steps.layers.err_incomplete"),
    ({"vmin": "1", "vmax": "1"}, "steps.layers.err_range"),
    ({"vmin": "", "vmax": "1"}, "steps.layers.err_range"),
    ({"style": "rgb", "bands": ("B4", "B4", "B2")}, "steps.layers.err_duplicate_bands"),  # three distinct bands
    ({"name": ""}, "steps.layers.name_empty"),
])
def test_an_incomplete_earth_engine_layer_is_refused_before_any_request(requests, over, key):
    gee, calls = _gee_answering(lambda obj: ["B4", "B3", "B2"])
    kw = dict(kind="gee", name="x", ref="users/x/img", asset_type="IMAGE", band="B4", vmin="0", vmax="1")
    kw.update(over)
    assert _check(gee, _with_aoi(), **kw) == {"error": key} and calls == []


def test_an_rgb_earth_engine_image_keeps_its_roles_and_combination(requests):
    gee, _ = _gee_answering(lambda obj: ["B2", "B3", "B4", "B8"])
    roles = {"red": "B4", "green": "B3", "blue": "B2", "nir": "B8"}
    out = _check(gee, _with_aoi(), kind="gee", name="fc", ref="users/x/img", asset_type="IMAGE", style="rgb",
                 bands=("B8", "B4", "B3"), roles=roles, combo="false_color", vmin="0", vmax="3000")
    assert out["layer"] == GeeVisLayer(name="fc", asset="users/x/img", bands=["B8", "B4", "B3"], min=0, max=3000,
                                       roles=roles, combo="false_color", style="rgb")


def test_an_old_palette_that_is_no_ramp_is_kept_until_a_card_is_picked(requests):
    gee, _ = _gee_answering(lambda obj: ["B8"])
    kw = dict(kind="gee", name="n", ref="users/x/img", asset_type="IMAGE", band="B8", vmin="0", vmax="1")
    assert _check(gee, _with_aoi(), palette=["ff0000", "00ff00"], **kw)["layer"].palette == ["ff0000", "00ff00"]
    picked = _check(gee, _with_aoi(), palette=["ff0000", "00ff00"], ramp="magma", **kw)["layer"]
    assert picked.palette == vis_palettes.ramp_palette("magma")
    assert _check(gee, _with_aoi(), **kw)["layer"].palette == vis_palettes.ramp_palette(vis_palettes.DEFAULT_RAMP)


def test_a_geotiff_rgb_layer(tmp_path, requests):
    tif = _tif(tmp_path / "rgb.tif", size=10, count=4)
    gee, _ = _gee_answering(lambda obj: None)
    roles = {"red": 3, "green": 2, "blue": 1, "nir": 4}
    out = _check(gee, _with_aoi(), kind="file", name="rgb", ref=str(tif), style="rgb", bands=(4, 3, 2), roles=roles,
                 combo="false_color", vmin="0", vmax="3000")
    assert out["layer"] == FileVisLayer(name="rgb", path=str(tif), bands=[4, 3, 2], min=0, max=3000, roles=roles,
                                        combo="false_color", style="rgb")
    assert _check(gee, _with_aoi(), kind="file", name="rgb", ref=str(tif), style="rgb", bands=(4, 3, 5), vmin="0",
                  vmax="1") == {"error": "steps.layers.err_bands"}
    assert _check(gee, _with_aoi(), kind="file", name="rgb", ref=str(tif), style="rgb", bands=(4, 3, None), vmin="0",
                  vmax="1") == {"error": "steps.layers.err_incomplete"}


def test_a_geotiff_layer(tmp_path, requests):
    tif = _tif(tmp_path / "h.tif", size=10, count=2)
    gee, calls = _gee_answering(lambda obj: None)
    out = _check(gee, _with_aoi(), kind="file", name="height", ref=str(tif), band=2, ramp="viridis", invert=True,
                 vmin="0", vmax="45")
    assert out["layer"] == FileVisLayer(name="height", path=str(tif), band=2, min=0, max=45,
                                        palette=vis_palettes.ramp_palette("viridis", True), style="continuous")
    assert calls == []
    assert _check(gee, _with_aoi(), kind="file", name="height", ref=str(tif), band=3, vmin="0", vmax="1") == {
        "error": "steps.alerts.err_missing_band"}
    (tmp_path / "bad.tif").write_text("not a tiff")
    assert _check(gee, _with_aoi(), kind="file", name="height", ref=str(tmp_path / "bad.tif"), band=1, vmin="0",
                  vmax="1") == {"error": "steps.alerts.err_unreadable"}


def test_a_categorical_earth_engine_layer_is_saved_with_its_classes(requests):
    gee, calls = _gee_answering(lambda obj: ["landcover"])
    classes = [VisClass(value=1, color="#AA0000", label="Forest"), VisClass(value=2, color="00BB00", visible=False)]
    out = _check(gee, _with_aoi(), kind="gee", name="lc", ref="users/x/img", asset_type="IMAGE",
                 style="categorical", band="landcover", classes=classes, vmin="x", vmax="", ramp="viridis")
    assert out["layer"] == GeeVisLayer(
        name="lc", asset="users/x/img", bands=["landcover"], style="categorical", palette=[],
        classes=[VisClass(value=1, color="aa0000", label="Forest"), VisClass(value=2, color="00bb00", visible=False)])
    assert (out["layer"].min, out["layer"].max) == (0.0, 1.0)
    assert calls == [("BANDS", "users/x/img", "IMAGE")]
    gee, _ = _gee_answering(lambda obj: ["B4"])  # the band probe still runs
    assert _check(gee, _with_aoi(), kind="gee", name="lc", ref="users/x/img", asset_type="IMAGE",
                  style="categorical", band="landcover", classes=classes) == {"error": "steps.layers.err_bands"}


@pytest.mark.parametrize("classes, key", [
    ([], "steps.layers.err_no_classes"),
    ([VisClass(value=1, color="aa0000"), VisClass(value=1, color="00bb00")], "steps.layers.err_duplicate_class"),
    ([VisClass(value=1, color="aa0000", visible=False)], "steps.layers.err_no_visible_class"),
    ([VisClass(value=v, color="aa0000") for v in range(51)], "steps.layers.err_class_limit"),
])
def test_a_categorical_layer_needs_a_visible_unique_class(requests, classes, key):
    gee, calls = _gee_answering(lambda obj: ["landcover"])
    out = _check(gee, _with_aoi(), kind="gee", name="lc", ref="users/x/img", asset_type="IMAGE",
                 style="categorical", band="landcover", classes=classes)
    assert out == {"error": key} and calls == []


def test_a_categorical_geotiff_layer_checks_its_band_and_folder(tmp_path, requests):
    tif = _tif(tmp_path / "lc.tif", size=10, count=2)
    gee, _ = _gee_answering(lambda obj: None)
    classes = [VisClass(value=1, color="#AA0000")]
    kw = dict(kind="file", name="lc", style="categorical", classes=classes)
    out = _check(gee, _with_aoi(), ref=str(tif), band=2, **kw)
    assert out["layer"] == FileVisLayer(name="lc", path=str(tif), band=2, style="categorical", palette=[],
                                        classes=[VisClass(value=1, color="aa0000")])
    assert _check(gee, _with_aoi(), ref=str(tif), band=3, **kw) == {"error": "steps.alerts.err_missing_band"}
    ro = tmp_path / "ro"
    ro.mkdir()
    ro_tif = _tif(ro / "lc.tif", size=10)
    ro.chmod(0o555)
    try:
        assert _check(gee, _with_aoi(), ref=str(ro_tif), band=1, **kw) == {
            "error": "steps.layers.err_folder", "args": {"folder": str(ro)}}
    finally:
        ro.chmod(0o755)


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


@pytest.mark.parametrize("text, refused", [
    ("ImageCollection asset 'projects/planet-nicfi/assets/basemaps/asia' not found "
     "(does not exist or caller does not have access).", True),
    ("Caller does not have access to the asset.", True),
    ("ImageCollection asset 'projects/planet-nicfi/assets/basemaps/asia' not found.", True),
    ("Request had invalid authentication credentials: access token expired.", False),
    ("Too many concurrent access requests.", False),
])
def test_only_ee_s_no_access_answer_is_a_refusal(text, refused):
    """One check, shared with ``error_text``: the modal's refusal and the
    toast's wording cannot disagree."""
    import ee

    from gui.scripts.error_text import ee_no_access

    assert not hasattr(vlm, "_no_access")
    assert ee_no_access(ee.EEException(text)) is refused
    assert ee_no_access(RuntimeError("caller does not have access")) is False  # not an Earth Engine answer


# ---------------------------------------------------------------- the modal, rendered
@solara.component
def _StubRasterInput(gee_interface, origin, on_origin, asset, path, asset_types=("IMAGE", "IMAGE_COLLECTION"),
                     disabled=False, show_origin=True):
    """Stands in for pysepal's asset and file pickers: the test sets ``path``.
    It draws one empty marker (``dv-stub-raster-input``), so its place among the
    form's fields can be asserted."""
    _StubRasterInput.seen = SimpleNamespace(origin=origin, asset=asset, path=path, asset_types=asset_types,
                                            show_origin=show_origin)
    rv.Html(tag="div", class_="dv-stub-raster-input")


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


def _shown(rc, key, cls=v.Select):
    """The field labelled ``key``, or None while the form does not show it."""
    found = rc.find(cls, label=t(key)).widgets
    return found[0] if found else None


def _band_count(rc):
    """How many bands the Band select lists, 0 while it is not shown."""
    band = _shown(rc, "steps.layers.field_band")
    return len(band.items) if band is not None else 0


def _save_btn(rc):
    """The primary button: "Add layer" when adding, "Save" when editing."""
    [btn] = [b for b in rc.find(v.Btn).widgets
             if t("steps.layers.add") in b.children or t("common.save") in b.children]
    return btn


def _ramp_select(rc):
    """The Colour ramp select, or None while it is not shown."""
    found = [s for s in rc.find(v.Select).widgets if "dv-ramp-select" in (s.class_ or "").split()]
    return found[0] if found else None


def _flat(w):
    """All text under a widget."""
    out = []
    for c in getattr(w, "children", None) or []:
        out.append(c if isinstance(c, str) else _flat(c))
    return " ".join(out)


def _defaults(rc):
    """The buttons Enter would press (gui/widget/enter_key)."""
    return [b for b in rc.find(v.Btn).widgets if ENTER_DEFAULT in (b.class_ or "").split()]


def _icons(btn):
    return [c.children[0] for c in btn.children if isinstance(c, v.Icon)]


def _bands_choice(rc):
    """The Bands select (one band, or an RGB composite), or None when it is not asked."""
    return _shown(rc, "steps.layers.field_bands")


async def _pick_all_bands(rc):
    """Answer the Bands question "RGB composite" once the band list asks it."""
    assert await _settle(lambda: _bands_choice(rc) is not None)
    _bands_choice(rc).v_model = "all"


def _texts(rc):
    return " ".join(str(w.children) for w in rc.find(v.Html).widgets)


def test_a_geotiff_layer_is_saved_inside_the_task_and_the_modal_closes(tmp_path, monkeypatch):
    tif = _tif(tmp_path / "h.tif", size=10, count=2)
    monkeypatch.setattr(vlm, "RasterInput", _StubRasterInput)
    state, open_, saved = _state(_with_aoi()), solara.reactive(True), []

    async def main():
        box, rc = reacton.render(_modal(state, open_, saved), handle_error=False)
        try:
            _field(rc, "steps.layers.field_source", v.Select).v_model = "file"
            _field(rc, "steps.layers.field_name").v_model = "height"
            assert _StubRasterInput.seen.origin == "file" and _StubRasterInput.seen.show_origin is False
            assert _StubRasterInput.seen.asset_types == ("IMAGE",)
            _StubRasterInput.seen.path.set(str(tif))
            assert await _settle(lambda: _band_count(rc) == 2)
            _field(rc, "steps.layers.field_band", v.Select).v_model = 2
            _ramp_select(rc).v_model = "terrain"
            _field(rc, "steps.layers.field_invert", v.Switch).v_model = True
            _field(rc, "steps.layers.field_max").v_model = "45"
            _field(rc, "steps.layers.field_show_in_analysis", v.Checkbox).v_model = True
            _save_btn(rc).fire_event("click", None)
            assert await _settle(lambda: open_.value is False)
            assert saved == [(FileVisLayer(name="height", path=str(tif), band=2, min=0, max=45,
                                           palette=vis_palettes.ramp_palette("terrain", True),
                                           style="continuous", show_in_analysis=True), None)]
        finally:
            rc.close()

    asyncio.run(main())


def test_a_kind_change_clears_the_band(monkeypatch, tmp_path):
    tif = _tif(tmp_path / "two.tif", size=10, count=2)
    p = _with_aoi()
    p.vis_layers = {"evi": GeeVisLayer(name="evi", asset="users/x/img", bands=["EVI"], min=0, max=1)}
    state, open_, saved, gee = _gee_modal(monkeypatch, ["NDVI", "EVI"], p)

    async def main():
        box, rc = reacton.render(vlm.VisLayerModal(open_=open_, app_state=state, gee_interface=gee,
                                                   on_save=lambda *a: saved.append(a), editing="evi"),
                                 handle_error=False)
        try:
            assert await _settle(lambda: _band_count(rc) == 2)
            assert _field(rc, "steps.layers.field_band", v.Select).v_model == "EVI"
            _field(rc, "steps.layers.field_source", v.Select).v_model = "file"
            assert _shown(rc, "steps.layers.field_band") is None  # no file picked: nothing below the picker
            _StubRasterInput.seen.path.set(str(tif))
            assert await _settle(lambda: _band_count(rc) == 2)
            assert _field(rc, "steps.layers.field_band", v.Select).v_model == 1  # an index, no Earth Engine name
        finally:
            rc.close()

    asyncio.run(main())


def test_an_edit_opens_with_the_layers_values(monkeypatch):
    p = _with_aoi()
    p.vis_layers = {"rgb": GeeVisLayer(name="rgb", asset="users/x/img", bands=["B4", "B3", "B2"], min=0, max=3000)}
    state, open_, saved, gee = _gee_modal(monkeypatch, ["B4", "B3", "B2"], p)

    async def main():
        box, rc = reacton.render(vlm.VisLayerModal(open_=open_, app_state=state, gee_interface=gee,
                                                   on_save=lambda *a: saved.append(a), editing="rgb"),
                                 handle_error=False)
        try:
            assert t("steps.layers.edit_title", name="rgb") in _texts(rc)
            assert _field(rc, "steps.layers.field_name").v_model == "rgb"
            assert await _settle(lambda: _bands_choice(rc) is not None)
            assert _bands_choice(rc).v_model == "all"
            assert [_field(rc, f"steps.layers.field_{c}", v.Select).v_model for c in ("red", "green", "blue")] == [
                "B4", "B3", "B2"]
            assert _field(rc, "steps.layers.field_max").v_model == "3000"
            assert _StubRasterInput.seen.asset.value["asset_id"] == "users/x/img"
            assert _ramp_select(rc) is None  # RGB: no colour ramp
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
        return {"layer": GeeVisLayer(name=kw["name"], asset="x")}

    def on_save(layer, previous):
        raise ValueError(t("steps.layers.name_duplicate"))

    monkeypatch.setattr(vlm, "RasterInput", _StubRasterInput)
    monkeypatch.setattr(vlm, "validate_vis_layer", fake_validate)
    state, open_ = _state(_with_aoi()), solara.reactive(True)

    async def main():
        box, rc = reacton.render(vlm.VisLayerModal(open_=open_, app_state=state, gee_interface=object(),
                                                   on_save=on_save), handle_error=False)
        try:
            _field(rc, "steps.layers.field_name").v_model = "ndvi"
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
        return {"layer": GeeVisLayer(name=kw["name"], asset="x")}

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
            assert _field(rc, "steps.layers.field_source", v.Select).disabled is True
            assert _defaults(rc) == []  # Review Focus 1: mid-check, Enter presses nothing
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


@pytest.mark.parametrize("editing, label, icon", [
    (None, "steps.layers.add", "mdi-plus"), ("ndvi", "common.save", "mdi-content-save-outline")])
def test_the_layer_form_sits_on_the_form_frame(monkeypatch, editing, label, icon):
    monkeypatch.setattr(vlm, "RasterInput", _StubRasterInput)
    p = _with_aoi()
    p.vis_layers = {"ndvi": GeeVisLayer(name="ndvi", asset="users/x/img", bands=["NDVI"], min=0, max=1)}
    state, open_, saved = _state(p), solara.reactive(True), []

    async def main():
        _box, rc = reacton.render(_modal(state, open_, saved, editing=editing), handle_error=False)
        try:
            dialog = rc.find(v.Dialog).widget
            assert (dialog.max_width, dialog.persistent, dialog.eager) == ("560px", True, True)
            [primary] = _defaults(rc)
            assert t(label) in primary.children and _icons(primary) == [icon]
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
        "ndvi": GeeVisLayer(name="ndvi", asset="y", bands=["NDVI"]),
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


def test_rows_show_the_kind_and_analysis_chips_and_the_details_note(monkeypatch):
    seen, _, _ = _stub_children(monkeypatch)
    state = _state(_layers_project())
    box, rc = _render_step(state, _FakeMap(), MapLayers(_FakeMap()))
    try:
        assert seen["columns"] == layers_step.layer_columns()
        assert [r["key"] for r in seen["rows"]] == ["height", "ndvi", "S2"]  # by name, case-folded
        s2 = next(r for r in seen["rows"] if r["key"] == "S2")
        assert s2["cells"] == [{"value": "S2", "chips": [
            {"value": t("steps.layers.kind_gee")}, {"value": t("steps.layers.in_analysis"), "color": "primary"}]}]
        assert s2["notes"] == [{"text": "R/G/B: B4, B3, B2 · 0–3000", "tone": None}]
        assert [a["kind"] for a in s2["actions"]] == ["map_toggle", "edit", "delete"]
        assert len(s2["actions"]) == layers_step.LAYER_ACTIONS
        ndvi = next(r for r in seen["rows"] if r["key"] == "ndvi")
        assert ndvi["cells"][0]["chips"] == [{"value": t("steps.layers.kind_gee")}]  # not in Analysis: no chip
        assert ndvi["notes"] == [{"text": "NDVI · 0–1", "tone": None}]
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
        _action(seen, "ndvi", "map_toggle")["on_click"]()
        assert built == [("ndvi", "vis:ndvi", PARAGUAY, True)] and layers.on.value == {"vis:ndvi"}
        [legend] = layers.legends.value
        assert legend.layer_id == "vis:ndvi" and legend.spec.kind == "gradient"
        assert _action(seen, "ndvi", "map_toggle")["is_on"] is True
        _action(seen, "ndvi", "map_toggle")["on_click"]()
        assert layers.on.value == frozenset() and "vis:ndvi" in map_.removed and layers.legends.value == ()
    finally:
        rc.close()


def test_save_adds_renames_and_drops_the_old_layer_from_the_map(monkeypatch):
    _sync_spawn(monkeypatch)
    seen, modal, _ = _stub_children(monkeypatch)
    state, map_ = _state(_layers_project()), _FakeMap()
    layers = MapLayers(map_)
    box, rc = _render_step(state, map_, layers)
    try:
        new = GeeVisLayer(name="mosaic", asset="z", bands=["B1"])
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
            modal["on_save"](GeeVisLayer(name="s2", asset="z"), None)
        assert set(state.project.value.vis_layers) == {"S2", "ndvi", "mosaic", "relief"}
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


# ---------------------------------------------------------------- ramps, roles and combinations in the modal
S2_BANDS = ["B2", "B3", "B4", "B8", "B11"]


def _gee_modal(monkeypatch, bands, p=None, editing=None):
    monkeypatch.setattr(vlm, "RasterInput", _StubRasterInput)

    async def get_info_async(obj):
        return bands

    monkeypatch.setattr(vlm, "band_list_request", lambda ref, kind: ref)
    import gui.widget.raster_input as ri
    monkeypatch.setattr(ri.user_raster, "band_list_request", lambda ref, kind: ref)
    state, open_, saved = _state(p or _with_aoi()), solara.reactive(True), []
    gee = SimpleNamespace(get_info_async=get_info_async)
    return state, open_, saved, gee


def test_the_ramp_select_starts_on_grayscale_and_an_edit_prefills_ramp_and_invert(monkeypatch):
    p = _with_aoi()
    p.vis_layers = {
        "n": GeeVisLayer(name="n", asset="users/x/img", bands=["NDVI"], palette=vis_palettes.ramp_palette("magma", True)),
        "old": GeeVisLayer(name="old", asset="users/x/img", bands=["NDVI"], palette=["ff0000", "00ff00"]),
    }
    state, open_, saved, gee = _gee_modal(monkeypatch, ["NDVI"], p)

    def render(editing=None):
        return reacton.render(vlm.VisLayerModal(open_=open_, app_state=state, gee_interface=gee,
                                                on_save=lambda *a: saved.append(a), editing=editing),
                              handle_error=False)

    async def main():
        _box, rc = render()
        try:
            _StubRasterInput.seen.asset.set({"asset_id": "users/x/img", "type": "IMAGE", "column": "ALL",
                                             "value": None})
            assert await _settle(lambda: _ramp_select(rc) is not None)
            assert _ramp_select(rc).v_model == "grayscale"
            assert [i["value"] for i in _ramp_select(rc).items] == list(vis_palettes.RAMPS)
        finally:
            rc.close()
        _box, rc = render("n")
        try:
            assert await _settle(lambda: _ramp_select(rc) is not None)
            assert _ramp_select(rc).v_model == "magma"
            assert _field(rc, "steps.layers.field_invert", v.Switch).v_model is True
        finally:
            rc.close()
        _box, rc = render("old")
        try:
            assert await _settle(lambda: _ramp_select(rc) is not None)
            assert _ramp_select(rc).v_model is None  # a palette that is no ramp preselects no ramp
        finally:
            rc.close()

    asyncio.run(main())


def _combo_cards(rc):
    return [b for b in rc.find(v.Btn).widgets if "dv-combo" in (b.class_ or "").split()]


def _combo_card(rc, key):
    """The composite card of ``key`` (a ``vis_palettes.RGB_COMBOS`` key or "custom")."""
    [card] = [b for b in _combo_cards(rc) if _flat(b).startswith(_capital_text(f"steps.layers.combo_{key}"))]
    return card


def _capital_text(key):
    text = t(key)
    return text[:1].upper() + text[1:]


def _card_has(card, cls):
    return cls in (card.class_ or "").split()


def _selected_combo(rc):
    """The key of the selected composite card, or None."""
    for key in [*vis_palettes.RGB_COMBOS, "custom"]:
        if _card_has(_combo_card(rc, key), "dv-combo--selected"):
            return key
    return None


def _enabled_combos(rc):
    return [k for k in [*vis_palettes.RGB_COMBOS, "custom"] if not _card_has(_combo_card(rc, k), "dv-combo--off")]


def _rgb(rc):
    return [_field(rc, f"steps.layers.field_{c}", v.Select).v_model for c in ("red", "green", "blue")]


def _roles_link(rc):
    """The "edit" / "set them" link of the band roles line, or None."""
    links = [b for b in rc.find(v.Btn).widgets if "dv-roles-link" in (b.class_ or "").split()]
    return links[0] if links else None


def _role_selects(rc):
    return rc.find(v.Select, label=t("steps.layers.role_red")).widgets


async def _open_roles(rc):
    """Unfold the band roles behind the summary line."""
    assert await _settle(lambda: _roles_link(rc) is not None)
    if not _role_selects(rc):
        _roles_link(rc).fire_event("click", None)
    assert _role_selects(rc)


def test_loading_bands_guesses_the_roles_and_offers_the_combinations(monkeypatch):
    state, open_, saved, gee = _gee_modal(monkeypatch, S2_BANDS)

    async def main():
        _box, rc = reacton.render(vlm.VisLayerModal(open_=open_, app_state=state, gee_interface=gee,
                                                    on_save=lambda *a: saved.append(a)), handle_error=False)
        try:
            _StubRasterInput.seen.asset.set({"asset_id": "users/x/img", "type": "IMAGE", "column": "ALL",
                                             "value": None})
            await _pick_all_bands(rc)
            assert await _settle(lambda: _selected_combo(rc) == "true_color")
            await _open_roles(rc)
            roles = {r: _field(rc, f"steps.layers.role_{r}", v.Select).v_model for r in vis_palettes.ROLES}
            assert roles == {"red": "B4", "green": "B3", "blue": "B2", "nir": "B8", "swir": "B11"}
            assert _enabled_combos(rc) == ["true_color", "false_color", "swir", "custom"]
            _combo_card(rc, "swir").fire_event("click", None)
            assert _rgb(rc) == ["B11", "B8", "B4"]
            _field(rc, "steps.layers.role_nir", v.Select).v_model = "B2"  # a role moves: the combination re-applies
            assert _rgb(rc) == ["B11", "B2", "B4"]
            assert "B2" in _flat(_combo_card(rc, "false_color"))  # the cards follow the roles
            _field(rc, "steps.layers.role_swir", v.Select).v_model = None  # swir no longer possible
            assert _selected_combo(rc) == "custom"
            assert _enabled_combos(rc) == ["true_color", "false_color", "custom"]
        finally:
            rc.close()

    asyncio.run(main())


def test_the_default_name_follows_the_image_and_the_combination_until_typed_over(monkeypatch):
    state, open_, saved, gee = _gee_modal(monkeypatch, S2_BANDS)

    async def main():
        _box, rc = reacton.render(vlm.VisLayerModal(open_=open_, app_state=state, gee_interface=gee,
                                                    on_save=lambda *a: saved.append(a)), handle_error=False)
        try:
            _StubRasterInput.seen.asset.set({"asset_id": "users/x/mosaic", "type": "IMAGE", "column": "ALL",
                                             "value": None})
            await _pick_all_bands(rc)  # spectral bands: already RGB, on the first combination
            want = "mosaic_true_color"
            assert await _settle(lambda: _field(rc, "steps.layers.field_name").v_model == want)
            _bands_choice(rc).v_model = "one"
            _field(rc, "steps.layers.field_band", v.Select).v_model = "B8"
            assert await _settle(lambda: _field(rc, "steps.layers.field_name").v_model == "mosaic_B8")
            _bands_choice(rc).v_model = "all"
            assert await _settle(lambda: _field(rc, "steps.layers.field_name").v_model == want)
            _combo_card(rc, "false_color").fire_event("click", None)
            want = "mosaic_false_color"
            assert await _settle(lambda: _field(rc, "steps.layers.field_name").v_model == want)
            _field(rc, "steps.layers.field_name").v_model = "mine"
            _combo_card(rc, "swir").fire_event("click", None)
            await asyncio.sleep(0.1)
            assert _field(rc, "steps.layers.field_name").v_model == "mine"
        finally:
            rc.close()

    asyncio.run(main())


def test_a_geotiff_rgb_layer_guesses_roles_from_band_names_and_saves_indexes(tmp_path, monkeypatch):
    tif = tmp_path / "rgb.tif"
    with rasterio.open(tif, "w", driver="GTiff", width=10, height=10, count=4, dtype="uint8", crs="EPSG:4326",
                       transform=Affine(0.0002, 0, -55.5, 0, -0.0002, -24.4)) as dst:
        dst.write(np.zeros((4, 10, 10), "uint8"))
        dst.descriptions = ("blue", "green", "red", "nir")
    monkeypatch.setattr(vlm, "RasterInput", _StubRasterInput)
    state, open_, saved = _state(_with_aoi()), solara.reactive(True), []

    async def main():
        _box, rc = reacton.render(_modal(state, open_, saved), handle_error=False)
        try:
            _field(rc, "steps.layers.field_source", v.Select).v_model = "file"
            _StubRasterInput.seen.path.set(str(tif))
            await _pick_all_bands(rc)
            await _open_roles(rc)
            assert await _settle(lambda: _field(rc, "steps.layers.role_red", v.Select).v_model == 3)
            assert _enabled_combos(rc) == ["true_color", "false_color", "custom"]
            _combo_card(rc, "false_color").fire_event("click", None)
            _field(rc, "steps.layers.field_name").v_model = "fc"
            _field(rc, "steps.layers.field_max").v_model = "3000"
            _save_btn(rc).fire_event("click", None)
            assert await _settle(lambda: open_.value is False)
            [(layer, _prev)] = saved
            assert layer == FileVisLayer(name="fc", path=str(tif), bands=[4, 3, 2], min=0, max=3000,
                                         roles={"red": 3, "green": 2, "blue": 1, "nir": 4}, combo="false_color",
                                         style="rgb")
        finally:
            rc.close()

    asyncio.run(main())


def test_editing_an_rgb_layer_saved_before_the_roles_keeps_its_bands(monkeypatch):
    p = _with_aoi()
    p.vis_layers = {"rgb": GeeVisLayer(name="rgb", asset="users/x/img", bands=["B8", "B4", "B3"], min=0, max=3000)}
    state, open_, saved, gee = _gee_modal(monkeypatch, S2_BANDS, p)

    async def main():
        _box, rc = reacton.render(vlm.VisLayerModal(open_=open_, app_state=state, gee_interface=gee,
                                                    on_save=lambda *a: saved.append(a), editing="rgb"),
                                  handle_error=False)
        try:
            await _open_roles(rc)
            assert await _settle(lambda: _field(rc, "steps.layers.role_red", v.Select).v_model == "B4")
            assert _rgb(rc) == ["B8", "B4", "B3"]
            _save_btn(rc).fire_event("click", None)
            assert await _settle(lambda: open_.value is False)
            assert saved[0][0].bands == ["B8", "B4", "B3"]
        finally:
            rc.close()

    asyncio.run(main())


def test_a_layer_saved_with_a_colormap_keeps_it_and_its_invert_switch_is_off(monkeypatch, tmp_path):
    tif = _tif(tmp_path / "g.tif", size=10)
    monkeypatch.setattr(vlm, "RasterInput", _StubRasterInput)
    p = _with_aoi()
    p.vis_layers = {"g": FileVisLayer(name="g", path=str(tif), colormap="Greens", min=0, max=5)}
    state, open_, saved = _state(p), solara.reactive(True), []

    async def main():
        _box, rc = reacton.render(_modal(state, open_, saved, editing="g"), handle_error=False)
        try:
            assert await _settle(lambda: _ramp_select(rc) is not None)
            assert _ramp_select(rc).v_model is None
            assert _field(rc, "steps.layers.field_invert", v.Switch).disabled is True
            _save_btn(rc).fire_event("click", None)
            assert await _settle(lambda: open_.value is False)
            assert saved[0][0].colormap == "Greens" and saved[0][0].palette == []
        finally:
            rc.close()

    asyncio.run(main())
