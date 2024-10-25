import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, Polygon
from ipyleaflet import GeoJSON, AwesomeIcon, Marker, GeoData, Popup, MarkerCluster
from typing import Any, Dict, List, Optional, Sequence, Tuple, Type
import ipywidgets

# Function to convert list of polygons to geodataframe
def convert_to_geopandas(polygon_features):
    # Convert polygon features into GeoDataFrame and calculate centroids
    combined_features = []

    for feature in polygon_features:
        polygon = Polygon(feature["geometry"]["coordinates"][0])

        # Extract properties and add relevant information
        properties = feature["properties"]
        properties["gee_id"] = feature["id"]
        properties["bounding_box"] = polygon
        properties["point"] = polygon.centroid  # Add centroid as 'point'

        combined_features.append(properties)

    # Create GeoDataFrame
    gdf = gpd.GeoDataFrame(combined_features, geometry="point")

    # Add additional columns with default values
    gdf["status"] = "Not reviewed"
    gdf["before_img"] = pd.Series(dtype="object")
    gdf["before_img_info"] = pd.Series(dtype="object")
    gdf["after_img"] = pd.Series(dtype="object")
    gdf["after_img_info"] = pd.Series(dtype="object")
    gdf["alert_polygon"] = pd.Series(dtype="object")
    gdf["area_ha"] = pd.Series(dtype="float")
    gdf["description"] = pd.Series(dtype="object")

    # Save gdf to file
    # gdf.set_crs(epsg = '4326', allow_override=True, inplace=True).to_file(gpkg_name, driver='GPKG')

    # Check result
    return gdf


# Function to get the color based on the dictionary and handle missing values
def get_color(value, color_dict):
    if value is None or value not in color_dict:
        return "gray"  # Default color for None or missing value
    return color_dict[value]


def create_grouped_layers(geodataframe, field, color_dict, m):
    """
    Function to read a GeoDataFrame, separate it into unique groups based on a field,
    and create GeoJSON layers for each group with fill colors based on a color dictionary.
    If the value is None or missing in the dictionary, set the color to gray.

    Parameters:
    geodataframe (gpd.GeoDataFrame): Input GeoDataFrame
    field (str): Field name to group by
    color_dict (dict): Dictionary mapping field values to colors
    """

    # Get the unique values in the specified field
    unique_values = geodataframe[field].unique()
    # Loop through each unique value and create GeoJSON layers
    for value in unique_values:
        # Filter GeoDataFrame for the current unique value
        group_gdf = geodataframe[geodataframe[field] == value]

        # Convert the filtered GeoDataFrame to GeoJSON format
        geojson_data = group_gdf.__geo_interface__

        # Get the color for this group based on the dictionary
        color = get_color(value, color_dict)

        # Create the GeoJSON layer for this group
        geo_json_layer = GeoData(
            geo_dataframe=group_gdf.drop(columns=["bounding_box", "alert_polygon"]),
            style={
                "color": "black",
                "radius": 8,
                "fillColor": color,
                "opacity": 0.5,
                "weight": 1.9,
                "dashArray": "2",
                "fillOpacity": 0.6,
            },
            point_style={
                "radius": 5,
                "color": "black",
                "fillOpacity": 0.8,
                "fillColor": color,
                "weight": 3,
            },
            hover_style={"fillColor": "yellow"},
            name=f'{field}: {value if value is not None else "Unknown"}',
        )

        # Add the GeoJSON layer to the map
        m.add_layer(geo_json_layer)

    # Display the map
    return m


def calculateAlertClasses(gpdf):
    x = len(gpdf.loc[gpdf["status"] == "Confirmed"])
    y = len(gpdf.loc[gpdf["status"] == "False Positive"])
    xy = len(gpdf.loc[gpdf["status"] == "Maybe"])
    z = len(gpdf)
    r = x + y + xy
    result = list([z, r, x, y, xy])

    return result


# Taken from leafmap  https://github.com/opengeos/leafmap/blob/f576896844a668b3bf6cd20c744d646696b3745e/leafmap/leafmap.py#L3190


