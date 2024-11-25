from sepal_ui import sepalwidgets as sw
from sepal_ui import aoi
from sepal_ui.mapping import SepalMap
import ipyvuetify as v
from IPython.display import display, HTML

from sepal_ui.scripts import utils as su
from component.message import cm

from traitlets import Any, Unicode, link

from component.scripts.aoi_helper import *

import ee

su.init_ee()


class AoiTile(sw.Layout):
    def __init__(self, aoi_date_model, alert_filter_model, aux_model, app_tile_model):

        self._metadata = {"mount_id": "aoi_tile"}
        self.aoi_date_model = aoi_date_model
        self.alert_filter_model = alert_filter_model
        self.aux_model = aux_model
        self.app_tile_model = app_tile_model

        # Bind first empty state
        self.alert_filter_model.available_alerts_raster_list = []
        self.alert_filter_model.available_alerts_list = []

        self.search_button = sw.Btn(msg="Search alerts")
        self.search_button_alert = sw.Alert().hide()
        self.process_alerts = su.loading_button(
            alert=self.search_button_alert, button=self.search_button
        )(self.process_alerts)

        super().__init__()

        # 1. Crear el mapa para seleccionar el área de estudio
        display(
            HTML(
                """
        <style>
            .custom-map-class {
                width: 100% !important;
                height: 90vh !important;
                }
        </style>
        """
            )
        )

        self.map_1 = SepalMap()
        self.map_1.add_class("custom-map-class")
        self.map_1.add_basemap("SATELLITE")
        self.aoi_view = aoi.AoiView(gee=True, map_=self.map_1)
        self.aoi_view.flat = True
        section_title1 = v.CardTitle(class_="pa-1 ma-1", children=["AOI selection"])

        card11 = v.Card(
            class_="pa-3 ma-5",
            hover=True,
            children=[
                section_title1,
                self.aoi_view,
            ],
        )

        # 2. Crear los widgets para la selección lugar y de fechas
        self.start_date = sw.DatePicker(label="Start date")
        self.end_date = sw.DatePicker(label="End date")
        section_title2 = v.CardTitle(class_="pa-1 ma-1", children=["Date selection"])

        # 3.Search button
        self.search_button.on_event("click", self.process_alerts)

        card12 = v.Card(
            class_="pa-3 ma-5",
            hover=True,
            children=[
                section_title2,
                self.start_date,
                self.end_date,
                self.search_button,
                self.search_button_alert,
            ],
        )

        card0 = v.Card(class_="py-2", children=[card11, card12])

        # Layout 1 de la aplicación
        layout = sw.Row(
            dense=True,
            children=[
                sw.Col(cols=10, children=[self.map_1]),
                sw.Col(cols=2, children=[card0]),
            ],
        )

        self.children = [layout]

        self.alert_filter_model.alerts_dictionary = create_basic_alerts_dictionary()
        aux_model.observe(self.update_dictionary_ccdc, "ccdc_layer")

    def update_dictionary_ccdc(self, change):
        # Update the alerts dictionary when ccdc model changes
        if self.aux_model.ccdc_layer:
            add_ccdc_alerts_dictionary(
                self.alert_filter_model.alerts_dictionary, self.aux_model.ccdc_layer
            )
        elif self.aux_model.ccdc_layer is None:
            remove_ccdc_alerts_dictionary(self.alert_filter_model.alerts_dictionary)

    def process_alerts(self, widget, event, data):
        # Check inputs
        aoi, date1, date2 = check_aoi_inputs(self)
        dictionary = self.alert_filter_model.alerts_dictionary

        # Save data to model
        self.aoi_date_model.aoi = self.aoi_view.model.feature_collection
        self.aoi_date_model.start_date = self.start_date.v_model
        self.aoi_date_model.end_date = self.end_date.v_model

        # Generate list of available names and dictionary of filtered rasters
        self.alert_filter_model.available_alerts_list = (
            create_available_alert_dictionary(dictionary, aoi, date1, date2)
        )
        self.alert_filter_model.available_alerts_raster_list = (
            create_filtered_alert_raster_dictionary(
                self.alert_filter_model.available_alerts_list,
                aoi,
                date1,
                date2,
                self.aux_model.ccdc_layer,
            )
        )
        self.app_tile_model.current_page_view = "filter_alerts"

    def load_saved_parameters(data):
        self.aoi_view.model.feature_collection = data.get("aoi", self.aoi)
        self.start_date.v_model = data.get("start_date", self.start_date)
        self.end_date.v_model = data.get("end_date", self.end_date)
