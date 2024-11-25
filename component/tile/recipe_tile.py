from sepal_ui import sepalwidgets as sw
from sepal_ui import aoi
from sepal_ui.mapping import SepalMap
from component.message import cm
import ipyvuetify as v
from sepal_ui.scripts.utils import init_ee
from traitlets import Any, Unicode, link
from sepal_ui.planetapi import PlanetView
from datetime import datetime
from component.scripts.recipe_helper import (
    generate_recipe_string,
    create_directory,
    load_parameters_from_json,
)

init_ee()


class RecipeTile(sw.Layout):
    def __init__(
        self,
        aux_model,
        aoi_date_model,
        alert_filter_model,
        selected_alerts_model,
        analyzed_alerts_model,
        app_tile_model,
        aoi_tile,
        alert_filter_tile,
    ):

        self._metadata = {"mount_id": "recipe_tile"}
        self.aux_model = aux_model
        self.aoi_date_model = aoi_date_model
        self.alert_filter_model = alert_filter_model
        self.selected_alerts_model = selected_alerts_model
        self.analyzed_alerts_model = analyzed_alerts_model
        self.app_tile_model = app_tile_model
        self.aoi_tile = aoi_tile
        self.alert_filter_tile = alert_filter_tile

        super().__init__()

        title1 = v.CardTitle(class_="pa-1 ma-1", children=["New Recipe"])
        self.new_recipe_input = sw.TextField(
            class_="pa-1 ma-1 d-flex align-center",
            v_model="",
            hint="Set the recipe name",
            persistent_hint=True,
            # suffix=".json",
        )

        self.new_recipe_input.v_model = generate_recipe_string()
        self.app_tile_model.bind(self.new_recipe_input, "temporary_recipe_name")
        create_new_btn = sw.Btn(msg="New", medium=True, class_="pa-1 ma-1")
        create_new_btn_2 = v.Row(
            class_="pa-1 ma-1 d-flex justify-end", children=[create_new_btn]
        )
        create_new_btn.on_event("click", self.create_directory_button)

        title2 = v.CardTitle(class_="pa-1 ma-1", children=["Load Recipe"])
        self.load_recipe_input = sw.FileInput(
            class_="pa-1 ma-1 d-flex align-center", medium=True
        )
        load_btn = sw.Btn(msg="Load", medium=True, class_="pa-1 ma-1")
        load_new_btn_2 = v.Row(
            class_="pa-1 ma-1 d-flex justify-end", children=[load_btn]
        )
        load_btn.on_event("click", self.load_recipe_button)

        card1 = v.Card(
            class_="pa-3 ma-5",
            hover=True,
            children=[title1, self.new_recipe_input, create_new_btn_2],
        )
        card2 = v.Card(
            class_="pa-3 ma-5",
            hover=True,
            children=[title2, self.load_recipe_input, load_new_btn_2],
        )

        layout = sw.Row(
            children=[
                sw.Col(children=[card1, card2]),
            ]
        )

        self.children = [layout]

    def create_directory_button(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks

        recipe_folder = create_directory(self.new_recipe_input.v_model)
        self.app_tile_model.recipe_name = self.new_recipe_input.v_model
        self.app_tile_model.recipe_folder_path = recipe_folder

        widget.loading = False  # Remove loading state
        widget.disabled = False  # Re-enable the button

    def load_recipe_button(self, widget, event, data):
        load_parameters_from_json(
            self.load_recipe_input,
            self.aux_model,
            self.aoi_date_model,
            self.selected_alerts_model,
            self.analyzed_alerts_model,
            self.app_tile_model,
            self.aoi_tile,
            self.alert_filter_tile,
        )
