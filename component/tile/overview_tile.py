from sepal_ui import sepalwidgets as sw
from sepal_ui import aoi
from sepal_ui.mapping import SepalMap
from component.message import cm
import ipyvuetify as v
from IPython.display import display, HTML
from ipyleaflet import WidgetControl, GeoData, LayersControl as ipyLayersControl

from sepal_ui.scripts.utils import init_ee
from traitlets import Any, Unicode, link
from component.scripts.overview_helper import *
from sepal_ui.mapping.layers_control import LayersControl
from sepal_ui.mapping.menu_control import MenuControl
from sepal_ui.mapping.map_btn import MapBtn
import ee

init_ee()


class OverviewTile(sw.Layout):
    def __init__(
        self,
        aoi_date_model,
        analyzed_alerts_model,
        selected_alerts_model,
        aux_model,
        app_tile_model,
    ):
        self._metadata = {"mount_id": "overview_tile"}
        self.aux_model = aux_model
        self.analyzed_alerts_model = analyzed_alerts_model
        self.selected_alerts_model = selected_alerts_model
        self.aoi_date_model = aoi_date_model
        self.app_tile_model = app_tile_model
        self.initialize_layout()
        self.update_table()
        self.update_map()
        
        ## Observe changes and update tile when it changes
        self.analyzed_alerts_model.observe(self.update_tile, "last_save_time")
        self.analyzed_alerts_model.observe(self.update_tile, "alerts_gdf")

        super().__init__()

    def initialize_layout(self):
        # Set default table values
        self.listaNumeros = [0, 0, 0, 0, 0]
        self.alert_labels = [
            cm.overview_tile.total,
            cm.overview_tile.reviewed,
            cm.overview_tile.confirmed,
            cm.overview_tile.false_positives,
            cm.overview_tile.revision,
        ]
        # Side information table using v-simple-table
        self.info_table = v.SimpleTable(
            dense=False,
            children=[
                v.Html(
                    tag="tbody",
                    children=create_table_rows(self.listaNumeros, self.alert_labels),
                )
            ],
        )

        # 1. Crear el mapa para seleccionar el área de estudio
        # Inject CSS to style the custom class
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
        self.map_1 = SepalMap()
        self.map_1.add_class("custom-map-class")
        self.map_1.add_basemap("SATELLITE")
        
        # Side information labels
        section_title = v.CardTitle(
            class_="pa-1 ma-1", children=[cm.overview_tile.title]
        )
        self.dwn_all_btn = sw.DownloadBtn(
            text=cm.overview_tile.alerts_db_label, small=True
        )
        self.dwn_summary_btn = sw.DownloadBtn(
            text=cm.overview_tile.summary_button_label, small=True
        )
        gpkg_name = self.app_tile_model.recipe_folder_path + "/alert_db.csv"
        self.dwn_all_btn.set_url(path=gpkg_name)

        card01 = v.Card(
            class_="pa-3 ma-3", hover=True, children=[section_title, self.info_table]
        )
        card02 = v.Card(
            class_="pa-3 ma-3 d-flex justify-center",
            hover=True,
            children=[self.dwn_all_btn],  # , self.dwn_summary_btn],
        )
        # Layout 
        # Two-panel layout using Flex
        left_panel = v.Flex(
            children=[self.map_1],
            style_='flex: 1 1 auto ; overflow: hidden'
        )
        right_panel = v.Flex(
            children=[card01, card02],
            style_='flex: 0 0 16rem ; overflow: auto'
        )

        self.children = [left_panel,right_panel]


    def update_map(self):
        if (
            self.analyzed_alerts_model.alerts_gdf is None
            or len(self.analyzed_alerts_model.alerts_gdf) == 0
        ):
            self.children = self.children
        else:
            self.map_1.remove_all()
            self.map_1.add_ee_layer(self.aoi_date_model.feature_collection, name="AOI")
           
            color_dictionary = {
                "Not reviewed": "lightgrey",
                "Confirmed": "red",
                "Need revision": "orange",
                "False Positive": "green"
            }
            newgdf = self.analyzed_alerts_model.alerts_gdf.drop(columns=['bounding_box','alert_polygon'])
            alerts_db = newgdf.set_geometry('point')
            add_colored_layers(alerts_db, 'status', color_dictionary, self.map_1, self.on_go_button_click)

            if self.selected_alerts_model.alert_selection_area_n == 1:
                draw_selection = ee.FeatureCollection(
                    self.selected_alerts_model.alert_selection_polygons
                )
                self.map_1.add_ee_layer(draw_selection, name="Drawn Item", vis_params = {'color':'7eb9e8'})
            if self.aux_model.mask_layer:
                self.map_1.add_ee_layer(
                    ee.Image(self.aux_model.mask_layer).selfMask(),
                    name=cm.filter_tile.layer_mask_name,
                    # vis_params=self.aux_model.aux_layer_vis,
                    vis_params={"min": 0, "max": 1, "palette": ["white", "gray"]},
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

    def update_table(self):
        if self.analyzed_alerts_model.alerts_gdf is not None:
            self.listaNumeros = calculate_alert_classes(
                    self.analyzed_alerts_model.alerts_gdf, "Confirmed", "False Positive", "Need revision"
                )
            self.info_table.children[0].children = create_table_rows(
                    self.listaNumeros, self.alert_labels
                )
    def update_tile(self, change):
        # Update the tile when aoi_date_model changes
        self.update_table()
        self.update_map()
            

    # Function to change actual alert id and move to analysis tile
    def on_go_button_click(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks
        self.analyzed_alerts_model.actual_alert_id = int(widget.value)
        self.app_tile_model.current_page_view = "analysis_tile"
        widget.loading = False  # Remove loading state
        widget.disabled = False  # Re-enable the button
