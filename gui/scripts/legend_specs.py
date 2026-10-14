"""The palettes of every layer the app draws, and their legends (spec §9.1, §9.4).

One table per palette produces the Earth Engine vis parameters, the
``localtileserver`` colormaps, the PMTiles paint values and the legend chips:
no other module writes these colours (tests/test_legend_specs.py scans gui/).
Legend text is language-neutral (``Label`` keys), translated at render.
"""

from matplotlib import colormaps
from matplotlib.colors import LinearSegmentedColormap, ListedColormap

from gui.scripts.legend_data import Label, LegendSpec, format_number, gradient_colors
from gui.scripts.legend_registry import LayerLegend
from gui.scripts.vis_palettes import ramp_colormap

#: (code, colour, label key): source confidence, for every source and the combined display band
ALERT_CLASSES = ((1, "#ff9800", "legend.alert.confirmed"), (2, "#7b1fa2", "legend.alert.probable"))
#: (status_code, colour, label key): review status in Overview (engine/pmtiles.STATUS_CODES)
STATUS_CLASSES = (
    (0, "#9e9e9e", "legend.status.not_reviewed"),
    (1, "#d32f2f", "legend.status.confirmed"),
    (2, "#388e3c", "legend.status.false_positive"),
    (3, "#f9a825", "legend.status.needs_revision"),
)
#: The order charts stack and list the review statuses in. The Confirmed and
#: False positive steps are 2.8 apart under simulated deuteranopia (OKLab x100)
#: -- indistinguishable -- so Needs revision sits between them, which lifts the
#: worst adjacent pair to 13.8. This reorders, it does not repaint: the map and
#: the PMTiles paint values draw the same colours as before. All-pairs
#: separation still fails, so status is never drawn as a pie, donut, scatter or
#: choropleth; stacked bars, grouped bars and lines are fine.
STACK_ORDER = (1, 3, 2, 0)  # Confirmed, Needs revision, False positive, Not reviewed
#: (value, colour, label key) of the combined raster's band 4 states a run cannot analyse
#: (integrate.STATE_TOO_SMALL / STATE_OVER_CAP). Analysable pixels (1) are not drawn: the layer
#: reads a VRT of band 4 that maps 0 and 1 to nodata (alert_layers.states_vrt), because
#: localtileserver drops a colormap's alpha.
STATE_CLASSES = (
    (2, "#9e9e9e", "legend.state_too_small"),
    (3, "#fb8c00", "legend.state_over_cap"),
)
MASK_COLOR = "#757575"  # keep and exclude masks alike
MASK_OPACITY = 0.5
_GREY_RAMP = ("#000000", "#ffffff")


def lighten(hex_color: str, amount: float = 0.6) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    mix = lambda c: int(c + (255 - c) * amount)  # noqa: E731
    return f"#{mix(r):02x}{mix(g):02x}{mix(b):02x}"


def alert_colormap() -> ListedColormap:
    return ListedColormap([c for _, c, _ in ALERT_CLASSES], name="alerts")


def alert_ee_vis() -> dict:
    return {"min": ALERT_CLASSES[0][0], "max": ALERT_CLASSES[-1][0], "palette": [c for _, c, _ in ALERT_CLASSES]}


def status_colors(faded: bool = False) -> dict:
    return {code: (lighten(c) if faded else c) for code, c, _ in STATUS_CLASSES}


def state_colormap() -> ListedColormap:
    return ListedColormap([c for _, c, _ in STATE_CLASSES], name="states")


def mask_colormap() -> ListedColormap:
    # Two stops of the one grey: localtileserver turns a ListedColormap into a
    # LinearSegmentedColormap, which a single colour cannot make.
    return ListedColormap([MASK_COLOR, MASK_COLOR], name="mask")


def mask_ee_vis() -> dict:
    return {"palette": [MASK_COLOR]}


def preview_title(source, start, end) -> Label:
    return Label(key="legend.title.preview", args=(("source", source), ("start", str(start)), ("end", str(end))))


def harmonized_title(source, start, end) -> Label:
    return Label(key="legend.title.harmonized", args=(("source", source), ("start", str(start)), ("end", str(end))))


def _label(name) -> Label:
    """A dropdown label: a ``Label`` as given, or a user's literal name."""
    return name if isinstance(name, Label) else Label(literal=str(name))


def alert_legend(key: str, name, title: Label) -> LayerLegend:
    return LayerLegend(layer_id=key, label=_label(name), spec=LegendSpec(
        kind="chips", title=title, colors=tuple(c for _, c, _ in ALERT_CLASSES),
        labels=tuple(Label(key=k) for _, _, k in ALERT_CLASSES)))


def status_legend(key: str, name, title: Label, faded: bool = False) -> LayerLegend:
    colors = status_colors(faded)
    return LayerLegend(layer_id=key, label=_label(name), spec=LegendSpec(
        kind="chips", title=title, colors=tuple(colors[code] for code, _, _ in STATUS_CLASSES),
        labels=tuple(Label(key=k) for _, _, k in STATUS_CLASSES)))


def states_legend(key: str) -> LayerLegend:
    """The two states drawn (analysable pixels are masked)."""
    return LayerLegend(layer_id=key, label=Label(key="legend.states_title"), spec=LegendSpec(
        kind="chips", title=Label(key="legend.states_title"), colors=tuple(c for _, c, _ in STATE_CLASSES),
        labels=tuple(Label(key=k) for _, _, k in STATE_CLASSES)))


def mask_legend(key: str, mask_name: str, choice) -> LayerLegend:
    """One grey chip whose label names the active period's choice (spec §9.4)."""
    state = choice.mode if choice is not None and choice.on else "off"
    title = Label(key="legend.title.mask", args=(("name", mask_name),))
    return LayerLegend(layer_id=key, label=Label(key="legend.name.mask", args=(("name", mask_name),)),
                       spec=LegendSpec(kind="chips", title=title, colors=(MASK_COLOR,),
                                       labels=(Label(key=f"legend.mask.{state}"),)))


def _range(vmin, vmax) -> tuple:
    return Label(literal=format_number(vmin)), Label(literal=format_number(vmax))


def vis_legend(key: str, layer, *, month=None) -> LayerLegend:
    name = Label(literal=layer.name)
    if layer.kind == "nicfi":
        spec = LegendSpec(kind="note", title=name,
                          labels=(Label(key="legend.vis.nicfi", args=(("month", month or ""),)),))
    elif layer.kind in ("gee", "file") and len(layer.bands) == 3:
        spec = LegendSpec(kind="note", title=name, labels=(Label(key="legend.vis.rgb", args=(
            ("bands", ", ".join(str(b) for b in layer.bands)), ("min", format_number(layer.min)), ("max", format_number(layer.max)))),))
    elif layer.kind == "gee":
        palette = [c if c.startswith("#") else f"#{c}" for c in layer.palette] or list(_GREY_RAMP)
        cmap = LinearSegmentedColormap.from_list(layer.name or "vis", palette)
        spec = LegendSpec(kind="gradient", title=name, colors=gradient_colors(cmap), labels=_range(layer.min, layer.max))
    else:  # a GeoTIFF: its ramp, or the colormap of a layer saved before the ramps
        cmap = ramp_colormap(layer.palette, layer.name or "vis") if layer.palette else colormaps[layer.colormap]
        spec = LegendSpec(kind="gradient", title=name, colors=gradient_colors(cmap),
                          labels=_range(layer.min, layer.max))
    return LayerLegend(layer_id=key, label=name, spec=spec)
