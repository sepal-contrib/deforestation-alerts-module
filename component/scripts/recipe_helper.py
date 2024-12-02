import pandas as pd
import geopandas as gpd
import ee
import geemap
import os
import pathlib
import zipfile
import ast
import numpy as np
from datetime import datetime
import json
from shapely import wkt
from component.parameter import directory
from component.scripts.alert_filter_helper import check_alert_filter_inputs


def generate_recipe_string():
    # Get the current date and time
    current_time = datetime.now()
    # Format the string as 'recipe_YYYY-MM-DD-HHMMSS'
    recipe_string = current_time.strftime("recipe_%Y-%m-%d-%H%M%S")
    return recipe_string


def create_directory(folder_name):
    """
    Creates a directory with the given folder name.
    If the directory already exists, it will notify the user.

    Args:
        folder_name (str): The name of the directory to create.

    Returns:
        str: The path of the created or existing directory.
    """

    try:
        folder = directory.module_dir / folder_name
        folder_temp = directory.module_dir / "temp"
        folder.mkdir(parents=True, exist_ok=True)
        folder_temp.mkdir(parents=True, exist_ok=True)
        # print(f"Directory '{folder_name}' created successfully.")
    except Exception as e:
        print(f"An error occurred while creating the directory: {e}")

    return os.path.abspath(folder)


def save_model_parameters_to_json(
    file_name,
    aux_model,
    aoi_date_model,
    alert_filter_model,
    selected_alerts_model,
    analyzed_alerts_model,
    app_tile_model,
):
    """
    Saves the variables (parameters) of models to a JSON file.

    Args:
        file_name (str): The name of the JSON file to save the parameters.

    Returns:
        str: The path of the created JSON file.
    """
    a = aux_model.export_dictionary()  # .update(model_parameters)
    b = aoi_date_model.export_dictionary()  # .update(model_parameters)
    c = selected_alerts_model.export_dictionary()  # .update(model_parameters)
    d = analyzed_alerts_model.export_dictionary()  # .update(model_parameters)
    e = app_tile_model.export_dictionary()  # .update(model_parameters)

    model_parameters = a | b | c | d | e
    # model_parameters = {key: value for d in (a, b, c, d, e) for key, value in d.items()}

    print(a, b, c, d, e, model_parameters)

    ##File path to save the JSON file
    file_path = file_name

    # Save dictionary to JSON file
    with open(file_path, "w") as json_file:
        json.dump(model_parameters, json_file)

    return file_path


def load_parameters_from_json(
    file_name,
    aux_model,
    aoi_date_model,
    selected_alerts_model,
    analyzed_alerts_model,
    app_tile_model,
    aoi_tile,
    alert_filter_tile,
):
    """
    Loads variables (parameters) from a JSON file and applies them to the models.

    Args:
        models (dict): A dictionary where keys are model names (str) and values are model objects.
        file_name (str): The name of the JSON file to load parameters from.

    Returns:
        None
    """

    # Read JSON file
    with open(file_name, "r") as json_file:
        model_parameters = json.load(json_file)

    aux_model.import_from_dictionary(model_parameters)
    aoi_tile.load_saved_parameters(model_parameters)
    aoi_tile.process_alerts2()
    alert_filter_tile.load_saved_parameters(model_parameters)
    (
        alert_source,
        user_min_alert_size,
        alert_area_selection,
        alert_sorting_method,
        user_max_number_alerts,
        user_selection_polygon,
    ) = check_alert_filter_inputs(alert_filter_tile)
    alert_filter_tile.create_planet_images_dictionary(
        aoi_date_model.feature_collection,
        aoi_date_model.start_date,
        aoi_date_model.end_date,
    )
    alert_filter_tile.create_filtered_alert_raster(
        alert_source,
        user_min_alert_size,
        alert_area_selection,
        alert_sorting_method,
        user_max_number_alerts,
        user_selection_polygon,
    )
    app_tile_model.import_from_dictionary(model_parameters)
    analyzed_alerts_model.alerts_gdf = load_gdf_from_csv(
        app_tile_model.recipe_folder_path + "/alert_db.csv",
        ["bounding_box", "point", "alert_polygon"]
    )
    analyzed_alerts_model.import_from_dictionary(model_parameters)
    app_tile_model.current_page_view = "analysis_tile"


def load_gdf_from_csv (csv_file, geometry_columns_list):
    # load encoded dataframe
    df = pd.read_csv(csv_file)
    # decode geometry columns as strings back into shapely objects
    for c in geometry_columns_list:
        df[c] = df[c].apply(lambda x: wkt.loads(x) if pd.notnull(x) and x != '' else None)
    
    # finally reconstruct geodataframe
    gdf = gpd.GeoDataFrame(df)
    return gdf


def update_actual_id(json_file_path, new_value):
    """
    Updates the value of the 'actual_id' key in a JSON file and rewrites the file.

    Args:
        json_file_path (str): Path to the JSON file.
        new_value (any): New value to assign to the 'actual_id' key.
    
    Returns:
        None
    """
    try:
        # Read the JSON file
        with open(json_file_path, 'r') as file:
            data = json.load(file)
        
        # Ensure the content is a dictionary
        if not isinstance(data, dict):
            raise ValueError("JSON content must be a dictionary.")
        
        # Update the 'actual_id' key
        data['actual_alert_id'] = new_value
        
        # Write the updated content back to the same file
        with open(json_file_path, 'w') as file:
            json.dump(data, file)
        
        print(f"'actual_id' successfully updated to {new_value}.")
    except FileNotFoundError:
        print(f"Error: File '{json_file_path}' not found.")
    except json.JSONDecodeError:
        print(f"Error: File '{json_file_path}' does not contain valid JSON.")
    except Exception as e:
        print(f"An error occurred: {e}")
