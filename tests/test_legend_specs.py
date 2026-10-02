import re
from pathlib import Path

from matplotlib.colors import to_hex

from deforestation_alerts.project.models import FileVisLayer, GeeVisLayer, NicfiVisLayer, PeriodMask
from gui.scripts import legend_specs as ls
from gui.scripts import pmtiles_map, raster_map
from gui.scripts.legend_data import to_legend_data

ROOT = Path(__file__).resolve().parents[1]


def _t(key, **kw):
    return f"<{key}>" + "".join(f"{k}={v}" for k, v in sorted(kw.items()))


def test_one_table_feeds_ee_vis_colormaps_paint_and_chips():
    colors = [c for _, c, _ in ls.ALERT_CLASSES]
    assert ls.alert_ee_vis() == {"min": 1, "max": 2, "palette": colors}
    assert [to_hex(raster_map.ALERT_COLORMAP(i)) for i in range(2)] == colors
    assert raster_map.ALERT_EE_VIS == ls.alert_ee_vis()
    assert pmtiles_map.STATUS_COLORS == ls.status_colors()
    assert ls.status_colors(faded=True)[0] == ls.lighten("#9e9e9e")
    spec = ls.alert_legend("src:RADD", "RADD · preview", ls.preview_title("RADD", "2026-01-01", "2026-01-31")).spec
    assert spec.kind == "chips" and list(spec.colors) == colors
    data = to_legend_data(spec, _t)
    assert [i.label for i in data.items][1:] == ["<legend.alert.confirmed>", "<legend.alert.probable>"]


def test_no_other_module_writes_these_colours():
    palette = {c.lower() for _, c, _ in ls.ALERT_CLASSES + ls.STATUS_CLASSES} | {ls.MASK_COLOR.lower()}
    offenders = []
    for py in (ROOT / "gui").rglob("*.py"):
        if py.name == "legend_specs.py":
            continue
        text = py.read_text(encoding="utf-8").lower()
        offenders += [f"{py.relative_to(ROOT)}:{c}" for c in palette if c in text]
    assert offenders == []


def test_mask_legend_follows_the_choice():
    keep = ls.mask_legend("mask:forest", "forest", PeriodMask(on=True, mode="keep")).spec
    excl = ls.mask_legend("mask:forest", "forest", PeriodMask(on=True, mode="exclude")).spec
    off = ls.mask_legend("mask:forest", "forest", None).spec
    assert [l.key for l in keep.labels] == ["legend.mask.keep"] and list(keep.colors) == [ls.MASK_COLOR]
    assert [l.key for l in excl.labels] == ["legend.mask.exclude"]
    assert [l.key for l in off.labels] == ["legend.mask.off"]
    assert [l.key for l in ls.mask_legend("k", "f", PeriodMask(on=False, mode="keep")).spec.labels] == [
        "legend.mask.off"]


def test_vis_legends():
    rgb = ls.vis_legend("vis:s2", GeeVisLayer(name="s2", asset="x", bands=["B4", "B3", "B2"], min=0, max=3000)).spec
    assert rgb.kind == "note" and rgb.labels[0].key == "legend.vis.rgb"
    ndvi = ls.vis_legend("vis:n", GeeVisLayer(name="n", asset="x", bands=["NDVI"], min=0, max=1,
                                              palette=["ffffff", "00ff00"])).spec
    assert ndvi.kind == "gradient" and ndvi.colors[0] == "#ffffff" and ndvi.colors[-1] == "#00ff00"
    grey = ls.vis_legend("vis:g", GeeVisLayer(name="g", asset="x", bands=["b"], min=0, max=1)).spec
    assert grey.colors[0] == "#000000" and grey.colors[-1] == "#ffffff"
    tif = ls.vis_legend("vis:t", FileVisLayer(name="t", path="/x.tif", colormap="viridis", min=0, max=45)).spec
    assert tif.kind == "gradient" and [l.literal for l in tif.labels] == ["0", "45"]
    nicfi = ls.vis_legend("vis:p", NicfiVisLayer(name="p"), month="2026-03").spec
    assert nicfi.kind == "note" and dict(nicfi.labels[0].args) == {"month": "2026-03"}
