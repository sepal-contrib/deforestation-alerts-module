from ipyleaflet import Marker, AwesomeIcon, Map, Popup, MarkerCluster, WidgetControl
import ipywidgets as widgets
from ipywidgets import HTML, HBox, VBox, Label, Button, Layout
from shapely.geometry import Point, Polygon
import pandas as pd
import geopandas as gpd
from datetime import datetime, timedelta
from ipyevents import Event

import ipyleaflet
import ipyvuetify as v
from sepal_ui.mapping.menu_control import MenuControl


def calculateAlertClasses(gpdf):
    x = len(gpdf.loc[gpdf["status"] == "Confirmed"])
    y = len(gpdf.loc[gpdf["status"] == "False Positive"])
    xy = len(gpdf.loc[gpdf["status"] == "Maybe"])
    z = len(gpdf)
    r = x + y + xy
    result = list([z, r, x, y, xy])

    return result


def convert_julian_to_date(julian_date):
    # Split the input string into year and julian day
    julian_date_str = "%.3f" % julian_date
    year_str, julian_str = julian_date_str.split(".")
    year = int(year_str)

    # Calculate the date by adding the julian day to the beginning of the year
    date = datetime(year, 1, 1) + timedelta(days=int(julian_str) - 1)

    # Return the formatted date string
    return date.strftime("%Y-%m-%d")


def create_markers(gdf, point_col, title_cols, group_by_col, marker_popup_function):
    """
    Create ipyleaflet.Marker objects from a GeoDataFrame and group them by unique attribute.

    Parameters:
        gdf (GeoDataFrame): The GeoDataFrame containing the data.
        point_col (str): The name of the geometry column containing point geometries.
        title_cols (list of str): List of column names to join into a title string.
        icon_map (dict): Dictionary mapping unique attribute values to icon configurations.
        group_by_col (str): Column name to group markers by.

    Returns:
        dict: A dictionary where keys are unique values of `group_by_col` and values are lists of Marker objects.
    """
    icon_dictionary = {
        "Not reviewed": AwesomeIcon(
            name="fa-light fa-circle-question", marker_color="gray", icon_color="white"
        ),
        "Confirmed": AwesomeIcon(
            name="fa-check-circle", marker_color="red", icon_color="white"
        ),
        "Maybe": AwesomeIcon(
            name="fa-flag-o", marker_color="orange", icon_color="white"
        ),
        "False Positive": AwesomeIcon(
            name=" fa-ban", marker_color="green", icon_color="black"
        ),
    }

    markers_dict = {}

    for _, row in gdf.iterrows():
        # Extract point coordinates from the geometry
        if isinstance(row[point_col], Point):
            location = (
                row[point_col].y,
                row[point_col].x,
            )  # Leaflet uses (lat, lon) format

            # Choose icon based on the icon_map dictionary
            icon = icon_dictionary.get(row[group_by_col], {})

            index = row.name

            # Create marker
            marker = Marker(location=location, icon=icon, draggable=False)
            message = HTML()
            message.value = (
                "<b>Start: </b> "
                + convert_julian_to_date(row[title_cols[0]])
                + " <b>End: </b>"
                + convert_julian_to_date(row[title_cols[1]])
                + " <b>ID: </b>"
                + str(index)
            )
            # marker.popup = message
            # Create a "Go" button for the popup
            go_button = Button(
                description="Go", button_style="success", layout=Layout(width="40px")
            )
            # Define the click handler with a loading state
            def on_button_click(event, index=None):
                # Set the loading state
                go_button.description = "Moving to alert..."
                #go_button.button_style = "warning"
                go_button.disabled = True
            
                # Perform the desired function (e.g., marker_popup_function)
                marker_popup_function(index)
            
                # Reset the button after the task completes
                go_button.description = "Go"
                #go_button.button_style = "success"
                go_button.disabled = False
            
            # Attach the click handler to the button
            #go_button.on_click(lambda event, index=index: marker_popup_function(index))
            go_button.on_click(lambda event: on_button_click(event, index=index))


            # Combine HTML message and button in a VBox
            # Center alignment for the VBox
            popup_content = VBox(
                [message, go_button],
                layout=Layout(
                    justify_content="center",  # Aligns items vertically to the center
                    align_items="center",  # Aligns items horizontally to the center
                    background_color="white",  # Background color
                    # width='100%',              # Ensures the VBox takes the full width
                    # height='100%'              # Ensures the VBox takes the full height
                ),
            )

            # Create a popup and add it to the marker
            marker.popup = Popup(child=popup_content, max_width=300)

            # Group markers by unique attribute
            group_value = row[group_by_col]
            if group_value not in markers_dict:
                markers_dict[group_value] = []
            markers_dict[group_value].append(marker)

    return markers_dict


