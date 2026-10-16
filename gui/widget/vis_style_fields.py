"""The visualization form's style fields (gui/widget/vis_layer_modal): the
Bands question (keep every band for an RGB composite, or draw one), the Style
tiles of a one-band layer (Continuous or Categorical), and the legend preview
that ends a style's fields.
"""

from typing import Optional

import reacton.ipyvuetify as rv
import solara

from gui.i18n import t
from gui.scripts import vis_palettes
from gui.scripts.legend_data import LegendSpec, resolve_label
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
