from sepal_ui import sepalwidgets as sw
from sepal_ui import aoi
from sepal_ui.mapping import SepalMap
from sepal_ui.mapping.draw_control import DrawControl
from sepal_ui.mapping.layers_control import LayersControl
from sepal_ui.mapping.inspector_control import InspectorControl
from sepal_ui.mapping.aoi_control import AoiControl
from sepal_ui.scripts import utils as su
from traitlets import Any, HasTraits, Unicode, link, observe
import ipyvuetify as v
from IPython.display import display, HTML


from component.message import cm
from component.scripts.alert_filter_helper import convert_to_geopandas
from component.scripts.mosaics_helper import *
from component.scripts.report_builder import *
from component.scripts.recipe_helper import update_actual_id
from component.widget.custom_sw import CustomDrawControl, CustomSlideGroup

import os
import numpy as np
import rasterio
import math
import ee
from shapely.geometry import Point, Polygon
import time
from datetime import datetime
from operator import itemgetter
from ipyleaflet import GeoData, GeoJSON
import threading
import queue

su.init_ee()


class SepalCard(sw.SepalWidget, v.Card):
    def __init__(self, **kwargs):

        super().__init__(**kwargs)


class AnalysisTile(sw.Layout):
    def __init__(
        self, aux_model, selected_alerts_model, analyzed_alerts_model, app_tile_model
    ):

        self._metadata = {"mount_id": "analysis_tile"}
        self.aux_model = aux_model
        self.selected_alerts_model = selected_alerts_model
        self.analyzed_alerts_model = analyzed_alerts_model
        self.app_tile_model = app_tile_model

        self.apply_DL_model_button = sw.Btn("Auto", color="primary", outlined=True)
        self.edit_layer_button = sw.Btn("Edit", color="primary", outlined=True)
        self.alert_draw_alert = sw.Alert().hide()
        self.add_defo_prediction_map = su.loading_button(
            alert=self.alert_draw_alert, button=self.apply_DL_model_button
        )(self.add_defo_prediction_map)
        self.edit_defo_prediction_map = su.loading_button(
            alert=self.alert_draw_alert, button=self.edit_layer_button
        )(self.edit_defo_prediction_map)

        self.defo_dl_layer = None
        self.actual_bb = None
        self.selected_img_before = None
        self.selected_img_after = None
        self.selected_img_before_info_list = None
        self.selected_img_after_info_list = None

        self.initialize_layout()

        ## Observe changes in selected_alerts_model and update tile when it changes
        self.selected_alerts_model.observe(self.update_gdf, "alerts_bbs")
        self.selected_alerts_model.observe(self.add_gdf, "alerts_total_bbs")
        self.analyzed_alerts_model.observe(self.view_actual_alert, "actual_alert_id")
        self.analyzed_alerts_model.observe(self.slider_s2_before, "before_s2_images_time")
        self.analyzed_alerts_model.observe(self.slider_s2_after, "after_s2_images_time")

        # Queue for communication between main and worker threads
        self.file_queue = queue.Queue()
        self.result_queue = queue.Queue()

        # Start the worker thread
        worker_thread = threading.Thread(target=self.worker)
        worker_thread.start()

        super().__init__()

    def worker(self):
        import os
        import rasterio
        import numpy as np
        import tensorflow as tf
        from tensorflow.keras import layers, models
        from tensorflow.keras import backend as K
        from MightyMosaic import MightyMosaic

        os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
        from component.scripts.model_worker import apply_dl_model, save_prediction_prob

        """Worker thread that processes files from the file_queue."""
        while True:
            file_path = self.file_queue.get()  # Get a file path from the queue
            if file_path == "exit":  # Stop the thread if "exit" command is received
                break
            # Process the file and put the result in the result_queue
            processed_file = apply_dl_model(file_path, "utils/model2.h5")
            self.result_queue.put(processed_file)
            self.file_queue.task_done()

    def send_file_for_processing(self, file_path):
        """Function to send a file path to the worker and retrieve the result."""
        # Put the file path in the queue for the worker to process
        self.file_queue.put(file_path)

        # Wait for the worker to process the file and return the result
        processed_file = (
            self.result_queue.get()
        )  # Blocking wait until result is available
        self.result_queue.task_done()
        return processed_file

    def initialize_layout(self):
        display(
            HTML(
                """
        <style>
            .custom-map-class2 {
                width: 100% !important;
                height: 55vh !important;
                }
        </style>
        """
            )
        )

        # Create map
        self.map_31 = SepalMap()
        self.map_31.add_class("custom-map-class2")
        self.map_31.add_basemap("SATELLITE")

        self.map_32 = SepalMap()
        self.map_32.add_class("custom-map-class2")
        self.map_32.add_basemap("SATELLITE")

        # Link the center and zoom between both maps
        center_link = link((self.map_31, "center"), (self.map_32, "center"))
        zoom_link = link((self.map_31, "zoom"), (self.map_32, "zoom"))

        
        # slider_before_planet = sw.SlideGroup(show_arrows=True)
        # slider_before_s2 = sw.SlideGroup(show_arrows=True)

        slider_before_planet = CustomSlideGroup()
        slider_before_s2 = CustomSlideGroup()      

        selected_img_before_info = v.Html(
            tag="div", children=["Source: Date: Cloud Cover:"]
        )
        imgSelection1 = v.Card(
            # class_="pa-3 ma-5",
            children=[
                v.CardTitle(class_="pa-1 ma-1", children=["Actual image info"]),
                selected_img_before_info,
                v.CardTitle(class_="pa-1 ma-1", children=["Planet Monthly Mosaics"]),
                slider_before_planet,
                v.CardTitle(class_="pa-1 ma-1", children=["Sentinel 2 Images"]),
                slider_before_s2,
            ]
        )

        # slider_after_planet = sw.SlideGroup(show_arrows=True)
        # slider_after_s2 = sw.SlideGroup(show_arrows=True)
        
        slider_after_planet = CustomSlideGroup()
        slider_after_s2 = CustomSlideGroup()        
        
        selected_img_after_info = v.Html(
            tag="div", children=["Source: Date: Cloud Cover:"]
        )
        imgSelection2 = v.Card(
            # class_="pa-3 ma-5",
            children=[
                v.CardTitle(class_="pa-1 ma-1", children=["Actual image info"]),
                selected_img_after_info,
                v.CardTitle(class_="pa-1 ma-1", children=["Planet Monthly Mosaics"]),
                slider_after_planet,
                v.CardTitle(class_="pa-1 ma-1", children=["Sentinel 2 Images"]),
                slider_after_s2,
            ]
        )

        # Create map with buttons1
        self.map31 = sw.Col(children=[self.map_31, imgSelection1])

        # Create map with buttons2
        self.map32 = sw.Col(children=[self.map_32, imgSelection2])

        # Create drawer control and add to maps
        self.draw_alerts1 = CustomDrawControl(self.map_31)
        self.draw_alerts2 = CustomDrawControl(self.map_32)

        # Create aoi control
        self.aoi_control = AoiControl(self.map_32)
        self.map_32.add(self.aoi_control)

        link((self.draw_alerts1, "data"), (self.draw_alerts2, "data"))

        label31 = v.CardTitle(class_="pa-1 ma-1", children=["Navigation Bar"])
        prev_button = sw.Btn(
            icon="fa-solid fa-backward-step",
            color="primary",
            outlined=True,
            value=-1,
            small=True,
        )
        next_button = sw.Btn(
            icon="fa-solid fa-forward-step",
            color="primary",
            outlined=True,
            value=1,
            small=True,
        )
        self.alert_id_button = sw.TextField(
            v_model=self.analyzed_alerts_model.actual_alert_id,
            outlined=True,
            dense=True,
            single_line=True,
            style_="width:5%, height:10px",
        )
        go_to_alert_button = sw.Btn(
            "Go", color="primary", outlined=True, value=0, small=True
        )

        prev_button.on_event("click", self.navigate)
        next_button.on_event("click", self.navigate)
        go_to_alert_button.on_event("click", self.navigate)

        section_title = v.CardTitle(class_="pa-1 ma-1", children=["Alert revision"])
        card01 = v.Card(
            class_="pa-3 ma-3 d-flex justify-center",
            hover=True,
            children=[
                prev_button,
                next_button,
                self.alert_id_button,
                go_to_alert_button,
            ],
        )
        self.selected_alert_info = v.Html(
            tag="div",
            children=[
                v.Html(tag="strong", children=["First Date: "]),
                "",
                # v.Html(tag='br'),
                v.Html(tag="strong", children=["Last Date: "]),
                "",
            ],
        )
        card02 = v.Card(
            class_="pa-3 ma-3",
            hover=True,
            children=[
                v.CardTitle(class_="pa-1 ma-1", children=["Alert info"]),
                self.selected_alert_info,
            ],
        )

        self.boton_confirmacion = sw.Select(
            items=["Yes", "No", "Need further revision"],
            v_model="Yes",
            # label="Is this a true alert?",
            multiple=False,
            clearable=True,
            chips=True,
        )

        card03 = v.Card(
            class_="pa-3 ma-3",
            hover=True,
            children=[
                v.CardTitle(class_="pa-1 ma-1", children=["Is this a true alert?"]),
                self.boton_confirmacion,
            ],
        )

        label33 = v.CardTitle(class_="pa-1 ma-1", children=["Alert drawing"])
        self.apply_DL_model_button.on_event("click", self.add_defo_prediction_map)
        self.edit_layer_button.on_event("click", self.edit_defo_prediction_map)

        card04 = v.Card(
            class_="pa-3 ma-3",
            hover=True,
            children=[
                label33,
                self.apply_DL_model_button,
                self.edit_layer_button,
                self.alert_draw_alert,
            ],
        )

        label34 = v.CardTitle(class_="pa-1 ma-1", children=["Description Field"])
        self.comments_input = sw.TextField(label="Enter text here", v_model="")

        card05 = v.Card(
            class_="pa-3 ma-3", hover=True, children=[label34, self.comments_input]
        )

        save_btn = sw.Btn("Save", color="primary", outlined=True)
        save_btn.on_event("click", self.save_attributes_to_gdf)
        download_alert_data_btn = sw.Btn("Download", color="primary", outlined=True)
        download_alert_data_btn.on_event("click", self.download_data)
        self.files_dwn_btn = sw.DownloadBtn(text="Files")
        self.report_dwn_btn = sw.DownloadBtn(text="Report")

        card06 = v.Card(
            class_="pa-3 ma-5 d-flex justify-center",
            fluid=True,
            hover=True,
            children=[save_btn, download_alert_data_btn],
        )
        card07 = v.Card(
            class_="pa-3 ma-5 d-flex justify-center",
            fluid=True,
            hover=True,
            children=[self.files_dwn_btn, self.report_dwn_btn],
        )

        card00 = v.Card(
            class_="py-2",
            children=[card02, card01, card03, card04, card05, card06, card07],
        )

        # Layout de la aplicación

        layout = sw.Row(
            dense=True,
            children=[
                sw.Col(cols=5, children=[self.map31]),
                sw.Col(cols=5, children=[self.map32]),
                sw.Col(cols=2, children=[card00]),
            ],
        )
        self.children = [layout]

    def create_gdf(self):
        print("Ejecutando create gdf")
        if self.selected_alerts_model.alerts_bbs is None:
            print("opcion 1, no gdf", len(self.selected_alerts_model.alerts_bbs))
            self.analyzed_alerts_model.alerts_gdf = None
        else:
            print("opcion 2 , Creando GDF")
            alertas_gdf = convert_to_geopandas(self.selected_alerts_model.alerts_bbs)
            self.analyzed_alerts_model.alerts_gdf = alertas_gdf
            self.analyzed_alerts_model.actual_alert_id = 0
            self.analyzed_alerts_model.max_alert_id = len(alertas_gdf)+1
            # alerta = self.analyzed_alerts_model.alerts_gdf.iloc[
            #     self.analyzed_alerts_model.actual_alert_id
            # ]
            # self.map_31.zoom_bounds(alerta["bounding_box"].bounds)

    def add_gdf(self, change):
        print("Full bbs received", len(self.selected_alerts_model.alerts_total_bbs))
        total_alertas_gdf = convert_to_geopandas(
            self.selected_alerts_model.alerts_total_bbs
        )
        analyzed_temp_alerts = self.analyzed_alerts_model.alerts_gdf[
            self.analyzed_alerts_model.alerts_gdf["status"] != "Not reviewed"
        ]
        unique_values = analyzed_temp_alerts["bounding_box"].unique()
        if len(unique_values) == 0:
            filtered_total = total_alertas_gdf
        else:
            filtered_total = total_alertas_gdf[
                ~total_alertas_gdf["bounding_box"].isin(unique_values)
            ].reset_index(drop=True)

        combined_gdf = gpd.GeoDataFrame(
            pd.concat([analyzed_temp_alerts, filtered_total])
        )
        self.analyzed_alerts_model.alerts_gdf = combined_gdf
        self.analyzed_alerts_model.max_alert_id = len(combined_gdf)+1
        # Save gdf to app_tile_model.recipe_folder_path
        self.save_alerts_to_gdf()

    def save_alerts_to_gdf(self):
        alertas_gdf = self.analyzed_alerts_model.alerts_gdf

        # Set the geometry column if necessary (optional, if it is not already set)
        alertas_gdf.set_geometry("bounding_box", inplace=True)
        alert_db_name = self.app_tile_model.recipe_folder_path + "/alert_db.csv"
        # Export to GPKG (GeoPackage)
        alertas_gdf.set_crs(epsg="4326", allow_override=True, inplace=True).to_csv(
            alert_db_name
        )  # Save as CSV

    def update_gdf(self, change):
        print("cambio detectado en selected_Alerts, ejecutando create gdf")
        # Update the tile when selected alert centroids changes
        self.create_gdf()

    def navigate(self, widget, event, data):
        # widget.loading = True  # Set button to loading state
        # widget.disabled = True  # Disable button to prevent further clicks

        if widget.value == 0:
            self.analyzed_alerts_model.actual_alert_id = int(
                self.alert_id_button.v_model
            )
        else:
            self.analyzed_alerts_model.actual_alert_id = (
                (self.analyzed_alerts_model.actual_alert_id + widget.value) % self.analyzed_alerts_model.max_alert_id
            )

        self.files_dwn_btn.set_url()
        self.report_dwn_btn.set_url()

        # widget.loading = False  # Remove loading state
        # widget.disabled = False  # Re-enable the button

    def create_horizontal_slide_group(
        self,
        data_list,
        main_component,
        default_v_model,
        callback,
        model_att1,
        callback2,
        model_att2,
        fire_callback
    ):

        map_element = main_component.children[0]
        info_element = main_component.children[1].children[1]
        slide_group = main_component.children[1].children[3]

        # Sort data by 'milis' attribute
        sorted_data = sorted(data_list, key=itemgetter("milis"), reverse=False)
        date_indices = {i: item for i, item in enumerate(sorted_data)}
        if default_v_model == 1:
            default_v_model = len(sorted_data) - 1
        else:
            default_v_model = len(sorted_data) - 3

        # Assign colors based on source attribute
        color_map = {
            "Sentinel 2": "blue",
            "Planet NICFI": "green",
            "selected": "orange",
        }
        # Initialize slide_group as a v-slide-group
        #slide_group.slide_group.children = []  # Clear any previous content
        #slide_group.mandatory = True
        #slide_group.show_arrows = True  # Show arrows for navigation if needed
        # slide_group.style_="max-width: 90%;"

        # Helper function to create a button for each item
        def create_slide_button(i, item):
            # Get the color based on source, or use default if source is undefined
            img_source = item["source"]
            button_color = color_map.get(img_source, "lightgray")

            if img_source == "Planet NICFI":
                date_string = datetime.utcfromtimestamp(item["milis"] / 1000).strftime(
                    "%b"
                )
            elif img_source == "Sentinel 2":
                date_string = datetime.utcfromtimestamp(item["milis"] / 1000).strftime(
                    "%b %d"
                )

            # Button representing each slide showing 'milis'
            button = sw.Btn(
                text=date_string,
                color=button_color,
                class_="ma-1",
                value=i,
                style_="min-width: 40px; min-height: 40px;",
                # Use a default argument in lambda to capture the current index
            )
            button.on_event("click", on_slide_button_click)
            return button

        # Function to handle button click triggering callbacks
        def on_slide_button_click(widget, event, data):
            widget.loading = True  # Set button to loading state
            widget.disabled = True  # Disable button to prevent further clicks

            # Call the callbacks with the selected item
            selected_item = date_indices[widget.value]
            callback(selected_item, map_element, model_att1)
            callback2(selected_item, info_element, model_att2)

            widget.loading = False  # Remove loading state
            widget.disabled = False  # Re-enable the button

        # Create buttons for each item and add to slide group
        slides = [create_slide_button(i, item) for i, item in enumerate(sorted_data)]
        slide_group.slide_group.children = slides
        slide_group.set_loading_state(False)

        if fire_callback == True:
            #Set the initial slide callback
            selected_item = date_indices[slide_group.slide_group.children[default_v_model].value]
            callback(selected_item, map_element, model_att1)
            callback2(selected_item, info_element, model_att2)


    def create_horizontal_slide_group_s2(
        self,
        data_list,
        main_component,
        default_v_model,
        callback,
        model_att1,
        callback2,
        model_att2,
    ):

        map_element = main_component.children[0]
        info_element = main_component.children[1].children[1]
        slide_group = main_component.children[1].children[5]

        # Sort data by 'milis' attribute
        sorted_data = sorted(data_list, key=itemgetter("milis"), reverse=False)
        date_indices = {i: item for i, item in enumerate(sorted_data)}
        if default_v_model == 1:
            default_v_model = len(sorted_data) - 1
        else:
            default_v_model = len(sorted_data) - 3

        # Assign colors based on source attribute
        color_map = {
            "Sentinel 2": "blue",
            "Planet NICFI": "green",
            "selected": "orange",
        }
        # Initialize slide_group as a v-slide-group
        slide_group.children = []  # Clear any previous content
        slide_group.mandatory = True
        slide_group.show_arrows = True  # Show arrows for navigation if needed
        # slide_group.style_="max-width: 90%;"

        # Helper function to create a button for each item
        def create_slide_button(i, item):
            # Get the color based on source, or use default if source is undefined
            img_source = item["source"]
            button_color = color_map.get(img_source, "lightgray")

            if img_source == "Planet NICFI":
                date_string = datetime.utcfromtimestamp(item["milis"] / 1000).strftime(
                    "%b"
                )
            elif img_source == "Sentinel 2":
                date_string = datetime.utcfromtimestamp(item["milis"] / 1000).strftime(
                    "%b %d"
                )

            # Button representing each slide showing 'milis'
            button = sw.Btn(
                text=date_string,
                color=button_color,
                class_="ma-1",
                value=i,
                style_="min-width: 40px; min-height: 40px;",
                # Use a default argument in lambda to capture the current index
            )
            button.on_event("click", on_slide_button_click)
            return button

        # Function to handle button click triggering callbacks
        def on_slide_button_click(widget, event, data):
            widget.loading = True  # Set button to loading state
            widget.disabled = True  # Disable button to prevent further clicks

            # Call the callbacks with the selected item
            selected_item = date_indices[widget.value]
            callback(selected_item, map_element, model_att1)
            callback2(selected_item, info_element, model_att2)

            widget.loading = False  # Remove loading state
            widget.disabled = False  # Re-enable the button

        # Create buttons for each item and add to slide group
        slides = [create_slide_button(i, item) for i, item in enumerate(sorted_data)]
        slide_group.slide_group.children = slides
        slide_group.set_loading_state(False)
        
    def image_slider_map_callback(self, selected_item, map_element, model_att):
        et1 = time.time()
        print('Add image to map callback')
        geom = self.actual_alert_grid
        image_id = selected_item["image_id"]
        img_source = selected_item["source"]
        vis1p = {"min": 0, "max": 1600, "bands": ["R", "G", "B"]}
        vis2p = {"min": 0, "max": [6500, 1600, 1600], "bands": ["N", "R", "G"]}

        vis1s = {"min": 0, "max": 3000, "bands": ["B4", "B3", "B2"]}
        vis2s = {"min": 0, "max": [6500, 3000, 3000], "bands": ["B8", "B4", "B3"]}

        if img_source == "Sentinel 2":
            vis1 = vis1s
            vis2 = vis2s
            s2 = ee.ImageCollection("COPERNICUS/S2_HARMONIZED").filterBounds(geom).filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 90))
            s2_same_date = s2.filter(ee.Filter.eq("GENERATION_TIME", image_id))
            img = s2_same_date.mosaic().clip(geom)

        elif img_source == "Planet NICFI":
            vis1 = vis1p
            vis2 = vis2p
            img = ee.Image(image_id).clip(geom)

        if model_att == 0:
            self.selected_img_before = img
        elif model_att == 1:
            self.selected_img_after = img

        orig_alert = ee.Image(self.selected_alerts_model.filtered_alert_raster).clip(
            self.actual_bb.geometry()
        )
        et2 = time.time()
        print('images created ', et2- et1)

        map_element.addLayer(img, vis1, "True Color", False)
        map_element.addLayer(img, vis2, "False Color", True)
        map_element.addLayer(
            orig_alert.select("alert"),
            {"min": 1, "max": 2, "palette": ["orange", "purple"]},
            "Original Alert",
            False,
            0.5,
        )
        et3 = time.time()
        print('Images addde to map', et3- et2)

    def image_slider_info_callback(self, selected_item, info_element, model_att):
        info_element.loading = True
        info1 = selected_item["source"]
        info2 = datetime.utcfromtimestamp(selected_item["milis"] / 1000).strftime(
            "%Y-%m-%d"
        )
        info3 = selected_item["cloud_cover"]
        info4 = selected_item["value"]
        lista = [info1, info2, info3, info4]
        if model_att == 0:
            self.selected_img_before_info_list = lista
        elif model_att == 1:
            self.selected_img_after_info_list = lista
        info_element.loading = False
        info_element.children = [
            f"Source: {info1}, Date: {info2}, Cloud Cover: {info3}"
        ]

    def view_actual_alert(self, change):
        print("cambiando alerta", self.analyzed_alerts_model.actual_alert_id)

        recipe_dictionary_path = self.app_tile_model.recipe_folder_path + '/recipe_parameters.json'
        
        update_actual_id(recipe_dictionary_path ,self.analyzed_alerts_model.actual_alert_id)
        
        # Select alert
        alerta = self.analyzed_alerts_model.alerts_gdf.iloc[
            self.analyzed_alerts_model.actual_alert_id
        ]
        #Obtener fechas de la alerta 
        fecha1 = convert_julian_to_date(alerta["alert_date_min"])
        fecha2 = convert_julian_to_date(alerta["alert_date_max"])

        
        #Cambio en boton de navegacion
        self.alert_id_button.v_model = self.analyzed_alerts_model.actual_alert_id
        
        #Cambio en alert info
        self.selected_alert_info.children = [
            v.Html(tag="strong", children=["First Date: "]),
            fecha1,
            v.Html(tag="br"),
            v.Html(tag="strong", children=[" Last Date: "]),
            fecha2,
        ]
        self.map31.children[1].children[5].set_loading_state(True)
        self.map32.children[1].children[5].set_loading_state(True)
        
        #Reset del mapa
        self.map_31.remove_all()
        self.map_32.remove_all()

        # Clean previous draw elements
        self.draw_alerts1.hide()
        self.draw_alerts2.hide()
        # Zoom to alert bb
        alerta_bb_geojson = alerta["bounding_box"].__geo_interface__
        self.map_31.center = (alerta.point.y, alerta.point.x)
        self.map_31.zoom = 16
        #self.map_31.zoom_bounds(alerta["bounding_box"].bounds)
        #self.map_31.zoom_bounds(alerta["bounding_box"].bounds)
        ##Add bounding box
        geojson_layer = GeoJSON(
            data=alerta_bb_geojson,
            style={
                "color": "yellow",
                "fillColor": "#3366cc",
                "opacity": 0.5,
                "weight": 1,
                "dashArray": "2",
                "fillOpacity": 0,
            },
            name="Alert BB",
        )
        self.map_31.add_layer(geojson_layer)
        self.map_32.add_layer(geojson_layer)

        # Create gee feature
        alerta_bb_geojson_ee = ee.Feature(alerta_bb_geojson).buffer(100, 1).bounds(1)
        self.aoi_control.add_aoi("AOI", alerta_bb_geojson_ee)
        self.actual_bb = alerta_bb_geojson_ee

        # Generar grilla de descarga
        gridDescarga = alerta_bb_geojson_ee.geometry().coveringGrid(
            "EPSG:3857", 1 * 256 * 4.77
        )
        gridDescargaBounds = gridDescarga.geometry().bounds(1)

        self.actual_alert_grid = gridDescargaBounds
       
        
        # Actualizar slider de imagenes
        self.create_horizontal_slide_group(
            self.analyzed_alerts_model.before_planet_monthly_images,
            self.map31,
            0,
            self.image_slider_map_callback,
            0,
            self.image_slider_info_callback,
            0,
            True,
        )
        self.create_horizontal_slide_group(
            self.analyzed_alerts_model.after_planet_monthly_images,
            self.map32,
            1,
            self.image_slider_map_callback,
            1,
            self.image_slider_info_callback,
            1,
            True,
        )

        # Obtener imagenes
        et6 = time.time()
        sentinel2_mosaics_dates = get_sentinel2_dates(fecha1, fecha2)
        self.start_s2_dictionary_thread(gridDescargaBounds, sentinel2_mosaics_dates[0], sentinel2_mosaics_dates[1], self.assign_s2_before_dictionary)
        self.start_s2_dictionary_thread(gridDescargaBounds, sentinel2_mosaics_dates[2], sentinel2_mosaics_dates[3], self.assign_s2_after_dictionary)
        et7 = time.time()
        print("imagenes S2 seleccionadas", et7 - et6)
        #s2ind_images_dict_before = getIndividualS2(gridDescargaBounds, sentinel2_mosaics_dates[0], sentinel2_mosaics_dates[1])
        #s2ind_images_dict_after = getIndividualS2(gridDescargaBounds, sentinel2_mosaics_dates[2], sentinel2_mosaics_dates[3])
        #print(s2ind_images_dict_before, s2ind_images_dict_after)
        
        # et7 = time.time()
        # print("imagenes S2 seleccionadas", et7 - et6)

        # self.create_horizontal_slide_group_s2(
        #     s2ind_images_dict_before,
        #     self.map31,
        #     0,
        #     self.image_slider_map_callback,
        #     0,
        #     self.image_slider_info_callback,
        #     0,
        #     #False,
        # )
        # self.create_horizontal_slide_group_s2(
        #     s2ind_images_dict_after,
        #     self.map32,
        #     1,
        #     self.image_slider_map_callback,
        #     1,
        #     self.image_slider_info_callback,
        #     1,
        #     #False,
        # )
        
    def download_images_button(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks
        image_name = (
            self.app_tile_model.recipe_folder_path
            + "/alert_"
            + str(self.analyzed_alerts_model.actual_alert_id)
            + ".tif"
        )
        source1 = self.selected_img_before_info_list[0]
        source2 = self.selected_img_after_info_list[0]

        download_both_images(
            self.selected_img_before,
            self.selected_img_after,
            image_name,
            source1,
            source2,
            self.actual_alert_grid,
        )
        widget.loading = False  # Remove loading state
        widget.disabled = False  # Re-enable the button

    def add_defo_prediction_map(self, widget, event, data):
        # widget.loading = True  # Set button to loading state
        # widget.disabled = True  # Disable button to prevent further clicks

        print("Inicio modelo")
        image_name = (
            self.app_tile_model.recipe_folder_path
            + "/alert_"
            + str(self.analyzed_alerts_model.actual_alert_id)
            + ".tif"
        )
        source1 = self.selected_img_before_info_list[0]
        source2 = self.selected_img_after_info_list[0]

        download_both_images(
            self.selected_img_before,
            self.selected_img_after,
            image_name,
            source1,
            source2,
            self.actual_alert_grid,
        )

        prediction_name = self.send_file_for_processing(image_name)

        defo_gdf_layer = raster_to_gdf(prediction_name, "4326", 0.20)

        self.defo_dl_layer = defo_gdf_layer
        geo_json_layer = GeoData(geo_dataframe=self.defo_dl_layer, name="Defo DL")

        self.map_31.add_layer(geo_json_layer)
        self.map_32.add_layer(geo_json_layer)

        # widget.loading = False  # Remove loading state
        # widget.disabled = False  # Re-enable the button

    def edit_defo_prediction_map(self, widget, event, data):
        # widget.loading = True  # Set button to loading state
        # widget.disabled = True  # Disable button to prevent further clicks

        self.draw_alerts1.clear()
        self.draw_alerts2.clear()

        # Simplify polygon for edition
        edit_layer = simplify_and_extract_features(self.defo_dl_layer, "geometry", 20)
        self.draw_alerts1.data = convertir_formato2(edit_layer)

        self.draw_alerts1.show()
        self.draw_alerts2.show()

        # widget.loading = False  # Remove loading state
        # widget.disabled = False  # Re-enable the button

    def save_attributes_to_gdf(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks
        alertas_gdf = self.analyzed_alerts_model.alerts_gdf
        actual_alert_id = self.analyzed_alerts_model.actual_alert_id
        alerta = self.analyzed_alerts_model.alerts_gdf.iloc[actual_alert_id]

        status_dict = {
            "Yes": "Confirmed",
            "No": "False Positive",
            "Need further revision": "maybe",
        }
        alertas_gdf.at[actual_alert_id, "status"] = status_dict[
            self.boton_confirmacion.v_model
        ]
        alertas_gdf.at[actual_alert_id, "description"] = self.comments_input.v_model
        alertas_gdf.at[
            actual_alert_id, "before_img"
        ] = self.selected_img_before_info_list[3]
        alertas_gdf.at[
            actual_alert_id, "after_img"
        ] = self.selected_img_after_info_list[3]


        #Get adminstrative location attributes
        adminL2 = ee.FeatureCollection("FAO/GAUL/2015/level2")
        selected_admin = adminL2.filterBounds(self.actual_bb.geometry())
        at1 = selected_admin.aggregate_array("ADM0_NAME").distinct()
        at2 = selected_admin.aggregate_array("ADM1_NAME").distinct()
        at3 = selected_admin.aggregate_array("ADM2_NAME").distinct()

        st1 = at1.iterate(
            lambda list_element, result: ee.String(result)
            .cat(list_element)
            .cat(ee.String(", ")),
            ee.String(""),
        )
        st2 = at2.iterate(
            lambda list_element, result: ee.String(result)
            .cat(list_element)
            .cat(ee.String(", ")),
            ee.String(""),
        )
        st3 = at3.iterate(
            lambda list_element, result: ee.String(result)
            .cat(list_element)
            .cat(ee.String(", ")),
            ee.String(""),
        )
       
        alertas_gdf.at[actual_alert_id, "admin1"] = st1.getInfo()[:-1]
        alertas_gdf.at[actual_alert_id, "admin2"] = st2.getInfo()[:-1]
        alertas_gdf.at[actual_alert_id, "admin3"] = st3.getInfo()[:-1]

        if self.boton_confirmacion.v_model != "Yes":
            alertas_gdf.at[actual_alert_id, "alert_polygon"] = None
            alertas_gdf.at[actual_alert_id, "area_ha"] = 0
        else:
            # Add deforestation geometry to gdf
            alertas_gdf.at[actual_alert_id, "alert_polygon"] = self.defo_dl_layer[
                "geometry"
            ].union_all()
            alertas_gdf.at[actual_alert_id, "area_ha"] = calculate_total_area(
                self.defo_dl_layer
            )
        self.save_alerts_to_gdf()
        widget.loading = False  # Remove loading state
        widget.disabled = False  # Re-enable the button

    def download_data(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks

        alertas_gdf = self.analyzed_alerts_model.alerts_gdf
        actual_alert_id = self.analyzed_alerts_model.actual_alert_id
        alerta = self.analyzed_alerts_model.alerts_gdf.iloc[actual_alert_id]

        # Create new file
        # Select an element (for example, select by index or a condition)
        selected_element = alertas_gdf.iloc[
            actual_alert_id
        ]  # Select the first row as an example
        # Convert it to a GeoDataFrame (since a single row becomes a Series)
        selected_gdf = gpd.GeoDataFrame([selected_element], columns=alertas_gdf.columns)
        # Set the geometry column if necessary (optional, if it is not already set)
        selected_gdf.set_geometry("alert_polygon", inplace=True)
        gpkg_name = (
            self.app_tile_model.recipe_folder_path
            + "/alert_"
            + str(self.analyzed_alerts_model.actual_alert_id)
            + ".gpkg"
        )
        # Export to GPKG (GeoPackage)
        selected_gdf.set_crs(epsg="4326", allow_override=True, inplace=True).to_file(
            gpkg_name, driver="GPKG"
        )  # Save as GPKG

        image_name = (
            self.app_tile_model.recipe_folder_path
            + "/alert_"
            + str(self.analyzed_alerts_model.actual_alert_id)
            + ".tif"
        )
        prediction_name = (
            self.app_tile_model.recipe_folder_path
            + "/alert_"
            + str(self.analyzed_alerts_model.actual_alert_id)
            + "_prediction.tif"
        )
        zipfile_name = (
            self.app_tile_model.recipe_folder_path
            + "/alert_"
            + str(self.analyzed_alerts_model.actual_alert_id)
            + ".zip"
        )

        add_files_to_zip(zipfile_name, image_name, prediction_name, gpkg_name)
        zip_path = os.path.abspath(zipfile_name)
        self.files_dwn_btn.set_url(path=zip_path)

        # Create report
        output_report_path = (
            self.app_tile_model.recipe_folder_path
            + "/alert_"
            + str(self.analyzed_alerts_model.actual_alert_id)
            + ".docx"
        )

        if self.aux_model.custom_report_template == "":
            report_template = "utils/report_template.docx"
        else:
            report_template = self.aux_model.custom_report_template

        generate_deforestation_report_with_word_template(
            image_name, gpkg_name, report_template, output_report_path
        )

        report_path = os.path.abspath(output_report_path)
        self.report_dwn_btn.set_url(path=report_path)

        widget.loading = False  # Remove loading state
        widget.disabled = False  # Re-enable the button
        # self.analyzed_alerts_model.actual_alert_id = self.analyzed_alerts_model.actual_alert_id + 1
     
    def assign_s2_before_dictionary(self, json):
        self.analyzed_alerts_model.before_s2_images = json
        self.analyzed_alerts_model.before_s2_images_time = datetime.today().timestamp()

    def assign_s2_after_dictionary(self, json):
        self.analyzed_alerts_model.after_s2_images = json
        self.analyzed_alerts_model.after_s2_images_time = datetime.today().timestamp()


    def start_s2_dictionary_thread(
        self,
        poly,
        fecha1,
        fecha2,
        assign_function,
    ):
        thread = threading.Thread(
            target=lambda: assign_function(
                getIndividualS2(
                    poly,
                    fecha1,
                    fecha2,
                )
            )
        )
        thread.start()

    def slider_s2_before(self, change):
        print(self.analyzed_alerts_model.before_s2_images, self.analyzed_alerts_model.before_s2_images_time)
        self.create_horizontal_slide_group_s2(
            self.analyzed_alerts_model.before_s2_images,
            self.map31,
            0,
            self.image_slider_map_callback,
            0,
            self.image_slider_info_callback,
            0,
            #False,
        )
    
    def slider_s2_after(self, change):
        print(self.analyzed_alerts_model.after_s2_images, self.analyzed_alerts_model.after_s2_images_time)
        self.create_horizontal_slide_group_s2(
            self.analyzed_alerts_model.after_s2_images,
            self.map32,
            0,
            self.image_slider_map_callback,
            0,
            self.image_slider_info_callback,
            0,
            #False,
        )
