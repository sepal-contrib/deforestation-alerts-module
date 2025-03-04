from sepal_ui import sepalwidgets as sw
from sepal_ui import aoi
from sepal_ui.mapping import SepalMap
from sepal_ui.mapping.draw_control import DrawControl
from sepal_ui.mapping.layers_control import LayersControl
from sepal_ui.mapping.inspector_control import InspectorControl
from sepal_ui.mapping.map_btn import MapBtn
from sepal_ui.scripts import utils as su
import ipyvuetify as v
from IPython.display import display, HTML
from traitlets import Any, HasTraits, Unicode, link, observe
from ipyleaflet import TileLayer

from component.message import cm
from component.scripts.alert_filter_helper import *
from component.scripts.mosaics_helper import *
from component.scripts.recipe_helper import (
    create_directory,
    save_model_parameters_to_json,
)

from component.widget.custom_sw import CustomDrawControl, CustomBtnWithLoader

import math
import ee
import time
import threading
import logging
from shapely.geometry import Point, Polygon


su.init_ee()


class SepalCard(sw.SepalWidget, v.Card):
    def __init__(self, **kwargs):

        super().__init__(**kwargs)


class AlertsFilterTile(sw.Layout):
    def __init__(
        self,
        aoi_date_model,
        aux_model,
        alert_filter_model,
        selected_alerts_model,
        analyzed_alerts_model,
        app_tile_model,
    ):

        self._metadata = {"mount_id": "filter_alerts"}
        self.aoi_date_model = aoi_date_model
        self.aux_model = aux_model
        self.alert_filter_model = alert_filter_model
        self.selected_alerts_model = selected_alerts_model
        self.analyzed_alerts_model = analyzed_alerts_model
        self.app_tile_model = app_tile_model

        # Variables to describe collections
        self.lista_vis_params = [{"min": 1, "max": 2, "palette": ["orange", "purple"]}]

        # user interface elements
        self.card00 = None
        self.card01 = None

        # Create analyze button and functions
        self.analyze_button = CustomBtnWithLoader(
            text=cm.filter_tile.analyze_alerts_button, loader_type="text"
        )
        self.analyze_alert = sw.Alert().hide()
        self.analyze_alerts_function = su.loading_button(
            alert=self.analyze_alert, button=self.analyze_button
        )(self.analyze_alerts_function)

        self.initialize_layout()

        ## Observe changes in alert_model and update tile when it changes
        alert_filter_model.observe(self.update_tile, "available_alerts_raster_list")

        super().__init__()

    def initialize_layout(self):
        # Map Style
        display(
            HTML(
                """
        <style>
            .custom-map-class {
                width: 100% !important;
                height: 80vh !important;
                }
        </style>
        """
            )
        )
        # Create map
        self.map_1 = SepalMap()
        self.map_1.add_class("custom-map-class")
        self.map_1.add_basemap("SATELLITE")
        self.drawn_item = DrawControl(self.map_1)
        self.map_1.add(self.drawn_item)
        self.map_1.add(InspectorControl(self.map_1))
        self.drawn_item.hide()

        # No alerts message
        main_title = v.CardTitle(children=[cm.filter_tile.available_alerts_title])
        mkd = sw.Markdown(cm.filter_tile.no_alerts_message)

        self.card00 = SepalCard(class_="pa-2", children=[main_title, mkd])

        # Create user interface components
        # Create alert source select component
        self.alert_source_select = sw.Select(
            items=self.alert_filter_model.available_alerts_list,
            v_model=self.alert_filter_model.available_alerts_list,
            label=cm.filter_tile.available_alerts_hint,
            multiple=True,
            clearable=True,
            chips=True,
        )
        self.alert_source_select.on_event("change", self.link_checkbox_map_btn)

        self.card01 = SepalCard(
            class_="pa-2",
            children=[
                main_title,
                self.alert_source_select,
            ],
        )

        # Create min alert area input
        min_area_title = v.CardTitle(children=[cm.filter_tile.set_min_alert_siz_title])
        self.min_area_input = sw.TextField(v_model=0.5)
        self.card02 = SepalCard(
            class_="pa-2",
            children=[min_area_title, self.min_area_input],
        )

        # Create Area selection method component
        area_selection_title = v.CardTitle(
            children=[cm.filter_tile.area_selection_method_title]
        )
        alert_selection_method_list = [
            cm.filter_tile.area_selection_method_label1,
            cm.filter_tile.area_selection_method_label2,
        ]
        self.alert_selection_method_select = sw.Select(
            items=alert_selection_method_list,
            v_model=cm.filter_tile.area_selection_method_label2,
            multiple=False,
            clearable=True,
            chips=True,
        )
        self.card03 = SepalCard(
            class_="pa-2",
            children=[
                area_selection_title,
                self.alert_selection_method_select,
            ],
        )

        # Create alert sorting method component
        alert_sorting_title = v.CardTitle(
            children=[cm.filter_tile.alert_sorting_method_title]
        )
        alert_sorting_method_list = [
            cm.filter_tile.alert_sorting_method_label1,
            cm.filter_tile.alert_sorting_method_label2,
            cm.filter_tile.alert_sorting_method_label3,
            cm.filter_tile.alert_sorting_method_label4,
        ]
        self.alert_sorting_select = sw.Select(
            items=alert_sorting_method_list,
            v_model=cm.filter_tile.alert_sorting_method_label1,
            multiple=False,
            clearable=True,
            chips=True,
        )

        self.card06 = SepalCard(
            class_="pa-2",
            children=[
                alert_sorting_title,
                self.alert_sorting_select,
            ],
        )

        # Define the callback function that will be triggered
        def prior_option(widget, event, data):
            if data == cm.filter_tile.area_selection_method_label2:
                self.card04.show()
                self.drawn_item.hide()
            else:
                self.card04.hide()
                self.drawn_item.show()

        self.alert_selection_method_select.on_event("change", prior_option)

        # Create max number of alerts component
        max_number_title = v.CardTitle(
            children=[cm.filter_tile.max_number_of_alerts_title]
        )
        self.number_of_alerts = sw.Combobox(
            items=[cm.filter_tile.max_number_of_alerts_option1],
            v_model=[cm.filter_tile.max_number_of_alerts_option1],
            label=cm.filter_tile.max_number_of_alerts_hint,
            multiple=False,
            clearable=True,
            chips=True,
        )
        self.card04 = SepalCard(
            class_="pa-2",
            children=[max_number_title, self.number_of_alerts],
        )

        # Define analyze button function
        self.analyze_button.on_event("click", self.analyze_alerts_function)
        self.card05 = SepalCard(
            class_="pa-2", children=[self.analyze_button, self.analyze_alert]
        )

        self.card00.show()
        self.card01.hide()
        self.card02.hide()
        self.card03.hide()
        self.card04.hide()
        self.card05.hide()
        self.card06.hide()

        layout = sw.Row(
            dense=True,
            children=[
                sw.Col(cols=10, children=[self.map_1]),
                sw.Col(
                    cols=2,
                    children=[
                        self.card00,
                        self.card01,
                        self.card02,
                        self.card03,
                        self.card06,
                        self.card04,
                        self.card05,
                    ],
                ),
            ],
        )

        self.children = [layout]

    ## User interface functions

    def update_layout(self):

        alerts_names_list = self.alert_filter_model.available_alerts_list
        alerts_raster_dictionary = self.alert_filter_model.available_alerts_raster_list

        if not alerts_names_list or len(alerts_names_list) == 0:
            self.card00.show()
            self.card01.hide()
            self.card02.hide()
            self.card03.hide()
            self.card04.hide()
            self.card05.hide()
            self.card06.hide()
        else:
            self.alert_source_select.items = alerts_names_list
            self.alert_source_select.v_model = [alerts_names_list[0]]

            self.card00.hide()
            self.card01.show()
            self.card02.show()
            self.card03.show()
            self.card04.show()
            self.card05.show()
            self.card06.show()

            # Reset map content
            self.map_1.remove_all()
            # Add content to map
            self.map_1.add_ee_layer(self.aoi_date_model.feature_collection, name="AOI")
            # self.map_1.centerObject(self.aoi_date_model.feature_collection)

            for nombre in alerts_raster_dictionary:
                self.map_1.add_ee_layer(
                    alerts_raster_dictionary[nombre]["alert_raster"].select("alert"),
                    # .selfMask(),
                    name=nombre,
                    vis_params=self.lista_vis_params[0],
                    shown=False,
                )
                self.map_1.add_ee_layer(
                    alerts_raster_dictionary[nombre]["alert_raster"].select("date")
                    # .selfMask()
                    .randomVisualizer(),
                    name=nombre + cm.filter_tile.layer_date_helper1,
                    shown=False,
                )
            if self.aux_model.aux_layer:
                if self.aux_model.aux_layer_vis:
                    vis_aux = {self.aux_model.aux_layer_vis}
                else:
                    vis_aux = {"min": 0, "max": 1, "palette": ["white", "brown"]}
                self.map_1.add_ee_layer(
                    ee.Image(self.aux_model.aux_layer).selfMask(),
                    name=cm.filter_tile.layer_aux_name,
                    vis_params=vis_aux,
                )

            if self.aux_model.mask_layer:
                self.map_1.add_ee_layer(
                    ee.Image(self.aux_model.mask_layer).selfMask(),
                    name=cm.filter_tile.layer_mask_name,
                    # vis_params=self.aux_model.aux_layer_vis,
                    vis_params={"min": 0, "max": 1, "palette": ["white", "gray"]},
                )
            self.link_checkbox_map()

    def update_tile(self, change):
        # Update the tile when aoi_date_model changes
        self.update_layout()  # Reinitialize the layout with the new data

    def link_checkbox_map_btn(self, widget, event, data):
        self.link_checkbox_map()

    def link_checkbox_map(self):
        """
        Updates the visibility of EELayers on the given ipyleaflet map
        based on the value of the v_model.

        Parameters:
        - v_model (str): The value of the ipyvuetify element to match layer names.
        - map_element (Map): The ipyleaflet map containing the layers.

        The function will make visible all layers with names matching the v_model
        value while hiding other EELayers.
        """
        map_element = self.map_1
        check_box_v_model = self.alert_source_select.v_model

        # Iterate over layers in the map
        for layer in map_element.layers:
            # Check if the layer is an EELayer and if its name matches the v_model
            if isinstance(layer, TileLayer) and hasattr(layer, "name"):
                # Enable layers with the same name as the v_model
                if layer.name in check_box_v_model:
                    layer.visible = True
                # Disable all other EELayers
                elif "Google Earth Engine" in layer.attribution and layer.name != "AOI":
                    layer.visible = False

    ## Processing functions

    def assign_bb_partial(self, json_file):
        self.selected_alerts_model.alerts_bbs = json_file
        self.selected_alerts_model.received_alerts = "Yes"
        print(self.selected_alerts_model.received_alerts)

    def assign_bb_full(self, json_file):
        self.selected_alerts_model.alerts_total_bbs = json_file
        self.selected_alerts_model.received_alerts = "Yes"
        print(self.selected_alerts_model.received_alerts)

    def start_thread_full(
        self,
        poly,
        alerta_reducir,
        custom_reducer,
        pixel_size,
        min_size_pixels,
        max_number_alerts,
        sorting,
    ):
        thread = threading.Thread(
            target=lambda: self.assign_bb_full(
                obtener_datos_gee_total_v3(
                    poly,
                    alerta_reducir,
                    custom_reducer,
                    pixel_size,
                    min_size_pixels,
                    max_number_alerts,
                    sorting,
                )
            )
        )
        thread.start()

    def start_thread_partial(
        self,
        poly,
        alerta_reducir,
        custom_reducer,
        pixel_size,
        min_size_pixels,
        max_number_alerts,
        sorting,
        grid_size,
    ):
        thread2 = threading.Thread(
            target=lambda: self.assign_bb_partial(
                obtener_datos_gee_parcial_map(
                    poly,
                    alerta_reducir,
                    custom_reducer,
                    pixel_size,
                    min_size_pixels,
                    max_number_alerts,
                    sorting,
                    grid_size,
                )
            )
        )
        thread2.start()

    def create_filtered_alert_raster(
        self,
        alert_source,
        user_min_alert_size,
        alert_area_selection,
        alert_sorting_method,
        user_max_number_alerts,
        user_selection_polygon,
    ):
        # Process alerts comming from different sources, retun a single image
        filtered_alert_dictionary = self.alert_filter_model.available_alerts_raster_list

        if len(alert_source) == 1:
            alert_union = ee.Image(
                filtered_alert_dictionary[alert_source[0]]["alert_raster"]
            )
        else:
            selected_alert_list = []
            for label in alert_source:
                selected_alert_list.append(
                    filtered_alert_dictionary[label]["alert_raster"]
                )
            img_collection_alertas = ee.ImageCollection.fromImages(selected_alert_list)
            alert_union = custom_reduce_image_collection(img_collection_alertas)

        alerta = alert_union

        # Enmascarar si existe capa mask
        if self.aux_model.mask_layer:
            mask_layer = ee.Image(self.aux_model.mask_layer)
            mask_alert = alerta.where(mask_layer.eq(0), 0)
        else:
            mask_alert = alerta

        # Cortar si existe poligono y alert selection es by drawn polygon
        if alert_area_selection == cm.filter_tile.area_selection_method_label1:
            clip_alert = mask_alert.clip(
                ee.FeatureCollection(user_selection_polygon).geometry()
            )
        else:
            user_selection_polygon = None
            clip_alert = mask_alert

        return clip_alert

    def create_vector_download_params(
        self,
        aoi,
        user_selection_polygon,
        clip_alert,
        alert_source,
        user_min_alert_size,
        alert_area_selection,
    ):

        # Generar 3 bandas para las alertas de todo el area
        i = clip_alert.select("alert").gt(0).rename("mask_alert")
        a = clip_alert.select("alert")
        d = clip_alert.select("date")
        # alertarea = i.multiply(ee.Image.pixelArea()).rename('area');

        alerta_reducir = i.addBands(i).addBands(a).addBands(d)

        # Definir tamaño de pixel
        pixel_landsat = 28
        pixel_sentinel = 10

        if set(["GLAD-S2", "RADD"]).intersection(alert_source):
            pixel_size = pixel_sentinel
            grid_size = 20000
        else:
            pixel_size = pixel_landsat
            grid_size = 20000

        # Definir numero minimo de pixels para alertas
        min_size_hectareas = float(user_min_alert_size) * 10000
        min_size_pixels = math.ceil(min_size_hectareas / (pixel_size**2))

        # Generar poligono de area de estudio considerando alert_area_selection
        if alert_area_selection == cm.filter_tile.area_selection_method_label1:
            poly = ee.FeatureCollection(user_selection_polygon)
        else:
            poly = aoi

        return poly, alerta_reducir, pixel_size, min_size_pixels, grid_size

    def create_planet_images_dictionary(self, polygon, date1, date2):
        planet_mosaics_dates = get_planet_dates(date1, date2)
        planet_images_dict_before = getPlanetMonthly(
            polygon, planet_mosaics_dates[0], planet_mosaics_dates[1]
        )
        planet_images_dict_after = getPlanetMonthly(
            polygon, planet_mosaics_dates[4], planet_mosaics_dates[3]
        )

        return planet_images_dict_before, planet_images_dict_after

    def create_recipe_directory(self):
        if self.app_tile_model.recipe_name == "":
            self.app_tile_model.recipe_name = self.app_tile_model.temporary_recipe_name

        self.app_tile_model.recipe_folder_path = create_directory(
            self.app_tile_model.recipe_name
        )

    def save_recipe_parameters(self):
        json_save_filename = (
            self.app_tile_model.recipe_folder_path + "/recipe_parameters.json"
        )
        save_model_parameters_to_json(
            json_save_filename,
            self.aux_model,
            self.aoi_date_model,
            self.alert_filter_model,
            self.selected_alerts_model,
            self.analyzed_alerts_model,
            self.app_tile_model,
        )

    def analyze_alerts_function(self, widget, event, data):

        widget.set_loader_text("Checking inputs...")

        # Check User inputs
        (
            alert_source,
            user_min_alert_size,
            alert_area_selection,
            alert_sorting_method,
            user_max_number_alerts,
            user_selection_polygon,
        ) = check_alert_filter_inputs(self)
        widget.set_loader_text("Saving inputs...")

        # Bind variables to models
        self.selected_alerts_model.selected_alert_sources = alert_source
        self.selected_alerts_model.alert_selection_area = alert_area_selection
        self.selected_alerts_model.alert_sorting_method = alert_sorting_method
        self.selected_alerts_model.min_area = float(user_min_alert_size)
        self.selected_alerts_model.max_number_alerts = int(user_max_number_alerts)
        self.selected_alerts_model.alert_selection_polygons = user_selection_polygon

        # Numeric values for exporting dictionary
        alert_selection_dict_n = {
            cm.filter_tile.area_selection_method_label1: 1,
            cm.filter_tile.area_selection_method_label2: 2,
        }

        self.selected_alerts_model.alert_selection_area_n = alert_selection_dict_n.get(
            alert_area_selection, 0
        )

        alert_sorting_method_dict_n = {
            cm.filter_tile.alert_sorting_method_label1: 1,
            cm.filter_tile.alert_sorting_method_label2: 2,
            cm.filter_tile.alert_sorting_method_label3: 3,
            cm.filter_tile.alert_sorting_method_label4: 4,
        }

        self.selected_alerts_model.alert_sorting_method_n = (
            alert_sorting_method_dict_n.get(alert_sorting_method, 0)
        )

        # Create directory in case it does not exist
        self.create_recipe_directory()

        widget.set_loader_text("Getting images...")
        # Create planet dictionaries
        (
            self.analyzed_alerts_model.before_planet_monthly_images,
            self.analyzed_alerts_model.after_planet_monthly_images,
        ) = self.create_planet_images_dictionary(
            self.aoi_date_model.feature_collection,
            self.aoi_date_model.start_date,
            self.aoi_date_model.end_date,
        )

        # Save recipe parameters
        self.save_recipe_parameters()

        widget.set_loader_text("Creating filtered rasters...")

        # Create filtered alerts raster

        self.selected_alerts_model.filtered_alert_raster = (
            self.create_filtered_alert_raster(
                alert_source,
                user_min_alert_size,
                alert_area_selection,
                alert_sorting_method,
                user_max_number_alerts,
                user_selection_polygon,
            )
        )
        widget.set_loader_text("Creating alerts vectors...")

        # Create vector download required variables

        # Crear reducer a aplicar sobre las alertas
        custom_reducer = (
            ee.Reducer.count()
            .combine(ee.Reducer.toList().unweighted(), "alert_type_")
            .combine(ee.Reducer.minMax().unweighted(), "alert_date_")
        )
        max_number_alerts = int(user_max_number_alerts)

        (
            poly,
            alerta_reducir,
            pixel_size,
            min_size_pixels,
            grid_size,
        ) = self.create_vector_download_params(
            self.aoi_date_model.feature_collection,
            user_selection_polygon,
            self.selected_alerts_model.filtered_alert_raster,
            alert_source,
            user_min_alert_size,
            alert_area_selection,
        )

        # Create partial vector alerts
        self.start_thread_partial(
            poly,
            alerta_reducir,
            custom_reducer,
            pixel_size,
            min_size_pixels,
            max_number_alerts,
            alert_sorting_method,
            grid_size,
        )

        # Create complete vector alerts
        self.start_thread_full(
            poly,
            alerta_reducir,
            custom_reducer,
            pixel_size,
            min_size_pixels,
            max_number_alerts,
            alert_sorting_method,
        )

        wait_messages = [
            "Processing...",
            "Just 1 minute...",
            "Drink some water...",
            "We are almost done...",
        ]

        widget.update_button_with_messages(
            wait_messages, self.selected_alerts_model, "received_alerts"
        )
        self.app_tile_model.current_page_view = "analysis_tile"

    def load_saved_parameters(self, data):
        self.alert_source_select.v_model = data.get("selected_alert_sources")
        self.min_area_input.v_model = data.get("min_area")
        drawn_selection_polygons = data.get("alert_selection_polygons")
        self.drawn_item.data = drawn_selection_polygons.get(
            "features", self.drawn_item.data
        )
        max_number = data.get("max_number_alerts")
        self.number_of_alerts.v_model = (
            cm.filter_tile.max_number_of_alerts_option1
            if (max_number := data.get("max_number_alerts")) == 0
            else str(max_number)
        )
        alert_selection_dict = {
            1: cm.filter_tile.area_selection_method_label1,
            2: cm.filter_tile.area_selection_method_label2,
        }
        self.alert_selection_method_select.v_model = alert_selection_dict.get(
            data.get("alert_selection_area"), ""
        )

        alert_sorting_method_dict = {
            1: cm.filter_tile.alert_sorting_method_label1,
            2: cm.filter_tile.alert_sorting_method_label2,
            3: cm.filter_tile.alert_sorting_method_label3,
            4: cm.filter_tile.alert_sorting_method_label4,
        }
        self.alert_sorting_select.v_model = alert_sorting_method_dict.get(
            data.get("alert_sorting_method"), ""
        )
