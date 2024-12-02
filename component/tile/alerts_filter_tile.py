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

from component.message import cm
from component.scripts.alert_filter_helper import *
from component.scripts.mosaics_helper import *
from component.scripts.recipe_helper import (
    create_directory,
    save_model_parameters_to_json,
)

from component.widget.custom_sw import CustomDrawControl

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
        self.card02 = None
        self.card03 = None
        self.card04 = None
        self.card05 = None
        self.card06 = None

        self.analyze_button = sw.Btn(msg="Analyze alerts")
        self.analyze_alert = sw.Alert().hide()
        self.create_filtered_alert_list = su.loading_button(
            alert=self.analyze_alert, button=self.analyze_button
        )(self.create_filtered_alert_list)

        self.initialize_layout()
        self.update_layout()

        ## Observe changes in aoi_date_model and update tile when it changes
        alert_filter_model.observe(self.update_tile, "available_alerts_raster_list")

        super().__init__()

    def initialize_layout(self):
        # Create map
        display(
            HTML(
                """
        <style>
            .custom-map-class {
                width: 100% !important;
                height: 85vh !important;
                }
        </style>
        """
            )
        )
        self.map_2 = SepalMap(statebar=False)
        self.map_2.add_class("custom-map-class")
        self.map_2.add_basemap("SATELLITE")
        self.drawn_item = CustomDrawControl(self.map_2)
        self.map_2.add(self.drawn_item)
        self.map_2.add(InspectorControl(self.map_2))
        self.drawn_item.hide()

        mkd = sw.Markdown("No alerts are available for the date/area selected")
        self.card00 = SepalCard(
            class_="pa-2", children=[v.CardTitle(children=["Available alerts"]), mkd]
        )
        self.checkbox_container21 = sw.Select(
            items=self.alert_filter_model.available_alerts_list,
            v_model=self.alert_filter_model.available_alerts_list,
            label="Select one or multiple alert sources",
            multiple=True,
            clearable=True,
            chips=True,
        )
        self.card01 = SepalCard(
            class_="pa-2",
            children=[
                v.CardTitle(children=["Available alerts"]),
                self.checkbox_container21,
            ],
        )
        min_area_input = sw.TextField(v_model=0.5)
        self.card02 = SepalCard(
            class_="pa-2",
            children=[v.CardTitle(children=["Min alert size (ha)"]), min_area_input],
        )

        alert_selection_method_list = ["Chose by drawn polygon", "Whole area"]

        checkbox_container22 = sw.Select(
            items=alert_selection_method_list,
            v_model="Whole area",
            multiple=False,
            clearable=True,
            chips=True,
        )
        self.card03 = SepalCard(
            class_="pa-2",
            children=[
                v.CardTitle(children=["Alert area selection"]),
                checkbox_container22,
            ],
        )

        # List of options
        alert_sorting_method_list = [
            "Prioritize bigger area alerts",
            "Prioritize smaller area alerts",
            "Prioritize recent alerts",
            "Prioritize older alerts",
        ]
        # Create and display the checkboxes
        checkbox_container23 = sw.Select(
            items=alert_sorting_method_list,
            v_model="Prioritize bigger area alerts",
            multiple=False,
            clearable=True,
            chips=True,
        )
        self.card06 = SepalCard(
            class_="pa-2",
            children=[
                v.CardTitle(children=["Alert sorting"]),
                checkbox_container23,
            ],
        )
        # Define the callback function that will be triggered
        def prior_option(widget, event, data):
            if data == "Whole area":
                self.card04.show()
                self.drawn_item.hide()
            else:
                self.card04.hide()
                self.drawn_item.show()

        checkbox_container22.on_event("change", prior_option)

        number_of_alerts = sw.TextField(v_model=0)
        self.card04 = SepalCard(
            class_="pa-2",
            children=[v.CardTitle(children=["Number of alerts"]), number_of_alerts],
        )
        self.analyze_button.on_event("click", self.create_filtered_alert_list)
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
                sw.Col(cols=10, children=[self.map_2]),
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
            self.checkbox_container21.items = alerts_names_list
            self.checkbox_container21.v_model = alerts_names_list

            self.card00.hide()
            self.card01.show()
            self.card02.show()
            self.card03.show()
            self.card04.show()
            self.card05.show()
            self.card06.show()

            # Reset map content
            self.map_2.remove_all()
            # Add content to map
            self.map_2.add_ee_layer(self.aoi_date_model.feature_collection, name="AOI")
            # self.map_2.centerObject(self.aoi_date_model.feature_collection)

            for nombre in alerts_raster_dictionary:
                self.map_2.add_ee_layer(
                    alerts_raster_dictionary[nombre]["alert_raster"].select("alert"),
                    # .selfMask(),
                    name=nombre,
                    vis_params=self.lista_vis_params[0],
                )
                self.map_2.add_ee_layer(
                    alerts_raster_dictionary[nombre]["alert_raster"].select("date")
                    # .selfMask()
                    .randomVisualizer(),
                    name=nombre + " date",
                    shown=False,
                )
            if self.aux_model.aux_layer:
                if self.aux_model.aux_layer_vis:
                    vis_aux = {self.aux_model.aux_layer_vis}
                else:
                    vis_aux = {"min": 0, "max": 1, "palette": ["white", "brown"]}
                self.map_2.add_ee_layer(
                    ee.Image(self.aux_model.aux_layer).selfMask(),
                    name="Auxiliary Layer",
                    vis_params=vis_aux,
                )

            if self.aux_model.mask_layer:
                self.map_2.add_ee_layer(
                    ee.Image(self.aux_model.mask_layer).selfMask(),
                    name="Mask Layer",
                    # vis_params=self.aux_model.aux_layer_vis,
                    vis_params={"min": 0, "max": 1, "palette": ["white", "gray"]},
                )

    def update_tile(self, change):
        # Update the tile when aoi_date_model changes
        self.update_layout()  # Reinitialize the layout with the new data

    def assign_bb_full(self, json):
        self.selected_alerts_model.alerts_total_bbs = json

    def start_thread(
        self,
        poly,
        alerta_reducir,
        custom_reducer,
        pixel_size,
        min_size_pixels,
        max_number_alerts,
        sorting,
    ):
        #print(pixel_size, min_size_pixels, self.selected_alerts_model.max_number_alerts)
        thread = threading.Thread(
            target=lambda: self.assign_bb_full(
                obtener_datos_gee_total_v2(
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
            alert_union = ee.ImageCollection.fromImages(selected_alert_list).mosaic()

        alerta = alert_union

        # Enmascarar si existe capa mask
        if self.aux_model.mask_layer:
            mask_layer = ee.Image(self.aux_model.mask_layer)
            mask_alert = alerta.where(mask_layer.eq(0), 0)
        else:
            mask_alert = alerta

        # Cortar si existe poligono y alert selection es by drawn polygon
        if alert_area_selection == "Chose by drawn polygon":
            clip_alert = mask_alert.clip(ee.FeatureCollection(user_selection_polygon))
        else:
            user_selection_polygon = None
            clip_alert = mask_alert

        self.selected_alerts_model.filtered_alert_raster = clip_alert

    def create_vectors(
        self,
        alert_source,
        user_min_alert_size,
        alert_area_selection,
        alert_sorting_method,
        user_max_number_alerts,
        user_selection_polygon,
    ):

        clip_alert = self.selected_alerts_model.filtered_alert_raster

        # Generar 3 bandas para las alertas de todo el area
        i = clip_alert.select("alert").gt(0).rename("mask_alert")
        a = clip_alert.select("alert")
        d = clip_alert.select("date")
        # alertarea = i.multiply(ee.Image.pixelArea()).rename('area');

        alerta_reducir = i.addBands(i).addBands(a).addBands(d)

        # Definir tamaño de pixel
        pixel_landsat = 30
        pixel_sentinel = 10

        if set(["GLAD-S2", "RADD"]).intersection(alert_source):
            pixel_size = pixel_sentinel
        else:
            pixel_size = pixel_landsat

        # Definir numero minimo de pixels para alertas
        min_size_hectareas = float(user_min_alert_size) * 10000
        min_size_pixels = math.ceil(min_size_hectareas / (pixel_size**2))

        # Definir numero maximo de alertas
        max_number_alerts = self.selected_alerts_model.max_number_alerts

        # Crear reducer a aplicar sobre las alertas
        custom_reducer = (
            ee.Reducer.count()
            .combine(ee.Reducer.mean().unweighted(), "alert_type_")
            .combine(ee.Reducer.minMax(), "alert_date_")
        )

        # Generar poligono de area de estudio
        poly = self.aoi_date_model.feature_collection
        # Grid for faster download
        poly_grid = poly.geometry().coveringGrid("EPSG:4326", 50000)

        # Obtener 20 bounding boxes rapidamente
        print("GEE Main started")
        st = time.time()
        bb_sorted_short_temp = obtener_datos_gee_parcial_map(
            poly_grid,
            alerta_reducir,
            custom_reducer,
            pixel_size,
            min_size_pixels,
            30,
            alert_sorting_method,
        )
        et = time.time()
        print("GEE Main finished", et - st)

        # Save centroids and bb to model
        self.selected_alerts_model.alerts_bbs = bb_sorted_short_temp

        # Inicio de tarea completa en background
        print("Inicio de tarea en background")
        self.start_thread(
            poly,
            alerta_reducir,
            custom_reducer,
            pixel_size,
            min_size_pixels,
            max_number_alerts,
            alert_sorting_method,
        )
        self.app_tile_model.current_page_view = "analysis_tile"

    def create_planet_images_dictionary(self, polygon, date1, date2):
        planet_mosaics_dates = get_planet_dates(date1, date2)
        planet_images_dict_before = getPlanetMonthly(
            polygon, planet_mosaics_dates[0], planet_mosaics_dates[1]
        )
        planet_images_dict_after = getPlanetMonthly(
            polygon, planet_mosaics_dates[4], planet_mosaics_dates[3]
        )
        self.analyzed_alerts_model.before_planet_monthly_images = (
            planet_images_dict_before
        )
        self.analyzed_alerts_model.after_planet_monthly_images = (
            planet_images_dict_after
        )

    def create_recipe_directory(self):
        if self.app_tile_model.recipe_folder_path == "":
            recipe_folder = create_directory(self.app_tile_model.temporary_recipe_name)
            self.app_tile_model.recipe_name = self.app_tile_model.temporary_recipe_name
            self.app_tile_model.recipe_folder_path = recipe_folder

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

    def create_filtered_alert_list(self, widget, event, data):
        # Check User inputs
        (
            alert_source,
            user_min_alert_size,
            alert_area_selection,
            alert_sorting_method,
            user_max_number_alerts,
            user_selection_polygon,
        ) = check_alert_filter_inputs(self)

        # Bind variables to models
        self.selected_alerts_model.selected_alert_sources = alert_source
        self.selected_alerts_model.alert_selection_area = alert_area_selection
        self.selected_alerts_model.alert_sorting_method = alert_sorting_method
        self.selected_alerts_model.min_area = float(user_min_alert_size)
        self.selected_alerts_model.max_number_alerts = int(user_max_number_alerts)
        self.selected_alerts_model.alert_selection_polygons = user_selection_polygon

        self.create_recipe_directory()
        self.create_planet_images_dictionary(
            self.aoi_date_model.feature_collection,
            self.aoi_date_model.start_date,
            self.aoi_date_model.end_date,
        )
        self.save_recipe_parameters()
        self.create_filtered_alert_raster(
            alert_source,
            user_min_alert_size,
            alert_area_selection,
            alert_sorting_method,
            user_max_number_alerts,
            user_selection_polygon,
        )
        self.create_vectors(
            alert_source,
            user_min_alert_size,
            alert_area_selection,
            alert_sorting_method,
            user_max_number_alerts,
            user_selection_polygon,
        )

    def load_saved_parameters(self, data):
        self.card01.children[1].v_model = data.get(
            "selected_alert_sources"
        )
        self.card02.children[1].v_model = data.get("min_area")
        self.card03.children[1].v_model = data.get(
            "alert_selection_area"
        )
        self.card06.children[1].v_model = data.get(
            "alert_sorting_method"
        )
        drawn_selection_polygons = data.get(
            "alert_selection_polygons")
        self.drawn_item.data = drawn_selection_polygons.get(
            "features", self.drawn_item.data
        )
        self.card04.children[1].v_model = data.get(
            "max_number_alerts"
        )
