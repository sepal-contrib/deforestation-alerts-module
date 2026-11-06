from types import SimpleNamespace

from deforestation_alerts.project import io
from deforestation_alerts.project.models import FileVisLayer, GeeVisLayer, VisClass
from deforestation_alerts.project.share import trimmed_manifest
from gui.scripts import vis_layers as vl
from tests._share_helpers import CREATOR, SET1, make_creator


def test_an_old_layer_reads_as_today():
    assert vl.effective_style(GeeVisLayer(name="a", asset="x", bands=["B4", "B3", "B2"])) == "rgb"
    assert vl.effective_style(GeeVisLayer(name="a", asset="x", bands=["NDVI"])) == "continuous"
    assert vl.effective_style(FileVisLayer(name="f", path="/x.tif", band=2)) == "continuous"
    old = GeeVisLayer.model_validate({"kind": "gee", "name": "a", "asset": "x", "bands": ["NDVI"]})
    assert old.style is None and old.classes == []


def test_a_set_style_wins_over_the_band_count():
    assert vl.effective_style(GeeVisLayer(name="a", asset="x", bands=["Map"], style="categorical")) == "categorical"
    assert vl.effective_style(FileVisLayer(name="f", path="/x.tif", bands=[1, 2, 3], style="continuous")) == "continuous"


def test_a_categorical_layer_round_trips_through_save_load_duplicate_and_the_share_manifest(data_root):
    p, _ = make_creator(data_root)
    layer = GeeVisLayer(
        name="gee", asset="users/x/img", style="categorical", bands=["Map"],
        classes=[VisClass(value=10, color="006400", label="Tree"),
                 VisClass(value=80, color="0064c8", label="Water", visible=False)])
    p.vis_layers["gee"] = layer
    io.save_project(p, data_root)
    assert io.load_project(CREATOR, data_root).vis_layers["gee"] == layer
    assert io.duplicate_project(p, "copy").vis_layers["gee"] == layer
    assert trimmed_manifest(p, SET1).vis_layers["gee"] == layer


# ---------------------------------------------------------------- drawing, legend and summary (Task 3)
PARAGUAY = (-55.5, -24.5, -55.4, -24.4)
CLASSES = [VisClass(value=10, color="006400", label="Tree"), VisClass(value=20, color="ffbb22", visible=False),
           VisClass(value=80, color="0064c8")]


def _project():
    from datetime import datetime

    from deforestation_alerts.project.models import AoiRecord, Project

    p = Project(name="v", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="d", bounds=PARAGUAY)
    return p


def _tif(path, data, count=1):
    import numpy as np
    import rasterio
    from affine import Affine

    data = np.asarray(data, "uint8")
    with rasterio.open(path, "w", driver="GTiff", width=data.shape[1], height=data.shape[0], count=count,
                       dtype="uint8", crs="EPSG:4326", transform=Affine(0.0002, 0, -55.5, 0, -0.0002, -24.4)) as dst:
        for i in range(1, count + 1):
            dst.write(data, i)
    return path


def _fake_localtileserver(monkeypatch):
    """Record what reaches localtileserver (the pattern of test_vis_layers.py)."""
    import sys
    import types

    seen = []
    fake = types.ModuleType("localtileserver")
    fake.TileClient = lambda path: SimpleNamespace(path=path)
    fake.get_leaflet_tile_layer = lambda client, **kw: seen.append((client.path, kw)) or "layer"
    monkeypatch.setitem(sys.modules, "localtileserver", fake)
    return seen


class _FakeMap:
    def __init__(self):
        self.layers, self.ee_calls = {}, []

    def add_layer(self, layer, key=""):
        self.layers[key] = layer

    def add_ee_layer(self, img, vis, *, name, key, shown=True):
        self.ee_calls.append({"img": img, "vis": vis, "name": name, "key": key, "shown": shown})


