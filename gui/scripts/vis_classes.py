"""Class lists for categorical visualization layers.

Pure (no Solara, no i18n); matplotlib is imported inside the function that needs it.
Colours are stored bare (no ``#``), as in ``vis_palettes``.
"""

from typing import List, Optional, Tuple

from deforestation_alerts.project.models import VisClass
from gui.scripts import vis_palettes
from gui.scripts.vis_palettes import DEFAULT_QUALITATIVE, _bare

MAX_CLASSES = 50


def classes_from_values(values, *, palette: str = DEFAULT_QUALITATIVE, known: Optional[dict] = None) -> List[VisClass]:
    """One class per distinct value, sorted; ``known`` (the band's ``*_class_*`` properties) wins."""
    ordered = sorted({int(v) for v in values})
    colors = vis_palettes.qualitative_colors(palette, len(ordered))
    meta = {}
    if known:
        for v, color, name in zip(known.get("values") or [], known.get("palette") or [], known.get("names") or []):
            meta[int(v)] = (_bare(color), str(name))
    zero_one = ordered == [0, 1] and not known
    out = []
    for i, v in enumerate(ordered):
        color, label = colors[i], str(v)
        if v in meta:
            color, label = meta[v]
        elif zero_one and v == 1:
            color = colors[0]
        out.append(VisClass(value=v, color=color, label=label, visible=not (zero_one and v == 0)))
    return out


def recolor(classes, palette: str) -> List[VisClass]:
    """The same classes painted from a palette in list order; value, label and visibility stay."""
    colors = vis_palettes.qualitative_colors(palette, len(classes))
    return [c.model_copy(update={"color": colors[i]}) for i, c in enumerate(classes)]


def visible_classes(classes) -> List[VisClass]:
    return sorted((c for c in classes if c.visible), key=lambda c: c.value)


def ee_remap(classes) -> Tuple[List[int], List[int], dict]:
    """``(from, to, vis)`` for ``Image.remap`` over the visible classes, drawn with a palette."""
    vis = visible_classes(classes)
    m = len(vis)
    return (
        [c.value for c in vis],
        list(range(m)),
        {"min": 0, "max": max(m - 1, 1), "palette": [_bare(c.color) for c in vis]},
    )


def _num(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def lut_text(classes) -> str:
    """A discrete ``colormap`` LUT: each visible class is a narrow spike over its value."""
    parts = []
    for k, c in enumerate(visible_classes(classes), start=1):
        v = c.value
        parts += [f"{_num(v - 0.25)}:0", f"{_num(v)}:{k}", f"{_num(v + 0.25)}:0"]
    return ",".join(parts)


def class_colormap(classes):
    from matplotlib.colors import ListedColormap
    colors = ["#" + _bare(c.color) for c in visible_classes(classes)]
    if len(colors) == 1:
        # Two stops of the one colour, as legend_specs.mask_colormap does.
        colors = colors * 2
    return ListedColormap(colors, name="classes")


def legend_colors_labels(classes) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    vis = visible_classes(classes)
    return (
        tuple("#" + _bare(c.color) for c in vis),
        tuple(c.label or str(c.value) for c in vis),
    )
