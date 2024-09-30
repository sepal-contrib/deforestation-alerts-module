from sepal_ui import sepalwidgets as sw
from sepal_ui import aoi
from sepal_ui.mapping import SepalMap
from component.message import cm
import ipyvuetify as v
from sepal_ui.scripts.utils import init_ee
from traitlets import Any, Unicode, link
from sepal_ui.planetapi import PlanetView

init_ee()


class RecipeTile(sw.Layout):
    def __init__(self, recipe_model):

        self._metadata = {"mount_id": "recipe_tile"}
        self.recipe_model = recipe_model

        super().__init__()

        new_recipe_input = sw.FileInput()
        card01 = v.Card(
            class_="pa-2",
            children=[v.CardTitle(children=["New Recipe"]), new_recipe_input],
        )

        load_recipe_input = sw.FileInput()
        card02 = v.Card(
            class_="pa-2",
            children=[v.CardTitle(children=["Load Recipe"]), new_recipe_input],
        )

        layout = sw.Row(
            children=[
                sw.Col(children=[card01, card02]),
            ]
        )

        self.children = [layout]

        recipe_model.bind(new_recipe_input, "new_recipe_name").bind(
            load_recipe_input, "load_recipe_name"
        )
