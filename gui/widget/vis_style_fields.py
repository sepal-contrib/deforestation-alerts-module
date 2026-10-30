"""The visualization form's style fields (gui/widget/vis_layer_modal): the
bands helper (``BandsField``: one band, the Band select, or the Bands
question), the Raster type of a one-band layer (``RasterTypeSelect``), a
Continuous layer's ``RampSelect``, an RGB layer's ``CompositeSelect``, a
Categorical layer's class list and palette menu, and the legend preview that
ends a style's fields.
"""

from typing import Optional

import reacton.ipyvuetify as rv
import solara

from deforestation_alerts.project.models import VisClass
from gui.i18n import t
from gui.scripts import vis_classes, vis_palettes
from gui.scripts.legend_data import LegendSpec, resolve_label
from gui.widget.enter_key import ENTER_OWN
from gui.widget.text_style import MUTED

_PREVIEW_BAR = "height:12px;border-radius:3px;border:1px solid rgba(0,0,0,0.1);background:{gradient};"
_TICKS = "display:flex;justify-content:space-between;font-size:0.75rem;opacity:0.75;"
_CHIPS = "display:flex;flex-wrap:wrap;gap:4px 12px;font-size:0.8125rem;"
_CHIP = "display:inline-flex;align-items:center;gap:4px;"
_SWATCH = "display:inline-block;width:12px;height:12px;border-radius:2px;border:1px solid rgba(0,0,0,0.2);background:{color};"

BANDS_MODES = ("one", "all")  # the Bands question's items: one band, or an RGB composite
STYLES = ("continuous", "categorical")

CHANNEL_COLORS = ("#e53935", "#43a047", "#1e88e5")  # the red, green and blue band dots
# The dot that labels a red, green or blue band select (class ``dv-channel-N``):
# drawn by CSS before the label, as a label slot loses its content in the
# browser once the select is picked from.
CHANNEL_CSS = " ".join(
    f".dv-channel-{i} .v-label::before{{content:'';display:inline-block;width:10px;height:10px;"
    f"border-radius:50%;margin-right:6px;vertical-align:baseline;background:{color};}}"
    for i, color in enumerate(CHANNEL_COLORS))


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def channel_class(channel: int) -> str:
    """The class that puts the red (0), green (1) or blue (2) dot before a band
    select's label (``CHANNEL_CSS``)."""
    return f"dv-channel-{channel}"


def _role_word(label: str) -> str:
    """A role's name inside a sentence: "Red" reads "red", "NIR" stays "NIR"."""
    return label if label.isupper() else label[:1].lower() + label[1:]


def _roles_list(roles, band_text) -> str:
    """The recognized roles as "B2 blue · B3 green · …", in the band list's
    order; a band the list does not hold (yet) is named as it is stored. Empty
    when no role is recognized."""
    order = {b: i for i, b in enumerate(band_text)}
    assigned = sorted(((b, r) for r in vis_palettes.ROLES if (b := roles.get(r)) is not None),
                      key=lambda pair: order.get(pair[0], len(order)))
    return " · ".join(f"{band_text.get(b, str(b))} {_role_word(t(f'steps.layers.role_{r}'))}"
                      for b, r in assigned)


# The dropdown fields: a component that shows more than one field stacks them
# with the form's own gap (form_dialog.FIELDS).
_STACK = "display:flex;flex-direction:column;gap:4px;"
_RAMP_ROW = "display:flex;align-items:center;gap:16px;"
RAMP_MENU = "dv-ramp-menu"  # the ramp select's menu, whose rows ramp_menu_css draws the bars of
_SELECTION_BAR = ("display:inline-block;width:72px;height:12px;border-radius:2px;margin-right:10px;"
                  "vertical-align:middle;background:{gradient};")


def _suggested(text: str, suggested: bool) -> str:
    """An item's text, marked "(suggested)" when ``suggested``."""
    return t("steps.layers.suggested", text=text) if suggested else text


@solara.component
def BandsField(band_items, mode, on_mode, band, on_band, rgb_found, disabled=False):
    """The bands helper. One band: a line naming it, no field. Two: the Band
    select. Three or more: the Bands question (one band, or an RGB composite,
    suggested when red, green and blue are ``rgb_found``), then the Band select
    when one band is drawn. ``band_items`` are the select's ``{"text", "value"}``."""
    n = len(band_items)

    def band_select(hint=None):
        rv.Select(label=t("steps.layers.field_band"), items=band_items, v_model=band, on_v_model=on_band,
                  dense=True, outlined=True, disabled=disabled, class_="dv-band", hint=hint or "",
                  persistent_hint=hint is not None).key("band")

    with rv.Html(tag="div", style_=_STACK):
        if n == 1:
            solara.Text(t("steps.layers.bands_one_hint", name=band_items[0]["text"]), style=MUTED)
        elif n == 2:
            band_select(t("steps.layers.bands_two_hint"))
        elif n >= 3:
            items = [{"text": _suggested(t(f"steps.layers.bands_{m}"), m == "all" and rgb_found), "value": m}
                     for m in BANDS_MODES]
            hint = t("steps.layers.bands_found" if rgb_found else "steps.layers.bands_not_found", count=n)
            rv.Select(label=t("steps.layers.field_bands"), items=items, v_model=mode, on_v_model=on_mode,
                      dense=True, outlined=True, disabled=disabled, hint=hint, persistent_hint=True).key("mode")
            if mode == "one":
                band_select()


