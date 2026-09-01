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


def _build_attribute_card(aux_model, workspace):
    """Card: pick a GEE asset OR local raster, 'Load bands', then per-band
    include/stat/column rows. Writes the assembled config to
    aux_model.attribute_raster on any change."""
    title = v.CardTitle(class_="pa-1 ma-1", children=[cm.aux_tile.attribute_title])
    asset_input = sw.inputs.AssetSelect(types=["IMAGE"], label=cm.aux_tile.attribute_asset_hint)
    file_input = _remote_file_input(
        workspace, [".tif", ".tiff"], label=cm.aux_tile.attribute_file_hint
    )
    load_btn = v.Btn(class_="ma-1", small=True, children=[cm.aux_tile.attribute_load_bands])
    rows_box = v.Container(class_="pa-0 ma-0", children=[])
    status = v.Html(tag="div", class_="pa-1 ma-1 caption", children=[])

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
    return v.Card(class_="pa-2 ma-0", outlined=True, hover=True,
                  children=[title, asset_input, file_input, load_btn, status, rows_box])


def build_aux_layout(aux_model, workspace):
    """Build the full card layout and bind widgets to aux_model traits.

    Returns the outermost ipyvuetify layout widget. ``Page`` hands this RAW
    widget straight to MapApp as the dialog content (no ``solara.display`` /
    ``render_fixed`` wrapper): a widget captured via ``solara.display`` lives
    inside an ``Output`` whose view is NOT recreated when MapApp destroys the
    dialog DOM (v-if) on close and rebuilds it on reopen — the card content
    vanishes on the 2nd open. A raw ipyvuetify widget tree is re-viewed from its
    model on reopen like any ipywidget, so it survives. (The ``AuxTile``
    component above keeps the solara.display form for any standalone use.)
    """
    # Card 1 — CCDC alerts asset
    title1 = v.CardTitle(class_="pa-1 ma-1", children=[cm.aux_tile.ccdc_alerts_title])
    ccdc_alerts_input = sw.inputs.AssetSelect(
        types=["IMAGE", "IMAGE_COLLECTION"], label=cm.aux_tile.ccdc_alerts_hint
    )
    card1 = v.Card(
        class_="pa-2 ma-0", outlined=True, hover=True, children=[title1, ccdc_alerts_input]
    )

    # Card 1b — Custom alert source (RADD-shaped), placed right after CCDC. A named
    # GEE asset (Alert + Date bands, RADD encoding) added as a selectable alert
    # source in the Filter tile.
    title_custom = v.CardTitle(
        class_="pa-1 ma-1", children=[cm.aux_tile.custom_alerts_title]
    )
    custom_name_input = sw.TextField(
        label=cm.aux_tile.custom_alerts_name_hint, v_model=None
    )
    custom_alerts_input = sw.inputs.AssetSelect(
        types=["IMAGE", "IMAGE_COLLECTION"], label=cm.aux_tile.custom_alerts_hint
    )
    card_custom = v.Card(
        class_="pa-2 ma-0", outlined=True,
        hover=True,
        children=[title_custom, custom_name_input, custom_alerts_input],
    )

    # Card 2 — Forest mask asset
    title2 = v.CardTitle(class_="pa-1 ma-1", children=[cm.aux_tile.mask_asset_title])
    fnf_input = sw.inputs.AssetSelect(
        types=["IMAGE"], label=cm.aux_tile.mask_asset_hint
    )
    card2 = v.Card(class_="pa-2 ma-0", outlined=True, hover=True, children=[title2, fnf_input])

    # Card 3 — Auxiliary asset + viz params
    title3 = v.CardTitle(class_="pa-1 ma-1", children=[cm.aux_tile.aux_asset_title])
    aux_input = sw.inputs.AssetSelect(
        types=["IMAGE"], label=cm.aux_tile.aux_asset_hint
    )
    aux_viz = sw.TextField(label=cm.aux_tile.aux_viz, v_model=None)
    card3 = v.Card(
        class_="pa-2 ma-0", outlined=True, hover=True, children=[title3, aux_input, aux_viz]
    )

    # Card 3b — Attribute alerts from a (multiband) raster.
    card_attr = _build_attribute_card(aux_model, workspace)

    # Card 4 — Report template upload
    title4 = v.CardTitle(
        class_="pa-1 ma-1", children=[cm.aux_tile.report_template_title]
    )
    form_input = _remote_file_input(workspace, [".docx"])
    card4 = v.Card(class_="pa-2 ma-0", outlined=True, hover=True, children=[title4, form_input])

    # Card 5 — Planet login. PlanetView itself is unchanged; note the OLD tile
    # built card5 but never added it to its layout (only cards 1-4 shipped), so
    # the Planet login UI was invisible. We render it here (second column),
    # restoring the clearly-intended UI.
    title5 = v.CardTitle(
        class_="pa-1 ma-1", children=[cm.aux_tile.planet_login_title]
    )
    planet_view = PlanetView()
    card5 = v.Card(class_="pa-2 ma-0", outlined=True, hover=True, children=[title5, planet_view])

    # Responsive grid: all cards flow left-to-right and wrap into rows so the
    # popup stays compact and needs no inner scroll. 3 across on wide viewports,
    # 2 on medium, 1 on narrow; align="start" keeps cards top-aligned so a taller
    # card (the 2-input ones) doesn't stretch its row-mates. The step panel's own
    # opaque background covers the fullscreen main map (see the z-index rule in
    # solara_app.py), so no extra backdrop is needed here.
    cards = [card1, card_custom, card2, card3, card_attr, card4, card5]
    layout = sw.Row(
        class_="ma-0",
        align="start",
        children=[
            sw.Col(cols=12, sm=6, md=4, class_="pa-1", children=[c])
            for c in cards
        ],
    )

    # Bind widgets to model traits (traitlets linking; stable because this
    # function runs exactly once inside use_memo).
    aux_model.bind(ccdc_alerts_input, "ccdc_layer").bind(
        custom_name_input, "custom_alert_name"
    ).bind(custom_alerts_input, "custom_alert_layer").bind(
        fnf_input, "mask_layer"
    ).bind(aux_input, "aux_layer").bind(aux_viz, "aux_layer_vis").bind(
        form_input, "custom_report_template"
    )

    return layout
