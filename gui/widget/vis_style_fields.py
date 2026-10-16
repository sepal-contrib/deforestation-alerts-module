"""The visualization form's style fields (gui/widget/vis_layer_modal): the
Bands question (keep every band for an RGB composite, or draw one), the Style
tiles of a one-band layer (Continuous or Categorical), a Categorical layer's
class list and palette menu, and the legend preview that ends a style's
fields.
"""

from typing import Optional

import reacton.ipyvuetify as rv
import solara

from deforestation_alerts.project.models import VisClass
from gui.i18n import t
from gui.scripts import vis_classes, vis_palettes
from gui.scripts.legend_data import LegendSpec, resolve_label
from gui.widget.enter_key import ENTER_OWN
from gui.widget.form_dialog import field_group
from gui.widget.text_style import MUTED

# Ramp and style cards (as spatial_risk's variable modal): a gradient bar over
# its name in a bordered card; the selected card is ringed and bold.
_RAMP_CARD = (
    "display:block;height:auto;min-width:0;width:100%;padding:6px;"
    "text-transform:none;letter-spacing:normal;font-weight:400;text-align:left;"
    "border:1px solid rgba(128,128,128,0.5);border-radius:4px;background:transparent;"
)
_RAMP_CARD_SELECTED = "box-shadow:0 0 0 2px currentColor;font-weight:600;"
_RAMP_BAR = (
    "display:block;height:14px;border-radius:3px;margin-bottom:6px;"
    "border:1px solid rgba(0,0,0,0.1);background:{gradient};"
)
_RAMP_GRID = "display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;width:100%;"
_STYLE_GRID = "display:grid;grid-template-columns:1fr 1fr;gap:8px;width:100%;margin:4px 0 8px;"
_HINT = "display:block;font-size:0.75rem;font-weight:400;opacity:0.75;white-space:normal;"
_PREVIEW_BAR = "height:12px;border-radius:3px;border:1px solid rgba(0,0,0,0.1);background:{gradient};"
_TICKS = "display:flex;justify-content:space-between;font-size:0.75rem;opacity:0.75;"
_CHIPS = "display:flex;flex-wrap:wrap;gap:4px 12px;font-size:0.8125rem;"
_CHIP = "display:inline-flex;align-items:center;gap:4px;"
_SWATCH = "display:inline-block;width:12px;height:12px;border-radius:2px;border:1px solid rgba(0,0,0,0.2);background:{color};"

BANDS_MODES = ("all", "one")
STYLES = ("continuous", "categorical")


def card_button(children, *, selected, on_click, disabled, classes):
    """A bordered card as a text button; ``classes`` gets a ``--selected`` twin when picked."""
    solara.Button(children=children, on_click=on_click, disabled=disabled, text=True,
                  style=_RAMP_CARD + (_RAMP_CARD_SELECTED if selected else ""),
                  classes=list(classes) + [f"{c}--selected" for c in classes if selected])


@solara.component
def BandsChoice(value, on_value, disabled=False):
    """Keep every band for an RGB composite ("all"), or draw one band ("one")."""
    with field_group(t("steps.layers.field_bands")):
        with rv.RadioGroup(v_model=value, on_v_model=on_value, dense=True, hide_details=True, disabled=disabled,
                           class_="mt-0"):
            for mode in BANDS_MODES:
                rv.Radio(label=t(f"steps.layers.bands_{mode}"), value=mode)


