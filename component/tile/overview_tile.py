from sepal_ui import sepalwidgets as sw
from sepal_ui import aoi
from sepal_ui.mapping import SepalMap
from component.message import cm
import ipyvuetify as v
from sepal_ui.scripts.utils import init_ee
from traitlets import Any, Unicode, link
from component.scripts.overview_helper import *
from sepal_ui.mapping.layers_control import LayersControl
import ee

init_ee()


class OverviewTile(sw.Layout):
    def __init__(self, aoi_date_model, analyzed_alerts_model, selected_alerts_model):

        self._metadata = {"mount_id": "overview_tile"}
        self.analyzed_alerts_model = analyzed_alerts_model
        self.selected_alerts_model = selected_alerts_model
        self.aoi_date_model = aoi_date_model

        self.listaNumeros = [0, 0, 0, 0, 0]
        self.label61 = sw.Markdown(
            children=["Total Alerts: " + str(self.listaNumeros[0])]
        )
        self.label62 = sw.Markdown(
            children=["Reviewed Alerts: " + str(self.listaNumeros[1])]
        )
        self.label63 = sw.Markdown(
            children=["Confirmed Alerts: " + str(self.listaNumeros[2])]
        )
        self.label64 = sw.Markdown(
            children=["False positives: " + str(self.listaNumeros[3])]
        )
        self.label65 = sw.Markdown(children=["Maybe: " + str(self.listaNumeros[4])])
        self.initialize_layout()
        self.update_layout()

        ## Observe changes and update tile when it changes
        analyzed_alerts_model.observe(self.update_tile, "alerts_gdf")
        # analyzed_alerts_model.observe(self.update_tile,'actual_alert_id')

        super().__init__()

    def initialize_layout(self):

        # 1. Crear el mapa para seleccionar el área de estudio
        self.map_1 = SepalMap()
        # self.map_1.layout.height = "100%"

        # Side information labels
        self.label61 = sw.Markdown(
            children=["Total Alerts: " + str(self.listaNumeros[0])]
        )
        self.label62 = sw.Markdown(
            children=["Reviewed Alerts: " + str(self.listaNumeros[1])]
        )
        self.label63 = sw.Markdown(
            children=["Confirmed Alerts: " + str(self.listaNumeros[2])]
        )
        self.label64 = sw.Markdown(
            children=["False positives: " + str(self.listaNumeros[3])]
        )
        self.label65 = sw.Markdown(children=["Maybe: " + str(self.listaNumeros[4])])
        refresh_button = sw.Btn("Refresh", color="primary", outlined=True)
        refresh_button.on_event("click", self.update_button)

        # Layout 6 de la aplicación
        layout = sw.Row(
            style="height: 100vh;",
            children=[
                sw.Col(cols=9, style="height: 100vh;", children=[self.map_1]),
                sw.Col(
                    cols=3,
                    style="height: 100vh;",
                    children=[
                        self.label61,
                        self.label62,
                        self.label63,
                        self.label64,
                        self.label65,
                        refresh_button,
                    ],
                ),
            ],
        )
        self.children = [layout]

    def update_layout(self):
        if (
            self.analyzed_alerts_model.alerts_gdf is None
            or len(self.analyzed_alerts_model.alerts_gdf) == 0
        ):
            self.children = self.children
        else:
            self.map_1.clear()
            self.default_basemap = (
                "CartoDB.DarkMatter" if v.theme.dark is True else "CartoDB.Positron"
            )
            self.map_1.add_basemap(self.default_basemap)
            self.map_1.add(LayersControl(self.map_1))
            centroides_gdf = self.analyzed_alerts_model.alerts_gdf
            # Define a color dictionary where keys are field values and values are colors
            color_dict = {
                "Confirmed": "red",
                "False Positive": "black",
                "maybe": "orange",
                "Not reviewed": "gray",
            }
            self.map_1 = create_grouped_layers(
                centroides_gdf, "status", color_dict, self.map_1
            )
            # self.map_1 = add_point_layer(self.map_1, centroides_gdf, popup= ["status","area", "description"],  layer_name="Test Popups")
            # self.map_1 = create_grouped_layers_with_popup(centroides_gdf, 'status', color_dict, self.map_1, ["status","area", "description"])
            self.map_1.add_ee_layer(self.aoi_date_model.aoi, name="AOI")

            if self.selected_alerts_model.alert_selection_polygons is None:
                self.map_1.centerObject(self.aoi_date_model.aoi)
            else:
                draw_selection = ee.FeatureCollection(
                    self.selected_alerts_model.alert_selection_polygons
                )
                self.map_1.add_ee_layer(draw_selection, name="Drawn Item")
                self.map_1.centerObject(draw_selection)

            self.listaNumeros = calculateAlertClasses(centroides_gdf)
            self.label61.children = "Total Alerts: " + str(self.listaNumeros[0])
            self.label62.children = "Reviewed Alerts: " + str(self.listaNumeros[1])
            self.label63.children = "Confirmed Alerts: " + str(self.listaNumeros[2])
            self.label64.children = "False positives: " + str(self.listaNumeros[3])
            self.label65.children = "Maybe: " + str(self.listaNumeros[4])

    def update_tile(self, change):
        # Update the tile when aoi_date_model changes
        self.update_layout()  # Reinitialize the layout with the new data

    def update_button(self, widget, event, data):
        # Update the tile when aoi_date_model changes
        self.update_layout()  # Reinitialize the layout with the new data
