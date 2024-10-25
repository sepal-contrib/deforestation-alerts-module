from sepal_ui import sepalwidgets as sw
from sepal_ui import aoi
from sepal_ui.mapping import SepalMap
from component.message import cm
import ipyvuetify as v
from sepal_ui.scripts.utils import init_ee
from traitlets import Any, Unicode, link
from component.scripts.alert_filter_helper import *
from sepal_ui.mapping.draw_control import DrawControl
from sepal_ui.mapping.layers_control import LayersControl
from sepal_ui.mapping.inspector_control import InspectorControl
import math
import ee
from shapely.geometry import Point, Polygon
from traitlets import Any, HasTraits, Unicode, link, observe
import time

init_ee()


class SepalCard(sw.SepalWidget, v.Card):
    def __init__(self, **kwargs):

        super().__init__(**kwargs)


class AlertsFilterTile(sw.Layout):
    def __init__(
        self, aoi_date_model, aux_model, alert_filter_model, selected_alerts_model
    ):

        self._metadata = {"mount_id": "filter_alerts"}
        self.aoi_date_model = aoi_date_model
        self.aux_model = aux_model
        self.alert_filter_model = alert_filter_model
        self.selected_alerts_model = selected_alerts_model

        # Variables to describe collections
        self.lista_vis_params = [{"min": 1, "max": 2, "palette": ["orange", "purple"]}]

        # Load available alerts names and rasters from model
        # self.selected_alert_names = self.alert_filter_model.available_alerts_list
        # self.filtered_alert_rasters = self.alert_filter_model.available_alerts_raster_list

        # user interface elements
        self.card00 = None
        self.card01 = None
        self.card02 = None
        self.card03 = None
        self.card04 = None
        self.card05 = None
        self.card06 = None

        self.initialize_layout()
        self.update_layout()

        ## Observe changes in aoi_date_model and update tile when it changes
        alert_filter_model.observe(self.update_tile, "available_alerts_raster_list")
        # alert_filter_model.observe(self.update_tile)

        super().__init__()

    def initialize_layout(self):
        # Create map
        self.map_2 = SepalMap()
        self.map_2.layout.height = "100%"
        # Create UI components based on the alert list

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

        # List of options
        alert_selection_method_list = ["Chose by drawn polygon", "Whole area"]
        # Create and display the checkboxes
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
                v.CardTitle(children=["Alert selection"]),
                checkbox_container22,
            ],
        )

        # List of options
        alert_sorting_method_list = [
            "Prioritize bigger area alerts",
            "Prioritize smaller area alerts",
            "Prioritize recent alerts",
            "Prioritize older alerts",
            "Prioritize confirmed alerts",
        ]
        # Create and display the checkboxes
        checkbox_container23 = sw.Select(
            items=alert_sorting_method_list,
            v_model=["Prioritize bigger area alerts", "Prioritize recent alerts"],
            multiple=True,
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
            else:
                self.card04.hide()

        checkbox_container22.on_event("change", prior_option)

        number_of_alerts = sw.TextField(v_model=0)
        self.card04 = SepalCard(
            class_="pa-2",
            children=[v.CardTitle(children=["Number of alerts"]), number_of_alerts],
        )
        analyze_button = sw.Btn(text="Analyze alerts", loading=False)
        analyze_button.on_event("click", self.create_filtered_alert_list)
        self.card05 = v.Card(class_="pa-2", children=[analyze_button])

        layout = sw.Row(
            children=[
                sw.Col(cols=9, style="height: 100vh;", children=[self.map_2]),
                sw.Col(
                    cols=3,
                    style="height: 100vh;",
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
            ]
        )

        self.children = [layout]

    def update_layout(self):
        print("Update layout executing", self.alert_filter_model.available_alerts_list)
        if not self.alert_filter_model.available_alerts_list:
            # print('Opcion 1 sin alertas', len(self.alert_filter_model.available_alerts_list), self.alert_filter_model.available_alerts_list)
            self.card00.show()
            self.card01.hide()
            self.card02.hide()
            self.card03.hide()
            self.card04.hide()
            self.card05.hide()
            self.card06.hide()
        else:
            print(
                "Opcion 2 con alertas",
                len(self.alert_filter_model.available_alerts_list),
                self.alert_filter_model.available_alerts_list,
            )
            self.map_2.clear()
            self.default_basemap = (
                "CartoDB.DarkMatter" if v.theme.dark is True else "CartoDB.Positron"
            )
            self.map_2.add_basemap(self.default_basemap)
            self.map_2.add(DrawControl(self.map_2))
            self.map_2.add(LayersControl(self.map_2))
            self.map_2.add(InspectorControl(self.map_2))
            self.checkbox_container21.items = (
                self.alert_filter_model.available_alerts_list
            )
            self.checkbox_container21.v_model = (
                self.alert_filter_model.available_alerts_list
            )

            self.card00.hide()
            self.card01.show()
            self.card02.show()
            self.card03.show()
            self.card04.show()
            self.card05.show()
            self.card06.show()

            # Add content to map
            # self.map_2.zoom_ee_object(aoi_date_model.aoi, zoom_out = 3)
            self.map_2.centerObject(self.aoi_date_model.aoi)
            self.map_2.add_ee_layer(self.aoi_date_model.aoi, name="AOI")

            for i in range(len(self.alert_filter_model.available_alerts_list)):
                self.map_2.add_ee_layer(
                    self.alert_filter_model.available_alerts_raster_list[i]
                    .select("alert")
                    .selfMask(),
                    name=self.alert_filter_model.available_alerts_list[i],
                    vis_params=self.lista_vis_params[0],
                )
                self.map_2.add_ee_layer(
                    self.alert_filter_model.available_alerts_raster_list[i]
                    .select("date")
                    .selfMask()
                    .randomVisualizer(),
                    name=self.alert_filter_model.available_alerts_list[i] + " date",
                )
            if self.aux_model.aux_layer:
                self.map_2.add_ee_layer(
                    ee.Image(self.aux_model.aux_layer).selfMask(),
                    name="Auxiliary Layer",
                    # vis_params=self.aux_model.aux_layer_vis,
                    vis_params={"min": 0, "max": 1, "palette": ["white", "yellow"]},
                )

            if self.aux_model.mask_layer:
                self.map_2.add_ee_layer(
                    ee.Image(self.aux_model.mask_layer).selfMask(),
                    name="Mask Layer",
                    # vis_params=self.aux_model.aux_layer_vis,
                    vis_params={"min": 0, "max": 1, "palette": ["white", "yellow"]},
                )

    def update_tile(self, change):
        print("change detected in alert filter tile")
        # Update the tile when aoi_date_model changes
        self.update_layout()  # Reinitialize the layout with the new data

    def create_filtered_alert_list(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks

        # Bind variables to models
        self.selected_alerts_model.alert_selection_area = self.card03.children[
            1
        ].v_model
        self.selected_alerts_model.alert_sorting_method = self.card06.children[
            1
        ].v_model
        self.selected_alerts_model.min_area = self.card02.children[1].v_model
        self.selected_alerts_model.max_number_alerts = int(
            self.card04.children[1].v_model
        )
        self.selected_alerts_model.alert_selection_polygons = self.map_2.controls[
            0
        ].to_json()

        # Process alerts
        # alerts_list = self.alert_filter_model.available_alerts_raster_list
        # alerta = alerts_list[0]

        # Whole area fc
        poly = self.aoi_date_model.aoi
        # Grid for faster download
        poly_grid = poly.geometry().coveringGrid("EPSG:4326", 100000)

        # Enmascarar si existe capa mask
        if self.aux_model.mask_layer:
            mask_layer = ee.Image(self.aux_model.mask_layer)
            mask_alert = self.alert_filter_model.available_alerts_raster_list[0].where(
                mask_layer.eq(0), 0
            )
        else:
            mask_alert = self.alert_filter_model.available_alerts_raster_list[0]

        # Cortar si existe poligono y alert selection es by drawn polygon
        if self.selected_alerts_model.alert_selection_area == "Chose by drawn polygon":
            clip_alert = mask_alert.clip(
                ee.FeatureCollection(
                    self.selected_alerts_model.alert_selection_polygons
                )
            )
        else:
            self.selected_alerts_model.alert_selection_polygons = None
            clip_alert = mask_alert

        # Generar 3 bandas para las alertas de todo el area
        i = clip_alert.select("alert").gt(0).rename("mask_alert")
        a = clip_alert.select("alert")
        d = clip_alert.select("date")
        # alertarea = i.multiply(ee.Image.pixelArea()).rename('area');

        alerta_reducir = i.addBands(i).addBands(a).addBands(d)

        # Definir tamaño de pixel
        pixel_landsat = 30
        pixel_sentinel = 10
        pixel_size = pixel_landsat

        # Definir numero minimo de pixels para alertas
        min_size_hectareas = float(self.selected_alerts_model.min_area) * 10000
        min_size_pixels = math.ceil(min_size_hectareas / (pixel_size**2))

        # Definir numero maximo de alertas
        max_number_alerts = self.selected_alerts_model.max_number_alerts

        # Crear reducer a aplicar sobre las alertas
        custom_reducer = (
            ee.Reducer.count()
            .combine(ee.Reducer.mean().unweighted(), "alert_type_")
            .combine(ee.Reducer.minMax(), "alert_date_")
        )

        # Falta agregar opciones de ordenar segun user input
        # Obtener 20 bounding boxes rapidamente
        print("Esperando info de gee")
        st = time.time()
        # bb_sorted_short_temp = obtener_datos_gee_parcial (poly_grid, alerta_reducir, custom_reducer, pixel_size ,min_size_pixels, 20)
        bb_full_list = obtener_datos_gee_total(
            poly,
            alerta_reducir,
            custom_reducer,
            pixel_size,
            min_size_pixels,
            self.selected_alerts_model.max_number_alerts,
        )
        et = time.time()
        print("Info obtebnida de gee", et - st)

        # Save centroids and bb to model
        self.selected_alerts_model.alerts_bbs = bb_full_list

        # Obtener el total de bounding boxes en otro thread
        # import threading
        # tarea_hilo = threading.Thread(target=obtener_datos_gee_total, args = (poly, alerta_reducir, custom_reducer, pixel_size, min_size_pixels, self.selected_alerts_model.max_number_alerts))

        # bb_full_list = obtener_datos_gee_total(poly, alerta_reducir, custom_reducer, pixel_size, min_size_pixels, self.selected_alerts_model.max_number_alerts)
        # self.selected_alerts_model.alerts_total_bbs = bb_full_list

        # print('Info guardada en modelo', self.selected_alerts_model.alerts_bbs[0])
        widget.loading = False  # Remove loading state
        widget.disabled = False  # Re-enable the button
