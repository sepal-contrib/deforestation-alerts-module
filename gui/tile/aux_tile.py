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

from deforestation_alerts.message import cm


@solara.component
def AuxTile(aux_model):
    """Solara port of the old AuxTile ipyvuetify widget.

    Parameters
    ----------
    aux_model : AuxModel
        Domain model holding the auxiliary layer configuration traits.
        Widgets are bound to it once via the traitlets `.bind()` chain
        inside use_memo; no observers, no GEE calls.
    """

    # ------------------------------------------------------------------
    # Memoized widget tree — created once per kernel, stable across
    # re-renders.  The .bind() chain is executed as part of the builder
    # so bindings are attached exactly once.
    # ------------------------------------------------------------------
    layout_widget = solara.use_memo(
        lambda: _build_layout(aux_model),
        dependencies=[],
    )

    solara.display(layout_widget)


def _build_layout(aux_model):
    """Build the full card layout and bind widgets to aux_model traits.

    Called exactly once (inside use_memo).  Returns the outermost layout
    widget so solara.display() can render it.
    """
    # Card 1 — CCDC alerts asset
    title1 = v.CardTitle(class_="pa-1 ma-1", children=[cm.aux_tile.ccdc_alerts_title])
    ccdc_alerts_input = sw.inputs.AssetSelect(
        types=["IMAGE", "IMAGE_COLLECTION"], label=cm.aux_tile.ccdc_alerts_hint
    )
    card1 = v.Card(
        class_="pa-3 ma-5", hover=True, children=[title1, ccdc_alerts_input]
    )

    # Card 2 — Forest mask asset
    title2 = v.CardTitle(class_="pa-1 ma-1", children=[cm.aux_tile.mask_asset_title])
    fnf_input = sw.inputs.AssetSelect(
        types=["IMAGE"], label=cm.aux_tile.mask_asset_hint
    )
    card2 = v.Card(class_="pa-3 ma-5", hover=True, children=[title2, fnf_input])

    # Card 3 — Auxiliary asset + viz params
    title3 = v.CardTitle(class_="pa-1 ma-1", children=[cm.aux_tile.aux_asset_title])
    aux_input = sw.inputs.AssetSelect(
        types=["IMAGE"], label=cm.aux_tile.aux_asset_hint
    )
    aux_viz = sw.TextField(label=cm.aux_tile.aux_viz, v_model=None)
    card3 = v.Card(
        class_="pa-3 ma-5", hover=True, children=[title3, aux_input, aux_viz]
    )

    # Card 4 — Report template upload
    title4 = v.CardTitle(
        class_="pa-1 ma-1", children=[cm.aux_tile.report_template_title]
    )
    form_input = sw.inputs.FileInput(extensions=[".docx"])
    card4 = v.Card(class_="pa-3 ma-5", hover=True, children=[title4, form_input])

    # Card 5 — Planet login. PlanetView itself is unchanged; note the OLD tile
    # built card5 but never added it to its layout (only cards 1-4 shipped), so
    # the Planet login UI was invisible. We render it here (second column),
    # restoring the clearly-intended UI.
    title5 = v.CardTitle(
        class_="pa-1 ma-1", children=[cm.aux_tile.planet_login_title]
    )
    planet_view = PlanetView()
    card5 = v.Card(class_="pa-3 ma-5", hover=True, children=[title5, planet_view])

    # Two-column layout: aux settings left, Planet right. The step panel's own
    # opaque background covers the fullscreen main map (see the z-index rule in
    # solara_app.py), so no extra backdrop is needed here.
    layout = sw.Row(
        children=[
            sw.Col(children=[card1, card2, card3, card4]),
            sw.Col(children=[card5]),
        ],
    )

    # Bind widgets to model traits (traitlets linking; stable because this
    # function runs exactly once inside use_memo).
    aux_model.bind(ccdc_alerts_input, "ccdc_layer").bind(
        fnf_input, "mask_layer"
    ).bind(aux_input, "aux_layer").bind(aux_viz, "aux_layer_vis").bind(
        form_input, "custom_report_template"
    )

    return layout
