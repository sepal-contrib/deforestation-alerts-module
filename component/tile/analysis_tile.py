from sepal_ui import sepalwidgets as sw
from sepal_ui import aoi
from sepal_ui.mapping import SepalMap
from component.message import cm
import ipyvuetify as v
from sepal_ui.scripts.utils import init_ee
from traitlets import Any, HasTraits, Unicode, link, observe
from sepal_ui.mapping.draw_control import DrawControl
from sepal_ui.mapping.layers_control import LayersControl
from sepal_ui.mapping.inspector_control import InspectorControl
import math
import ee
from shapely.geometry import Point, Polygon

init_ee()
from ipyleaflet import SplitMapControl
import ipywidgets as widgets
from component.scripts.overview_helper import *
from component.scripts.mosaics_helper import *
import numpy as np
import rasterio


class SepalCard(sw.SepalWidget, v.Card):
    def __init__(self, **kwargs):

        super().__init__(**kwargs)


class AnalysisTile(sw.Layout):
    def __init__(self, selected_alerts_model, analyzed_alerts_model):

        self._metadata = {"mount_id": "analysis_tile"}
        self.selected_alerts_model = selected_alerts_model
        self.analyzed_alerts_model = analyzed_alerts_model

        self.card00 = None
        self.card01 = None
        self.card02 = None
        self.card03 = None
        self.card04 = None
        self.card05 = None
        self.card06 = None

        self.planet_before_1 = None
        self.planet_after_1 = None
        self.defo_dl_layer = None
        self.actual_bb = None

        self.initialize_layout()

        ## Observe changes in selected_alerts_model and update tile when it changes
        selected_alerts_model.observe(self.update_gdf, "alerts_bbs")
        self.analyzed_alerts_model.observe(self.view_actual_alert, "actual_alert_id")

        super().__init__()

    def initialize_layout(self):
        # Create map
        self.map_31 = SepalMap()
        # self.map_31.layout.height = "100%"

        self.map_32 = SepalMap()
        # self.map_32.layout.height = "100%"

        # Link the zoom level between the two maps
        # zoom_link = widgets.jslink((self.map_31, 'zoom'), ( self.map_32, 'zoom'))
        # Link the center coordinates between the two maps
        # center_link = widgets.jslink((self.map_31, 'center'), ( self.map_32, 'center'))

        # Link the center and zoom between both maps
        center_link = link((self.map_31, "center"), (self.map_32, "center"))
        zoom_link = link((self.map_31, "zoom"), (self.map_32, "zoom"))

        # splitControl = SplitMapControl(left_layer=left_layer, right_layer=right_layer)
        # self.map_1.add(splitControl)

        # Map label
        mapLabel1 = v.CardTitle(children=["Image Before"])
        mapLabel2 = v.CardTitle(children=["Image After"])

        # Add image Selection Button1
        # imgBtn11 = sw.Btn("Planet Monthly", color="primary", outlined=True)
        # imgBtn12 = sw.Btn("Sentinel 2", color="primary", outlined=True)
        # imgBtn13 = sw.Btn("Planet Daily", color="primary", outlined=True)
        # imgSelection1 = sw.Row(children=[imgBtn11, imgBtn12, imgBtn13])
        # List of options
        before_images_list = [
            "Planet Monthly 1",
            "Planet Monthly 2",
            "Sentinel 2 1",
            "Sentinel 2 2",
            "Planet Daily 1",
            "Planet Daily 2",
        ]
        # Create and display the checkboxes
        before_image_selection_checkbox = sw.Select(
            items=before_images_list,
            v_model="Planet Monthly 1",
            multiple=False,
            clearable=True,
            chips=True,
        )

        img_before_Btn = sw.Btn("Confirm", color="primary", outlined=True)
        img_before_Btn.on_event("click", self.dummy_download_button)

        imgSelection1 = SepalCard(
            class_="pa-2",
            children=[
                v.CardTitle(children=["Select best image"]),
                before_image_selection_checkbox,
                img_before_Btn,
            ],
        )

        # Add image Selection Button2
        # imgBtn21 = sw.Btn("Planet Monthly", color="primary", outlined=True)
        # imgBtn22 = sw.Btn("Sentinel 2", color="primary", outlined=True)
        # imgBtn23 = sw.Btn("Planet Daily", color="primary", outlined=True)
        # imgSelection2 = sw.Row(children=[imgBtn21, imgBtn22, imgBtn23])

        # List of options
        after_images_list = [
            "Planet Monthly 1",
            "Planet Monthly 2",
            "Sentinel 2 1",
            "Sentinel 2 2",
            "Planet Daily 1",
            "Planet Daily 2",
        ]
        # Create and display the checkboxes
        after_image_selection_checkbox = sw.Select(
            items=after_images_list,
            v_model="Planet Monthly 1",
            multiple=False,
            clearable=True,
            chips=True,
        )

        img_after_Btn = sw.Btn("Confirm", color="primary", outlined=True)
        img_after_Btn.on_event("click", self.download_images_button)

        imgSelection2 = SepalCard(
            class_="pa-2",
            children=[
                v.CardTitle(children=["Select best image"]),
                after_image_selection_checkbox,
                img_after_Btn,
            ],
        )

        # Create map with buttons1
        self.map31 = sw.Col(children=[mapLabel1, self.map_31, imgSelection1])

        # Create map with buttons2
        self.map32 = sw.Col(children=[mapLabel2, self.map_32, imgSelection2])

        # Create drawer control and add to maps
        self.draw_alerts1 = DrawControl(self.map_31)
        self.draw_alerts2 = DrawControl(self.map_32)

        link((self.draw_alerts1, "data"), (self.draw_alerts2, "data"))

        label31 = v.CardTitle(children=["Alert revision"])
        prev_button = sw.Btn("Prev", color="primary", outlined=True, value=-1)
        next_button = sw.Btn("Next", color="primary", outlined=True, value=1)
        self.alert_id_button = sw.TextField(
            v_model=self.analyzed_alerts_model.actual_alert_id
        )
        go_to_alert_button = sw.Btn("Go to", color="primary", outlined=True, value=0)
        re_zoom_btn = sw.Btn("Re-zoom", color="primary", outlined=True)

        prev_button.on_event("click", self.navigate)
        next_button.on_event("click", self.navigate)
        go_to_alert_button.on_event("click", self.navigate)
        re_zoom_btn.on_event("click", self.re_zoom)

        self.card00 = SepalCard(
            class_="pa-2",
            children=[
                label31,
                sw.Col(
                    children=[
                        prev_button,
                        next_button,
                        self.alert_id_button,
                        go_to_alert_button,
                        re_zoom_btn,
                    ]
                ),
            ],
        )

        boton_confirmacion = sw.Select(
            items=["Yes", "No", "Need further revision"],
            v_model="Yes",
            # label="Is this a true alert?",
            multiple=False,
            clearable=True,
            chips=True,
        )

        self.card01 = SepalCard(
            class_="pa-2",
            children=[
                v.CardTitle(children=["Is this a true alert?"]),
                boton_confirmacion,
            ],
        )

        label33 = v.CardTitle(children=["Alert drawing"])
        button33 = sw.Btn("Auto", color="primary", outlined=True)
        button34 = sw.Btn("Edit", color="primary", outlined=True)
        button33.on_event("click", self.add_defo_prediction_map)
        button34.on_event("click", self.edit_defo_prediction_map)

        self.card02 = SepalCard(
            class_="pa-2",
            children=[label33, button33, button34],
        )

        label34 = v.CardTitle(children=["Description Field"])
        comments_input = sw.TextField(label="Enter text here", v_model="")

        self.card03 = SepalCard(
            class_="pa-2",
            children=[label34, comments_input],
        )

        self.button37 = sw.DownloadBtn(text="Files")
        button38 = sw.DownloadBtn(text="Report")
        button39 = sw.Btn("Save", color="primary", outlined=True)
        button39.on_event("click", self.save_attributes_to_gdf)

        self.card04 = SepalCard(
            class_="pa-2",
            children=[button39, self.button37, button38],
        )

        # Layout 3 de la aplicación
        layout = sw.Row(
            children=[
                sw.Col(cols=5, children=[self.map31]),
                sw.Col(cols=5, children=[self.map32]),
                sw.Col(
                    cols=2,
                    children=[
                        self.card00,
                        self.card01,
                        self.card02,
                        self.card03,
                        self.card04,
                    ],
                ),
            ]
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
            if self.analyzed_alerts_model.alerts_gdf is None:
                self.analyzed_alerts_model.alerts_gdf = alertas_gdf
            else:
                # self.analyzed_alerts_model.alerts_gdf =  pd.concat([self.analyzed_alerts_model.alerts_gdf, alertas_gdf])
                self.analyzed_alerts_model.alerts_gdf = alertas_gdf
            self.analyzed_alerts_model.actual_alert_id = 0
            self.map_31.centerObject(self.actual_bb)

    def update_gdf(self, change):
        print("cambio detectado en selected_Alerts, ejecutando create gdf")
        # Update the tile when selected alert centroids changes
        # self.create_gdf(self.selected_alerts_model, self.analyzed_alerts_model)
        self.create_gdf()

    def navigate(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks

        if widget.value == 0:
            self.analyzed_alerts_model.actual_alert_id = int(
                self.alert_id_button.v_model
            )
        else:
            self.analyzed_alerts_model.actual_alert_id = (
                self.analyzed_alerts_model.actual_alert_id + widget.value
            )

        self.button37.set_url()

        widget.loading = False  # Remove loading state
        widget.disabled = False  # Re-enable the button

    def view_actual_alert(self, change):
        print("cambiando alerta", self.analyzed_alerts_model.actual_alert_id)
        self.alert_id_button.v_model = self.analyzed_alerts_model.actual_alert_id
        alertas_gdf = self.analyzed_alerts_model.alerts_gdf
        actual_alert_id = self.analyzed_alerts_model.actual_alert_id
        alerta = self.analyzed_alerts_model.alerts_gdf.iloc[actual_alert_id]
        # alerta_punto_geojson = alerta['point'].__geo_interface__
        alerta_bb_geojson = alerta["bounding_box"].__geo_interface__
        # alerta_bb_geojson_feature = {"type": "Feature","geometry": alerta_punto_geojson}

        alerta_bb_geojson_ee = ee.Feature(alerta_bb_geojson)
        self.actual_bb = alerta_bb_geojson_ee

        self.draw_alerts1.hide()
        self.draw_alerts2.hide()

        self.map_31.centerObject(alerta_bb_geojson_ee)

        gridDescarga = alerta_bb_geojson_ee.geometry().coveringGrid(
            "EPSG:3857", 1 * 256 * 4.77
        )
        gridDescargaBounds = gridDescarga.geometry().bounds(1)

        planetSA = ee.ImageCollection("projects/planet-nicfi/assets/basemaps/americas")
        planetAF = ee.ImageCollection("projects/planet-nicfi/assets/basemaps/africa")
        planetAS = ee.ImageCollection("projects/planet-nicfi/assets/basemaps/asia")
        planet = planetSA.merge(planetAF).merge(planetAS)

        planet_before_clip = ee.Image(
            "projects/planet-nicfi/assets/basemaps/americas/planet_medres_normalized_analytic_2024-07_mosaic"
        ).clip(gridDescargaBounds)
        planet_clip_last = (
            planetSA.filterBounds(alerta_bb_geojson_ee.geometry())
            .sort("system:time_start", False)
            .first()
            .clip(gridDescargaBounds)
        )

        self.map_31.add_ee_layer(
            planet_before_clip,
            {"min": 0, "max": 2500, "bands": ["R", "G", "B"]},
            "Before Planet rgb",
            False,
        )
        self.map_31.add_ee_layer(
            planet_before_clip,
            {"min": 0, "max": [6500, 2500, 2500], "bands": ["N", "R", "G"]},
            "Before Planet NRG",
        )
        self.map_32.add_ee_layer(
            planet_clip_last,
            {"min": 0, "max": 2500, "bands": ["R", "G", "B"]},
            "Last Planet rgb",
            False,
        )
        self.map_32.add_ee_layer(
            planet_clip_last,
            {"min": 0, "max": [6500, 2500, 2500], "bands": ["N", "R", "G"]},
            "Last Planet NRG",
        )
        self.map_31.add_ee_layer(alerta_bb_geojson_ee, name="Alert BB", opacity=0.5)
        self.map_32.add_ee_layer(alerta_bb_geojson_ee, name="Alert BB", opacity=0.5)

        planet_before_clip_r = reEscalePlanet(planet_before_clip)
        planet_clip_last_r = reEscalePlanet(planet_clip_last)

        self.planet_before_1 = planet_before_clip_r
        self.planet_after_1 = planet_clip_last_r

    def dummy_download_button(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks
        from time import sleep

        sleep(2)
        widget.loading = False  # Remove loading state
        widget.disabled = False  # Re-enable the button

    def download_images_button(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks
        image_name = (
            "outputs/alert_" + str(self.analyzed_alerts_model.actual_alert_id) + ".tif"
        )
        print("Inicio descarga")
        download_both_images(self.planet_before_1, self.planet_after_1, image_name)
        print("Fin descarga")
        widget.loading = False  # Remove loading state
        widget.disabled = False  # Re-enable the button

    def add_defo_prediction_map(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks

        print("Inicio modelo")
        image_name = (
            "outputs/alert_" + str(self.analyzed_alerts_model.actual_alert_id) + ".tif"
        )
        prediction = apply_dl_model(image_name, "utils/model2.h5")
        prediction_name = (
            "outputs/alert_"
            + str(self.analyzed_alerts_model.actual_alert_id)
            + "_prediction.tif"
        )
        save_raster = save_prediction_prob(
            image_name, np.squeeze(prediction), prediction_name
        )
        defo_gdf_layer = raster_to_gdf(prediction_name, "4326", 0.20)

        self.defo_dl_layer = defo_gdf_layer
        geo_json_layer = GeoData(geo_dataframe=self.defo_dl_layer, name="Defo DL")
        print("agregado como capa")

        self.map_31.add_layer(geo_json_layer)
        self.map_32.add_layer(geo_json_layer)

        widget.loading = False  # Remove loading state
        widget.disabled = False  # Re-enable the button

    def edit_defo_prediction_map(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks

        self.draw_alerts1.hide()
        self.draw_alerts2.hide()

        # Simplify polygon for edition
        edit_layer = simplify_and_extract_features(self.defo_dl_layer, "geometry", 10)
        self.draw_alerts1.data = convertir_formato2(edit_layer)

        self.draw_alerts1.show()
        self.draw_alerts2.show()

        widget.loading = False  # Remove loading state
        widget.disabled = False  # Re-enable the button

    def re_zoom(self, widget, event, data):
        self.map_31.centerObject(self.actual_bb)

    def save_attributes_to_gdf(self, widget, event, data):
        alertas_gdf = self.analyzed_alerts_model.alerts_gdf
        actual_alert_id = self.analyzed_alerts_model.actual_alert_id
        alerta = self.analyzed_alerts_model.alerts_gdf.iloc[actual_alert_id]

        status_dict = {
            "Yes": "Confirmed",
            "No": "False Positive",
            "Need further revision": "maybe",
        }
        alertas_gdf.at[actual_alert_id, "status"] = status_dict[
            self.card01.children[1].v_model
        ]
        alertas_gdf.at[actual_alert_id, "description"] = self.card03.children[1].v_model
        alertas_gdf.at[actual_alert_id, "before_img"] = (
            self.map31.children[2].children[1].v_model
        )
        alertas_gdf.at[actual_alert_id, "after_img"] = (
            self.map32.children[2].children[1].v_model
        )

        if self.card01.children[1].v_model == "No":
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
            "outputs/alert_" + str(self.analyzed_alerts_model.actual_alert_id) + ".gpkg"
        )
        # Export to GPKG (GeoPackage)
        selected_gdf.set_crs(epsg="4326", allow_override=True, inplace=True).to_file(
            gpkg_name, driver="GPKG"
        )  # Save as GPKG

        image_name = (
            "outputs/alert_" + str(self.analyzed_alerts_model.actual_alert_id) + ".tif"
        )
        prediction_name = (
            "outputs/alert_"
            + str(self.analyzed_alerts_model.actual_alert_id)
            + "_prediction.tif"
        )
        zipfile_name = (
            "outputs/alert_" + str(self.analyzed_alerts_model.actual_alert_id) + ".zip"
        )

        add_files_to_zip(zipfile_name, image_name, prediction_name, gpkg_name)
        zip_path = "defoAlerts/Deforestation_Alerts_Analysis/" + zipfile_name
        self.button37.set_url(path=zip_path)
        # self.analyzed_alerts_model.actual_alert_id = self.analyzed_alerts_model.actual_alert_id + 1
