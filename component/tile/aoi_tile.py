from sepal_ui import sepalwidgets as sw
from sepal_ui import aoi
from sepal_ui.mapping import SepalMap
from component.message import cm
import ipyvuetify as v
from sepal_ui.scripts.utils import init_ee
from traitlets import Any, Unicode, link

init_ee()


class AoiTile(sw.Layout):
    def __init__(self, aoi_date_model):

        self._metadata = {"mount_id": "aoi_tile"}
        self.aoi_date_model = aoi_date_model

        super().__init__()

        # 1. Crear el mapa para seleccionar el área de estudio
        map_1 = SepalMap()
        map_1.layout.height = "100%"
        self.aoi_view = aoi.AoiView(gee=True, map_=map_1)
        card11 = v.Card(
            class_="pa-2",
            children=[
                v.CardTitle(children=["Area of interest selection"]),
                self.aoi_view,
            ],
        )

        # 2. Crear los widgets para la selección lugar y de fechas
        self.start_date = sw.DatePicker(label="Start date")
        self.end_date = sw.DatePicker(label="End date")
        card12 = v.Card(
            class_="pa-2",
            children=[
                v.CardTitle(children=["Date selection"]),
                self.start_date,
                self.end_date,
            ],
        )

        # 3.Search button
        search_button = sw.Btn(text="Search alerts")
        search_button.on_event("click", self.bind_variables)
        card13 = v.Card(class_="pa-2", children=[search_button])

        # Layout 1 de la aplicación
        layout1 = sw.Row(
            children=[
                sw.Col(cols=9, style="height: 100vh;", children=[map_1]),
                sw.Col(
                    cols=3, style="height: 100vh;", children=[card11, card12, card13]
                ),
            ]
        )

        self.children = [layout1]

    def bind_variables(self, widget, event, data):
        # self.aoi_view.model.observe(self.set_feature_collection, "name")
        self.aoi_date_model.aoi = self.aoi_view.model.feature_collection
        self.aoi_date_model.start_date = self.start_date.v_model
        self.aoi_date_model.end_date = self.end_date.v_model

    def set_feature_collection(self, change):
        """set feature collection trait"""

        if change["new"]:
            self.aoi_date_model.aoi = self.aoi_view.model.feature_collection
