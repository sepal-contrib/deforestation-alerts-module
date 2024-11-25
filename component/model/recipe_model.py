from sepal_ui import model
from traitlets import Any, Int, Unicode

from component import parameter as cp
from component.message import cm


class RecipeModel(model.Model):

    ############################################################################
    # recipe folder definition
    ############################################################################
    recipe_folder = Unicode("temp").tag(sync=True)
    "Recipe folder"

    def export_dictionary(self):
        dictionary = {
            "recipe_folder": self.recipe_folder,
        }
        return dictionary
