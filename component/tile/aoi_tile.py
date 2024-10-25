from sepal_ui import sepalwidgets as sw
from sepal_ui import aoi
from sepal_ui.mapping import SepalMap
from component.message import cm
import ipyvuetify as v
from sepal_ui.scripts.utils import init_ee
from traitlets import Any, Unicode, link
import ee
from component.scripts.alert_filter_helper import *

init_ee()


class AoiTile(sw.Layout):
    def __init__(self, aoi_date_model, alert_filter_model, aux_model):

        self._metadata = {"mount_id": "aoi_tile"}
        self.aoi_date_model = aoi_date_model
        self.alert_filter_model = alert_filter_model
        self.aux_model = aux_model

        # Variables to describe collections
        self.lista_nombres_alertas = ["GLAD-L", "GLAD-S2", "RADD", "CCDC"]

        # Bind first empty state
        self.alert_filter_model.available_alerts_raster_list = []
        self.alert_filter_model.available_alerts_list = []

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
        search_button = sw.Btn(text="Search alerts", disabled=False)
        # self.check_inputs(search_button)
        search_button.on_event("click", self.create_available_alert_list)
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

    # Function to check if all variables are set and enable the search button
    def check_inputs(self, widget):
        if (
            self.start_date.v_model
            and self.end_date.v_model
            and self.aoi_view.model.v_model
        ):
            widget.disabled = True
        else:
            widget.disabled = False

    def bind_variables(self, widget, event, data):
        # self.aoi_view.model.observe(self.set_feature_collection, "name")
        self.aoi_date_model.aoi = self.aoi_view.model.feature_collection
        self.aoi_date_model.start_date = self.start_date.v_model
        self.aoi_date_model.end_date = self.end_date.v_model

    def set_feature_collection(self, change):
        """set feature collection trait"""

        if change["new"]:
            self.aoi_date_model.aoi = self.aoi_view.model.feature_collection

    def create_available_alert_list(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks

        self.aoi_date_model.aoi = self.aoi_view.model.feature_collection
        self.aoi_date_model.start_date = self.start_date.v_model
        self.aoi_date_model.end_date = self.end_date.v_model

        # If the AOI is None, return an empty alert list
        if (
            not self.aoi_date_model.aoi
            or not self.aoi_date_model.end_date
            or not self.aoi_date_model.start_date
        ):
            selected_alert_names = []
        else:
            # Colecciones de alertas
            # Reset list of alerts
            lista_colecciones_alertas = []
            lista_start_date_alertas = []
            lista_end_date_alertas = []
            selected_alert_rasters = []
            selected_alert_names = []
            filtered_alert_rasters = []

            # RADD
            radd = ee.ImageCollection("projects/radar-wur/raddalert/v1").filterMetadata(
                "layer", "contains", "alert"
            )
            dateInicioRADD = "2019-01-01"
            dateLastRADD = (
                radd.sort("system:time_end", False)
                .first()
                .get("version_date")
                .getInfo()
            )
            lista_start_date_alertas.append(dateInicioRADD)
            lista_end_date_alertas.append(dateLastRADD)
            # GLADS2
            gladS2 = ee.ImageCollection("projects/glad/S2alert")
            dateInicioGLADS2 = "2019-01-01"
            dateLastGLADS2 = (
                ee.Image("projects/glad/S2alert/alert").get("date").getInfo()
            )
            lista_start_date_alertas.append(dateInicioGLADS2)
            lista_end_date_alertas.append(dateLastGLADS2)
            # GLADl
            gladL18 = ee.ImageCollection("projects/glad/alert/2018final")
            dateInicioGLADL = "2018-01-01"
            dateLastGLADL = (
                ee.Date(
                    ee.ImageCollection("projects/glad/alert/UpdResult")
                    .sort("system:time_start", False)
                    .first()
                    .get("system:time_start")
                )
                .format("yyyy-MM-dd")
                .getInfo()
            )
            lista_start_date_alertas.append(dateInicioGLADL)
            lista_end_date_alertas.append(dateLastGLADL)
            # CCDC
            if self.aux_model.ccdc_layer:
                ccdc = ee.ImageCollection.fromImages(
                    [ee.Image(self.aux_model.ccdc_layer)]
                )
                dateInicioCCDC = (
                    ee.Date(
                        ee.Image(self.aux_model.ccdc_layer).get("system:time_start")
                    )
                    .format("yyyy-MM-dd")
                    .getInfo()
                )
                dateLastCCDC = (
                    ee.Date(ee.Image(self.aux_model.ccdc_layer).get("system:time_end"))
                    .format("yyyy-MM-dd")
                    .getInfo()
                )
                lista_start_date_alertas.append(dateInicioCCDC)
                lista_end_date_alertas.append(dateLastCCDC)

                lista_colecciones_alertas = [gladL18, gladS2, radd, ccdc]
            else:
                lista_colecciones_alertas = [gladL18, gladS2, radd]

            # Filter alerts based on the AOI/dayes and get list of available alert names
            for i in range(len(lista_colecciones_alertas)):
                alerta = lista_colecciones_alertas[i]
                nombre = self.lista_nombres_alertas[i]
                n1 = (
                    ee.ImageCollection(alerta)
                    .filterBounds(self.aoi_date_model.aoi)
                    .limit(10)
                    .size()
                    .getInfo()
                )
                date_test = date_range_check(
                    self.aoi_date_model.start_date,
                    self.aoi_date_model.end_date,
                    lista_start_date_alertas[i],
                    lista_end_date_alertas[i],
                )
                if n1 > 0 and date_test == "Pass":
                    selected_alert_rasters.append(alerta)
                    selected_alert_names.append(nombre)
                else:
                    selected_alert_rasters, selected_alert_names

            # Filter alerts based on AOI/dates and return rasters

            for i in range(len(selected_alert_names)):
                alerta = selected_alert_rasters[i]
                aoi = self.aoi_date_model.aoi
                start_date = self.aoi_date_model.start_date
                end_date = self.aoi_date_model.end_date
                nombre = selected_alert_names[i]
                raster = get_alerts(nombre, start_date, end_date, aoi, alerta)
                clip_raster = raster.clip(aoi.geometry())
                filtered_alert_rasters.append(clip_raster)

            self.alert_filter_model.available_alerts_list = selected_alert_names
            self.alert_filter_model.available_alerts_raster_list = (
                filtered_alert_rasters
            )
            # self.alert_filter_model.rnd_number =

            widget.loading = False  # Remove loading state
            widget.disabled = False  # Re-enable the button
