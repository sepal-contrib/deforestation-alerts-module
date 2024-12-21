from sepal_ui import color, sepalwidgets as sw
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
from component.scripts.recipe_helper import update_saved_dictionary
from component.widget.custom_sw import CustomDrawControl, CustomSlideGroup, CustomBtnWithLoader

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

    
        self.dl_button1 = CustomBtnWithLoader(class_='pa-1 ma-1', color = color.secondary, rounded=True, small=True, text='M 1')
        self.dl_button2 = CustomBtnWithLoader(class_='pa-1 ma-1', color = color.secondary, rounded=True, small=True, text='M 2')
        self.alert_draw_alert = sw.Alert().hide()
        self.run_dl_model_1 = su.loading_button(alert=self.alert_draw_alert, button=self.dl_button1)(self.run_dl_model_1)
        self.run_dl_model_2 = su.loading_button(alert=self.alert_draw_alert, button=self.dl_button2)(self.run_dl_model_2)

        self.initialize_layout()

        ## Observe changes in selected_alerts_model and update tile when it changes
        self.selected_alerts_model.observe(self.update_gdf_partial, "alerts_bbs")
        self.selected_alerts_model.observe(self.update_gdf_full, "alerts_total_bbs")
        self.analyzed_alerts_model.observe(self.view_actual_alert, "actual_alert_id")
        self.analyzed_alerts_model.observe(self.slider_s2_before, "before_s2_images_time")
        self.analyzed_alerts_model.observe(self.slider_s2_after, "after_s2_images_time")
        self.analyzed_alerts_model.observe(self.add_defo_layer, "defo_dl_layer")
        self.analyzed_alerts_model.observe(self.enable_dl1, "model1_prediction_file")
        self.analyzed_alerts_model.observe(self.enable_dl2, "model2_prediction_file")
        
        # Queue for communication between main and worker threads
        self.file_queue1 = queue.Queue()
        self.result_queue1 = queue.Queue()
        self.file_queue2 = queue.Queue()
        self.result_queue2 = queue.Queue()

        # Start the worker thread
        worker1_thread = threading.Thread(target=self.worker_m1)
        worker1_thread.start()
        worker2_thread = threading.Thread(target=self.worker_m2)
        worker2_thread.start()

        super().__init__()

    def initialize_layout(self):
        display(
            HTML(
                """
        <style>
            .custom-map-class2 {
                width: 100% !important;
                height: 55vh !important;
                }
             .v-text-field .v-input__control .v-input__slot {
                min-height: auto !important;
                min-width: 40px,
                max-width: 40px,
                display: flex !important;
                align-items: center !important;
              }
        </style>
        """
            )
        )
        #Create Variables
        self.actual_bb = None
        self.selected_img_before = None
        self.selected_img_after = None
        self.selected_img_before_info_list = None
        self.selected_img_after_info_list = None
        self.active_before_button = None
        self.active_after_button = None
        
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
        
        slider_before_planet = CustomSlideGroup()
        slider_before_s2 = CustomSlideGroup(style_="max-width: 80vh")      

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

        slider_after_planet = CustomSlideGroup()
        slider_after_s2 = CustomSlideGroup(style_="max-width: 80vh")      
        
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

        label31 = v.CardTitle(class_="pa-0 ma-1", children=["Navigation Bar"])
        self.prev_button = sw.Btn(
            icon="fa-solid fa-backward-step",
            color="primary",
            outlined=True,
            value=-1,
            small=True,
        )
        self.next_button = sw.Btn(
            icon="fa-solid fa-forward-step",
            color="primary",
            outlined=True,
            value=1,
            small=True,
        )
        self.alert_id_button = v.TextField(
            v_model=self.analyzed_alerts_model.actual_alert_id,
            outlined=True,
            single_line=True,
            #class_="ma-0 pa-n6",
            #style_ = "max-width:50px",
        )
        self.go_to_alert_button = sw.Btn(
            "Go", color="primary", outlined=True, value=0, small=True
        )

        self.prev_button.on_event("click", self.navigate)
        self.next_button.on_event("click", self.navigate)
        self.go_to_alert_button.on_event("click", self.navigate)

        section_title = v.CardTitle(class_="pa-1 ma-1", children=["Alert revision"])

        self.selected_alert_info = v.Html(
            tag="div",
            children=[
                v.Html(tag="strong", children=["First Date: "]),
                "",
                # v.Html(tag='br'),
                v.Html(tag="strong", children=["Last Date: "]),
                "",
                v.Html(tag="strong", children=["Status: "]),
                "",
            ],
        )

        self.boton_confirmacion = sw.Select(
            items=["Yes", "No", "Need further revision"],
            v_model="Yes",
            # label="Is this a true alert?",
            small=True,
            multiple=False,
            clearable=True,
            chips=True,
        )

        label33 = v.CardTitle(class_="pa-1 ma-1", children=["Alert drawing"])
        self.start_edit_button = v.Btn(class_='pa-1 ma-1', color = color.primary, small=True, children=[v.Icon(color = color.bg, children=['fa-solid fa-pen'])])
        self.clear_button = v.Btn(class_='pa-1 ma-1', color = color.error, small=True, children=[v.Icon(color = color.bg, children=['fa-solid fa-trash'])], disabled = True)
        self.save_edit_button = v.Btn(class_='pa-1 ma-1', color = color.primary, small=True, children=[v.Icon(color = color.bg, children=['fa-solid fa-floppy-disk'])], disabled = True)
        self.stop_edit_button = v.Btn(class_='pa-1 ma-1', color = color.secondary, small=True, children=[v.Icon(color = color.bg, children=['fa-solid fa-x'])], disabled = True)


        self.start_edit_button.on_event("click", self.start_edition_function)
        self.clear_button.on_event("click", self.clear_edition_function)
        self.save_edit_button.on_event("click", self.save_edition_function)
        self.stop_edit_button.on_event("click", self.stop_edition_function)


        tooltip1 = sw.Tooltip(self.start_edit_button, tooltip ='Start Drawing', top=True, open_delay=100, close_delay = 100)
        tooltip2 = sw.Tooltip(self.clear_button, tooltip ='Clear all', top=True, open_delay=100, close_delay = 100)
        tooltip3 = sw.Tooltip(self.save_edit_button, tooltip ='Save', top=True, open_delay=100, close_delay = 100)
        tooltip4 = sw.Tooltip(self.stop_edit_button, tooltip ='Stop Drawing', top=True, open_delay=100, close_delay = 100)

        self.toolBarEdition = v.Toolbar(class_="px-3 d-flex align-center",children = [tooltip1, tooltip3, tooltip4])

        #self.dl_button1 = v.Btn(class_='pa-1 ma-1', color = color.secondary, rounded=True, small=True, children=['DL 1'])
        self.dl_button1_add = v.Btn(class_='pa-1 ma-1', color = color.primary, small=True, children=[v.Icon(color = color.bg, children=['fa-solid fa-plus'])], disabled = True)
        self.dl_button1_remove = v.Btn(class_='pa-1 ma-1', color = color.error, small=True, children=[v.Icon(color = color.bg, children=['fa-solid fa-minus',])], disabled = True)

        self.dl_button1.on_event("click", self.run_dl_model_1)
        self.dl_button1_add.on_event("click", self.add_model1_prediction)
        self.dl_button1_remove.on_event("click", self.remove_model1_prediction)

        tooltip5 = sw.Tooltip(self.dl_button1, tooltip ='Smaller and faster model', top=True, open_delay=100, close_delay = 100)
        tooltip6 = sw.Tooltip(self.dl_button1_add, tooltip ='Add to map', top=True, open_delay=100, close_delay = 100)
        tooltip7 = sw.Tooltip(self.dl_button1_remove, tooltip ='Remove from map', top=True, open_delay=100, close_delay = 100)
        
        self.toolBarDL1 = sw.Toolbar(children = [tooltip5,  tooltip6, tooltip7]).hide()

        
        #self.dl_button2 = v.Btn(class_='pa-1 ma-1', color = color.secondary, rounded=True, small=True, children=['DL 2'])
        self.dl_button2_add = v.Btn(class_='pa-1 ma-1', color = color.primary, small=True, children=[v.Icon(color = color.bg, children=['fa-solid fa-plus'])], disabled = True)
        self.dl_button2_remove = v.Btn(class_='pa-1 ma-1', color = color.error, small=True, children=[v.Icon(color = color.bg, children=['fa-solid fa-minus',])], disabled = True)

        self.dl_button2.on_event("click", self.run_dl_model_2)
        self.dl_button2_add.on_event("click", self.add_model2_prediction)
        self.dl_button2_remove.on_event("click", self.remove_model2_prediction)

        tooltip8 = sw.Tooltip(self.dl_button2, tooltip ='Larger but slower model', top=True, open_delay=100, close_delay = 100)
        tooltip9 = sw.Tooltip(self.dl_button2_add, tooltip ='Add to map', top=True, open_delay=100, close_delay = 100)
        tooltip10 = sw.Tooltip(self.dl_button2_remove, tooltip ='Remove from map', top=True, open_delay=100, close_delay = 100)
        
        self.toolBarDL2 = sw.Toolbar(children = [tooltip8, tooltip9, tooltip10]).hide()

        
        # self.alert_polygon_selection = v.BtnToggle(children=[
        #             sw.Btn(msg="DL", value="DL", color="primary", outlined=True, small=True,),
        #             sw.Btn(msg="Manual", value="Manual", color="primary", outlined=True, small=True,),
        #             ],
        #             v_model='DL',  # This will hold the value of the selected button
        #             mandatory = True,
        #             color="primary", outlined=True,
        #         )


        label34 = v.CardTitle(class_="pa-1 ma-1", children=["Loss driver"])
        defo_drivers = ['Agriculture expansion','Livestock grazing','Logging', 'Urban expansion', 'Shifting cultivation', 'Mining','Forest fires', 'Hurricanes', 'Other']
        self.comments_input = sw.Combobox( items= defo_drivers,
                                v_model=['Other'],
                                label="Analyze cause of deforestation",
                                multiple=True,
                                clearable=True,
                                chips=True,
                              )
        
        label35 = v.CardTitle(class_="pa-1 ma-1", children=["Save and Export"])
        save_btn = sw.Btn("Save", color="primary", outlined=True, small = True)
        save_btn.on_event("click", self.save_attributes_to_gdf)
        self.download_alert_data_btn = sw.Btn("Download", color="primary", outlined=True, small = True, disabled = True)
        self.download_alert_data_btn.on_event("click", self.download_data)
        self.files_dwn_btn = sw.DownloadBtn(text="Files", small = True)
        self.report_dwn_btn = sw.DownloadBtn(text="Report", small = True)
        
        self.toolBarSaveExport = sw.Toolbar(children = [save_btn,  self.download_alert_data_btn])
        self.toolBarDownloads = sw.Toolbar(children = [self.files_dwn_btn,  self.report_dwn_btn]).hide()

        #Layout
        
        card01 = v.Card(
            class_="pa-3 ma-3 d-flex justify-center",
            hover=True,
            children=[self.prev_button, self.next_button, v.Html(tag = 'body',style_ = 'height: auto, width: 40px', children = [self.alert_id_button]),self.go_to_alert_button],
        )          
        card02 = v.Card(
            class_="pa-3 ma-3",
            hover=True,
            children=[
                v.CardTitle(class_="pa-0 ma-1", children=["Alert info"]),
                self.selected_alert_info,
            ],
        )
        
        card03 = v.Card(
            class_="pa-3 ma-3",
            hover=True,
            children=[
                v.CardTitle(class_="pa-0 ma-1", children=["Is this a true alert?"]),
                self.boton_confirmacion,
            ],
        )

        card35 = v.Card(
            class_="pa-3 ma-3",
            hover=True,
            children=[
                v.CardTitle(class_="pa-0 ma-1", children=["Is this a true alert?"]),
                self.boton_confirmacion,
                label34, 
                self.comments_input,
            ],
        )
      
        card04 = v.Card(
            class_="pa-3 ma-3",
            hover=True,
            children=[
                label33,
                self.toolBarEdition,
                self.toolBarDL1,
                self.toolBarDL2,
                self.alert_draw_alert,
            ],
        )
        
        card05 = v.Card(
            class_="pa-3 ma-3", 
            hover=True, 
            children=[label34, self.comments_input],
        )

        card06 = v.Card(
            class_="pa-3 ma-3",
            fluid=True,
            hover=True,
            children=[label35, self.toolBarSaveExport, self.toolBarDownloads],
        )

        card00 = v.Card(
            class_="py-2",
            overflow_y = 'auto',
            children=[card02, card01, card35, card04, card06],
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


    # Saving alerts to gdf functions
    
    def save_alerts_to_gdf(self):
        alertas_gdf = self.analyzed_alerts_model.alerts_gdf

        # Set the geometry column if necessary (optional, if it is not already set)
        alertas_gdf.set_geometry("bounding_box", inplace=True)
        alert_db_name = self.app_tile_model.recipe_folder_path + "/alert_db.csv"
        # Export to GPKG (GeoPackage)
        alertas_gdf.set_crs(epsg="4326", allow_override=True, inplace=True).to_csv(
            alert_db_name
        )  # Save as CSV
    
    def create_gdf_partial(self):
        """
        Determines the value of `x` based on the conditions of `partial` and `complete`.
    
        Args:
            partial: A variable that can be None or set to a value.
            complete: A variable that can be None or set to a value.
    
        Returns:
            The value of `x` based on the given conditions.
        """
        partial = self.selected_alerts_model.alerts_bbs
        data = self.analyzed_alerts_model.alerts_gdf
    
        if partial is None:
            print('nothing arrived yet')
            self.analyzed_alerts_model.alerts_gdf = None
            
        elif data is None and partial is not None:
            print('partial arrived first')
            alertas_gdf = convert_to_geopandas(partial)
            self.analyzed_alerts_model.alerts_gdf = alertas_gdf
            self.analyzed_alerts_model.actual_alert_id = 0
            self.analyzed_alerts_model.max_alert_id = len(alertas_gdf)
            recipe_dictionary_path = self.app_tile_model.recipe_folder_path + '/recipe_parameters.json'       
            update_saved_dictionary(recipe_dictionary_path ,'max_alert_id',self.analyzed_alerts_model.max_alert_id)
        
        elif data is not None and partial is not None:
            print('partial arrived after')
            pass
            
    def create_gdf_full(self):
        """
        Determines the value of `x` based on the conditions of `partial` and `complete`.
    
        Args:
            partial: A variable that can be None or set to a value.
            complete: A variable that can be None or set to a value.
    
        Returns:
            The value of `x` based on the given conditions.
        """
        complete = self.selected_alerts_model.alerts_total_bbs
        data = self.analyzed_alerts_model.alerts_gdf
    
        if complete is None:
            print('nothing arrived yet')
            self.analyzed_alerts_model.alerts_gdf = None
                      
        elif data is None and complete is not None:
            print('complete arrived first')       
            alertas_gdf = convert_to_geopandas(complete)
            self.analyzed_alerts_model.alerts_gdf = alertas_gdf
            self.analyzed_alerts_model.actual_alert_id = 0
            self.analyzed_alerts_model.max_alert_id = len(alertas_gdf)
            recipe_dictionary_path = self.app_tile_model.recipe_folder_path + '/recipe_parameters.json'       
            update_saved_dictionary(recipe_dictionary_path ,'max_alert_id',self.analyzed_alerts_model.max_alert_id)
            self.save_alerts_to_gdf()
    
        elif data is not None and complete is not None:
            print('merging complete data to analyzed partial')
            total_alertas_gdf = convert_to_geopandas(complete)
            analyzed_temp_alerts = data[data["status"] != "Not reviewed"]
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
            self.analyzed_alerts_model.max_alert_id = len(combined_gdf)
            recipe_dictionary_path = self.app_tile_model.recipe_folder_path + '/recipe_parameters.json'       
            update_saved_dictionary(recipe_dictionary_path ,'max_alert_id',self.analyzed_alerts_model.max_alert_id)
            self.save_alerts_to_gdf()    
    
    def update_gdf_partial(self, change):
        print("cambio detectado en selected_Alerts, ejecutando create gdf")
        self.create_gdf_partial()
    
    def update_gdf_full(self, change):
        print("cambio detectado en selected_Alerts, ejecutando create gdf")
        self.create_gdf_full()


    # Navigation functions
    
    def navigate(self, widget, event, data):
        self.prev_button.disabled = True
        self.next_button.disabled = True
        self.alert_id_button.disabled = True
        self.go_to_alert_button.disabled = True
        
        if widget.value == 0:
            self.analyzed_alerts_model.actual_alert_id = int(
                self.alert_id_button.v_model
            )
        else:
            max_value = self.analyzed_alerts_model.max_alert_id+1
            self.analyzed_alerts_model.actual_alert_id = (
                (self.analyzed_alerts_model.actual_alert_id + widget.value) % max_value
            )

        self.files_dwn_btn.set_url()
        self.report_dwn_btn.set_url()

        self.prev_button.disabled = False
        self.next_button.disabled = False
        self.alert_id_button.disabled = False
        self.go_to_alert_button.disabled = False

    #Function for image sliders
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
        slide_group_secondary = main_component.children[1].children[5]

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
        slide_group.show_arrows = True  # Show arrows for navigation if needed
        slide_group.defaul_child_color =  "green"
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

            selected_item = date_indices[widget.value]
            img_source = selected_item["source"]
           
            slide_group.reset_default_color()
            slide_group_secondary.reset_default_color()
        
            #Change color of selected wigdet
            widget.color = color_map.get("selected")
            
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
            slide_group.slide_group.children[default_v_model].color = color_map.get("selected")
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
        slide_group_secondary = main_component.children[1].children[3]

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
        slide_group.show_arrows = True  # Show arrows for navigation if needed
        slide_group.defaul_child_color =  "blue"
        # slide_group.style_="max-width: 90%;"

        # Helper function to create a button for each item
        def create_slide_button(i, item):
            # Get the color based on source, or use default if source is undefined
            img_source = item["source"]
            cloud_cover = float(item["cloud_cover"])
            button_color = color_map.get(img_source, "lightgray")

            # Determine the icon based on the cloud cover range
            if 0 <= cloud_cover <= 30:
                icon = 'mdi-weather-sunny'
            elif 30 < cloud_cover <= 60:
                icon = 'mdi-cloud-outline'
            elif 60 < cloud_cover <= 100:
                icon = 'mdi-cloud'

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
                msg=date_string,
                gliph = icon,
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

            selected_item = date_indices[widget.value]
            img_source = selected_item["source"]

            slide_group.reset_default_color()
            slide_group_secondary.reset_default_color()
            
            #Change color of selected wigdet
            widget.color = color_map.get("selected")
            
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

        map_element.addLayer(img, vis1, "True Color", True)
        map_element.addLayer(img, vis2, "False Color", False)
        map_element.addLayer(
            orig_alert.select("alert"),
            {"min": 1, "max": 2, "palette": ["orange", "purple"]},
            "Original Alert",
            False,
            0.5,
        )

    def image_slider_info_callback(self, selected_item, info_element, model_att):
        info_element.loading = True
        info1 = selected_item["source"]
        info2 = datetime.utcfromtimestamp(selected_item["milis"] / 1000).strftime(
            "%Y-%m-%d"
        )
        info3 = selected_item["cloud_cover"]
        info4 = selected_item["value"]
        info5 = datetime.utcfromtimestamp(selected_item["milis"] / 1000).strftime(
            "_%d%m%y"
        )
        lista = [info1, info2, info3, info4,info5]
        if model_att == 0:
            self.selected_img_before_info_list = lista
        elif model_att == 1:
            self.selected_img_after_info_list = lista
        info_element.loading = False
        # info_element.children = [
        #     f"Source: {info1}, Date: {info2}, Cloud Cover: {info3}"
        # ]
        info_element.children = [
            v.Html(tag="strong", children=["Source: "]),
            info1,
            v.Html(tag="strong", children=[", Date: "]),
            info2,
            v.Html(tag="strong", children=[", Cloud Cover: "]),
            info3,
        ]

    #Process for each alert
    
    def view_actual_alert(self, change):
        print("cambiando alerta", self.analyzed_alerts_model.actual_alert_id)

        recipe_dictionary_path = self.app_tile_model.recipe_folder_path + '/recipe_parameters.json'
        
        update_saved_dictionary(recipe_dictionary_path ,'actual_alert_id',self.analyzed_alerts_model.actual_alert_id)

        #Reset ui elements
        self.dl_button1_add.disabled = True
        self.dl_button1_remove.disabled = True
        self.dl_button1_add.disabled = True
        self.dl_button1_remove.disabled = True
        self.clear_button.disabled = True
        self.save_edit_button.disabled = True
        self.stop_edit_button.disabled = True
        self.start_edit_button.disabled = False
        self.toolBarDL1.hide()
        self.toolBarDL2.hide()
        self.download_alert_data_btn.disabled = True
        self.toolBarDownloads.hide()

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
        if alerta["status"] != 'Not reviewed':
            alert_st = 'Reviewed'
        else:
            alert_st = 'Not reviewed'

        self.selected_alert_info.children = [
            v.Html(tag="strong", children=["First Date: "]),
            fecha1,
            v.Html(tag="br"),
            v.Html(tag="strong", children=[" Last Date: "]),
            fecha2,
            v.Html(tag="br"),
            v.Html(tag="strong", children=["Status: "]),
            alert_st,
      ]
        self.map31.children[1].children[5].set_loading_state(True)
        self.map32.children[1].children[5].set_loading_state(True)
        
        # Cambio en boton de confirmacion
        status_dict_reverse = {
            "Confirmed": "Yes",
            "False Positive": "No",
            "Maybe":"Need further revision",
        }
        if alerta["status"] != 'Not reviewed':
            self.boton_confirmacion.v_model = status_dict_reverse[alerta["status"]]
            self.comments_input.v_model = parse_formatted_string(alerta["description"])
        
        # Clean previous draw elements
        self.draw_alerts1.hide()
        self.draw_alerts2.hide()
        
        # Zoom to alert bb
        alerta_bb_geojson = alerta["bounding_box"].__geo_interface__
        self.map_31.center = (alerta.point.y, alerta.point.x)
        self.map_31.zoom = 16

        #Reset del mapa
        self.map_31.remove_all()
        self.map_32.remove_all()
        
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

        #Agregar defo layer si ya fue revisado
        if alerta["status"] in {"Confirmed", "maybe"} and alerta["alert_polygon"] is not None:
            self.analyzed_alerts_model.defo_dl_layer = multipolygon_to_geodataframe(alerta["alert_polygon"]).set_crs(epsg="4326", allow_override=True, inplace=True)

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
        sentinel2_mosaics_dates = get_sentinel2_dates(fecha1, fecha2)
        self.start_s2_dictionary_thread(gridDescargaBounds, sentinel2_mosaics_dates[0], sentinel2_mosaics_dates[1], self.assign_s2_before_dictionary)
        self.start_s2_dictionary_thread(gridDescargaBounds, sentinel2_mosaics_dates[2], sentinel2_mosaics_dates[3], self.assign_s2_after_dictionary)

    ###### DL MODEL SECTION #######

    def worker_m1(self):
        import os
        os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
        import rasterio
        import numpy as np
        import tensorflow as tf
        from tensorflow.keras import layers, models
        from tensorflow.keras import backend as K
        from MightyMosaic import MightyMosaic

        from component.scripts.model_worker import apply_dl_model, save_prediction_prob

        """Worker thread that processes files from the file_queue."""
        while True:
            input_list = self.file_queue1.get()  # Get a file path from the queue
            file_path = input_list[0]
            model_option = input_list[1]
            file_suffix = input_list[2]

            
            if file_path == "exit":  # Stop the thread if "exit" command is received
                break
            # Process the file and put the result in the result_queue
            prediction_name = (
            self.app_tile_model.recipe_folder_path
            + "/alert_"
            + str(self.analyzed_alerts_model.actual_alert_id)
            + "_prediction"+ file_suffix +".tif"
        )
            processed_file = apply_dl_model(file_path, model_option, file_suffix)
            self.result_queue1.put(processed_file)
            self.file_queue1.task_done()
            
    def worker_m2(self):
        import os
        os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
        import rasterio
        import numpy as np
        import tensorflow as tf
        from tensorflow.keras import layers, models
        from tensorflow.keras import backend as K
        from MightyMosaic import MightyMosaic

        from component.scripts.model_worker import apply_dl_model, save_prediction_prob

        """Worker thread that processes files from the file_queue."""
        while True:
            input_list = self.file_queue2.get()  # Get a file path from the queue
            file_path = input_list[0]
            model_option = input_list[1]
            file_suffix = input_list[2]

            
            if file_path == "exit":  # Stop the thread if "exit" command is received
                break
            # Process the file and put the result in the result_queue
            prediction_name = (
            self.app_tile_model.recipe_folder_path
            + "/alert_"
            + str(self.analyzed_alerts_model.actual_alert_id)
            + "_prediction"+ file_suffix +".tif"
        )
            processed_file = apply_dl_model(file_path, model_option,file_suffix)
            self.result_queue2.put(processed_file)
            self.file_queue2.task_done()
    
    def process_file1(self, file_path):
        # Put the file path in the queue for the worker to process
        self.file_queue1.put(file_path)

        # Wait for the worker to process the file and return the result
        processed_file = self.result_queue1.get()  # Blocking wait in worker thread
        self.result_queue1.task_done()

        # Call the callback function with the result
        print('callback1 called')
        self.analyzed_alerts_model.model1_prediction_file = processed_file
        
    def process_file2(self, file_path):
        # Put the file path in the queue for the worker to process
        self.file_queue2.put(file_path)

        # Wait for the worker to process the file and return the result
        processed_file = self.result_queue2.get()  # Blocking wait in worker thread
        self.result_queue2.task_done()

        # Call the callback function with the result
        print('callback2 called')
        self.analyzed_alerts_model.model2_prediction_file = processed_file


    def send_file_for_processing_m1_v2(self, file_path, function):
        """
        Function to send a file path to the worker and process the result asynchronously.
        
        Args:
            file_path: The file path to be sent to the worker.
            function: A function to call with the processed file once available.
        """
        print('model1 started')
        # Start a new thread to handle file processing
        processing_thread1 = threading.Thread(target=function(file_path), daemon=True)
        processing_thread1.start()

    def send_file_for_processing_m2_v2(self, file_path, function):
        """
        Function to send a file path to the worker and process the result asynchronously.
        
        Args:
            file_path: The file path to be sent to the worker.
            function: A function to call with the processed file once available.
        """
        print('model2 started')
        # Start a new thread to handle file processing
        processing_thread2 = threading.Thread(target=function(file_path), daemon=True)
        processing_thread2.start()

    ##Edition creation functions
    
    def start_edition_function(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks
        self.draw_alerts1.show()
        self.draw_alerts2.show()
        if self.analyzed_alerts_model.defo_dl_layer is not None:
           self.draw_alerts2.data = convertir_formato2(self.analyzed_alerts_model.defo_dl_layer.__geo_interface__["features"], 'blue')
        self.clear_button.disabled = False
        self.save_edit_button.disabled = False
        self.stop_edit_button.disabled = False
        self.toolBarDL1.show()
        self.toolBarDL2.show()
        widget.loading = False  # Remove loading state

    def save_edition_function(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks

        ##function to save draw items to gdf
        geometries = self.draw_alerts1.to_json()['features']
        if len(geometries) > 0: 
            self.analyzed_alerts_model.defo_dl_layer = geojson_to_geodataframe(self.draw_alerts1.to_json()).set_crs(epsg="4326", allow_override=True, inplace=True)
        
        widget.loading = False  # Remove loading state
        widget.disabled = False  # Re-enable the button


    def stop_edition_function(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks

        self.draw_alerts1.hide()
        self.draw_alerts2.hide()
        
        self.toolBarDL1.hide()
        self.toolBarDL2.hide()
        
        self.dl_button1_add.disabled = True
        self.dl_button1_remove.disabled = True
        self.dl_button2_add.disabled = True
        self.dl_button2_remove.disabled = True

        self.save_edit_button.disabled = True
        self.clear_button.disabled = True
        self.start_edit_button.disabled = False
        widget.loading = False  # Remove loading state
        
    def clear_edition_function(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks
        self.draw_alerts1.clear()
        self.draw_alerts2.clear()
        #self.draw_alerts1.data = filter_features_by_color(self.draw_alerts1.data,'blue')
        widget.loading = False  # Remove loading state
        widget.disabled = False  # Re-enable the button

    def enable_dl1(self, change):
        self.dl_button1_add.disabled = False

    def enable_dl2(self, change):
        self.dl_button2_add.disabled = False

    def add_model1_prediction(self, widget, event, data):
        defo_gdf_layer = raster_to_gdf(self.analyzed_alerts_model.model1_prediction_file, "4326", 0.20)
        edit_layer = simplify_and_extract_features(defo_gdf_layer, "geometry", 15)
        orig_features = self.draw_alerts1.data
        test_features = convertir_formato2(edit_layer, 'green')
        self.draw_alerts1.clear()
        self.draw_alerts2.clear()
        self.draw_alerts1.data = orig_features + test_features
        #self.draw_alerts2.data = orig_features + test_features
        self.dl_button1_add.disabled = True
        self.dl_button1_remove.disabled = False

        
    def add_model2_prediction(self, widget, event, data):
        defo_gdf_layer = raster_to_gdf(self.analyzed_alerts_model.model2_prediction_file, "4326", 0.20)
        edit_layer = simplify_and_extract_features(defo_gdf_layer, "geometry", 15)
        orig_features = self.draw_alerts1.data
        test_features = convertir_formato2(edit_layer, 'purple')
        self.draw_alerts1.clear()
        self.draw_alerts2.clear()
        self.draw_alerts1.data = orig_features + test_features
        #self.draw_alerts2.data = orig_features + test_features
        self.dl_button2_add.disabled = True
        self.dl_button2_remove.disabled = False

        
    def remove_model1_prediction(self, widget, event, data):
        orig_features = self.draw_alerts1.data
        self.draw_alerts1.clear()
        self.draw_alerts2.clear()
        self.draw_alerts1.data = filter_features_by_color(orig_features,'green')
        #self.draw_alerts2.data = filter_features_by_color(orig_features,'green')
        self.dl_button1_add.disabled = False
        self.dl_button1_remove.disabled = True

        
    def remove_model2_prediction(self, widget, event, data):
        orig_features = self.draw_alerts1.data
        self.draw_alerts1.clear()
        self.draw_alerts2.clear()
        self.draw_alerts1.data = filter_features_by_color(orig_features,'purple')
        #self.draw_alerts2.data = filter_features_by_color(orig_features,'purple')
        self.dl_button2_add.disabled = False
        self.dl_button2_remove.disabled = True
        
    def add_defo_layer(self, change):
        if self.analyzed_alerts_model.defo_dl_layer is not None:
            geo_json_layer = GeoData(geo_dataframe=self.analyzed_alerts_model.defo_dl_layer, name="Defo Layer", style = {'color':'orange', "fillOpacity": "0"})
            self.map_31.add_layer(geo_json_layer)
            self.map_32.add_layer(geo_json_layer)

    def run_dl_model_1(self, widget, event, data):
        # widget.loading = True  # Set button to loading state
        # widget.disabled = True  # Disable button to prevent further clicks
        widget.indeterminate_state(True)
        self.dl_button1_add.disabled = True
        self.dl_button1_remove.disabled = True
        
        image_name = (
            self.app_tile_model.recipe_folder_path
            + "/alert_"
            + str(self.analyzed_alerts_model.actual_alert_id)
            + self.selected_img_before_info_list[4]
            + self.selected_img_after_info_list[4]
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
        model = "utils/Model1.h5"
        self.send_file_for_processing_m1_v2([image_name, model,'_m1'], self.process_file1)


    def run_dl_model_2(self, widget, event, data):
        # widget.loading = True  # Set button to loading state
        # widget.disabled = True  # Disable button to prevent further clicks
        widget.indeterminate_state(True)
        self.dl_button2_add.disabled = True
        self.dl_button2_remove.disabled = True
        
        image_name = (
            self.app_tile_model.recipe_folder_path
            + "/alert_"
            + str(self.analyzed_alerts_model.actual_alert_id)
            + self.selected_img_before_info_list[4]
            + self.selected_img_after_info_list[4]
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
        model = "utils/Model2.keras"
        self.send_file_for_processing_m2_v2([image_name, model,'_m2'], self.process_file2)
     
   
  ##Process to save results

    def save_attributes_to_gdf(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks

        self.download_alert_data_btn.disabled = False
        
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

        #Save alert driver/description
        drivers_list = ensure_list(self.comments_input.v_model)
        alertas_gdf.at[actual_alert_id, "description"] = format_list(drivers_list)

        #Save before and after img name
        alertas_gdf.at[
            actual_alert_id, "before_img"
        ] = self.selected_img_before_info_list[3]
        alertas_gdf.at[
            actual_alert_id, "after_img"
        ] = self.selected_img_after_info_list[3]

        ##Create dictionary of alert sources      
        alertas_gdf.at[
            actual_alert_id, "alert_sources"
        ] = format_list(self.selected_alerts_model.selected_alert_sources)
        
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
        alertas_gdf.at[actual_alert_id, "admin3"] = st3.getInfo()[:-1]
        
        if self.boton_confirmacion.v_model != "Yes":
            alertas_gdf.at[actual_alert_id, "alert_polygon"] = None
            alertas_gdf.at[actual_alert_id, "area_ha"] = 0
        elif self.boton_confirmacion.v_model == "Yes":
            alertas_gdf.at[actual_alert_id, "alert_polygon"] = self.analyzed_alerts_model.defo_dl_layer["geometry"].union_all()
            alertas_gdf.at[actual_alert_id, "area_ha"] = calculate_total_area(self.analyzed_alerts_model.defo_dl_layer)           

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
            + self.selected_img_before_info_list[4]
            + self.selected_img_after_info_list[4]
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

        add_files_to_zip(zipfile_name, image_name, gpkg_name)
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

        self.toolBarDownloads.show()

        widget.loading = False  # Remove loading state
        widget.disabled = False  # Re-enable the button
        # self.analyzed_alerts_model.actual_alert_id = self.analyzed_alerts_model.actual_alert_id + 1
     

    ## Create Sentinel 2 dictionary
    
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
        self.create_horizontal_slide_group_s2(
            self.analyzed_alerts_model.after_s2_images,
            self.map32,
            1,
            self.image_slider_map_callback,
            1,
            self.image_slider_info_callback,
            1,
            #False,
        )