def add_marker_clusters_with_hover_button(map_object, data_dict):
    # Dictionary to store the marker clusters associated with each category
    marker_clusters = {}

    # Create MarkerClusters for each category in the dictionary
    for category, markers in data_dict.items():
        marker_cluster = MarkerCluster(markers=markers, show_coverage_on_hover=False)
        marker_clusters[category] = marker_cluster

    # Function to handle checkbox changes
    def toggle_layer(change, category):
        if change["new"]:
            # Add the marker cluster to the map when checkbox is checked
            map_object.add_layer(marker_clusters[category])
        else:
            # Remove the marker cluster from the map when checkbox is unchecked
            map_object.layers = [
                layer
                for layer in map_object.layers
                if layer != marker_clusters[category]
            ]

    # Create a title for the widget
    title_label = Label(
        value="Marker control",
        layout=widgets.Layout(
            margin="0 0 10px 0",
            display="flex",
            justify_content="center",
            font_weight="bold",
        ),
    )

    # Create checkboxes for each category
    checkboxes = {}
    for category in data_dict.keys():
        checkbox = widgets.Checkbox(
            value=False,
            description=category,
            indent=False,
            layout=widgets.Layout(width="100px"),
        )
        checkbox.observe(
            lambda change, cat=category: toggle_layer(change, cat), names="value"
        )
        checkboxes[category] = checkbox

    # Arrange checkboxes vertically in a VBox widget
    checkbox_container = widgets.VBox(
        [title_label] + list(checkboxes.values()),
        layout=widgets.Layout(width="150px", padding="5px", border="solid 1px"),
    )

    # Create the button
    button = widgets.Button(button_style="primary", icon="list")
    # button.layout.width = "36px"

    # Container for button and checkboxes
    button_container = widgets.VBox([button])
    button_container.layout.max_width = "300px"

    # Event handler to show/hide checkboxes on hover
    def handle_hover(event):
        if event["type"] == "mouseenter":
            button_container.children = [button, checkbox_container]
        elif event["type"] == "mouseleave":
            button_container.children = [button]

    # Set up the hover events for the button container
    hover_event = Event(
        source=button_container, watched_events=["mouseenter", "mouseleave"]
    )
    hover_event.on_dom_event(handle_hover)

    # Add the button container as a widget control on the map
    widget_control = WidgetControl(widget=button_container, position="topright")
    map_object.add_control(widget_control)

    return map_object


def add_marker_clusters_with_menucontrol(map_object, data_dict):
    # Dictionary to store the marker clusters associated with each category
    marker_clusters = {}

    # Create MarkerClusters for each category in the dictionary
    for category, markers in data_dict.items():
        marker_cluster = MarkerCluster(markers=markers, show_coverage_on_hover=False)
        marker_clusters[category] = marker_cluster
        map_object.add_layer(marker_clusters[category])

    # Function to handle checkbox changes
    def toggle_layer(change, category):
        if change["new"]:
            # Add the marker cluster to the map when checkbox is checked
            map_object.add_layer(marker_clusters[category])
        else:
            # Remove the marker cluster from the map when checkbox is unchecked
            map_object.layers = [
                layer
                for layer in map_object.layers
                if layer != marker_clusters[category]
            ]

    # Create checkboxes for each category using ipyvuetify checkboxes
    checkboxes = {}
    for category in data_dict.keys():
        checkbox = v.Checkbox(v_model=False, label=category, dense=True)
        checkbox.observe(
            lambda change, cat=category: toggle_layer(change, cat), names="v_model"
        )
        checkboxes[category] = checkbox

    # Arrange checkboxes vertically in a layout
    checkbox_container = v.Container(
        children=[checkboxes[cat] for cat in checkboxes],
        class_="pa-3",
        style_="padding: 5px; max-height: 300px; max-width: 300px; background-color: white;",
    )

    menu_control = MenuControl(
        icon_content="mdi-layers",
        position="topright",
        card_content=checkbox_container,
        card_title="Marker Control",
    )
    menu_control.set_size(
        min_width="100px", max_width="400px", min_height="20vh", max_height="40vh"
    )
    map_object.add(menu_control)


# Function to create the table rows based on a list of 4 numbers
def create_table_rows(listaNumeros):
    return [
        v.Html(
            tag="tr",
            children=[
                v.Html(tag="td", children=["Total Alerts"]),
                v.Html(tag="td", children=[str(listaNumeros[0])]),
            ],
        ),
        v.Html(
            tag="tr",
            children=[
                v.Html(tag="td", children=["Reviewed Alerts"]),
                v.Html(tag="td", children=[str(listaNumeros[1])]),
            ],
        ),
        v.Html(
            tag="tr",
            children=[
                v.Html(tag="td", children=["Confirmed Alerts"]),
                v.Html(tag="td", children=[str(listaNumeros[2])]),
            ],
        ),
        v.Html(
            tag="tr",
            children=[
                v.Html(tag="td", children=["False Positives"]),
                v.Html(tag="td", children=[str(listaNumeros[3])]),
            ],
        ),
        v.Html(
            tag="tr",
            children=[
                v.Html(tag="td", children=["Need further revision"]),
                v.Html(tag="td", children=[str(listaNumeros[4])]),
            ],
        ),
    ]
