"""Aux tile as a Solara component (Phase 4 port of the old ipyvuetify class).

Architecture layer: view — imports solara and ipyvuetify for UI only.
No GEE calls; bindings use traitlets `.bind()` on a memoized widget tree.

NOTE: all ipyvuetify widgets (cards, inputs) are created inside use_memo
(dependencies=[]) so they are stable across re-renders and the .bind() chain
is attached exactly once.  Rebuilding them as plain Python objects on each
render would reattach bindings on every re-render and lose entered values.
See overview_tile.py for the same pattern with v.SimpleTable.
"""

import solara
import ipyvuetify as v
from pysepal import sepalwidgets as sw
from pysepal.planetapi import PlanetView
from pysepal.sepalwidgets.file_input import FileInput

from deforestation_alerts.message import cm
from deforestation_alerts.scripts.raster_attribute import band_names, build_attribute_config
from deforestation_alerts.scripts.attribute_source import gee_band_names
from gui.widget.custom_source_list import CustomSourceList
from gui.widget.text_style import MUTED


@solara.component
def AuxTile(aux_model, workspace):
    """Solara port of the old AuxTile ipyvuetify widget.

    Parameters
    ----------
    aux_model : AuxModel
        Domain model holding the auxiliary layer configuration traits.
        Widgets are bound to it once via the traitlets `.bind()` chain
        inside use_memo; no observers, no GEE calls.
    workspace : Workspace
        The session user-file adapter. Its client backs the two file pickers so
        they browse the user's SEPAL workspace, not the container's disk.
    """

    # ------------------------------------------------------------------
    # Memoized widget tree — created once per kernel, stable across
    # re-renders.  The .bind() chain is executed as part of the builder
    # so bindings are attached exactly once.
    # ------------------------------------------------------------------
    layout_widget = solara.use_memo(
        lambda: build_aux_layout(aux_model, workspace),
        dependencies=[],
    )

    solara.display(layout_widget)


_STATS = ["majority", "mean", "min", "max", "median"]


def _remote_file_input(workspace, extensions, **kwargs):
    """A file picker that browses the USER's SEPAL workspace.

    A bare ``sw.inputs.FileInput`` lists the CONTAINER's filesystem, where the
    user's rasters and report templates do not exist (inventory a33). Rooted at
    the SEPAL home rather than this module's results dir because these inputs
    point at files the user keeps anywhere. ``root`` is passed explicitly: with a
    client and no root the widget defaults it to "" and then rejects any absolute
    ``initial_folder``. The resulting ``.v_model`` is a REMOTE path, which is why
    ``attribute_source`` and the report builder stage their inputs down.
    """
    return FileInput(
        initial_folder=workspace.home,
        root=workspace.home,
        sepal_client=workspace.client,
        extensions=extensions,
        **kwargs,
    )


def _remote_band_names(workspace, remote_path):
    """Band names of a raster the user picked in their SEPAL workspace."""
    local = workspace.download_to_scratch(remote_path)
    try:
        return band_names(str(local))
    finally:
        workspace.discard_scratch(local)


