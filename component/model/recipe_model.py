from sepal_ui import model
from traitlets import Any, Int

from component import parameter as cp
from component.message import cm


class RecipeModel(model.Model):

    ############################################################################
    # alert filter section files
    ############################################################################
    new_recipe_name = Any(None).tag(sync=True)
    "New recipe name"

    load_recipe_name = Any(None).tag(sync=True)
    "Load recipe"

    save_recipe = Any(None).tag(sync=True)
    "Save recipe"
