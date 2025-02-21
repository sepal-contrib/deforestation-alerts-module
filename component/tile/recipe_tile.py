from sepal_ui import sepalwidgets as sw
from component.message import cm
import ipyvuetify as v
from component.widget.custom_sw import CustomBtnWithLoader
from sepal_ui.scripts import utils as su
from traitlets import Any, Unicode, link
from sepal_ui.planetapi import PlanetView
import pandas as pd
import geopandas as gpd
from datetime import datetime
from component.scripts.recipe_helper import (
    generate_recipe_string,
    create_directory,
    load_parameters_from_json_btn_state,
    load_gdf_from_csv,
)
from component.scripts.alert_filter_helper import check_alert_filter_inputs
from component.parameter import directory
import json
import time

from component.model.aoi_date_model import AoiDateModel
from component.model.aux_model import AuxModel
from component.model.alerts_model import AlertFilterModel, SelectedAlertsModel
from component.model.analysis_model import AlertAnalysisModel
from component.model.app_model import AppTileModel


su.init_ee()


class RecipeTile(sw.Layout):
    def __init__(
        self,
        aux_model,
        aoi_date_model,
        alert_filter_model,
        selected_alerts_model,
        analyzed_alerts_model,
        app_tile_model,
        aux_tile,
        aoi_tile,
        alert_filter_tile,
        analysis_tile,
        overview_tile,
    ):

        self._metadata = {"mount_id": "recipe_tile"}
        # Models
        self.aux_model = aux_model
        self.aoi_date_model = aoi_date_model
        self.alert_filter_model = alert_filter_model
        self.selected_alerts_model = selected_alerts_model
        self.analyzed_alerts_model = analyzed_alerts_model
        self.app_tile_model = app_tile_model

        # Tiles
        self.aux_tile = aux_tile
        self.aoi_tile = aoi_tile
        self.alert_filter_tile = alert_filter_tile
        self.analysis_tile = analysis_tile
        self.overview_tile = overview_tile

        # Create analyze button and functions
        self.create_new_btn = CustomBtnWithLoader(
            text=cm.recipe_tile.button_new_recipe,
            loader_type="text",
            large=True,
            class_="pa-1 ma-1",
        )
        self.new_alert = sw.Alert().hide()
        self.create_recipe_function = su.loading_button(
            alert=self.new_alert, button=self.create_new_btn
        )(self.create_recipe_function)
        self.load_btn = CustomBtnWithLoader(
            text=cm.recipe_tile.button_load_recipe,
            loader_type="text",
            large=True,
            class_="pa-1 ma-1",
        )
        self.load_alert = sw.Alert().hide()
        self.load_recipe_button = su.loading_button(
            alert=self.load_alert, button=self.load_btn
        )(self.load_recipe_button)

        super().__init__()

        title1 = v.CardTitle(
            class_="pa-1 ma-1", children=[cm.recipe_tile.card_title_new_recipe]
        )
        self.new_recipe_input = sw.TextField(
            class_="pa-1 ma-1 d-flex align-center",
            v_model="",
            hint=cm.recipe_tile.input_hint_set_recipe_name,
            persistent_hint=True,
            # suffix=".json",
        )

        self.new_recipe_input.v_model = generate_recipe_string()
        self.app_tile_model.bind(self.new_recipe_input, "temporary_recipe_name")
        create_new_btn_2 = v.Row(
            class_="pa-1 ma-1 d-flex justify-end", children=[self.create_new_btn]
        )
        self.create_new_btn.on_event("click", self.create_recipe_function)

        title2 = v.CardTitle(
            class_="pa-1 ma-1", children=[cm.recipe_tile.card_title_load_recipe]
        )
        self.load_recipe_input = sw.FileInput(
            class_="pa-1 ma-1 d-flex align-center",
            large=True,
            extensions=[".json"],
            folder=directory.module_dir,
        )
        load_new_btn_2 = v.Row(
            class_="pa-1 ma-1 d-flex justify-end", children=[self.load_btn]
        )
        self.load_btn.on_event("click", self.load_recipe_button)

        card1 = v.Card(
            class_="pa-3 ma-5",
            hover=True,
            children=[title1, self.new_recipe_input, create_new_btn_2, self.new_alert],
        )
        card2 = v.Card(
            class_="pa-3 ma-5",
            hover=True,
            children=[title2, self.load_recipe_input, load_new_btn_2, self.load_alert],
        )

        layout = sw.Row(
            children=[
                sw.Col(children=[card1, card2]),
            ]
        )

        self.children = [layout]

    def create_recipe_function(self, widget, event, data):
        widget.set_loader_text(cm.recipe_tile.loader_creating_recipe)
        time.sleep(1)

        if self.analyzed_alerts_model.actual_alert_id != -1:
            widget.set_loader_text(cm.recipe_tile.loader_resetting_models)
            time.sleep(1)

            # Reset models
            self.aux_model = AuxModel()
            self.aoi_date_model = AoiDateModel()
            self.alert_filter_model.reset_model()
            self.selected_alerts_model.reset_model()
            self.analyzed_alerts_model.reset_model()
            self.app_tile_model.reset_model()

            widget.set_loader_text(cm.recipe_tile.loader_resetting_tiles)
            time.sleep(1)

            # Reset Tiles
            self.aux_tile.initialize_layout()
            self.aoi_tile.initialize_layout()
            self.alert_filter_tile.initialize_layout()
            self.analysis_tile.initialize_layout()
            self.overview_tile.initialize_layout()

            link(
                (self.aoi_tile.map_1, "center"),
                (self.alert_filter_tile.map_1, "center"),
            )
            link((self.aoi_tile.map_1, "zoom"), (self.alert_filter_tile.map_1, "zoom"))
            link((self.aoi_tile.map_1, "center"), (self.overview_tile.map_1, "center"))
            link((self.aoi_tile.map_1, "zoom"), (self.overview_tile.map_1, "zoom"))

        self.app_tile_model.recipe_name = self.new_recipe_input.v_model
        self.load_recipe_input.v_model = ""
        widget.set_loader_text(cm.recipe_tile.alert_done_message)
        time.sleep(1)

    def load_recipe_button(self, widget, event, data):

        file_name = self.load_recipe_input.v_model
        aux_model = self.aux_model
        aoi_date_model = self.aoi_date_model
        selected_alerts_model = self.selected_alerts_model
        analyzed_alerts_model = self.analyzed_alerts_model
        app_tile_model = self.app_tile_model
        aoi_tile = self.aoi_tile
        alert_filter_tile = self.alert_filter_tile

        # Read JSON file
        with open(file_name, "r") as json_file:
            model_parameters = json.load(json_file)
        widget.set_loader_text(cm.recipe_tile.loader_loading_aoi)
        # widget.set_loader_percentage(10)
        aux_model.import_from_dictionary(model_parameters)
        aoi_tile.load_saved_parameters(model_parameters)
        widget.set_loader_text(cm.recipe_tile.loader_loading_alerts)
        # widget.set_loader_percentage(30)
        aoi_tile.process_alerts_silent()
        widget.set_loader_text(cm.recipe_tile.loader_loading_filters)
        # widget.set_loader_percentage(60)
        alert_filter_tile.load_saved_parameters(model_parameters)
        (
            alert_source,
            user_min_alert_size,
            alert_area_selection,
            alert_sorting_method,
            user_max_number_alerts,
            user_selection_polygon,
        ) = check_alert_filter_inputs(alert_filter_tile)
        (
            analyzed_alerts_model.before_planet_monthly_images,
            analyzed_alerts_model.after_planet_monthly_images,
        ) = alert_filter_tile.create_planet_images_dictionary(
            aoi_date_model.feature_collection,
            aoi_date_model.start_date,
            aoi_date_model.end_date,
        )
        widget.set_loader_text(cm.recipe_tile.loader_waiting_gee)
        # widget.set_loader_percentage(70)
        selected_alerts_model.filtered_alert_raster = (
            alert_filter_tile.create_filtered_alert_raster(
                alert_source,
                user_min_alert_size,
                alert_area_selection,
                alert_sorting_method,
                user_max_number_alerts,
                user_selection_polygon,
            )
        )
        widget.set_loader_text(cm.recipe_tile.loader_loading_alert_db)
        # widget.set_loader_percentage(90)
        app_tile_model.import_from_dictionary(model_parameters)
        analyzed_alerts_model.alerts_gdf = load_gdf_from_csv(
            app_tile_model.recipe_folder_path + "/alert_db.csv",
            ["bounding_box", "point", "alert_polygon"],
        )
        widget.set_loader_text(cm.recipe_tile.loader_loading_last_alert)
        # widget.set_loader_percentage(100)
        analyzed_alerts_model.import_from_dictionary(model_parameters)
        app_tile_model.current_page_view = "analysis_tile"