def _build_attribute_controls(aux_model, workspace, gee_interface=None):
    """Controls: pick a GEE asset OR a workspace raster, 'Load bands', then
    per-band include/stat/column rows. Writes the assembled config to
    aux_model.attribute_raster on any change."""
    asset_input = sw.inputs.AssetSelect(
        gee_interface=gee_interface, types=["IMAGE"], label=cm.aux_tile.asset_label
    )
    file_input = _remote_file_input(
        workspace, [".tif", ".tiff"], label=cm.aux_tile.attribute_file_hint
    )
    load_btn = v.Btn(
        small=True, outlined=True, color="primary",
        children=[cm.aux_tile.attribute_load_bands],
    )
    rows_box = v.Container(class_="pa-0 ma-0", children=[])
    status = v.Html(tag="div", class_="caption ml-3", children=[])
    actions = v.Html(
        tag="div", class_="d-flex align-center mt-3", children=[load_btn, status]
    )

    # Mutable holder of the live per-band row widgets (for config assembly).
    state = {"rows": [], "source": None, "kind": None}

    stat_items = [
        {"text": getattr(cm.aux_tile, f"attribute_stat_{s}"), "value": s} for s in _STATS
    ]

    def _collect(*_):
        band_rows = [
            {"include": inc.v_model, "band": name, "stat": stat.v_model, "column": col.v_model}
            for (name, inc, stat, col) in state["rows"]
        ]
        if state["source"]:
            aux_model.attribute_raster = build_attribute_config(
                state["source"], state["kind"], band_rows
            )

    def _make_row(name):
        inc = v.Checkbox(v_model=True, class_="ma-0 pa-0", label=cm.aux_tile.attribute_include_label)
        stat = v.Select(items=stat_items, v_model="majority", dense=True,
                        label=cm.aux_tile.attribute_stat_label, style_="max-width: 130px")
        col = v.TextField(v_model=name, dense=True, label=cm.aux_tile.attribute_column_label,
                          style_="max-width: 160px")
        for w in (inc, stat, col):
            w.on_event("change", _collect)
        row = v.Row(class_="ma-0 pa-0", align="center",
                    children=[v.Col(cols=2, children=[inc]),
                              v.Col(cols=5, children=[stat]),
                              v.Col(cols=5, children=[col])])
        return name, inc, stat, col, row

    def _on_load(*_):
        # Prefer the GEE asset; else the uploaded local file.
        if asset_input.v_model:
            source, kind = asset_input.v_model, "gee"
            try:
                names = gee_band_names(source)
            except Exception as exc:  # surface, don't crash the dialog
                state.update(source=None, rows=[])
                rows_box.children = []
                status.children = [f"{exc}"]
                return
        elif file_input.v_model:
            source, kind = file_input.v_model, "local"
            try:
                # `source` is a REMOTE workspace path and rasterio only opens
                # local files, so stage it just long enough to read the header.
                names = _remote_band_names(workspace, source)
            except Exception as exc:
                state.update(source=None, rows=[])
                rows_box.children = []
                status.children = [f"{exc}"]
                return
        else:
            status.children = [cm.aux_tile.attribute_no_source]
            return

        state.update(source=source, kind=kind, rows=[])
        built = [_make_row(n) for n in names]
        state["rows"] = [(n, inc, stat, col) for (n, inc, stat, col, _row) in built]
        rows_box.children = [r[4] for r in built]
        status.children = []
        _collect()

    load_btn.on_event("click", _on_load)
    return [asset_input, file_input, actions, rows_box]


def _status_icon(owner, names, is_set):
    """Leading icon of a setting row: a filled check once the setting holds a
    value, an empty ring while it is still unset. Tracks ``owner``'s ``names``
    traits so a recipe load or a cleared input updates it too."""
    icon = v.Icon(small=True, class_="mr-2", children=["mdi-circle-outline"])

    def _update(*_):
        done = bool(is_set())
        text = cm.aux_tile.status_set if done else cm.aux_tile.status_empty
        icon.children = ["mdi-check-circle" if done else "mdi-circle-outline"]
        icon.color = "primary" if done else None
        icon.style_ = "" if done else MUTED
        icon.attributes = {"title": text, "aria-label": text}

    owner.observe(_update, names=names)
    _update()
    return icon


def _setting_row(icon, title, description, controls):
    """One setting: what it is and what it does on the left, its inputs on the
    right. Stacks into a single column on narrow screens."""
    about = v.Col(cols=12, md=4, class_="py-3", children=[
        v.Html(tag="div", class_="d-flex align-center font-weight-medium",
               children=[icon, title]),
        # Indented by the icon's width + margin so the text lines up under the title.
        v.Html(tag="div", class_="body-2 mt-1", style_=f"{MUTED} padding-left: 24px;",
               children=[description]),
    ])
    inputs = v.Col(cols=12, md=8, class_="py-3", children=controls)
    return v.Row(class_="ma-0 px-1", children=[about, inputs])


def _group(heading, rows):
    """A titled, outlined block of related setting rows separated by dividers."""
    body = []
    for i, row in enumerate(rows):
        if i:
            body.append(v.Divider())
        body.append(row)
    return v.Html(tag="section", class_="mb-5", children=[
        v.Html(tag="div", class_="subtitle-1 font-weight-medium mb-2", children=[heading]),
        # The bundled Vuetify has no outlined sheet; a mid-grey hairline at low
        # alpha reads as a border in both the light and the dark theme.
        v.Sheet(class_="rounded", style_="border: thin solid rgba(128, 128, 128, 0.3);",
                children=body),
    ])


