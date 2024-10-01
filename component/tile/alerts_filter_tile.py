from sepal_ui import sepalwidgets as sw
from sepal_ui import aoi
from sepal_ui.mapping import SepalMap
from component.message import cm
import ipyvuetify as v
from sepal_ui.scripts.utils import init_ee
from traitlets import Any, Unicode, link
from component.scripts.alert_filter_helper import *
from sepal_ui.mapping.draw_control import DrawControl
from sepal_ui.mapping.layers_control import LayersControl

import ee

init_ee()


class SepalCard(sw.SepalWidget, v.Card):
    def __init__(self, **kwargs):

        super().__init__(**kwargs)


class AlertsFilterTile(sw.Layout):
    def __init__(self, aoi_date_model, aux_model, alert_filter_model, selected_alerts_model):

        self._metadata = {"mount_id": "filter_alerts"}
        self.aoi_date_model = aoi_date_model
        self.aux_model = aux_model
        self.alert_filter_model = alert_filter_model
        self.selected_alerts_model = selected_alerts_model

        # Variables to describe collections
        self.lista_nombres_alertas = ["GLAD-L", "GLAD-S2", "RADD", "CCDC"]
        self.lista_colecciones_alertas = []
        self.lista_start_date_alertas = []
        self.lista_end_date_alertas = []
        self.lista_vis_params = [{"min": 1, "max": 2, "palette": ["orange", "purple"]}]

        # Expected outputs from ee
        self.selected_alert_rasters = []
        self.selected_alert_names = []
        self.filtered_alert_rasters = []

        # user interface elements
        self.card00 = None
        self.card01 = None
        self.card02 = None
        self.card03 = None
        self.card04 = None
        self.card05 = None

        self.create_alert_list()
        self.initialize_layout()
        self.update_layout()
        ## Observe changes in aoi_date_model and update tile when it changes
        aoi_date_model.observe(self.update_tile)

        super().__init__()

    def create_alert_list(self):
        # If the AOI is None, return an empty alert list
        if (
            not self.aoi_date_model.aoi
            or not self.aoi_date_model.end_date
            or not self.aoi_date_model.start_date
        ):
            self.selected_alert_names.clear()
        else:
            # Colecciones de alertas
            # Reset list of alerts
            self.lista_colecciones_alertas.clear()
            self.lista_start_date_alertas.clear()
            self.lista_end_date_alertas.clear()
            self.selected_alert_rasters.clear()
            self.selected_alert_names.clear()
            self.filtered_alert_rasters.clear()

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
            self.lista_start_date_alertas.append(dateInicioRADD)
            self.lista_end_date_alertas.append(dateLastRADD)
            # GLADS2
            gladS2 = ee.ImageCollection("projects/glad/S2alert")
            dateInicioGLADS2 = "2019-01-01"
            dateLastGLADS2 = (
                ee.Image("projects/glad/S2alert/alert").get("date").getInfo()
            )
            self.lista_start_date_alertas.append(dateInicioGLADS2)
            self.lista_end_date_alertas.append(dateLastGLADS2)
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
            self.lista_start_date_alertas.append(dateInicioGLADL)
            self.lista_end_date_alertas.append(dateLastGLADL)
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
                self.lista_start_date_alertas.append(dateInicioCCDC)
                self.lista_end_date_alertas.append(dateLastCCDC)

                self.lista_colecciones_alertas = [gladL18, gladS2, radd, ccdc]
            else:
                self.lista_colecciones_alertas = [gladL18, gladS2, radd]

            # Filter alerts based on the AOI/dayes and get list of available alert names
            for i in range(len(self.lista_colecciones_alertas)):
                alerta = self.lista_colecciones_alertas[i]
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
                    self.lista_start_date_alertas[i],
                    self.lista_end_date_alertas[i],
                )
                if n1 > 0 and date_test == "Pass":
                    self.selected_alert_rasters.append(alerta)
                    self.selected_alert_names.append(nombre)
                else:
                    self.selected_alert_rasters, self.selected_alert_names

            # Filter alerts based on AOI/dates and return rasters

            for i in range(len(self.selected_alert_names)):
                alerta = self.selected_alert_rasters[i]
                aoi = self.aoi_date_model.aoi
                start_date = self.aoi_date_model.start_date
                end_date = self.aoi_date_model.end_date
                nombre = self.selected_alert_names[i]
                raster = get_alerts(nombre, start_date, end_date, aoi, alerta)
                clip_raster = raster.clip(aoi.geometry())
                self.filtered_alert_rasters.append(clip_raster)

    def initialize_layout(self):
        # Create map
        self.map_2 = SepalMap()
        self.map_2.layout.height = "100%"
        # Create UI components based on the alert list

        mkd = sw.Markdown(
            "No hay alertas disponibles para el área/fechas seleccionadas"
        )
        self.card00 = SepalCard(
            class_="pa-2", children=[v.CardTitle(children=["Available alerts"]), mkd]
        )
        self.checkbox_container21 = v.Select(
            items=self.selected_alert_names,
            v_model=self.selected_alert_names,
            label="Select one or multiple alert sources",
            multiple=True,
            clearable=True,
            chips=True,
        )
        self.card01 = SepalCard(
            class_="pa-2",
            children=[
                v.CardTitle(children=["Available alerts"]),
                self.checkbox_container21,
            ],
        )
        min_area_input = sw.TextField(v_model=None)
        self.card02 = SepalCard(
            class_="pa-2",
            children=[v.CardTitle(children=["Min alert size (ha)"]), min_area_input],
        )

        # List of options
        alert_selection_method_list = [
            "Chose by drawn items",
            "Prioritize Top Alerts by size",
            "Prioritize Top Alerts by date",
            "All",
        ]
        # Create and display the checkboxes
        checkbox_container22 = v.Select(
            items=alert_selection_method_list,
            v_model=None,
            multiple=False,
            clearable=True,
            chips=True,
        )
        self.card03 = SepalCard(
            class_="pa-2",
            children=[
                v.CardTitle(children=["Alert selection method"]),
                checkbox_container22,
            ],
        )
        # Define the callback function that will be triggered
        def prior_option(widget, event, data):
            if data in (
                "Prioritize Top Alerts by size",
                "Prioritize Top Alerts by date",
            ):
                self.card04.show()
            else:
                self.card04.hide()

        checkbox_container22.on_event("change", prior_option)

        number_of_alerts = sw.TextField(v_model=20)
        self.card04 = SepalCard(
            class_="pa-2",
            children=[v.CardTitle(children=["Number of alerts"]), number_of_alerts],
        )
        analyze_button = sw.Btn(text="Analyze alerts")
        analyze_button.on_event("click", self.bind_user_input_variables)
        self.card05 = v.Card(class_="pa-2", children=[analyze_button])

        layout = sw.Row(
            children=[
                sw.Col(cols=9, style="height: 100vh;", children=[self.map_2]),
                sw.Col(
                    cols=3,
                    style="height: 100vh;",
                    children=[
                        self.card00,
                        self.card01,
                        self.card03,
                        self.card02,
                        self.card04,
                        self.card05,
                    ],
                ),
            ]
        )

        self.children = [layout]

    def update_layout(self):
        self.map_2.clear()
        self.default_basemap = (
            "CartoDB.DarkMatter" if v.theme.dark is True else "CartoDB.Positron"
        )
        self.map_2.add_basemap(self.default_basemap)
        self.map_2.add(DrawControl(self.map_2))
        self.map_2.add(LayersControl(self.map_2))

        if (len(self.selected_alert_names)) == 0 or not (self.selected_alert_names):
            self.card00.show(), self.card01.hide(), self.card02.hide(), self.card03.hide(), self.card04.hide(), self.card05.hide()
        else:
            self.checkbox_container21 = v.Select(
                items=self.selected_alert_names,
                v_model=self.selected_alert_names,
                label="Select one or multiple alert sources",
                multiple=True,
                clearable=True,
                chips=True,
            )
            self.card01 = SepalCard(
            class_="pa-2",
            children=[
                v.CardTitle(children=["Available alerts"]),
                self.checkbox_container21,
            ],
            )
            layout = sw.Row(
            children=[
                sw.Col(cols=9, style="height: 100vh;", children=[self.map_2]),
                sw.Col(
                    cols=3,
                    style="height: 100vh;",
                    children=[
                        self.card00,
                        self.card01,
                        self.card03,
                        self.card02,
                        self.card04,
                        self.card05,
                        ],
                    ),
                ]
            )

            self.children = [layout]

            # Add content to map
            # self.map_2.zoom_ee_object(aoi_date_model.aoi, zoom_out = 3)
            self.map_2.add_ee_layer(self.aoi_date_model.aoi, name="AOI")
            self.map_2.centerObject(self.aoi_date_model.aoi)
            for i in range(len(self.selected_alert_names)):
                self.map_2.add_ee_layer(
                    self.filtered_alert_rasters[i].select("alert").selfMask(),
                    name=self.selected_alert_names[i],
                    vis_params=self.lista_vis_params[0],
                )
                self.map_2.add_ee_layer(
                    self.filtered_alert_rasters[i]
                    .select("date")
                    .selfMask()
                    .randomVisualizer(),
                    name=self.selected_alert_names[i] + " date",
                )
            self.card00.hide(), self.card01.show(), self.card02.show(), self.card03.show(), self.card04.hide(), self.card05.show()

    def update_tile(self, change):
        #print("observed")
        if change["new"]:
            # Update the tile when aoi_date_model changes
            self.create_alert_list()  # Recreate the alert list
            self.update_layout()  # Reinitialize the layout with the new data
   
    def bind_user_input_variables(self, widget, event, data):
        self.alert_filter_model.available_alerts_list = self.card01.children[1].v_model
        self.alert_filter_model.alert_selection_method = self.card03.children[1].v_model
        self.alert_filter_model.min_area = self.card02.children[1].v_model
        self.alert_filter_model.max_number_alerts = self.card04.children[1].v_model
        self.alert_filter_model.available_alerts_raster_list = self.filtered_alert_rasters


        
        