def add_point_layer(
    mapa,
    geodataframe,
    popup,
    layer_name,
    **kwargs,
) -> None:
    """Adds a point layer to the map with a popup attribute.

    Args:
        geodataframe,
        popup (str | list, optional): Column name(s) to be used for popup. Defaults to None.
        layer_name (str, optional): A layer name to use. Defaults to "Marker Cluster".

    Raises:
        ValueError: If the specified column name does not exist.
        ValueError: If the specified column names do not exist.
    """
    gdf = geodataframe
    df = gdf.to_crs(epsg="4326")
    col_names = df.columns.values.tolist()

    df["x"] = df.geometry.x
    df["y"] = df.geometry.y

    points = list(zip(df["y"], df["x"]))

    if popup is not None:
        if isinstance(popup, str):
            labels = df[popup]
            markers = [
                Marker(
                    location=point,
                    draggable=False,
                    popup=ipywidgets.HTML(str(labels[index])),
                )
                for index, point in enumerate(points)
            ]
        elif isinstance(popup, list):
            labels = []
            for i in range(len(points)):
                label = ""
                for item in popup:
                    label = label + str(item) + ": " + str(df[item][i]) + "<br>"
                labels.append(label)
            df["popup"] = labels

            markers = [
                Marker(
                    location=point,
                    draggable=False,
                    popup=ipywidgets.HTML(labels[index]),
                )
                for index, point in enumerate(points)
            ]

    else:
        markers = [Marker(location=point, draggable=False) for point in points]

    marker_cluster = MarkerCluster(markers=markers, name=layer_name)
    mapa.add_layer(marker_cluster)

    mapa.default_style = {"cursor": "default"}
    return mapa


def create_grouped_layers_with_popup(
    geodataframe, field, color_dict, m, popup_columns=None
):
    """
    Function to read a GeoDataFrame, separate it into unique groups based on a field,
    create GeoJSON layers for each group with fill colors based on a color dictionary,
    and add popups for points using the specified popup columns.

    Parameters:
    geodataframe (gpd.GeoDataFrame): Input GeoDataFrame
    field (str): Field name to group by
    color_dict (dict): Dictionary mapping field values to colors
    m (Map): Map object to add the layers to
    popup_columns (str or list, optional): Column name(s) to be used for popup. Defaults to None.
    """

    # Get the unique values in the specified field
    unique_values = geodataframe[field].unique()

    # Loop through each unique value and create GeoJSON layers
    for value in unique_values:
        # Filter GeoDataFrame for the current unique value
        group_gdf = geodataframe[geodataframe[field] == value]

        # Convert the filtered GeoDataFrame to GeoJSON format
        geojson_data = group_gdf.__geo_interface__

        # Get the color for this group based on the dictionary
        color = color_dict.get(value, "gray")

        # Create the GeoJSON layer for this group
        geo_json_layer = GeoData(
            geo_dataframe=group_gdf,
            style={
                "color": "black",
                "radius": 8,
                "fillColor": color,
                "opacity": 0.5,
                "weight": 1.9,
                "dashArray": "2",
                "fillOpacity": 0.6,
            },
            point_style={
                "radius": 5,
                "color": "black",
                "fillOpacity": 0.8,
                "fillColor": color,
                "weight": 3,
            },
            hover_style={"fillColor": "yellow"},
            name=f'{field}: {value if value is not None else "Unknown"}',
        )

        # Add the GeoJSON layer to the map
        m.add_layer(geo_json_layer)

        # Add popup functionality if specified
        if popup_columns is not None:
            # Convert to the appropriate coordinate reference system (CRS)
            df = group_gdf.to_crs(epsg="4326")

            # Extract x and y coordinates from the geometry
            df["x"] = df.geometry.x
            df["y"] = df.geometry.y

            points = list(zip(df["y"], df["x"]))

            # Handle popups based on the type of popup_columns
            if isinstance(popup_columns, str):
                # Single column popup
                labels = df[popup_columns].tolist()
                markers = [
                    Marker(
                        location=point,
                        draggable=False,
                        popup=ipywidgets.HTML(str(labels[index])),
                    )
                    for index, point in enumerate(points)
                ]
            elif isinstance(popup_columns, list):
                # Multiple column popups
                labels = []
                for i in range(len(points)):
                    label = ""
                    for item in popup_columns:
                        label += f"{item}: {df[item][i]}<br>"
                    labels.append(label)

                markers = [
                    Marker(
                        location=point,
                        draggable=False,
                        popup=ipywidgets.HTML(labels[index]),
                    )
                    for index, point in enumerate(points)
                ]
            else:
                # No popup columns provided
                markers = [Marker(location=point, draggable=False) for point in points]

            # Add the markers to a marker cluster and then add it to the map
            marker_cluster = MarkerCluster(
                markers=markers,
                name=f'{field}: {value if value is not None else "Unknown"}',
            )
            m.add_layer(marker_cluster)

    # Return the map with the added layers and popups
    return m