def build_aux_layout(aux_model, workspace, gee_interface=None):
    """Build the settings list and bind widgets to aux_model traits.

    Returns the outermost ipyvuetify layout widget. ``Page`` hands this RAW
    widget straight to MapApp as the dialog content (no ``solara.display`` /
    ``render_fixed`` wrapper): a widget captured via ``solara.display`` lives
    inside an ``Output`` whose view is NOT recreated when MapApp destroys the
    dialog DOM (v-if) on close and rebuilds it on reopen — the card content
    vanishes on the 2nd open. A raw ipyvuetify widget tree is re-viewed from its
    model on reopen like any ipywidget, so it survives. (The ``AuxTile``
    component above keeps the solara.display form for any standalone use.)
    """
    t = cm.aux_tile

    # Every input here is optional, so the dialog reads as a settings list:
    # related settings grouped by where they take effect, each row saying what
    # it does next to its inputs and showing whether it is set yet.

    # Extra alert sources, added to the source select of the Filter step.
    ccdc_alerts_input = sw.inputs.AssetSelect(
        gee_interface=gee_interface, types=["IMAGE", "IMAGE_COLLECTION"], label=t.asset_label
    )
    # Custom alert sources: a list of named, RADD-shaped GEE assets (up to 5).
    custom_list = CustomSourceList(aux_model, gee_interface)

    # Map layers: the mask clips alerts, the auxiliary layer is reference only.
    fnf_input = sw.inputs.AssetSelect(
        gee_interface=gee_interface, types=["IMAGE"], label=t.asset_label
    )
    aux_input = sw.inputs.AssetSelect(
        gee_interface=gee_interface, types=["IMAGE"], label=t.asset_label
    )
    aux_viz = sw.TextField(label=t.aux_viz, v_model=None)

    attribute_controls = _build_attribute_controls(aux_model, workspace, gee_interface)

    form_input = _remote_file_input(workspace, [".docx"], label=t.report_template_pick)

    # Planet login. The OLD tile built this card but never added it to its
    # layout, so the Planet login UI was invisible; it is restored here. The
    # subscription chips stay disabled until a login succeeds, so they are
    # hidden until then rather than shown as an empty strip.
    planet_view = PlanetView()
    planet_model = planet_view.planet_model

    def _toggle_subscriptions(*_):
        if planet_model.authenticated:
            planet_view.w_info_view.show()
        else:
            planet_view.w_info_view.hide()

    planet_model.observe(_toggle_subscriptions, "authenticated")
    _toggle_subscriptions()

    def _row(names, title, description, controls, owner=aux_model):
        icon = _status_icon(owner, names, lambda: all(getattr(owner, n) for n in names))
        return _setting_row(icon, title, description, controls)

    groups = [
        _group(t.group_sources, [
            _row(["ccdc_layer"], t.ccdc_alerts_title, t.ccdc_alerts_desc,
                 [ccdc_alerts_input]),
            _row(["custom_alerts"], t.custom_alerts_title, t.custom_alerts_desc,
                 [custom_list]),
        ]),
        _group(t.group_layers, [
            _row(["mask_layer"], t.mask_asset_title, t.mask_asset_desc, [fnf_input]),
            _row(["aux_layer"], t.aux_asset_title, t.aux_asset_desc, [aux_input, aux_viz]),
        ]),
        _group(t.group_attributes, [
            _row(["attribute_raster"], t.attribute_title, t.attribute_desc,
                 attribute_controls),
        ]),
        _group(t.group_report, [
            _row(["custom_report_template"], t.report_template_title,
                 t.report_template_desc, [form_input]),
        ]),
        _group(t.group_planet, [
            _row(["authenticated"], t.planet_login_title, t.planet_login_desc,
                 [planet_view], owner=planet_model),
        ]),
    ]

    # The step panel's own opaque background covers the fullscreen main map
    # (see the z-index rule in solara_app.py), so no extra backdrop is needed.
    layout = v.Html(tag="div", class_="pa-1", children=[
        v.Html(tag="p", class_="body-2 mb-5", style_=MUTED, children=[t.intro]),
        *groups,
    ])

    # Bind widgets to model traits (traitlets linking; stable because this
    # function runs exactly once inside use_memo).
    aux_model.bind(ccdc_alerts_input, "ccdc_layer").bind(
        fnf_input, "mask_layer"
    ).bind(aux_input, "aux_layer").bind(aux_viz, "aux_layer_vis").bind(
        form_input, "custom_report_template"
    )

    return layout
