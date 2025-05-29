from sepal_ui import color, sepalwidgets as sw
from sepal_ui import aoi
from sepal_ui.mapping import SepalMap
from sepal_ui.mapping.draw_control import DrawControl
from sepal_ui.mapping.layers_control import LayersControl
from sepal_ui.mapping.inspector_control import InspectorControl
from sepal_ui.mapping.aoi_control import AoiControl
from sepal_ui.scripts import utils as su
from traitlets import link, observe
import ipyvuetify as v
from IPython.display import display, HTML


from component.message import cm
from component.scripts.alert_filter_helper import convert_to_geopandas
from component.scripts.mosaics_helper import *
from component.scripts.report_builder import *
from component.scripts.recipe_helper import update_saved_dictionary
from component.widget.custom_sw import (
    CustomDrawControl,
    CustomSlideGroup,
    CustomBtnWithLoader,
    decorator_loading_v2
)

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
import ast
from pathlib import Path

su.init_ee()


class SepalCard(sw.SepalWidget, v.Card):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

class EditionButtons(v.Btn, sw.SepalWidget,):
    def __init__(self, **kwargs):
        kwargs.setdefault("small", True)
        kwargs.setdefault("class_", "pa-1 ma-1")
        kwargs.setdefault("disabled", True)
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

        self.dl_button1 = CustomBtnWithLoader(
            class_="pa-1 ma-1",
            color=color.secondary,
            rounded=True,
            small=True,
            text="M 1",
        )
        self.dl_button2 = CustomBtnWithLoader(
            class_="pa-1 ma-1",
            color=color.secondary,
            rounded=True,
            small=True,
            text="M 2",
        )
        self.alert_draw_alert = sw.Alert().hide()
        self.run_dl_model_1 = decorator_loading_v2(
            alert=self.alert_draw_alert, button=self.dl_button1
        )(self.run_dl_model_1)
        self.run_dl_model_2 = decorator_loading_v2(
            alert=self.alert_draw_alert, button=self.dl_button2
        )(self.run_dl_model_2)
        self.m1_btn_state = True
        self.m2_btn_state = True

        self.initialize_layout()

        ## Observe changes in selected_alerts_model and update tile when it changes
        self.selected_alerts_model.observe(self.update_gdf_partial, "alerts_bbs")
        self.selected_alerts_model.observe(self.update_gdf_full, "alerts_total_bbs")
        self.analyzed_alerts_model.observe(self.view_actual_alert, "actual_alert_id")
        self.analyzed_alerts_model.observe(
            self.slider_s2_before, "before_s2_images_time"
        )
        self.analyzed_alerts_model.observe(self.slider_s2_after, "after_s2_images_time")
        self.analyzed_alerts_model.observe(
            self.slider_landsat_before, "before_landsat_images_time"
        )
        self.analyzed_alerts_model.observe(
            self.slider_landsat_after, "after_landsat_images_time"
        )
        self.analyzed_alerts_model.observe(self.add_defo_layer, "defo_dl_layer")
        self.analyzed_alerts_model.observe(self.verify_model1_output, "model1_prediction_time")
        self.analyzed_alerts_model.observe(self.verify_model2_output, "model2_prediction_time")

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
        result_checker1_thread = threading.Thread(target=self.check_results1, daemon=True)
        result_checker1_thread.start()
        result_checker2_thread = threading.Thread(target=self.check_results2, daemon=True)
        result_checker2_thread.start()
        
        super().__init__()

    def initialize_layout(self):
        display(
            HTML(
                """
        <style>
            .custom-map-class2 {
                height: 55vh !important;
                }
             .v-text-field .v-input__control .v-input__slot {
                min-height: auto !important;
                min-width: 40px;
                display: flex !important;
                align-items: center !important;
              }
        </style>
        """
            )
        )
        # Create Variables
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

        #Maps title
        map1_title = v.CardTitle(
                    class_="pa-1 ma-1",
                    children=[cm.analysis_tile.img_selection.prev_image_title],
                )
        map2_title = v.CardTitle(
                    class_="pa-1 ma-1",
                    children=[cm.analysis_tile.img_selection.post_image_title],
                )       
               
        selected_img_before_info = v.Html(
            tag="div", class_="pa-1 ma-2", children=[cm.analysis_tile.img_selection.selected_img_info]
        )

        selected_img_after_info = v.Html(
            tag="div", class_="pa-1 ma-2", children=[cm.analysis_tile.img_selection.selected_img_info]
        )

        imgInfo1 = v.Card(
            children=[
                selected_img_before_info,
            ]
        )
        
        imgInfo2 = v.Card(
            children=[
                selected_img_after_info,
            ]
        )

        # imgSelection1= v.Card(children=[])

        # Create the Tab headers
        tabs = v.Tabs(
            v_model=0,
            children=[
                v.Tab(children=["Planet"]),
                v.Tab(children=["Sentinel-2"]),
                v.Tab(children=["Landsat"]),
            ],
            background_color=color.main,
            dark=True,
        )

        # Define the content for each tab
        tab_items = v.TabsItems(
            v_model=0,
            children=[
                v.TabItem(
                    children=[
                        SepalCard(
                            children=[
                                v.CardTitle(children=["Planet"]),
                                v.CardText(
                                    children=[
                                        cm.analysis_tile.img_selection.no_images_msg,
                                    ]
                                ),
                            ],
                            class_="ma-0",
                        ),
                        SepalCard(
                            children=[
                                v.CardTitle(children=["Planet"]),
                                CustomSlideGroup(),
                            ]
                        ).hide(),
                    ]
                ),
                v.TabItem(
                    children=[
                        SepalCard(
                            children=[
                                v.CardTitle(children=["Sentinel-2"]),
                                v.CardText(
                                    children=[
                                        cm.analysis_tile.img_selection.no_images_msg,
                                    ]
                                ),
                            ],
                            class_="ma-0",
                        ),
                        SepalCard(
                            children=[
                                v.CardTitle(children=["Sentinel-2"]),
                                CustomSlideGroup(),
                            ]
                        ).hide(),
                    ]
                ),
                v.TabItem(
                    children=[
                        SepalCard(
                            children=[
                                v.CardTitle(children=["Landsat"]),
                                v.CardText(
                                    children=[
                                        cm.analysis_tile.img_selection.no_images_msg,
                                    ]
                                ),
                            ],
                            class_="ma-0",
                        ),
                        SepalCard(
                            children=[
                                v.CardTitle(children=["Landsat"]),
                                CustomSlideGroup(),
                            ]
                        ).hide(),
                    ]
                ),
            ],
        )
        # Link the tab headers with their respective content
        link((tabs, "v_model"), (tab_items, "v_model"))


        # Create the Tab headers
        tabs2 = v.Tabs(
            v_model=0,
            children=[
                v.Tab(children=["Planet"]),
                v.Tab(children=["Sentinel-2"]),
                v.Tab(children=["Landsat"]),
            ],
            background_color=color.main,
            dark=True,
        )

        # Define the content for each tab
        tab_items2 = v.TabsItems(
            v_model=0,
            children=[
                v.TabItem(
                    children=[
                        SepalCard(
                            children=[
                                v.CardTitle(children=["Planet"]),
                                v.CardText(
                                    children=[
                                        cm.analysis_tile.img_selection.no_images_msg,
                                    ]
                                ),
                            ],
                            class_="ma-0",
                        ),
                        SepalCard(
                            children=[
                                v.CardTitle(children=["Planet"]),
                                CustomSlideGroup(),
                            ]
                        ).hide(),
                    ]
                ),
                v.TabItem(
                    children=[
                        SepalCard(
                            children=[
                                v.CardTitle(children=["Sentinel-2"]),
                                v.CardText(
                                    children=[
                                        cm.analysis_tile.img_selection.no_images_msg,
                                    ]
                                ),
                            ],
                            class_="ma-0",
                        ),
                        SepalCard(
                            children=[
                                v.CardTitle(children=["Sentinel-2"]),
                                CustomSlideGroup(),
                            ]
                        ).hide(),
                    ]
                ),
                v.TabItem(
                    children=[
                        SepalCard(
                            children=[
                                v.CardTitle(children=["Landsat"]),
                                v.CardText(
                                    children=[
                                        cm.analysis_tile.img_selection.no_images_msg,
                                    ]
                                ),
                            ],
                            class_="ma-0",
                        ),
                        SepalCard(
                            children=[
                                v.CardTitle(children=["Landsat"]),
                                CustomSlideGroup(),
                            ]
                        ).hide(),
                    ]
                ),
            ],
        )
        # Link the tab headers with their respective content
        link((tabs2, "v_model"), (tab_items2, "v_model"))
        
        # Arrange and display the Tab component
        self.imgSelection1 = v.Card(class_="pa-1 ma-1", flat = True, children=[tabs, tab_items])
        
        # Arrange and display the Tab component
        self.imgSelection2 = v.Card(class_="pa-1 ma-1", flat = True, children=[tabs2, tab_items2])

        # Create map with buttons1
        self.map31 = v.Container(class_="pa-1 ma-1", children=[map1_title, self.map_31, imgInfo1])

        # Create map with buttons2
        self.map32 = v.Container(class_="pa-1 ma-1", children=[map2_title, self.map_32, imgInfo2])

        # Create drawer control and add to maps
        self.draw_alerts1 = DrawControl(self.map_31)
        self.draw_alerts2 = DrawControl(self.map_32)

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
            hide_details = True,
            # class_="ma-0 pa-n6",
            # style_ = "max-width:50px",
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
                v.Html(tag="strong", children=[cm.analysis_tile.alert_info.first_date]),
                "",
                v.Html(tag="strong", children=[cm.analysis_tile.alert_info.last_date]),
                "",
                v.Html(tag="strong", children=[cm.analysis_tile.alert_info.status]),
                "",
            ],
        )

        self.boton_confirmacion = sw.Select(
            items=[
                cm.analysis_tile.questionarie.confirmation_yes,
                cm.analysis_tile.questionarie.confirmation_no,
                cm.analysis_tile.questionarie.confirmation_revision,
            ],
            v_model=cm.analysis_tile.questionarie.confirmation_yes,
            # label="Is this a true alert?",
            small=True,
            multiple=False,
            clearable=True,
            chips=True,
        )

        label33 = v.CardTitle(
            class_="pa-1 ma-1", children=[cm.analysis_tile.edition_labels.edit_title]
        )
        self.start_edit_button = EditionButtons(
            color=color.primary,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-pen"])],
            disabled=False,
        )
        self.clear_button =EditionButtons(
            color=color.error,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-trash"])],
        )
        self.save_edit_button = EditionButtons(
            color=color.primary,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-floppy-disk"])],
        )
        self.stop_edit_button = EditionButtons(
            color=color.secondary,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-x"])],
        )

        self.start_edit_button.on_event("click", self.start_edition_function)
        self.clear_button.on_event("click", self.clear_edition_function)
        self.save_edit_button.on_event("click", self.save_edition_function)
        self.stop_edit_button.on_event("click", self.stop_edition_function)

        tooltip1 = sw.Tooltip(
            self.start_edit_button,
            tooltip=cm.analysis_tile.edition_labels.start_edit_button,
            top=True,
            open_delay=100,
            close_delay=100,
        )
        tooltip2 = sw.Tooltip(
            self.clear_button,
            tooltip=cm.analysis_tile.edition_labels.clear_button,
            top=True,
            open_delay=100,
            close_delay=100,
        )
        tooltip3 = sw.Tooltip(
            self.save_edit_button,
            tooltip=cm.analysis_tile.edition_labels.save_edit_button,
            top=True,
            open_delay=100,
            close_delay=100,
        )
        tooltip4 = sw.Tooltip(
            self.stop_edit_button,
            tooltip=cm.analysis_tile.edition_labels.stop_edit_button,
            top=True,
            open_delay=100,
            close_delay=100,
        )

        self.toolBarEdition = v.Toolbar(
            class_="px-1 d-flex justify-space-around", children=[tooltip1, tooltip3, tooltip4]
        )

        # self.dl_button1 = v.Btn(class_='pa-1 ma-1', color = color.secondary, rounded=True, small=True, children=['DL 1'])
        self.dl_button1_add = EditionButtons(
            color=color.primary,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-plus"])],
        )
        self.dl_button1_remove = EditionButtons(
            color=color.error,
            children=[
                v.Icon(
                    color=color.bg,
                    children=[
                        "fa-solid fa-minus",
                    ],
                )
            ],
        )
        self.dl_button1_msg = EditionButtons(
            color=color.error,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-triangle-exclamation"])],
            disabled = False,
        ).hide()
        self.dl_button1.on_event("click", self.run_dl_model_1)
        self.dl_button1_add.on_event("click", self.add_model1_prediction)
        self.dl_button1_remove.on_event("click", self.remove_model1_prediction)

        tooltip5 = sw.Tooltip(
            self.dl_button1,
            tooltip=cm.analysis_tile.model_labels.dl_button1,
            top=True,
            open_delay=100,
            close_delay=100,
        )
        tooltip6 = sw.Tooltip(
            self.dl_button1_add,
            tooltip=cm.analysis_tile.model_labels.dl_button_add,
            top=True,
            open_delay=100,
            close_delay=100,
        )
        tooltip7 = sw.Tooltip(
            self.dl_button1_remove,
            tooltip=cm.analysis_tile.model_labels.dl_button_remove,
            top=True,
            open_delay=100,
            close_delay=100,
        )
        tooltip11 = sw.Tooltip(
            self.dl_button1_msg,
            tooltip=cm.analysis_tile.model_labels.dl_404,
            top=True,
            open_delay=100,
            close_delay=100,
        )
        self.toolBarDL1 = sw.Toolbar(children=[tooltip5, tooltip6, tooltip7, tooltip11]).hide()

        # self.dl_button2 = v.Btn(class_='pa-1 ma-1', color = color.secondary, rounded=True, small=True, children=['DL 2'])
        self.dl_button2_add =EditionButtons(
            color=color.primary,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-plus"])],
        )
        self.dl_button2_remove = EditionButtons(
            color=color.error,
            children=[
                v.Icon(
                    color=color.bg,
                    children=[
                        "fa-solid fa-minus",
                    ],
                )
            ],
        )
        self.dl_button2_msg = EditionButtons(
            color=color.error,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-triangle-exclamation"])],
            disabled = False,
        ).hide()
        self.dl_button2.on_event("click", self.run_dl_model_2)
        self.dl_button2_add.on_event("click", self.add_model2_prediction)
        self.dl_button2_remove.on_event("click", self.remove_model2_prediction)

        tooltip8 = sw.Tooltip(
            self.dl_button2,
            tooltip=cm.analysis_tile.model_labels.dl_button2,
            top=True,
            open_delay=100,
            close_delay=100,
        )
        tooltip9 = sw.Tooltip(
            self.dl_button2_add,
            tooltip=cm.analysis_tile.model_labels.dl_button_add,
            top=True,
            open_delay=100,
            close_delay=100,
        )
        tooltip10 = sw.Tooltip(
            self.dl_button2_remove,
            tooltip=cm.analysis_tile.model_labels.dl_button_remove,
            top=True,
            open_delay=100,
            close_delay=100,
        )
        tooltip12 = sw.Tooltip(
            self.dl_button2_msg,
            tooltip=cm.analysis_tile.model_labels.dl_404,
            top=True,
            open_delay=100,
            close_delay=100,
        )
        self.toolBarDL2 = sw.Toolbar(children=[tooltip8, tooltip9, tooltip10, tooltip12]).hide()


        label34 = v.CardTitle(
            class_="pa-1 ma-1",
            children=[cm.analysis_tile.questionarie.loss_driver_label],
        )
        defo_drivers = [
            cm.analysis_tile.questionarie.loss_driver1,
            cm.analysis_tile.questionarie.loss_driver2,
            cm.analysis_tile.questionarie.loss_driver3,
            cm.analysis_tile.questionarie.loss_driver4,
            cm.analysis_tile.questionarie.loss_driver5,
            cm.analysis_tile.questionarie.loss_driver6,
            cm.analysis_tile.questionarie.loss_driver7,
            cm.analysis_tile.questionarie.loss_driver8,
            cm.analysis_tile.questionarie.loss_driver9,
        ]
        self.comments_input = sw.Combobox(
            items=defo_drivers,
            v_model=[cm.analysis_tile.questionarie.loss_driver9],
            label=cm.analysis_tile.questionarie.loss_driver_hint,
            multiple=True,
            clearable=True,
            chips=True,
        )

        label35 = v.CardTitle(
            class_="pa-1 ma-1", children=[cm.analysis_tile.export_labels.export_title]
        )
        self.save_btn = sw.Btn(
            msg = cm.analysis_tile.export_labels.save_btn,
            color="primary",
            outlined=True,
            class_="pa-1 ma-1",
        )
        self.save_btn.on_event("click", self.save_attributes_to_gdf)
        self.download_alert_data_btn = sw.Btn(
            msg = cm.analysis_tile.export_labels.download_alert_data_btn,
            color="primary",
            outlined=True,
            disabled=True,
            class_="pa-1 ma-1",
        )
        self.download_alert_data_btn.on_event("click", self.download_data)
        self.files_dwn_btn = sw.DownloadBtn(
            class_="pa-1 ma-1",
            text=cm.analysis_tile.export_labels.files_dwn_btn)
        self.report_dwn_btn = sw.DownloadBtn(
            class_="pa-1 ma-1",
            text=cm.analysis_tile.export_labels.report_dwn_btn)

        self.toolBarSaveExport = sw.Toolbar(
            class_="pa-1 ma-1 d-flex justify-space-between", children=[self.save_btn, self.download_alert_data_btn]
        )
        self.toolBarDownloads = sw.Toolbar(
            class_="pa-1 ma-1 d-flex justify-space-between", children=[self.files_dwn_btn, self.report_dwn_btn]
        ).hide()

        # Layout

        card01 = v.Card(
            class_="pa-2 ma-2 d-flex justify-center",
            hover=True,
            children=[
                self.prev_button,
                self.next_button,
                self.alert_id_button,
                self.go_to_alert_button,
            ],
        )
        card02 = v.Card(
            class_="pa-2 ma-2",
            hover=True,
            children=[
                v.CardTitle(
                    class_="pa-0 ma-1",
                    children=[cm.analysis_tile.alert_info.card_title],
                ),
                self.selected_alert_info,
            ],
        )

        card03 = v.Card(
            class_="pa-2 ma-2",
            hover=True,
            children=[
                v.CardTitle(
                    class_="pa-0 ma-1",
                    children=[cm.analysis_tile.questionarie.confirmation_q],
                ),
                self.boton_confirmacion,
            ],
        )

        card35 = v.Card(
            class_="pa-2 ma-2",
            hover=True,
            children=[
                v.CardTitle(
                    class_="pa-0 ma-1",
                    children=[cm.analysis_tile.questionarie.confirmation_q],
                ),
                self.boton_confirmacion,
                label34,
                self.comments_input,
            ],
        )

        card04 = v.Card(
            class_="pa-2 ma-2",
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
            class_="pa-2 ma-2",
            hover=True,
            children=[label34, self.comments_input],
        )

        card06 = v.Card(
            class_="pa-2 ma-2",
            hover=True,
            children=[label35, self.toolBarSaveExport, self.toolBarDownloads],
        )

       
        # build the left panel as a row of two equal‐width flex‐items
        left_panel = v.Flex(
            # two children, each taking exactly half of the left space
            children=[
                v.Flex(
                    children=[self.map31, self.imgSelection1],
                    style_="width:50%;",

                ),
                v.Flex(
                    children=[self.map32, self.imgSelection2],
                    style_="width:50%;",
                ),
            ],
            # this makes the left_panel itself expand to fill remaining space
            style_="flex: 1 1 auto; display: flex; overflow: hidden;",
            #style_="flex: 1 1 auto; overflow: hidden;",

        )
        
        # right panel fixed at 16rem
        right_panel = v.Flex(
            children=[card02, card01, card35, card04, card06],
            style_="flex: 0 0 16rem;",
            class_ = 'overflow-y-auto'
        )
        
        self.children = [left_panel, right_panel]
    # Saving alerts to gdf functions

    def save_alerts_to_gdf(self):
        alertas_gdf = self.analyzed_alerts_model.alerts_gdf

        # Set the geometry column if necessary (optional, if it is not already set)
        alertas_gdf.set_geometry("bounding_box", inplace=True)
        alert_db_name = self.app_tile_model.recipe_folder_path + "/alert_db.csv"
        # Export to GPKG (GeoPackage)
        alertas_gdf.set_crs(epsg="4326", allow_override=True, inplace=True).to_csv(
            alert_db_name, index=False
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
            print("nothing arrived yet")
            self.analyzed_alerts_model.alerts_gdf = None

        elif data is None and partial is not None:
            print("partial arrived first")
            alertas_gdf = convert_to_geopandas(partial)
            self.analyzed_alerts_model.alerts_gdf = alertas_gdf
            self.analyzed_alerts_model.actual_alert_id = 0
            self.analyzed_alerts_model.max_alert_id = len(alertas_gdf)
            recipe_dictionary_path = (
                self.app_tile_model.recipe_folder_path + "/recipe_parameters.json"
            )
            update_saved_dictionary(
                recipe_dictionary_path,
                "max_alert_id",
                self.analyzed_alerts_model.max_alert_id,
            )

        elif data is not None and partial is not None:
            print("partial arrived after")
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
            print("nothing arrived yet")
            self.analyzed_alerts_model.alerts_gdf = None

        elif data is None and complete is not None:
            print("complete arrived first")
            alertas_gdf = convert_to_geopandas(complete)
            self.analyzed_alerts_model.alerts_gdf = alertas_gdf
            self.analyzed_alerts_model.actual_alert_id = 0
            self.analyzed_alerts_model.max_alert_id = len(alertas_gdf)
            recipe_dictionary_path = (
                self.app_tile_model.recipe_folder_path + "/recipe_parameters.json"
            )
            update_saved_dictionary(
                recipe_dictionary_path,
                "max_alert_id",
                self.analyzed_alerts_model.max_alert_id,
            )
            self.save_alerts_to_gdf()

        elif data is not None and complete is not None:
            print("merging complete data to analyzed partial")
            total_alertas_gdf = convert_to_geopandas(complete)
            mask = (data["status"] != "Not reviewed") | (data.index == self.analyzed_alerts_model.actual_alert_id)         
            analyzed_temp_alerts = data[mask]
            #analyzed_temp_alerts = data[data["status"] != "Not reviewed"]
            unique_values = analyzed_temp_alerts["bounding_box"].unique()

            if len(unique_values) == 0:
                filtered_total = total_alertas_gdf
            else:
                filtered_total = total_alertas_gdf[
                    ~total_alertas_gdf["bounding_box"].isin(unique_values)
                ].reset_index(drop=True)

            combined_gdf = gpd.GeoDataFrame(
                pd.concat([analyzed_temp_alerts, filtered_total])
            ).reset_index(drop=True)
            self.analyzed_alerts_model.alerts_gdf = combined_gdf
            self.analyzed_alerts_model.max_alert_id = len(combined_gdf)
            recipe_dictionary_path = (
                self.app_tile_model.recipe_folder_path + "/recipe_parameters.json"
            )
            update_saved_dictionary(
                recipe_dictionary_path,
                "max_alert_id",
                self.analyzed_alerts_model.max_alert_id,
            )
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
        #print('number of current threads is ', threading.active_count())
        if widget.value == 0:
            self.analyzed_alerts_model.actual_alert_id = int(
                self.alert_id_button.v_model
            )
        else:
            max_value = self.analyzed_alerts_model.max_alert_id + 1
            self.analyzed_alerts_model.actual_alert_id = (
                self.analyzed_alerts_model.actual_alert_id + widget.value
            ) % max_value

        self.files_dwn_btn.set_url()
        self.report_dwn_btn.set_url()

        self.prev_button.disabled = False
        self.next_button.disabled = False
        self.alert_id_button.disabled = False
        self.go_to_alert_button.disabled = False
    ##Image sliders function

    def image_slider_map_callback(self, selected_item, map_element, model_att):
        self.imgSelection1.disabled = True
        self.imgSelection2.disabled = True
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
            s2 = (
                ee.ImageCollection("COPERNICUS/S2_HARMONIZED")
                .filterBounds(geom)
                .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 90))
            )
            s2_same_date = s2.filter(ee.Filter.eq("GENERATION_TIME", image_id))
            img = s2_same_date.mosaic().clip(geom)

        elif img_source == "Planet NICFI":
            vis1 = vis1p
            vis2 = vis2p
            img = ee.Image(image_id).clip(geom)

        elif img_source == "Landsat":
            vis1 = vis1s
            vis2 = vis2s

            # Filter Landsat SR
            l8 = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
            l9 = ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
            landsat = l8.merge(l9)
            landsat_filtered = (
                landsat.filterBounds(geom)
                .filter(ee.Filter.lt("CLOUD_COVER", 90))
                .filter(ee.Filter.eq("LANDSAT_SCENE_ID", image_id))
            ).first()

            # Filter Landsat TOA
            l8_toa = ee.ImageCollection("LANDSAT/LC08/C02/T1_TOA")
            l9_toa = ee.ImageCollection("LANDSAT/LC09/C02/T1_TOA")
            landsat_toa = l8_toa.merge(l9_toa)
            landsat_toa_filtered = (
                landsat_toa.filterBounds(geom)
                .filter(ee.Filter.lt("CLOUD_COVER", 90))
                .filter(ee.Filter.eq("LANDSAT_SCENE_ID", image_id))
            ).first()

             # Pansharpening
            landsat_sr_img = landsat_filtered.select(
                ["SR_B1", "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"]
            )
            landsat_toa_img = landsat_toa_filtered.select("B8")
            pan_sharp = SFIM_pan_sharpen(landsat_sr_img, landsat_toa_img)
            # Harmonize
            harmonized_img = harmonizeL8ToS2_scaled(pan_sharp).clip(geom)
            img = harmonized_img

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
        self.imgSelection1.disabled = False
        self.imgSelection2.disabled = False

    def image_slider_info_callback(self, selected_item, info_element, model_att):
        info_element.loading = True
        info1 = selected_item["source"]
        info2 = selected_item["milis"]
        info3 = selected_item["cloud_cover"]
        info4 = selected_item["value"]
        info5 = datetime.utcfromtimestamp(selected_item["milis"] / 1000).strftime(
            "_%d%m%y"
        )
        lista = [info1, info2, info3, info4, info5]
        if model_att == 0:
            self.selected_img_before_info_list = lista
        elif model_att == 1:
            self.selected_img_after_info_list = lista
        info_element.loading = False
        info_element.children = [
            v.Html(
                tag="strong",
                children=[cm.analysis_tile.img_selection.selected_img_info_source],
            ),
            info1,
            v.Html(
                tag="strong",
                children=[", " + cm.analysis_tile.img_selection.selected_img_info_date],
            ),
            datetime.utcfromtimestamp(info2 / 1000).strftime(
            "%Y-%m-%d"
            ),
            v.Html(
                tag="strong",
                children=[
                    ", " + cm.analysis_tile.img_selection.selected_img_info_cloud
                ],
            ),
            info3,
        ]

    def create_horizontal_slide_group_v3(
        self,
        data_list,
        map_component,
        image_slider_component,
        default_v_model,
        callback,
        model_att1,
        callback2,
        model_att2,
        fire_callback=False,  # Added default value for fire_callback
    ):
        """Creates a horizontal slide group component."""
        map_element = map_component.children[1]
        info_element = map_component.children[2].children[0]

        # Define the mappings based on source type
        source_to_index = {"Planet NICFI": 0, "Sentinel 2": 1, "Landsat": 2}

        # Determine which tab and child tabs to use based on the first item's source
        if data_list:
            source_type = data_list[0].get("source")
            if source_type in source_to_index:
                index = source_to_index[source_type]
                main_tab_index = index
                child_tabs_indices = [(index + i) % 3 for i in range(1, 3)]

                # Update the indices to cycle through tabs correctly
                main_tab = image_slider_component.children[1].children[main_tab_index]
                child_tabs = [
                    image_slider_component.children[1].children[i]
                    for i in child_tabs_indices
                ]
            else:
                raise ValueError(f"Unknown source type: {source_type}")
        else:
            raise ValueError("data_list is empty")
            
        slide_group = main_tab.children[1].children[1]
        child_slide1_group = child_tabs[0].children[1].children[1]
        child_slide2_group = child_tabs[1].children[1].children[1]

        if data_list[0].get("value") == "Not available":
            print(f'No images for {data_list[0].get("source")}')
            slide_group.set_loading_state(False)
            main_tab.children[1].hide()
            main_tab.children[0].show()
            return
        
        # Sort data by 'milis' attribute
        sorted_data = sorted(data_list, key=itemgetter("milis"), reverse=False)
        date_indices = {i: item for i, item in enumerate(sorted_data)}

        # Determine default v-model index
        default_v_model = (
            len(sorted_data) - 1 if default_v_model == 1 else len(sorted_data) - 3
        )

        color_map = {
            "Sentinel 2": "blue",
            "Planet NICFI": "green",
            "Landsat": "blue-grey",
            "selected": "orange",
        }

        slide_group.defaul_child_color = color_map.get(
            data_list[0].get("source"), "lightgray"
        )

        def create_and_configure_slide_button(i, item):
            """Creates and configures a slide button."""
            img_source = item["source"]
            button_color = color_map.get(img_source, "lightgray")

            if img_source == "Planet NICFI":
                date_string = datetime.utcfromtimestamp(item["milis"] / 1000).strftime(
                    "%b"
                )
            elif img_source != "Planet NICFI":
                date_string = datetime.utcfromtimestamp(item["milis"] / 1000).strftime(
                    "%b %d"
                )

            icon = ""  # Initialize with no icon
            if img_source != "Planet NICFI" and "cloud_cover" in item:
                cloud_cover = float(item["cloud_cover"])
                if 0 <= cloud_cover <= 30:
                    icon = "mdi-weather-sunny"
                elif 30 < cloud_cover <= 60:
                    icon = "mdi-cloud-outline"
                elif 60 < cloud_cover <= 100:
                    icon = "mdi-cloud"

            button = sw.Btn(
                text=date_string,
                gliph=icon,  # Use the icon if it's available
                color=button_color,
                class_="ma-1",
                value=i,
                style_="min-width: 40px; min-height: 40px;",
            )
            button.on_event("click", on_slide_button_click)
            return button

        def on_slide_button_click(widget, event, data):
            """Handles the click event for a slide button."""
            widget.loading = True
            widget.disabled = True

            selected_item = date_indices[widget.value]
            img_source = selected_item["source"]

            slide_group.reset_default_color()
            child_slide1_group.reset_default_color()
            child_slide2_group.reset_default_color()

            widget.color = color_map.get("selected")

            callback(selected_item, map_element, model_att1)
            callback2(selected_item, info_element, model_att2)

            widget.loading = False
            widget.disabled = False

        slides = [
            create_and_configure_slide_button(i, item)
            for i, item in enumerate(sorted_data)
        ]
        slide_group.slide_group.children = slides  # Assign directly to children

        slide_group.set_loading_state(False)
        main_tab.children[0].hide()
        main_tab.children[1].show()

        if fire_callback:
            slide_group.slide_group.children[default_v_model].color = color_map.get(
                "selected"
            )
            selected_item = date_indices[
                slide_group.slide_group.children[default_v_model].value
            ]
            callback(selected_item, map_element, model_att1)
            callback2(selected_item, info_element, model_att2)
            
    # Process for each alert
    def view_actual_alert(self, change):
        print("cambiando alerta", self.analyzed_alerts_model.actual_alert_id)

        recipe_dictionary_path = (
            self.app_tile_model.recipe_folder_path + "/recipe_parameters.json"
        )

        update_saved_dictionary(
            recipe_dictionary_path,
            "actual_alert_id",
            self.analyzed_alerts_model.actual_alert_id,
        )

        # Reset ui elements
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
        self.analyzed_alerts_model.defo_dl_layer = None
        # Select alert
        alerta = self.analyzed_alerts_model.alerts_gdf.iloc[
            self.analyzed_alerts_model.actual_alert_id
        ]
        # Obtener fechas de la alerta
        fecha1 = convert_julian_to_date(alerta["alert_date_min"])
        fecha2 = convert_julian_to_date(alerta["alert_date_max"])
        #alert_source = format_list(get_unique_alerts(alerta["alert_type_unique"]))
        alert_source = alerta["alert_sources"]
        
        # Cambio en boton de navegacion
        self.alert_id_button.v_model = self.analyzed_alerts_model.actual_alert_id

        # Cambio en alert info
        if alerta["status"] != "Not reviewed":
            alert_st = cm.analysis_tile.alert_info.status_reviewed
        else:
            alert_st = cm.analysis_tile.alert_info.status_not_reviewed

        self.selected_alert_info.children = [
            v.Html(tag="strong", children=[cm.analysis_tile.alert_info.alert_source]),
            alert_source,
            v.Html(tag="br"),
            v.Html(tag="strong", children=[cm.analysis_tile.alert_info.first_date]),
            fecha1,
            v.Html(tag="br"),
            v.Html(tag="strong", children=[cm.analysis_tile.alert_info.last_date]),
            fecha2,
            v.Html(tag="br"),
            v.Html(tag="strong", children=[cm.analysis_tile.alert_info.status]),
            alert_st,
        ]

        self.imgSelection1.children[1].children[0].children[1].children[
            1
        ].set_loading_state(True)
        self.imgSelection1.children[1].children[1].children[1].children[
            1
        ].set_loading_state(True)
        self.imgSelection1.children[1].children[2].children[1].children[
            1
        ].set_loading_state(True)
        self.imgSelection2.children[1].children[0].children[1].children[
            1
        ].set_loading_state(True)
        self.imgSelection2.children[1].children[1].children[1].children[
            1
        ].set_loading_state(True)
        self.imgSelection2.children[1].children[2].children[1].children[
            1
        ].set_loading_state(True)

        # Cambio en boton de confirmacion
        status_dict_reverse = {
            "Confirmed": cm.analysis_tile.questionarie.confirmation_yes,
            "False Positive": cm.analysis_tile.questionarie.confirmation_no,
            "Need revision": cm.analysis_tile.questionarie.confirmation_revision,
        }
        if alerta["status"] != "Not reviewed":
            self.boton_confirmacion.v_model = status_dict_reverse[alerta["status"]]
            self.comments_input.v_model = parse_formatted_string(alerta["description"])

        # Clean previous draw elements
        self.draw_alerts1.hide()
        self.draw_alerts2.hide()

        # Zoom to alert bb
        alerta_bb_geojson = alerta["bounding_box"].__geo_interface__
        self.map_31.center = (alerta.point.y, alerta.point.x)
        self.map_31.zoom = 16

        # Reset del mapa
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

        # Agregar defo layer si ya fue revisado
        if (
            alerta["status"] in {"Confirmed", "Need revision"}
            and alerta["alert_polygon"] is not None
        ):
            self.analyzed_alerts_model.defo_dl_layer = multipolygon_to_geodataframe(
                alerta["alert_polygon"]
            ).set_crs(epsg="4326", allow_override=True, inplace=True)

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

        # Definir que imagen cargar por defecto
        self.show_image_collection = [True, False, False]
        
        if self.analyzed_alerts_model.before_planet_monthly_images[0].get("value") == "Not available":
            self.imgSelection1.children[0].v_model = 1
            self.show_image_collection = [False, True, False]
        if self.analyzed_alerts_model.after_planet_monthly_images[0].get("value") == "Not available":
            self.imgSelection2.children[0].v_model = 1
            self.show_image_collection = [False, True, False]

        # Actualizar slider de imagenes
        self.create_horizontal_slide_group_v3(
            self.analyzed_alerts_model.before_planet_monthly_images,
            self.map31,
            self.imgSelection1,
            0,
            self.image_slider_map_callback,
            0,
            self.image_slider_info_callback,
            0,
            self.show_image_collection[0],
        )
        self.create_horizontal_slide_group_v3(
            self.analyzed_alerts_model.after_planet_monthly_images,
            self.map32,
            self.imgSelection2,
            1,
            self.image_slider_map_callback,
            1,
            self.image_slider_info_callback,
            1,
            self.show_image_collection[0],
        )

        # Obtener imagenes
        sentinel2_mosaics_dates = get_sentinel2_dates(fecha1, fecha2)
        self.start_s2_dictionary_thread(
            gridDescargaBounds,
            sentinel2_mosaics_dates[0],
            sentinel2_mosaics_dates[1],
            self.assign_s2_before_dictionary,
        )
        self.start_s2_dictionary_thread(
            gridDescargaBounds,
            sentinel2_mosaics_dates[2],
            sentinel2_mosaics_dates[3],
            self.assign_s2_after_dictionary,
        )

        self.start_landsat_dictionary_thread(
            gridDescargaBounds,
            sentinel2_mosaics_dates[0],
            sentinel2_mosaics_dates[1],
            self.assign_landsat_before_dictionary,
        )
        self.start_landsat_dictionary_thread(
            gridDescargaBounds,
            sentinel2_mosaics_dates[2],
            sentinel2_mosaics_dates[3],
            self.assign_landsat_after_dictionary,
        )

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
        from huggingface_hub import hf_hub_download

        hf_hub_download(
            repo_id="joseserafinig/forest_cover_change_cnn_m1",
            filename="Model1.h5",
            local_dir="/tmp",
        )

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
                + "_prediction"
                + file_suffix
                + ".tif"
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
        from huggingface_hub import hf_hub_download

        hf_hub_download(
            repo_id="joseserafinig/forest_cover_change_cnn_m2",
            filename="Model2.keras",
            local_dir="/tmp",
        )

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
                + "_prediction"
                + file_suffix
                + ".tif"
            )
            processed_file = apply_dl_model(file_path, model_option, file_suffix)
            self.result_queue2.put(processed_file)
            self.file_queue2.task_done()

    def check_results1(self):
        """Check result queue periodically (e.g., from a separate thread or event loop)."""
        while True:
            try:
                processed_file = self.result_queue1.get(timeout=2)
                print("prediction 1 completed")
                self.analyzed_alerts_model.model1_prediction_file = processed_file
                self.analyzed_alerts_model.model1_prediction_time = int(time.time() * 1000)
                self.dl_button1.toggle_loading() 
                self.result_queue1.task_done()
            except queue.Empty:
                continue  
                
    def check_results2(self):
        """Check result queue periodically (e.g., from a separate thread or event loop)."""
        while True:
            try:
                processed_file = self.result_queue2.get(timeout=2)
                print("prediction 2 completed")
                self.analyzed_alerts_model.model2_prediction_file = processed_file
                self.analyzed_alerts_model.model2_prediction_time = int(time.time() * 1000)
                self.dl_button2.toggle_loading() 
                self.result_queue2.task_done()
            except queue.Empty:
                continue   

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
        model = "/tmp/Model1.h5"
        # Send file to processing queue1
        self.file_queue1.put([image_name, model, "_m1"])


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
        model = "/tmp/Model2.keras"
        # Send file to processing queue2
        self.file_queue2.put([image_name, model, "_m2"])

    
    ##Edition creation functions

    def start_edition_function(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks
        self.map_31.find_layer("Alert BB").visible = False
        self.map_32.find_layer("Alert BB").visible = False

        if self.map_31.find_layer("Defo Layer", none_ok=True) is not None:
            self.map_31.find_layer("Defo Layer").visible = False
            self.map_32.find_layer("Defo Layer").visible = False

        self.draw_alerts1.show()
        self.draw_alerts2.show()
        if self.analyzed_alerts_model.defo_dl_layer is not None:
            self.draw_alerts2.data = convertir_formato3(
                self.analyzed_alerts_model.defo_dl_layer.__geo_interface__["features"],
                "blue",
            )
        self.clear_button.disabled = False
        self.save_edit_button.disabled = False
        self.stop_edit_button.disabled = False
        self.toolBarDL1.show()
        self.toolBarDL2.show()
        self.save_btn.disabled = True
        self.imgSelection1.disabled = True
        self.imgSelection2.disabled = True

        widget.loading = False  # Remove loading state

    def save_edition_function(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks

        ##function to save draw items to gdf
        geometries = self.draw_alerts1.to_json()["features"]
        if len(geometries) > 0:
            self.analyzed_alerts_model.defo_dl_layer = geojson_to_geodataframe(
                self.draw_alerts1.to_json()
            ).set_crs(epsg="4326", allow_override=True, inplace=True)

        widget.loading = False  # Remove loading state
        widget.disabled = False  # Re-enable the button

    def stop_edition_function(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks

        self.draw_alerts1.hide()
        self.draw_alerts2.hide()

        self.map_31.find_layer("Alert BB").visible = True
        self.map_32.find_layer("Alert BB").visible = True

        if self.map_31.find_layer("Defo Layer", none_ok=True) is not None:
            self.map_31.find_layer("Defo Layer").visible = True
            self.map_32.find_layer("Defo Layer").visible = True

        self.enable_dl1(True)
        self.enable_dl2(True)
        self.toolBarDL1.hide()
        self.toolBarDL2.hide()

        self.dl_button1_add.disabled = True
        self.dl_button1_remove.disabled = True
        self.dl_button2_add.disabled = True
        self.dl_button2_remove.disabled = True

        self.save_edit_button.disabled = True
        self.clear_button.disabled = True
        self.start_edit_button.disabled = False
        self.save_btn.disabled = False
        self.imgSelection1.disabled = False
        self.imgSelection2.disabled = False
        widget.loading = False  # Remove loading state

    def clear_edition_function(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks
        self.draw_alerts1.clear()
        self.draw_alerts2.clear()
        # self.draw_alerts1.data = filter_features_by_color(self.draw_alerts1.data,'blue')
        widget.loading = False  # Remove loading state
        widget.disabled = False  # Re-enable the button

    def verify_model1_output(self, change):
        verification1 = verify_raster(
            self.analyzed_alerts_model.model1_prediction_file, 0.20
        )
        self.enable_dl1(verification1)
 
    def verify_model2_output(self, change):
        verification2 = verify_raster(
            self.analyzed_alerts_model.model2_prediction_file, 0.20
        )
        self.enable_dl2(verification2)
    
    def enable_dl1(self, state):
        if state == True:
            self.dl_button1_msg.hide()
            self.dl_button1_add.show()
            self.dl_button1_remove.show()
            self.dl_button1_add.disabled = False

        else:
            self.dl_button1_msg.show()
            self.dl_button1_add.hide()
            self.dl_button1_remove.hide()
            
    def enable_dl2(self, state):
        if state == True:
            self.dl_button2_msg.hide()
            self.dl_button2_add.show()
            self.dl_button2_remove.show()
            self.dl_button2_add.disabled = False

        else:
            self.dl_button2_msg.show()
            self.dl_button2_add.hide()
            self.dl_button2_remove.hide()

    def add_model1_prediction(self, widget, event, data):
        defo_gdf_layer = raster_to_gdf(
            self.analyzed_alerts_model.model1_prediction_file, "4326", 0.20
        )
        edit_layer = simplify_and_extract_features(defo_gdf_layer, "geometry", 15)
        orig_features = self.draw_alerts1.data
        test_features = convertir_formato3(edit_layer, "lime")
        #print(defo_gdf_layer, edit_layer, test_features)
        self.draw_alerts1.clear()
        self.draw_alerts2.clear()
        self.draw_alerts1.data = orig_features + test_features
        # self.draw_alerts2.data = orig_features + test_features
        self.dl_button1_add.disabled = True
        self.dl_button1_remove.disabled = False

    def add_model2_prediction(self, widget, event, data):
        defo_gdf_layer = raster_to_gdf(
            self.analyzed_alerts_model.model2_prediction_file, "4326", 0.20
        )
        edit_layer = simplify_and_extract_features(defo_gdf_layer, "geometry", 15)
        orig_features = self.draw_alerts1.data
        test_features = convertir_formato3(edit_layer, "purple")
        #print(defo_gdf_layer, edit_layer, test_features)
        self.draw_alerts1.clear()
        self.draw_alerts2.clear()
        self.draw_alerts1.data = orig_features + test_features
        # self.draw_alerts2.data = orig_features + test_features
        self.dl_button2_add.disabled = True
        self.dl_button2_remove.disabled = False

    def remove_model1_prediction(self, widget, event, data):
        orig_features = self.draw_alerts1.data
        self.draw_alerts1.clear()
        self.draw_alerts2.clear()
        self.draw_alerts1.data = filter_features_by_color(orig_features, "lime")
        # self.draw_alerts2.data = filter_features_by_color(orig_features,'green')
        self.dl_button1_add.disabled = False
        self.dl_button1_remove.disabled = True

    def remove_model2_prediction(self, widget, event, data):
        orig_features = self.draw_alerts1.data
        self.draw_alerts1.clear()
        self.draw_alerts2.clear()
        self.draw_alerts1.data = filter_features_by_color(orig_features, "purple")
        # self.draw_alerts2.data = filter_features_by_color(orig_features,'purple')
        self.dl_button2_add.disabled = False
        self.dl_button2_remove.disabled = True

    def add_defo_layer(self, change):
        if self.analyzed_alerts_model.defo_dl_layer is not None:
            geo_json_layer = GeoData(
                geo_dataframe=self.analyzed_alerts_model.defo_dl_layer,
                name="Defo Layer",
                style={"color": "orange", "fillOpacity": "0"},
            )
            self.map_31.add_layer(geo_json_layer)
            self.map_32.add_layer(geo_json_layer)

    ##Process to save results
    def save_attributes_to_gdf(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks

        self.download_alert_data_btn.disabled = False

        alertas_gdf = self.analyzed_alerts_model.alerts_gdf
        actual_alert_id = self.analyzed_alerts_model.actual_alert_id
        alerta = self.analyzed_alerts_model.alerts_gdf.iloc[actual_alert_id]

        status_dict = {
            cm.analysis_tile.questionarie.confirmation_yes: "Confirmed",
            cm.analysis_tile.questionarie.confirmation_no: "False Positive",
            cm.analysis_tile.questionarie.confirmation_revision: "Need revision",
        }
        alertas_gdf.at[actual_alert_id, "status"] = status_dict[
            self.boton_confirmacion.v_model
        ]

        # Save alert driver/description
        drivers_list = ensure_list(self.comments_input.v_model)
        alertas_gdf.at[actual_alert_id, "description"] = format_list(drivers_list)

        # Save before and after img name
        alertas_gdf.at[actual_alert_id, "before_img"] = (
            self.selected_img_before_info_list[3]
        )
        alertas_gdf.at[actual_alert_id, "after_img"] = (
            self.selected_img_after_info_list[3]
        )
        alertas_gdf.at[actual_alert_id, "before_img_info"] = (
            self.selected_img_before_info_list[1]
        )
        alertas_gdf.at[actual_alert_id, "after_img_info"] = (
            self.selected_img_after_info_list[1]
        )
        ##Create dictionary of alert sources
        # alertas_gdf.at[actual_alert_id, "alert_sources"] = format_list(
        #     get_unique_alerts(alertas_gdf.loc[actual_alert_id, "alert_type_unique"])
        # )

        # Get adminstrative location attributes
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

        if (
            self.boton_confirmacion.v_model
            != cm.analysis_tile.questionarie.confirmation_yes or self.analyzed_alerts_model.defo_dl_layer is None
        ):
            alertas_gdf.at[actual_alert_id, "alert_polygon"] = None
            alertas_gdf.at[actual_alert_id, "area_ha"] = 0
        elif (
            self.boton_confirmacion.v_model
            == cm.analysis_tile.questionarie.confirmation_yes
        ):
            alertas_gdf.at[actual_alert_id, "alert_polygon"] = (
                self.analyzed_alerts_model.defo_dl_layer["geometry"].union_all()
            )
            alertas_gdf.at[actual_alert_id, "area_ha"] = calculate_total_area(
                self.analyzed_alerts_model.defo_dl_layer
            )

        self.save_alerts_to_gdf()
        self.analyzed_alerts_model.last_save_time = datetime.today().timestamp()
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
        if not selected_gdf['alert_polygon'].isnull().all():
            selected_gdf.set_geometry("alert_polygon", inplace=True)
        else:
            selected_gdf.set_geometry("bounding_box", inplace=True)
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
        
        if not Path(image_name).exists():
            download_both_images(
                self.selected_img_before,
                self.selected_img_after,
                image_name,
                self.selected_img_before_info_list[0],
                self.selected_img_after_info_list[0],
                self.actual_alert_grid,
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
        self.create_horizontal_slide_group_v3(
            self.analyzed_alerts_model.before_s2_images,
            self.map31,
            self.imgSelection1,
            0,
            self.image_slider_map_callback,
            0,
            self.image_slider_info_callback,
            0,
            self.show_image_collection[1],
        )

    def slider_s2_after(self, change):
        self.create_horizontal_slide_group_v3(
            self.analyzed_alerts_model.after_s2_images,
            self.map32,
            self.imgSelection2,
            1,
            self.image_slider_map_callback,
            1,
            self.image_slider_info_callback,
            1,
            self.show_image_collection[1],
        )

    ## Create Landsat dictionary

    def assign_landsat_before_dictionary(self, json):
        self.analyzed_alerts_model.before_landsat_images = json
        self.analyzed_alerts_model.before_landsat_images_time = (
            datetime.today().timestamp()
        )

    def assign_landsat_after_dictionary(self, json):
        self.analyzed_alerts_model.after_landsat_images = json
        self.analyzed_alerts_model.after_landsat_images_time = (
            datetime.today().timestamp()
        )

    def start_landsat_dictionary_thread(
        self,
        poly,
        fecha1,
        fecha2,
        assign_function,
    ):
        thread = threading.Thread(
            target=lambda: assign_function(
                getIndividualLandsat(
                    poly,
                    fecha1,
                    fecha2,
                )
            )
        )
        thread.start()

    def slider_landsat_before(self, change):
        self.create_horizontal_slide_group_v3(
            self.analyzed_alerts_model.before_landsat_images,
            self.map31,
            self.imgSelection1,
            0,
            self.image_slider_map_callback,
            0,
            self.image_slider_info_callback,
            0,
            False,
        )

    def slider_landsat_after(self, change):
        self.create_horizontal_slide_group_v3(
            self.analyzed_alerts_model.after_landsat_images,
            self.map32,
            self.imgSelection2,
            1,
            self.image_slider_map_callback,
            1,
            self.image_slider_info_callback,
            1,
            False,
        )
