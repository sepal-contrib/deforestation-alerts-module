from deforestation_alerts.project import io
from deforestation_alerts.project.models import FileVisLayer, GeeVisLayer, NicfiVisLayer, VisClass
from deforestation_alerts.project.share import trimmed_manifest
from gui.scripts import vis_layers as vl
from tests._share_helpers import CREATOR, SET1, make_creator


def test_an_old_layer_reads_as_today():
    assert vl.effective_style(GeeVisLayer(name="a", asset="x", bands=["B4", "B3", "B2"])) == "rgb"
    assert vl.effective_style(GeeVisLayer(name="a", asset="x", bands=["NDVI"])) == "continuous"
    assert vl.effective_style(FileVisLayer(name="f", path="/x.tif", band=2)) == "continuous"
    assert vl.effective_style(NicfiVisLayer(name="n")) is None
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
