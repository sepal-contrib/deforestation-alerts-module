from sepal_ui import model
from traitlets import Any, Unicode
from component.message import cm


class AppTileModel(model.Model):

    ############################################################################
    # app model
    ############################################################################
    current_page_view = Unicode("about_tile").tag(sync=True)
    "Current app page"
    temporary_recipe_name = Unicode("").tag(sync=True)
    "Temporary Recipe name"
    recipe_name = Unicode("").tag(sync=True)
    "Recipe name"
    recipe_folder_path = Unicode("").tag(sync=True)
    "Recipe folder path"

    def export_dictionary(self):
        dictionary = {
            "current_page_view": self.current_page_view,
            "temporary_recipe_name": self.temporary_recipe_name,
            "recipe_name": self.recipe_name,
            "recipe_folder_path": self.recipe_folder_path,
        }
        return dictionary

    def import_from_dictionary(self, file_path):
        """
        Import class attributes from a JSON file.
        Args:
            file_path (str): Path to the JSON file.
        """
        try:
            # with open(file_path, 'r') as f:
            #     data = json.load(f)
            data = file_path
            # Update attributes
            self.temporary_recipe_name = data.get(
                "temporary_recipe_name", self.temporary_recipe_name
            )
            self.recipe_name = data.get("recipe_name", self.recipe_name)
            self.recipe_folder_path = data.get(
                "recipe_folder_path", self.recipe_folder_path
            )

        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading JSON file: {e}")

    def reset_model(self):
        self.current_page_view = "recipe_tile"
        self.temporary_recipe_name = ""
        self.recipe_name = ""
        self.recipe_folder_path = ""
