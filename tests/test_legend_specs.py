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
    ramp = ls.vis_legend("vis:r", FileVisLayer(name="r", path="/x.tif", min=0, max=9,
                                               palette=["ff0000", "0000ff"])).spec
    assert ramp.kind == "gradient" and ramp.colors[0] == "#ff0000" and ramp.colors[-1] == "#0000ff"
    rgb_tif = ls.vis_legend("vis:f", FileVisLayer(name="f", path="/x.tif", bands=[4, 3, 2], min=0, max=3000)).spec
    assert rgb_tif.kind == "note" and dict(rgb_tif.labels[0].args)["bands"] == "4, 3, 2"
    nicfi = ls.vis_legend("vis:p", NicfiVisLayer(name="p"), month="2026-03").spec
    assert nicfi.kind == "note" and dict(nicfi.labels[0].args) == {"month": "2026-03"}


def test_stack_order_is_a_permutation_of_the_status_codes():
    assert sorted(ls.STACK_ORDER) == sorted(code for code, _, _ in ls.STATUS_CLASSES)


def test_confirmed_and_false_positive_are_never_adjacent_in_the_stack():
    """Measured deuteranopia separation between the Confirmed and False positive
    steps is 2.8 (OKLab x100) -- invisible. They are never adjacent in a stack
    or a legend; putting Needs revision between them lifts the worst adjacent
    pair to 13.8, with the hexes unchanged."""
    codes = {label.rsplit(".", 1)[-1]: code for code, _, label in ls.STATUS_CLASSES}
    pair = {codes["confirmed"], codes["false_positive"]}
    adjacent = [{a, b} for a, b in zip(ls.STACK_ORDER, ls.STACK_ORDER[1:])]
    assert pair not in adjacent


def test_stack_order_is_an_order_and_carries_no_colour():
    """It reorders, it does not repaint: the map and the PMTiles paint values
    draw exactly the same colours as before."""
    assert all(isinstance(code, int) for code in ls.STACK_ORDER)


def test_states_legend_has_the_two_visible_states():
    from gui.i18n import t

    lg = ls.states_legend("k")
    assert [t(lb.key) for lb in lg.spec.labels] == [t("legend.state_too_small"), t("legend.state_over_cap")]
    assert lg.spec.colors == ("#9e9e9e", "#fb8c00")
    assert len(ls.state_colormap().colors) == 2
