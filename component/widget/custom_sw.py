from sepal_ui import color, model, sepalwidgets as sw
from traitlets import Any, HasTraits, Unicode, link, observe
from sepal_ui.scripts import utils as su
import time
from sepal_ui.frontend.resize_trigger import rt
from copy import deepcopy
import geopandas as gpd
from ipyleaflet import DrawControl, GeomanDrawControl
from shapely import geometry as sg
import ipyvuetify as v
from sepal_ui.sepalwidgets.sepalwidget import SepalWidget
from sepal_ui.translator import Translator
import json
from configparser import ConfigParser
from pathlib import Path
from typing import List, Tuple, Union
from box import Box
from component.parameter import module_dir
import zipfile


class CustomApp(sw.App):
    def __init__(self, app_tile_model, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app_tile_model = app_tile_model
        self.app_tile_model.observe(self.update_app_view, "current_page_view")
        self.app_tile_model.observe(self.update_recipe_name_text, "recipe_name")

    def update_app_view(self, change):
        self.show_tile_2(self.app_tile_model.current_page_view)

    def update_recipe_name_text(self, change):
        if self.app_tile_model.recipe_name != "":
            self.appBar.set_recipe(self.app_tile_model.recipe_name)

    def show_tile_2(self, name: str):
        """Select the tile to display when the app is launched.

        Args:
            name: the mount-id of the tile(s) to display
        """
        # show the tile
        for tile in self.tiles:
            tile.viz = name == tile._metadata["mount_id"]

        # activate the drawerItem
        if self.navDrawer:
            items = (i for i in self.navDrawer.items if i._metadata is not None)
            for i in items:
                if name == i._metadata["card_id"]:
                    i.input_value = True

        self.app_tile_model.current_page_view = name
        rt.resize()

        return self


class CustomDrawerItem(sw.DrawerItem):
    def __init__(self, app_tile_model, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app_tile_model = app_tile_model

    def _on_click(self, *args):
        # print(self.tiles, self._metadata["card_id"])
        self.app_tile_model.current_page_view = self._metadata["card_id"]
        return self


class ShareBtn(v.Btn, SepalWidget):
    def __init__(
        self, icon="fa-solid fa-share-nodes", path: Union[str, Path] = "#", **kwargs
    ) -> None:
        # set the btn parameters
        kwargs.setdefault("x_small", True)
        kwargs.setdefault("fab", True)
        kwargs.setdefault("class_", "mx-2 px-2")
        kwargs["children"] = [v.Icon(children=[icon])]
        super().__init__(**kwargs)

        self.set_url(path)

    def set_url(self, path: Union[str, Path] = "#"):
        """Set the URL of the download btn. and unable it.

        If nothing is provided the btn is disabled.

        Args:
            path: the absolute path to a downloadable content
        """
        # set the url
        url = su.create_download_link(path)
        self.href = url

        # unable or disable the btn
        self.disabled = str(path) == "#"

        # set the download attribute
        name = None if str(path) == "#" else Path(path).name
        self.attributes = {"download": name}

        return self


class RecipeBtn(sw.Btn):
    def __init__(
        self, download_button, msg: str = "", gliph: str = "", **kwargs
    ) -> None:
        kwargs.setdefault("small", True)
        self.download_button = download_button
        super().__init__(msg=msg, gliph=gliph, **kwargs)
        self.on_event("click", self.zip_set_url)

    def zip_set_url(self, widget, event, data):
        """Set the URL of the download btn. and unable it.

        If nothing is provided the btn is disabled.

        Args:
            path: the absolute path to a downloadable content
        """
        """Create a zip file from specified files in a directory."""
        # List of files to include in the zip file
        files = ["alert_db.csv", "recipe_parameters.json"]

        directory = module_dir / self.msg
        # Ensure the directory exists and contains the required files
        if not all((directory / file).exists() for file in files):
            print(
                [str(file) for file in directory.iterdir()],
                "Required files are missing from the specified directory.",
            )
            return None

        # Create a zip file with the name of the folder
        project_name = directory.parts[-1]
        zip_filename = f"{directory}/{project_name}.zip"
        with zipfile.ZipFile(zip_filename, "w") as zipf:
            for file in files:
                fullpath = directory / file
                zipf.write(fullpath, arcname=file)

        # Create URL
        # url = su.create_download_link(zip_filename)
        # Set download url to donwload button
        self.download_button.set_url(path=zip_filename)
        self.download_button.show()
        return self


class CustomAppBar(v.AppBar, SepalWidget):
    toogle_button = None
    "v.Btn: The btn to display or hide the drawer to the user"

    title = None
    "v.ToolBarTitle: the widget containing the app title"

    locale = None
    "sw.LocaleSelect: the locale selector of all apps"

    theme = None
    "sw.ThemeSelect: the theme selector of all apps"

    def __init__(self, title="SEPAL module", translator=None, **kwargs):

        self.toggle_button = v.Btn(
            icon=True,
            children=[v.Icon(class_="white--text", children=["fas fa-ellipsis-v"])],
        )

        self.title = v.ToolbarTitle(children=[title])

        self.locale = sw.LocaleSelect(translator=translator)
        self.theme = sw.ThemeSelect()
        self.save_button = ShareBtn().hide()
        self.recipe_name = RecipeBtn(
            msg="",
            color=color.accent,
            class_="ma-2 pa-n6",
            download_button=self.save_button,
        ).hide()

        # set the default parameters
        kwargs["color"] = kwargs.pop("color", color.main)
        kwargs["class_"] = kwargs.pop("class_", "white--text")
        kwargs["dense"] = kwargs.pop("dense", True)
        kwargs["app"] = True
        kwargs["children"] = [
            self.toggle_button,
            self.title,
            v.Spacer(),
            self.recipe_name,
            self.save_button,
            self.locale,
            self.theme,
        ]

        super().__init__(**kwargs)

    def set_title(self, title):
        """
        Set the title of the appBar

        Args:
            title (str): the new app title

        Return:
            self
        """

        self.title.children = [title]

        return self

    def set_recipe(self, recipe):
        """
        Set the title of the appBar

        Args:
            title (str): the new app title

        Return:
            self
        """
        self.recipe_name.msg = recipe
        self.recipe_name.show()

        return self


class CustomDrawControl(GeomanDrawControl):
    """
    A custom DrawingControl object to handle edition of features

    Args:
        m (ipyleaflet.Map): the map on which he drawControl is displayed
        kwargs (optional): any available arguments from a ipyleaflet.DrawingControl
    """

    m = None
    "(ipyleaflet.Map) the map on which he drawControl is displayed. It will help control the visibility"

    def __init__(self, m, **kwargs):

        # set some default parameters
        # options = {"shapeOptions": {"color": color.info}}
        options = {"shapeOptions": {"color": "blue"}}
        kwargs["marker"] = kwargs.pop("marker", {})
        kwargs["circlemarker"] = kwargs.pop("circlemarker", {})
        kwargs["polyline"] = kwargs.pop("polyline", {})
        kwargs["rectangle"] = kwargs.pop("rectangle", {})
        kwargs["circle"] = kwargs.pop("circle", {})
        kwargs["polygon"] = kwargs.pop("polygon", options)

        # save the map in the memeber of the objects
        self.m = m

        super().__init__(**kwargs)

    def show(self):
        """
        show the drawing control on the map. and clear it's content.
        """

        self.clear()
        self in self.m.controls or self.m.add_control(self)

        return

    def hide(self):
        """
        hide the drawing control from the map, and clear it's content.
        """

        self.clear()
        self not in self.m.controls or self.m.remove_control(self)

        return

    def to_json(self):
        """
        Return the content of the DrawCOntrol data without the styling properties and using a polygonized representation of circles.
        The output is fully compatible with __geo_interface__.

        Return:
            (dict): the json representation of all the geometries draw on the map
        """

        features = [self.polygonize(feat) for feat in deepcopy(self.data)]
        [feat["properties"].pop("style") for feat in features]

        return {"type": "FeatureCollection", "features": features}

    @staticmethod
    def polygonize(geo_json):
        """
        Transform a ipyleaflet circle (a point with a radius) into a GeoJson polygon.
        The methods preserves all the geo_json other attributes.
        If the geometry is not a circle (don't require polygonisation), do nothing.

        Params:
            geo_json (json): the circle geojson

        Return:
            (dict): the polygonised feature
        """

        if "Point" not in geo_json["geometry"]["type"]:
            return geo_json

        # create shapely point
        center = sg.Point(geo_json["geometry"]["coordinates"])
        point = gpd.GeoSeries([center], crs=4326)

        radius = geo_json["properties"]["style"]["radius"]
        circle = point.to_crs(3857).buffer(radius).to_crs(4326)

        # insert it in the geo_json
        output = geo_json.copy()
        output["geometry"] = circle[0].__geo_interface__

        return output


class CustomSlideGroup(sw.SepalWidget, v.Card):
    def __init__(self, slide_items=None, defaul_child_color="green", **kwargs):
        # Set default properties for the v.Card
        # kwargs.setdefault("flat", True)  # Make the card flat
        # kwargs.setdefault("elevation", 0)  # Remove elevation
        # kwargs.setdefault("outlined", False)  # Remove outline

        # Initialize base class v.Card
        super().__init__(**kwargs)
        # flat = True
        # Initialize slide group and loading spinner
        self.defaul_child_color = defaul_child_color
        self.slide_group = v.SlideGroup(children=slide_items or [], center_active=True, show_arrows=True)
        self.loading_spinner = v.Card(
            children=[
                v.Row(
                    class_="d-flex align-center",
                    children=[
                        v.ProgressCircular(
                            indeterminate=True, color="primary", size=24, class_="mx-4"
                        ),
                        "Searching images..",
                    ],
                )
            ]
        )

        # Set the initial state to show the slide group
        self.children = [self.slide_group]

    def set_loading_state(self, is_loading):
        """Set the loading state of the component."""
        self.children = [self.loading_spinner] if is_loading else [self.slide_group]

    def reset_default_color(self):
        for button in self.slide_group.children:
            if hasattr(button, "color") and button.color != self.defaul_child_color:
                button.color = self.defaul_child_color


class CustomBtnWithLoader(v.Btn):
    def __init__(self, text="Click", loader_type="circular", **kwargs):
        kwargs["color"] = kwargs.pop("color", "primary")
        kwargs["children"] = [text]
        # Initialize parent class
        super().__init__(**kwargs)

        # Define loader based on loader_type
        self.loader_type = loader_type
        self.loading = False
        self.disabled = False

        # Initialize loader element
        self.loader = self._create_loader(loader_type)

        # Add loader to slots
        self.v_slots = [{"name": "loader", "children": [self.loader]}]

    def _create_loader(self, loader_type):
        """Create loader element based on loader_type."""
        if loader_type == "circular":
            return v.ProgressCircular(
                color="primary", size=40, v_model=0, rotate=-90, children=["0"]
            )
        elif loader_type == "linear":
            return v.ProgressLinear(
                color="primary", height=10, v_model=0, children=["0"]
            )
        elif loader_type == "text":
            return v.Html(
                color="gray",
                tag="div",
                children=["Loading..."],
                style={"fontSize": "8px", "background-color": "transparent"},
            )
        else:
            raise ValueError(
                "Invalid loader_type. Choose 'circular', 'linear', or 'text'."
            )

    def set_loader_percentage(self, percentage):
        """Update loader percentage for circular or linear loader."""
        if self.loader_type in ["circular", "linear"]:
            self.loader.v_model = percentage
            self.loader.children = [f"{percentage}%"]
        else:
            raise ValueError(
                "set_loader_percentage only works with 'circular' or 'linear' loader types."
            )

    def set_loader_text(self, text):
        """Update loader text for text-based loader."""
        if self.loader_type == "text":
            self.loader.children = [text]
        else:
            raise ValueError("set_loader_text only works with 'text' loader type.")

    def simulate_progress(self, total_time):
        """Simulate progress updates every 10% over a total time."""
        if self.loader_type in ["circular", "linear"]:
            step_time = total_time / 10
            for i in range(1, 11):
                self.set_loader_percentage(i * 10)
                time.sleep(step_time)

    def indeterminate_state(self, boolean):
        """Set a indeterminate loader with no text inside"""
        if self.loader_type in ["circular", "linear"]:
            self.loader.indeterminate = boolean
            self.loader.children = [""]

    def toggle_loading(self):
        """Toggle between loading and enabled states."""
        self.loading = not self.loading
        self.disabled = self.loading

    def update_button_with_messages(self, messages, stop_model, stop_variable, pause=6):
        """
        Updates a button's text with a series of messages, pausing between changes. Stops if stop_variable is not None.

        Args:
            button (v.Btn): The button to update.
            messages (list): List of messages to display.
            stop_variable (HasTraits): A traitlet-based object whose traits are observed.
            pause (int): Time (in seconds) to pause between updates (default is 10).
        """
        stop_flag = {
            "should_stop": False
        }  # A mutable flag shared with the observer callback

        def stop_callback(change):
            """Observer callback to set the stop flag."""
            if change["new"]:  # Stop when the observed trait changes to `True`
                stop_flag["should_stop"] = True

        # Observe the `is_done` trait
        stop_model.observe(stop_callback, names=stop_variable)

        while not stop_flag["should_stop"]:
            for message in messages:
                if stop_flag["should_stop"]:
                    break
                self.set_loader_text(message)
                time.sleep(pause)

    # def update_button_with_messages(self, messages, stop_variables, pause=6):
    #     """
    #     Updates a button's text with a series of messages, pausing between changes. Stops if stop_variable is not None.

    #     Args:
    #         button (v.Btn): The button to update.
    #         messages (list): List of messages to display.
    #         stop_variables (list): A list of variables to monitor. Stops when any variable is not None.
    #         pause (int): Time (in seconds) to pause between updates (default is 10).
    #     """
    #     while all(var is None for var in stop_variables):
    #         for message in messages:
    #             if any(var is not None for var in stop_variables):
    #                 break
    #             self.set_loader_text(message)
    #             time.sleep(pause)


class CustomTranslator(Translator):
    def __init__(
        self, json_folder: Union[str, Path], target: str = "", default: str = "en"
    ) -> None:
        """Python ``Box`` of ``Box`` representing all the nested translation key, value pairs.

        It reads 2 Json files, the first one being the source language (usually English) and the second one the target language.
        It will replace in the source dictionary every key that exist in both json dictionaries. Following this procedure, every message that is not translated can still be accessed in the source language.
        To access the dictionary keys, instead of using [], you can simply use key name as in an object ex: translator.first_key.secondary_key.
        There are no depth limits, just respect the snake_case convention when naming your keys in the .json files.
        5 internal keys are created upon initialization (there name cannot be used as keys in the translation message):

        -   (str) _default : the default locale of the translator
        -   (str) _targeted : the initially requested language. Use to display debug information to the user agent
        -   (str) _target : the target locale of the translator
        -   (bool) _match : if the target language match the one requested one by user, used to trigger information in appBar
        -   (str) _folder : the path to the l10n folder

        Args:
            json_folder: The folder where the dictionaries are stored
            target: The language code (IETF BCP 47) of the target lang (it should be the same as the target dictionary). Default to either the language specified in the parameter file or the default one.
            default: The language code (IETF BCP 47) of the source lang. default to "en" (it should be the same as the source dictionary)
        """
        # the name of the 5 variables that cannot be used as init keys
        FORBIDDEN_KEYS = ["_folder", "_default", "_target", "_targeted", "_match"]

        # init the box with the folder
        folder = Path(json_folder)

        # reading the default dict
        default_dict = self.merge_dict(folder / default)

        # create a dictionary in the target language
        targeted, target = self.find_target(folder, target)
        target = target or default
        target_dict = self.merge_dict(folder / target)

        # evaluate the matching of requested and obtained values
        match = targeted == target

        # create the composite dictionary
        ms_dict = self._update(default_dict, target_dict)

        # check if forbidden keys are being used
        # this will raise an error if any
        [self.search_key(ms_dict, k) for k in FORBIDDEN_KEYS + self._protected_keys]

        # # unpack the json as a simple namespace
        ms_json = json.dumps(ms_dict, ensure_ascii=False)
        ms_boxes = json.loads(ms_json, object_hook=lambda d: Box(**d, frozen_box=True))

        private_keys = {
            "_folder": str(folder),
            "_default": default,
            "_targeted": targeted,
            "_target": target,
            "_match": match,
        }

        # the final box is not frozen
        # waiting for an answer here: https://github.com/cdgriffith/Box/issues/223
        # it the meantime it's easy to call the translator using a frozen_box argument
        super(Box, self).__init__(**private_keys, **ms_boxes)

    @classmethod
    def merge_dict(cls, folder: Path) -> dict:
        """Gather all the .json file in the provided l10n folder as 1 single json dict.

        The json dict will be sanityzed and the key will be used as if they were coming from 1 single file.
        be careful with duplication. empty string keys will be removed.

        Args:
            folder: the folder where all the .json files are stored

        Returns:
            the json dict with all the keys
        """
        final_json = {}
        for f in folder.glob("*.json"):
            tmp_dict = cls.delete_empty(json.loads(f.read_text(encoding="utf-8")))
            # Custom logic here, e.g., you can modify how keys are merged or add additional processing
            final_json.update(tmp_dict)  # Using update instead of unpacking

        return cls.sanitize(final_json)

    def key_use(self, folder: Path, name: str) -> List[str]:
        """Parse all the files in the folder and check if keys are all used at least once.

        Return the unused key names.

        .. warning::

            Don't forget that there are many ways of calling Translator variables
            (getattr, save.cm.xxx in another variable etc...) SO don't forget to check
            manually the variables suggested by this method before deleting them

        Args:
            folder: The application folder using this translator data
            name: the name use by the translator in this app (usually "cm")

        Returns:
            the list of unused keys
        """
        # cannot set FORBIDDEN_KEY in the Box as it would lock another key
        FORBIDDEN_KEYS = ["_folder", "_default", "_target", "_targeted", "_match"]

        # sanitize folder
        folder = Path(folder)

        # get all the python files recursively
        py_files = []
        all_files = [f for f in folder.glob("**/*") if f.suffix in [".py", ".ipynb"]]
        for f in all_files:
            generated_files = [".ipynb_checkpoints", "__pycache__"]
            if all([err not in str(f) for err in generated_files]):
                py_files.append(f)

        # get the flat version of all keys
        keys = list(set(pd.json_normalize(self).columns) ^ set(FORBIDDEN_KEYS))

        # init the unused keys list
        unused_keys = []

        for k in keys:

            # by default we consider that the is never used
            is_present = False

            # read each python file and search for the pattern of the key
            # if it's find change status of the counter and exit the search
            for f in py_files:
                tmp = f.read_text(encoding="utf-8")
                if f"{name}.{k}" in tmp:
                    is_present = True
                    break

            # if nothing is find, the value is still False and the key can be
            # added to the list
            is_present or unused_keys.append(k)

        return unused_keys
