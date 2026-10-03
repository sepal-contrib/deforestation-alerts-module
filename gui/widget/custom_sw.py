from pysepal import color
from pysepal.scripts import utils as su
from pysepal.frontend.resize_trigger import rt
from copy import deepcopy
import geopandas as gpd
from ipyleaflet import DrawControl, GeomanDrawControl
from shapely import geometry as sg
import ipyvuetify as v
from pysepal.sepalwidgets.sepalwidget import SepalWidget
from configparser import ConfigParser
from pathlib import Path
from typing import List, Tuple, Union
from typing import Callable, List
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