@solara.component
def StyleTiles(value, on_value, disabled=False):
    """How one band is drawn: a colour ramp (Continuous) or a colour per class (Categorical)."""
    bars = {"continuous": vis_palettes.ramp_css("viridis"),
            "categorical": vis_palettes.qualitative_css(vis_palettes.DEFAULT_QUALITATIVE)}
    with rv.Html(tag="div", style_=_STYLE_GRID):
        for key in STYLES:
            card_button(
                [rv.Html(tag="span", style_="display:block;width:100%;", children=[
                    rv.Html(tag="span", style_=_RAMP_BAR.format(gradient=bars[key])),
                    rv.Html(tag="span", children=[t(f"steps.layers.style_{key}")]),
                    rv.Html(tag="span", style_=_HINT, children=[t(f"steps.layers.style_{key}_hint")])])],
                selected=key == value, on_click=lambda *_, k=key: on_value(k), disabled=disabled,
                classes=["dv-style"])


def _css_color(color: str) -> str:
    return color if color.startswith("#") else f"#{color}"


@solara.component
def LegendPreview(spec: LegendSpec, mid: Optional[str] = None):
    """The legend the layer will get: a gradient bar with its min, ``mid`` and
    max ticks, or one chip per class."""
    labels = [resolve_label(label, t) for label in spec.labels]
    with rv.Html(tag="div", class_="dv-legend-preview", style_="margin-top:8px;"):
        if spec.kind == "gradient":
            gradient = "linear-gradient(to right, " + ", ".join(_css_color(c) for c in spec.colors) + ")"
            rv.Html(tag="div", key="bar", style_=_PREVIEW_BAR.format(gradient=gradient))
            ticks = [labels[0], mid, labels[-1]] if mid is not None and len(labels) >= 2 else labels
            with rv.Html(tag="div", key="ticks", style_=_TICKS):
                for i, text in enumerate(ticks):
                    rv.Html(tag="span", key=f"tick{i}", children=[text])
        elif spec.kind == "chips":
            with rv.Html(tag="div", key="chips", style_=_CHIPS):
                for i, (color, text) in enumerate(zip(spec.colors, labels)):
                    with rv.Html(tag="span", key=f"chip{i}", style_=_CHIP):
                        rv.Html(tag="span", style_=_SWATCH.format(color=_css_color(color)))
                        rv.Html(tag="span", children=[text])
        else:
            for text in labels:
                solara.Text(text, style=MUTED)


# The class rows: a swatch (its colour picker in a menu), the value, the label
# and the hide toggle; a hidden class is dimmed, as it is drawn transparent.
_CLASS_ROW = "display:flex;align-items:center;gap:8px;"
_CLASS_SWATCH = ("min-width:22px;width:22px;height:22px;padding:0;border-radius:4px;"
                 "border:1px solid rgba(0,0,0,0.25);background:{color} !important;")
_CLASS_VALUE = "min-width:3em;text-align:right;font-variant-numeric:tabular-nums;"
_PALETTE_BAR = "display:inline-block;width:72px;height:12px;border-radius:2px;margin-right:8px;background:{gradient};"


def _bare_hex(value: str) -> str:
    """A picker's ``#rrggbb[aa]`` as stored: six lowercase digits, no ``#``, alpha dropped."""
    return vis_palettes._bare(value)[:6]


def _parse_value(text):
    """The integer typed in "Add a value", or None when it is not a whole number."""
    try:
        number = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else None


