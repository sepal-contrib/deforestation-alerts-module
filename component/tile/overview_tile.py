from sepal_ui import sepalwidgets as sw
from sepal_ui import aoi
from sepal_ui.mapping import SepalMap
from component.message import cm
import ipyvuetify as v
from IPython.display import display, HTML
from ipyleaflet import WidgetControl, LayersControl as ipyLayersControl

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
        app_tile_model,
    ):
        self.fluid = True
        self._metadata = {"mount_id": "overview_tile"}
        self.analyzed_alerts_model = analyzed_alerts_model
        self.selected_alerts_model = selected_alerts_model
        self.aoi_date_model = aoi_date_model
        self.app_tile_model = app_tile_model
        self.initialize_layout()
        self.update_layout()

        ## Observe changes and update tile when it changes
        analyzed_alerts_model.observe(self.update_tile, "alerts_gdf")

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
        refresh_map_button = MapBtn("fa-light fa-rotate-right")
        self.widget_refresh = WidgetControl(
            widget=refresh_map_button, position="bottomright"
        )
        refresh_map_button.on_event("click", self.update_button)
        self.map_1.add(self.widget_refresh)
        # self.map_1.add(ipyLayersControl(position='topright'))
        menu_control = MenuControl(
            icon_content="mdi-layers",
            position="topright",
            card_content=v.CardTitle(class_="pa-1 ma-1", children=["Cluster Control"]),
            card_title="Marker Control",
        )
        menu_control.set_size(
            min_width="100px", max_width="400px", min_height="20vh", max_height="40vh"
        )
        self.map_1.add(menu_control)

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

    def update_layout(self):
        if (
            self.analyzed_alerts_model.alerts_gdf is None
            or len(self.analyzed_alerts_model.alerts_gdf) == 0
        ):
            self.children = self.children
        else:
            self.map_1.remove_all()
            self.map_1.remove(self.map_1.controls[-1])
            self.map_1.add_ee_layer(self.aoi_date_model.feature_collection, name="AOI")

            # Add centroids
            centroides_gdf = self.analyzed_alerts_model.alerts_gdf
            markers_dictionary = create_markers_ipyvuetify(
                centroides_gdf,
                "point",
                ["alert_date_min", "alert_date_max"],
                "status",
                self.on_go_button_click,
            )
            add_marker_clusters_with_menucontrol(self.map_1, markers_dictionary)

            if (
                self.selected_alerts_model.alert_selection_area
                == cm.filter_tile.area_selection_method_label1
            ):
                draw_selection = ee.FeatureCollection(
                    self.selected_alerts_model.alert_selection_polygons
                )
                # self.map_1.add_ee_layer(draw_selection, name="Drawn Item")

            self.listaNumeros = calculate_alert_classes(
                centroides_gdf, "Confirmed", "False Positive", "Need revision"
            )
            self.info_table.children[0].children = create_table_rows(
                self.listaNumeros, self.alert_labels
            )

    def update_tile(self, change):
        # Update the tile when aoi_date_model changes
        self.update_layout()  # Reinitialize the layout with the new data

    def update_button(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks
        # Update the tile when aoi_date_model changes
        self.update_layout()  # Reinitialize the layout with the new data
        widget.loading = False  # Remove loading state
        widget.disabled = False  # Re-enable the button

    # Function to change actual alert id and move to analysis tile
    def on_go_button_click(self, widget, event, data):
        widget.loading = True  # Set button to loading state
        widget.disabled = True  # Disable button to prevent further clicks
        self.analyzed_alerts_model.actual_alert_id = int(widget.value)
        self.app_tile_model.current_page_view = "analysis_tile"
        widget.loading = False  # Remove loading state
        widget.disabled = False  # Re-enable the button