def _comparable(kw):
    """``kw`` with its colormap swapped for what it draws (a ramp is rebuilt on every call)."""
    import numpy as np

    out = dict(kw)
    cmap = out.pop("colormap")
    out["colormap"] = None if cmap is None else (type(cmap).__name__, cmap.name,
                                                 cmap(np.linspace(0, 1, 16)).round(6).tolist())
    return out


def test_an_explicit_style_draws_legends_and_summarizes_as_the_old_layer(tmp_path, monkeypatch):
    from gui.scripts import legend_specs, vis_palettes

    tif = str(_tif(tmp_path / "t.tif", [[0, 1], [2, 3]], count=4))
    olds = [
        GeeVisLayer(name="rgb", asset="x", bands=["B4", "B3", "B2"], min=0, max=3000),
        GeeVisLayer(name="ramp", asset="x", bands=["NDVI"], palette=vis_palettes.ramp_palette("viridis", True)),
        GeeVisLayer(name="odd", asset="x", bands=["NDVI"], palette=["ff0000", "00ff00"], min=-1, max=2),
        FileVisLayer(name="frgb", path=tif, bands=[4, 3, 2], min=0, max=3000),
        FileVisLayer(name="framp", path=tif, band=2, palette=vis_palettes.ramp_palette("greens"), min=0, max=45),
        FileVisLayer(name="fold", path=tif, colormap="magma", min=0, max=45),
    ]
    seen = _fake_localtileserver(monkeypatch)
    for old in olds:
        new = old.model_copy(update={"style": vl.effective_style(old)})
        assert new.style in ("rgb", "continuous")
        if old.kind == "gee":
            assert vl.ee_vis_params(new) == vl.ee_vis_params(old)
        assert legend_specs.vis_legend("vis:x", new).spec == legend_specs.vis_legend("vis:x", old).spec
        assert vl.vis_layers_summary(new) == vl.vis_layers_summary(old)
        if old.kind == "file":
            seen.clear()
            vl.builder(_FakeMap(), old, "vis:x", project=_project(), aoi_bounds=PARAGUAY)()
            vl.builder(_FakeMap(), new, "vis:x", project=_project(), aoi_bounds=PARAGUAY)()
            (old_path, old_kw), (new_path, new_kw) = seen
            assert new_path == old_path == tif and _comparable(new_kw) == _comparable(old_kw)


def test_an_earth_engine_categorical_layer_remaps_its_visible_classes(gee):
    import ee

    from gui.scripts import vis_classes

    map_ = _FakeMap()
    classes = [VisClass(value=10, color="006400"), VisClass(value=20, color="ffbb22", visible=False),
               VisClass(value=30, color="0064c8")]
    layer = GeeVisLayer(name="wc", asset="ESA/WorldCover/v200/2021", bands=["Map"], style="categorical",
                        classes=classes)
    vl.builder(map_, layer, "vis:wc", project=_project(), aoi_bounds=PARAGUAY, shown=False)()
    [call] = map_.ee_calls
    expected = ee.Image("ESA/WorldCover/v200/2021").select(["Map"]).remap([10, 30], [0, 1])
    assert call["img"].serialize() == expected.serialize()
    assert call["vis"] == vis_classes.ee_remap(classes)[2]
    assert (call["key"], call["name"], call["shown"]) == ("vis:wc", "wc", False)


def test_the_categorical_vrt_maps_classes_to_1_m_and_the_rest_to_nodata(tmp_path):
    import rasterio

    tif = _tif(tmp_path / "c.tif", [[0, 10, 20], [30, 15, 255]])
    classes = [VisClass(value=10, color="006400"), VisClass(value=20, color="ffbb22", visible=False),
               VisClass(value=30, color="0064c8")]
    vrt = vl.categorical_vrt(tif, 1, classes)
    assert vrt.suffix == ".vrt" and vrt.name.startswith("vis_classes_")
    with rasterio.open(vrt) as src:
        assert src.read(1).tolist() == [[0, 1, 0], [2, 0, 0]] and src.nodata == 0 and src.dtypes[0] == "uint8"
    adjacent = _tif(tmp_path / "a.tif", [[1, 2, 3]])
    with rasterio.open(vl.categorical_vrt(adjacent, 1, [VisClass(value=v, color="ff0000") for v in (1, 2, 3)])) as s:
        assert s.read(1).tolist() == [[1, 2, 3]]