@solara.component
def ClassList(classes, on_classes, disabled=False):
    """One row per class (swatch, value, label, hide toggle), then "Add a value":
    a new class takes the next colour of the palette the classes are painted
    from (the default palette's when they follow none). Enter in the field adds
    the value, never presses the form's Save."""
    added, set_added = solara.use_state("")
    add_error, set_add_error = solara.use_state(None)
    palette = vis_classes.palette_of(classes) or vis_palettes.DEFAULT_QUALITATIVE

    def _update(i, **changes):
        on_classes([c.model_copy(update=changes) if j == i else c for j, c in enumerate(classes)])

    def _add(text):
        value = _parse_value(text)
        if value is None or value in {c.value for c in classes}:
            set_add_error(t("steps.layers.err_class_value"))
            return
        color = vis_palettes.qualitative_colors(palette, len(classes) + 1)[-1]
        on_classes([*classes, VisClass(value=value, color=color, label=str(value))])
        set_added("")
        set_add_error(None)

    # the rows sit in their own box: a row added before "Add a value" must not
    # take its field's widget (and with it the Enter handler, use_event below)
    with rv.Html(tag="div", class_="dv-class-list", style_="margin-top:8px;"):
        with rv.Html(tag="div", key="rows"):
            for i, c in enumerate(classes):
                color = f"#{vis_palettes._bare(c.color)}"
                with rv.Html(tag="div", key=f"class{c.value}", class_="dv-class-row",
                             style_=_CLASS_ROW + ("" if c.visible else "opacity:0.45;")):
                    swatch = solara.Button(children=[], style=_CLASS_SWATCH.format(color=color), disabled=disabled,
                                           classes=["dv-class-swatch"])
                    with solara.lab.Menu(activator=swatch, close_on_content_click=False, use_activator_width=False):
                        rv.ColorPicker(v_model=color, on_v_model=lambda value, i=i: _update(i, color=_bare_hex(value)),
                                       hide_inputs=True, hide_mode_switch=True, mode="hexa")
                    rv.Html(tag="span", style_=_CLASS_VALUE, children=[str(c.value)])
                    rv.TextField(label=t("steps.layers.class_label"), v_model=c.label,
                                 on_v_model=lambda value, i=i: _update(i, label=value or ""), dense=True,
                                 hide_details=True, disabled=disabled, class_="mt-0 pt-0")
                    solara.Button(icon_name="mdi-eye" if c.visible else "mdi-eye-off", icon=True, small=True,
                                  on_click=lambda *_, i=i, shown=c.visible: _update(i, visible=not shown),
                                  disabled=disabled, classes=["dv-class-hide"])
        with rv.Html(tag="div", key="add", style_=_CLASS_ROW):
            field = rv.TextField(label=t("steps.layers.class_add"), v_model=added, on_v_model=set_added,
                                 type="number", dense=True, class_=ENTER_OWN,
                                 disabled=disabled or len(classes) >= vis_classes.MAX_CLASSES,
                                 error_messages=[add_error] if add_error else [], style_="max-width:12em;")
            # the widget's own value: the Enter may be handled before the typed text re-renders
            rv.use_event(field, "keydown.enter", lambda widget, *_: _add(widget.v_model))
            solara.Button(icon_name="mdi-plus", icon=True, small=True, on_click=lambda *_: _add(added),
                          disabled=disabled or len(classes) >= vis_classes.MAX_CLASSES, classes=["dv-class-add"])


@solara.component
def PaletteMenu(value, on_value, disabled=False):
    """The qualitative palettes, each as its colour bar and name; a pick recolours
    every class. ``value`` is None when the classes follow no palette (their own
    or hand-picked colours): then no palette is selected."""
    bar = (_PALETTE_BAR.format(gradient=vis_palettes.qualitative_css(value)) if value is not None
           else "display:none;")
    text = t("steps.layers.palette_menu")
    activator = solara.Button(
        children=[rv.Html(tag="span", style_=bar),
                  f"{text}: {t(f'steps.layers.palette_{value}')}" if value is not None else text],
        icon_name="mdi-palette", outlined=True, small=True, disabled=disabled, classes=["dv-palette-menu"])
    with solara.lab.Menu(activator=activator, use_activator_width=False):
        with rv.List(dense=True):
            for key in vis_palettes.QUALITATIVE:
                solara.Button(
                    children=[rv.Html(tag="span", style_=_PALETTE_BAR.format(
                        gradient=vis_palettes.qualitative_css(key))), t(f"steps.layers.palette_{key}")],
                    text=True, on_click=lambda *_, k=key: on_value(k), disabled=disabled,
                    style="display:flex;justify-content:flex-start;width:100%;text-transform:none;",
                    classes=["dv-palette"] + (["dv-palette--selected"] if key == value else []))
