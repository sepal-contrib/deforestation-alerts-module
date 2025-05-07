from sepal_ui import sepalwidgets as sw
from sepal_ui import aoi
from sepal_ui.mapping import SepalMap
import ipyvuetify as v
from component.widget.custom_sw import CustomBtnWithLoader
from IPython.display import display, HTML

from sepal_ui.scripts import utils as su
from component.message import cm

from traitlets import Any, Unicode, link

from component.scripts.aoi_helper import *

import ee

su.init_ee()

class SepalCard(sw.SepalWidget, v.Card):
    def __init__(self, **kwargs):
        kwargs["class_"] = "pa-3 ma-3"
        super().__init__(**kwargs)
        
class SepalCardTitle(sw.SepalWidget, v.CardTitle):
    def __init__(self, **kwargs):
        kwargs["class_"] = "pa-1 ma-1"
        super().__init__(**kwargs)


class AoiTile(sw.Layout):
    def __init__(self, aoi_date_model, alert_filter_model, aux_model, app_tile_model):

        self._metadata = {"mount_id": "aoi_tile"}
        self.aoi_date_model = aoi_date_model
        self.alert_filter_model = alert_filter_model
        self.aux_model = aux_model
        self.app_tile_model = app_tile_model

        # Variable definition and model traits
        self.alert_filter_model.available_alerts_raster_list = []
        self.alert_filter_model.available_alerts_list = []
        self.alert_filter_model.alerts_dictionary = create_basic_alerts_dictionary()
        aux_model.observe(self.update_dictionary_ccdc, "ccdc_layer")

        # Create search button and functions
        self.search_button = CustomBtnWithLoader(
            text=cm.aoi_tile.search_button, loader_type="text"
        )
        self.search_button_alert = sw.Alert().hide()
        self.process_alerts = su.loading_button(
            alert=self.search_button_alert, button=self.search_button
        )(self.process_alerts)

        self.initialize_layout()

        super().__init__()

    def initialize_layout(self):
        # Map Style
        display(
            HTML(
                """
        <style>
            .custom-map-class {
                width: 100% !important;
                height: 80vh !important;
                }
        </style>
        """
            )
        )
        # Create map
        self.map_1 = SepalMap()
        self.map_1.add_class("custom-map-class")
        self.map_1.add_basemap("SATELLITE")

        # Create user interface components
        # Create AOI selection widget
        section_title1 = SepalCardTitle(
            children=[cm.aoi_tile.aoi_selection_title]
        )
        self.aoi_view = aoi.AoiView(
            gee=True,
            map_=self.map_1,
            methods=["ADMIN1", "ADMIN2", "SHAPE", "DRAW", "ASSET"],
        )
        self.aoi_view.flat = True

        # Create widget for date selection
        section_title2 = SepalCardTitle(
            children=[cm.aoi_tile.date_selection_title]
        )
        self.start_date = sw.DatePicker(
            label=cm.aoi_tile.start_date_label,
            class_="pa-1 ma-1",
        )
        self.end_date = sw.DatePicker(
            label=cm.aoi_tile.end_date_label,
            class_="pa-1 ma-1",
        )

        # Define search button function
        self.search_button.on_event("click", self.process_alerts)

        # Tile Layout
        card1 = SepalCard(
            hover=True,
            dense=True,
            children=[
                section_title1,
                self.aoi_view,
            ],
        )
        card2 = SepalCard(
            hover=True,
            dense=True,
            children=[
                section_title2,
                self.start_date,
                self.end_date,
                self.search_button,
                self.search_button_alert,
            ],
        )
        # Two-panel layout using Flex
        left_panel = v.Flex(
            children=[self.map_1],
            style_='flex: 1 1 auto ; overflow: hidden'
        )
        right_panel = v.Flex(
            children=[card1,card2],
            style_='flex: 0 0 16rem ; overflow: auto'
        )

        self.children = [left_panel, right_panel]

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
        widget.set_loader_text(cm.aoi_tile.search_button_alerts_text.loader_text1)
        aoi, date1, date2 = check_aoi_inputs(self)
        dictionary = self.alert_filter_model.alerts_dictionary

        # Save data to model
        widget.set_loader_text(cm.aoi_tile.search_button_alerts_text.loader_text2)
        self.aoi_date_model.admin = self.aoi_view.model.admin
        self.aoi_date_model.asset_name = self.aoi_view.model.asset_name
        self.aoi_date_model.method = self.aoi_view.model.method
        self.aoi_date_model.name = self.aoi_view.model.name
        self.aoi_date_model.asset_json = self.aoi_view.model.asset_json
        self.aoi_date_model.vector_json = self.aoi_view.model.vector_json
        self.aoi_date_model.geo_json = self.aoi_view.model.geo_json
        self.aoi_date_model.start_date = self.start_date.v_model
        self.aoi_date_model.end_date = self.end_date.v_model

        if self.aoi_view.model.method in ["ADMIN0", "ADMIN1", "ADMIN2"]:
            self.aoi_date_model._from_admin(self.aoi_view.model.admin)
        elif self.aoi_view.model.method == "SHAPE":
            self.aoi_date_model._from_vector(self.aoi_view.model.vector_json)
        elif self.aoi_view.model.method == "DRAW":
            self.aoi_date_model._from_geo_json(self.aoi_view.model.geo_json)
        elif self.aoi_view.model.method == "ASSET":
            self.aoi_date_model._from_asset(self.aoi_view.model.sset_json)

        widget.set_loader_text(cm.aoi_tile.search_button_alerts_text.loader_text3)
        # Generate list of available names and dictionary of filtered rasters
        self.alert_filter_model.available_alerts_list = (
            create_available_alert_dictionary(dictionary, aoi, date1, date2)
        )

        widget.set_loader_text(cm.aoi_tile.search_button_alerts_text.loader_text4)
        self.alert_filter_model.available_alerts_raster_list = (
            create_filtered_alert_raster_dictionary(
                self.alert_filter_model.available_alerts_list,
                aoi,
                date1,
                date2,
                self.aux_model.ccdc_layer,
            )
        )

        widget.set_loader_text(cm.aoi_tile.search_button_alerts_text.loader_text5)
        self.app_tile_model.current_page_view = "filter_alerts"

    def process_alerts_silent(self):
        # Check inputs
        aoi, date1, date2 = check_aoi_inputs(self)
        dictionary = self.alert_filter_model.alerts_dictionary

        # Save data to model
        self.aoi_date_model.admin = self.aoi_view.model.admin
        self.aoi_date_model.asset_name = self.aoi_view.model.asset_name
        self.aoi_date_model.method = self.aoi_view.model.method
        self.aoi_date_model.name = self.aoi_view.model.name
        self.aoi_date_model.asset_json = self.aoi_view.model.asset_json
        self.aoi_date_model.vector_json = self.aoi_view.model.vector_json
        self.aoi_date_model.geo_json = self.aoi_view.model.geo_json
        self.aoi_date_model.start_date = self.start_date.v_model
        self.aoi_date_model.end_date = self.end_date.v_model

        if self.aoi_view.model.method in ["ADMIN0", "ADMIN1", "ADMIN2"]:
            self.aoi_date_model._from_admin(self.aoi_view.model.admin)
        elif self.aoi_view.model.method == "SHAPE":
            self.aoi_date_model._from_vector(self.aoi_view.model.vector_json)
        elif self.aoi_view.model.method == "DRAW":
            self.aoi_date_model._from_geo_json(self.aoi_view.model.geo_json)
        elif self.aoi_view.model.method == "ASSET":
            self.aoi_date_model._from_asset(self.aoi_view.model.sset_json)

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

    def load_saved_parameters(self, data):
        self.aoi_view.model.admin = data.get("aoi_admin")
        self.aoi_view.model.asset_name = data.get("aoi_asset_name")
        self.aoi_view.model.method = data.get("aoi_method")
        self.aoi_view.model.name = data.get("aoi_name")
        self.aoi_view.model.asset_json = data.get("aoi_asset_json")
        self.aoi_view.model.vector_json = data.get("aoi_vector_json")
        self.aoi_view.model.geo_json = data.get("aoi_geo_json")

        if self.aoi_view.model.method in ["ADMIN0", "ADMIN1", "ADMIN2"]:
            self.aoi_view.model._from_admin(self.aoi_view.model.admin)
        elif self.aoi_view.model.method == "SHAPE":
            self.aoi_view.model._from_vector(self.aoi_view.model.vector_json)
        elif self.aoi_view.model.method == "DRAW":
            self.aoi_view.model._from_geo_json(self.aoi_view.model.geo_json)
        elif self.aoi_view.model.method == "ASSET":
            self.aoi_view.model._from_asset(self.aoi_view.model.asset_json)

        # self.aoi_view.model.set_object()
        self.map_1.addLayer(
            ee_object=self.aoi_view.model.feature_collection, name="aoi"
        )
        self.start_date.v_model = data.get("start_date")
        self.end_date.v_model = data.get("end_date")
