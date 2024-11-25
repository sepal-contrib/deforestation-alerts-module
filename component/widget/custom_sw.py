from sepal_ui import color, model, sepalwidgets as sw
from traitlets import Any, HasTraits, Unicode, link, observe
from component.message import cm
from time import sleep
from sepal_ui.frontend.resize_trigger import rt
from copy import deepcopy
import geopandas as gpd
from ipyleaflet import DrawControl, GeomanDrawControl
from shapely import geometry as sg
import ipyvuetify as v
from sepal_ui.sepalwidgets.sepalwidget import SepalWidget


class CustomApp(sw.App):
    def __init__(self, app_tile_model, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app_tile_model = app_tile_model
        self.app_tile_model.observe(self.update_app_view, "current_page_view")
        self.app_tile_model.observe(self.update_recipe_name_text, "recipe_name")

    def update_app_view(self, change):
        self.show_tile_2(self.app_tile_model.current_page_view)

    def update_recipe_name_text(self, change):
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
        self.recipe_name = v.Html(tag="div", children=[""])
        self.save_button = v.Btn(
            icon=True,
            children=[
                v.Icon(class_="white--text", children=["fa-solid fa-floppy-disk"])
            ],
        )

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
        title = v.Html(tag="strong", children=["Recipe: "])
        self.recipe_name.children = [title, recipe]

        return self


class CustomDrawControl(DrawControl):
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
        options = {"shapeOptions": {"color": color.info}}
        kwargs["marker"] = kwargs.pop("marker", {})
        kwargs["circlemarker"] = kwargs.pop("circlemarker", {})
        kwargs["polyline"] = kwargs.pop("polyline", {})
        kwargs["rectangle"] = kwargs.pop("rectangle", options)
        kwargs["circle"] = kwargs.pop("circle", options)
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