@solara.component
def RasterTypeSelect(value, on_value, suggested, disabled=False):
    """How one band is drawn: Continuous (a colour ramp) or Categorical (a
    colour per class, ``suggested`` when the band looks categorical)."""
    items = [{"text": _suggested(t(f"steps.layers.style_{key}_item"), key == "categorical" and suggested),
              "value": key} for key in STYLES]
    rv.Select(label=t("steps.layers.field_raster_type"), items=items, v_model=value, on_v_model=on_value,
              dense=True, outlined=True, disabled=disabled)


@solara.component
def RampSelect(value, on_value, invert, on_invert, disabled=False):
    """The colour ramp select and, on its row, the Invert switch (off with no
    ramp: a layer saved before ramps keeps its palette). The chosen ramp shows
    its bar and name; the menu rows' bars are CSS (``ramp_menu_css``), as a
    widget slot cannot read the row it is drawn for."""
    items = [{"text": t(f"steps.layers.ramp_{key}"), "value": key} for key in vis_palettes.RAMPS]
    slots = []
    if value is not None:
        gradient = vis_palettes.ramp_css(value, invert)
        slots = [{"name": "selection", "children": rv.Html(tag="span", children=[
            rv.Html(tag="span", style_=_SELECTION_BAR.format(gradient=gradient)), t(f"steps.layers.ramp_{value}")])}]
    with rv.Html(tag="div", style_=_RAMP_ROW):
        solara.Style(vis_palettes.ramp_menu_css(RAMP_MENU, invert))
        rv.Select(label=t("steps.layers.field_ramp"), items=items, v_model=value, on_v_model=on_value, dense=True,
                  outlined=True, hide_details=True, disabled=disabled, menu_props={"contentClass": RAMP_MENU},
                  v_slots=slots, class_="dv-ramp-select", style_="flex:1;min-width:0;")
        rv.Switch(label=t("steps.layers.field_invert"), v_model=invert, on_v_model=on_invert, dense=True,
                  hide_details=True, disabled=disabled or value is None, class_="mt-0 pt-0")


@solara.component
def CompositeSelect(combo, on_combo, roles, band_text, fallback, disabled=False):
    """The RGB combination: each standard one names its bands ("True colour (B4
    / B3 / B2)"), or is disabled when its roles were not all recognized; then
    Custom. The hint lists the recognized roles, or, with none, the
    ``fallback`` bands used as red, green and blue."""
    items = []
    for key in vis_palettes.RGB_COMBOS:
        name = _capital(t(f"steps.layers.combo_{key}"))
        picked = vis_palettes.combo_bands(key, roles)
        if picked is None:
            items.append({"text": t("steps.layers.combo_not_found", name=name), "value": key, "disabled": True})
        else:
            bands = " / ".join(band_text.get(b, str(b)) for b in picked)
            items.append({"text": t("steps.layers.combo_item", name=name, bands=bands), "value": key})
    items.append({"text": t("steps.layers.combo_custom_item"), "value": "custom"})
    listed = _roles_list(roles, band_text)
    hint = (t("steps.layers.roles_detected", list=listed) if listed else
            t("steps.layers.roles_none_rgb", bands=", ".join(band_text.get(b, str(b)) for b in fallback)))
    rv.Select(label=t("steps.layers.field_composite"), items=items, v_model=combo, on_v_model=on_combo, dense=True,
              outlined=True, disabled=disabled, hint=hint, persistent_hint=True)


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
_CLASS_HEADER_SKIP = "min-width:calc(22px + 3em + 8px);"  # the swatch, the gap and the value
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
            # one "Label" over the column: a floating label per field needs the
            # top padding the compact rows drop, and rises into the row above
            if classes:
                with rv.Html(tag="div", key="header", class_="dv-class-header", style_=_CLASS_ROW + MUTED):
                    rv.Html(tag="span", style_=_CLASS_HEADER_SKIP)
                    rv.Html(tag="span", class_="text-caption", children=[t("steps.layers.class_label")])
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
                    rv.TextField(placeholder=t("steps.layers.class_label"), v_model=c.label,
                                 on_v_model=lambda value, i=i: _update(i, label=value or ""), dense=True,
                                 hide_details=True, disabled=disabled, class_="dv-class-label mt-0 pt-0")
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
        icon_name="mdi-palette", outlined=True, color="primary", small=True, disabled=disabled,
        classes=["dv-palette-menu"])
    with solara.lab.Menu(activator=activator, use_activator_width=False):
        with rv.List(dense=True):
            for key in vis_palettes.QUALITATIVE:
                solara.Button(
                    children=[rv.Html(tag="span", style_=_PALETTE_BAR.format(
                        gradient=vis_palettes.qualitative_css(key))), t(f"steps.layers.palette_{key}")],
                    text=True, on_click=lambda *_, k=key: on_value(k), disabled=disabled,
                    style="display:flex;justify-content:flex-start;width:100%;text-transform:none;",
                    classes=["dv-palette"] + (["dv-palette--selected"] if key == value else []))
