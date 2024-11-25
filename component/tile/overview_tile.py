from sepal_ui import sepalwidgets as sw
from sepal_ui import aoi
from sepal_ui.mapping import SepalMap
from component.message import cm
import ipyvuetify as v
from IPython.display import display, HTML
from ipyleaflet import WidgetControl

from sepal_ui.scripts.utils import init_ee
from traitlets import Any, Unicode, link
from component.scripts.overview_helper import *
from sepal_ui.mapping.layers_control import LayersControl
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

        self.listaNumeros = [0, 0, 0, 0, 0]
        # Side information table using v-simple-table
        self.info_table = v.SimpleTable(
            dense=False,
            children=[
                v.Html(tag="tbody", children=create_table_rows(self.listaNumeros))
            ],
        )

        self.initialize_layout()
        self.update_layout()

        ## Observe changes and update tile when it changes
        analyzed_alerts_model.observe(self.update_tile, "alerts_gdf")
        # analyzed_alerts_model.observe(self.update_tile,'actual_alert_id')

        super().__init__()

    def initialize_layout(self):

        # 1. Crear el mapa para seleccionar el área de estudio
        # Inject CSS to style the custom class
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
        self.map_1 = SepalMap()
        self.map_1.add_class("custom-map-class")
        self.map_1.add_basemap("SATELLITE")
        refresh_map_button = MapBtn("fa-light fa-rotate-right")
        widget_refresh = WidgetControl(
            widget=refresh_map_button, position="bottomright"
        )
        self.map_1.add(widget_refresh)
        refresh_map_button.on_event("click", self.update_button)

        # Side information labels
        section_title = v.CardTitle(class_="pa-1 ma-1", children=["General Overview"])
        self.dwn_all_btn = sw.DownloadBtn(text="AlertsDB", small=True)
        self.dwn_summary_btn = sw.DownloadBtn(text="Summary", small=True)
        gpkg_name = self.app_tile_model.recipe_folder_path + "/alert_db.parquet"
        self.dwn_all_btn.set_url(path=gpkg_name)

        card01 = v.Card(
            class_="pa-3 ma-5", hover=True, children=[section_title, self.info_table]
        )
        card02 = v.Card(
            class_="pa-3 ma-5 d-flex justify-center",
            hover=True,
            children=[self.dwn_all_btn, self.dwn_summary_btn],
        )
        card0 = v.Card(class_="py-2", children=[card01, card02])

        # Layout 6 de la aplicación
        layout = sw.Row(
            dense=True,
            children=[
                sw.Col(cols=10, children=[self.map_1]),
                sw.Col(
                    cols=2,
                    children=[card0],
                ),
            ],
        )
        self.children = [layout]

    def update_layout(self):
        if (
            self.analyzed_alerts_model.alerts_gdf is None
            or len(self.analyzed_alerts_model.alerts_gdf) == 0
        ):
            self.children = self.children
        else:
            self.map_1.remove_all()
            self.map_1.remove(self.map_1.controls[-1])
            self.map_1.add_ee_layer(self.aoi_date_model.aoi, name="AOI")

            # Add centroids
            centroides_gdf = self.analyzed_alerts_model.alerts_gdf
            markers_dictionary = create_markers(
                centroides_gdf,
                "point",
                ["alert_date_min", "alert_date_max"],
                "status",
                self.on_go_button_click,
            )
            add_marker_clusters_with_menucontrol(self.map_1, markers_dictionary)

            if (
                self.selected_alerts_model.alert_selection_area
                == "Chose by drawn polygon"
            ):
                draw_selection = ee.FeatureCollection(
                    self.selected_alerts_model.alert_selection_polygons
                )
                self.map_1.add_ee_layer(draw_selection, name="Drawn Item")

            self.listaNumeros = calculateAlertClasses(centroides_gdf)
            self.info_table.children[0].children = create_table_rows(self.listaNumeros)

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
    def on_go_button_click(self, index):
        self.analyzed_alerts_model.actual_alert_id = int(index)
        self.app_tile_model.current_page_view = "analysis_tile"
