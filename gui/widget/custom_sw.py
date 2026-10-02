from pysepal import color, model, sepalwidgets as sw
from traitlets import Any, HasTraits, Unicode, link, observe
from pysepal.scripts import utils as su
from pysepal.scripts import decorator
from functools import wraps
from pysepal.message import msg
from pysepal.frontend.resize_trigger import rt
from copy import deepcopy
import geopandas as gpd
from ipyleaflet import DrawControl, GeomanDrawControl
from shapely import geometry as sg
import ipyvuetify as v
from pysepal.sepalwidgets.sepalwidget import SepalWidget
from pysepal.translator import Translator
import json
from configparser import ConfigParser
from pathlib import Path
from typing import List, Tuple, Union
from box import Box
from warnings import warn
from typing import Any, Callable, List, Optional
import time

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


# ``RecipeBtn`` (the "zip this recipe and hand it to a DownloadBtn" widget) was
# REMOVED here, not migrated. It was the last importer of the deleted
# ``parameter.module_dir`` and it was dead code: the Phase-4 port replaced the
# header RecipeBtn/ShareBtn pair with the Recipe tile's Save card
# (``gui/tile/recipe_tile.py``), and nothing has constructed it since. Every line
# of it assumed a container-local recipe folder (``module_dir / name``,
# ``.exists()``, ``.iterdir()``, a local ``zipfile.ZipFile``, and a ``set_url``
# with a container-absolute path). If exporting a folder as a zip is wanted
# again, build the zip in a ``local_files.scratch_dir()``, ``local_files.copy_into``
# the set folder, then ``set_url(local_files.home_relative(...))`` — the same
# shape as ``gui/tile/analysis/export.py::_download_data`` (inventory row a36).


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
        kwargs.setdefault("flat", True)  # Make the card flat
        kwargs.setdefault("elevation", 0)  # Remove elevation
        kwargs.setdefault("width", '100%')  # Remove outline

        # Initialize base class v.Card
        super().__init__(**kwargs)
        # Initialize slide group and loading spinner
        self.defaul_child_color = defaul_child_color
        self.slide_group = v.SlideGroup(class_="d-flex align-center", style_="width:100%",  center_active=True, show_arrows=True, children=slide_items or [])
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

def decorator_loading_v2(
    alert: Optional[v.Alert] = None,
    button: Optional[v.Btn] = None,
    debug: Optional[bool] = None,
) -> Any:
    """Decorator to execute try/except sentence and toggle loading button object.

    Designed to work within the Tile object, or any object that have a self.btn and self.alert set.

    Args:
        button: Toggled button
        alert: the alert to display the error message
        debug: Whethers or not the exception should stop the execution. default to False

    Returns:
        The return statement of the decorated method
    """
    if debug is not None:
        warn("debug argument defaults to `True`. It will be removed in v3.2")

    def decorator_loading(func):
        @wraps(func)
        def wrapper_loading(self, *args, **kwargs):
            # set btn and alert
            # Change name of variable to assign it again in this scope
            # check if they exist in the parent object if alert is not set manually
            assert hasattr(self, "alert") or alert, msg("decorator.no_alert")
            assert hasattr(self, "btn") or button, msg("decorator.no_button")
            button_ = self.btn if not button else button
            alert_ = self.alert if not alert else alert

            # Clean previous loaded messages in alert
            alert_.reset()
            
            button_.toggle_loading()  # Start loading

            value = None

            try:
                # run the function using the catch_error decorator
                value = decorator.catch_errors(alert=alert_)(func)(self, *args, **kwargs)

            except Exception as e:
                button_.toggle_loading()  # Start loading
                raise e

            return value

        return wrapper_loading

    return decorator_loading
