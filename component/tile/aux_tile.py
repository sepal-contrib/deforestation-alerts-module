from sepal_ui import sepalwidgets as sw
from sepal_ui import aoi
from sepal_ui.mapping import SepalMap
from component.message import cm
import ipyvuetify as v
from sepal_ui.scripts.utils import init_ee
from traitlets import Any, Unicode, link
from sepal_ui.planetapi import PlanetView

init_ee()


class AuxTile(sw.Layout):
    def __init__(self, aux_model):

        self._metadata = {"mount_id": "aux_tile"}
        self.aux_model = aux_model

        super().__init__()

        ccdc_alerts_input = sw.inputs.AssetSelect()
        card01 = v.Card(
            class_="pa-2",
            children=[v.CardTitle(children=["CCDC Alerts Asset"]), ccdc_alerts_input],
        )

        fnf_input = sw.inputs.AssetSelect()
        card02 = v.Card(
            class_="pa-2", children=[v.CardTitle(children=["Mask Asset"]), fnf_input]
        )

        aux_input = sw.inputs.AssetSelect()
        aux_viz = sw.TextField(
            label="Auxiliary Asset Visualization Parameters", v_model=None
        )
        card03 = v.Card(
            class_="pa-2",
            children=[v.CardTitle(children=["Auxiliary Asset"]), aux_input, aux_viz],
        )

        planet_view = PlanetView()
        card04 = v.Card(
            class_="pa-2",
            children=[v.CardTitle(children=["Planet Login Data"]), planet_view],
        )

        form_input = sw.inputs.FileInput()
        card05 = v.Card(
            class_="pa-2",
            children=[v.CardTitle(children=["Report template"]), form_input],
        )

        layout = sw.Row(
            fluid=True,
            children=[
                sw.Col(children=[card01, card02, card03, card04, card05]),
            ],
        )

        self.children = [layout]

        aux_model.bind(ccdc_alerts_input, "ccdc_layer").bind(
            fnf_input, "mask_layer"
        ).bind(aux_input, "aux_layer").bind(aux_viz, "aux_layer_vis").bind(
            form_input, "custom_report_template"
        )