def test_a_geotiff_categorical_layer_draws_its_vrt_with_the_class_colormap(tmp_path, monkeypatch):
    from gui.scripts import vis_classes

    tif = _tif(tmp_path / "c.tif", [[0, 10], [20, 80]])
    seen = _fake_localtileserver(monkeypatch)
    layer = FileVisLayer(name="c", path=str(tif), band=1, style="categorical", classes=CLASSES)
    vl.builder(_FakeMap(), layer, "vis:c", project=_project(), aoi_bounds=PARAGUAY)()
    [(path, kw)] = seen
    assert path.endswith(".vrt") and str(vl.categorical_vrt(tif, 1, CLASSES)) == path
    assert kw["indexes"] == [1] and (kw["vmin"], kw["vmax"], kw["nodata"]) == (1, 2, 0)
    assert kw["colormap"].colors == vis_classes.class_colormap(CLASSES).colors


def test_a_categorical_layer_with_no_visible_class_draws_nothing(tmp_path, monkeypatch):
    tif = _tif(tmp_path / "c.tif", [[0, 10], [20, 80]])
    seen = _fake_localtileserver(monkeypatch)
    hidden = [c.model_copy(update={"visible": False}) for c in CLASSES]
    vl.builder(_FakeMap(), FileVisLayer(name="c", path=str(tif), style="categorical", classes=hidden), "vis:c",
               project=_project(), aoi_bounds=PARAGUAY)()
    assert seen == []


def test_a_categorical_legend_is_one_chip_per_visible_class():
    from gui.scripts import legend_specs

    layer = GeeVisLayer(name="wc", asset="x", bands=["Map"], style="categorical", classes=CLASSES)
    spec = legend_specs.vis_legend("vis:wc", layer).spec
    assert spec.kind == "chips" and spec.title.literal == "wc"
    assert spec.colors == ("#006400", "#0064c8")
    assert tuple(lbl.literal for lbl in spec.labels) == ("Tree", "80")


def test_the_categorical_summary_counts_visible_classes():
    classes = [VisClass(value=v, color="ff0000") for v in (10, 20, 30, 40, 50)] + [
        VisClass(value=60, color="00ff00", visible=False)]
    assert vl.vis_layers_summary(GeeVisLayer(name="wc", asset="x", bands=["Map"], style="categorical",
                                             classes=classes)) == "band Map · 5 classes"
    assert vl.vis_layers_summary(FileVisLayer(name="c", path="/x.tif", band=2, style="categorical",
                                              classes=classes[4:])) == "band 2 · 1 class"


def _part_names_of_two_threads(monkeypatch, module, build):
    """The ``.part`` names ``build`` writes through ``module.os.replace``, run
    from this thread and then a worker (both alive, so their idents differ)."""
    import threading

    seen, real = [], module.os.replace

    def replace(src, dst):
        seen.append(str(src))
        real(src, dst)

    monkeypatch.setattr(module.os, "replace", replace)
    build()
    worker = threading.Thread(target=build)
    worker.start()
    worker.join()
    return seen


def test_two_threads_write_the_categorical_vrt_through_their_own_part_file(tmp_path, monkeypatch):
    tif = _tif(tmp_path / "c.tif", [[0, 10], [20, 80]])
    seen = _part_names_of_two_threads(monkeypatch, vl, lambda: vl.categorical_vrt(tif, 1, CLASSES))
    assert len(seen) == 2 and len(set(seen)) == 2
