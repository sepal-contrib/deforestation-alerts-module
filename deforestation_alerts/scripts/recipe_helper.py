"""Recipe + alert-store persistence.

Every *user* file here (the recipe JSON, ``alert_db.parquet`` / ``alert_db.csv``)
lives in the user's SEPAL workspace, so each function takes the ``Workspace``
adapter as a parameter and builds its paths with ``workspace.results_dir(...)``.
There is no module-level workspace and no local fallback: the container's
filesystem is not the user's, and one code path has to serve both targets.

pyarrow / GeoPandas / pandas only open LOCAL files, so the readers and writers
stage through ``Workspace.scratch_file`` / ``download_to_scratch`` /
``upload_from_scratch``. ``load_gdf_from_csv`` and ``save_gdf_to_csv`` stay pure
local serializers on purpose -- their callers own the staging (``load_alert_store``
below; the export path in ``alert_export.py``).
"""

from datetime import datetime
import json
from pathlib import PurePosixPath

import geopandas as gpd
import pandas as pd
from pysepal_api.errors import NotFound
from shapely import wkt

from deforestation_alerts.scripts.alert_filter_helper import check_alert_filter_inputs


def generate_recipe_string():
    # Get the current date and time
    current_time = datetime.now()
    # Format the string as 'recipe_YYYY-MM-DD-HHMMSS'
    recipe_string = current_time.strftime("recipe_%Y-%m-%d-%H%M%S")
    return recipe_string


def create_directory(workspace, folder_name):
    """Create the recipe folder in the user's SEPAL workspace.

    Args:
        workspace (Workspace): the session's user-file adapter.
        folder_name (str): the recipe name.

    Returns:
        str: the REMOTE path of the recipe folder (what
        ``app_tile_model.recipe_folder_path`` holds from here on).

    The old shared ``<module_dir>/temp`` sibling is gone: it was container-local
    scratch, and two concurrent jobs overwrote each other in it. Callers that
    need a local working directory ask for a private one with
    ``workspace.scratch_dir()``.
    """
    return workspace.ensure_dir(folder_name)


def save_model_parameters_to_json(
    workspace,
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
        workspace (Workspace): the session's user-file adapter.
        file_name (str): REMOTE path of the recipe JSON to write.

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

    # print(a, b, c, d, e, model_parameters)

    # Save the dictionary as JSON in the user's SEPAL workspace.
    return workspace.write_json(file_name, model_parameters)


def load_parameters_from_json(
    workspace,
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

    # Read the recipe JSON out of the user's SEPAL workspace.
    model_parameters = workspace.read_json(file_name)

    aux_model.import_from_dictionary(model_parameters)
    aoi_tile.load_saved_parameters(model_parameters)
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
    analyzed_alerts_model.alerts_gdf = load_alert_store(
        workspace, app_tile_model.recipe_folder_path
    )
    analyzed_alerts_model.import_from_dictionary(model_parameters)
    app_tile_model.current_page_view = "analysis_tile"


def load_parameters_from_json_btn_state(
    workspace,
    btn,
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

    # Read the recipe JSON out of the user's SEPAL workspace.
    model_parameters = workspace.read_json(file_name)
    btn.set_loader_text("Loading AOI...")
    # btn.set_loader_percentage(10)
    aux_model.import_from_dictionary(model_parameters)
    aoi_tile.load_saved_parameters(model_parameters)
    btn.set_loader_text("Loading Alerts...")
    # btn.set_loader_percentage(30)
    aoi_tile.process_alerts_silent()
    btn.set_loader_text("Loading Filters...")
    # btn.set_loader_percentage(60)
    alert_filter_tile.load_saved_parameters(model_parameters)
    (
        alert_source,
        user_min_alert_size,
        alert_area_selection,
        alert_sorting_method,
        user_max_number_alerts,
        user_selection_polygon,
    ) = check_alert_filter_inputs(alert_filter_tile)
    (
        analyzed_alerts_model.before_planet_monthly_images,
        analyzed_alerts_model.after_planet_monthly_images,
    ) = alert_filter_tile.create_planet_images_dictionary(
        aoi_date_model.feature_collection,
        aoi_date_model.start_date,
        aoi_date_model.end_date,
    )
    btn.set_loader_text("Waiting GEE...")
    # btn.set_loader_percentage(70)
    selected_alerts_model.filtered_alert_raster = (
        alert_filter_tile.create_filtered_alert_raster(
            alert_source,
            user_min_alert_size,
            alert_area_selection,
            alert_sorting_method,
            user_max_number_alerts,
            user_selection_polygon,
        )
    )
    btn.set_loader_text("Loading Alerts DB...")
    # btn.set_loader_percentage(90)
    app_tile_model.import_from_dictionary(model_parameters)
    analyzed_alerts_model.alerts_gdf = load_alert_store(
        workspace, app_tile_model.recipe_folder_path
    )
    btn.set_loader_text("Loading last alert...")
    # btn.set_loader_percentage(100)
    analyzed_alerts_model.import_from_dictionary(model_parameters)
    app_tile_model.current_page_view = "analysis_tile"


def load_gdf_from_csv(csv_file, geometry_columns_list):
    """Decode a LOCAL WKT-encoded CSV into a GeoDataFrame.

    Deliberately local-only: pandas cannot open a remote path, so the caller
    stages the file first (``load_alert_store`` uses
    ``Workspace.download_to_scratch``).
    """
    # load encoded dataframe
    df = pd.read_csv(csv_file)
    # decode geometry columns (any WKT type: Point, Polygon, ...) into shapely
    # objects. Tolerate columns absent from older recipes (e.g. the polygon
    # `geometry` column added in Phase 3) so requesting it never KeyErrors.
    for c in geometry_columns_list:
        if c not in df.columns:
            continue
        df[c] = df[c].apply(
            lambda x: wkt.loads(x) if pd.notnull(x) and x != "" else None
        )

    # finally reconstruct geodataframe
    gdf = gpd.GeoDataFrame(df)
    return gdf


def save_gdf_to_csv(gdf, path, geometry_cols):
    """Write a gdf to a LOCAL CSV, encoding each named geometry column as WKT.

    The mirror of ``load_gdf_from_csv``: local-only, because pandas writes local
    files. Its one caller is the export path (``alert_export.py``), which owns
    staging the result into the user's workspace.
    """
    out = pd.DataFrame(gdf).copy()
    for col in geometry_cols:
        if col in out.columns:
            # Treat NaN (an unset geometry column reindexed to floats) like None,
            # so it encodes to "" rather than blowing up on ``g.wkt``.
            out[col] = out[col].apply(
                lambda g: g.wkt
                if g is not None and not (isinstance(g, float) and pd.isna(g))
                else ""
            )
    out.to_csv(path, index=False)
    return path


def backfill_geometry(gdf):
    """Ensure a polygon `geometry` column and make it the active geometry.

    Old (pre-Phase-3) recipes stored only `point`/`bounding_box`; fill the new
    polygon `geometry` from `bounding_box` when it is missing (D10 back-compat).
    """
    if "geometry" not in gdf.columns and "bounding_box" in gdf.columns:
        gdf = gdf.copy()
        gdf["geometry"] = gdf["bounding_box"]
    if "geometry" in gdf.columns:
        gdf = gpd.GeoDataFrame(gdf, geometry="geometry")
    return gdf


# Geometry columns the alerts gdf may carry. Used to coerce secondary geometry
# columns to GeoSeries before a GeoParquet write and to decode the CSV fallback.
ALERT_GEOM_COLS = ["geometry", "bounding_box", "point", "alert_polygon"]


def save_gdf_to_parquet(gdf, workspace, remote_path):
    """Write the alerts gdf to GeoParquet in the user's SEPAL workspace.

    GeoParquet supports multiple geometry columns; coerce the known secondary
    geometry columns (`bounding_box`, `point`, `alert_polygon`) to GeoSeries so
    they are written as geometry (not opaque objects). The active geometry stays
    `geometry`.

    Args:
        gdf: the alerts GeoDataFrame.
        workspace (Workspace): the session's user-file adapter.
        remote_path (str): REMOTE path of the store, e.g.
            ``workspace.results_dir(recipe, "alert_db.parquet")``.

    Returns:
        str: the remote path written.
    """
    out = gpd.GeoDataFrame(gdf).copy()
    for col in ALERT_GEOM_COLS:
        if col in out.columns and not isinstance(out[col], gpd.GeoSeries):
            # An unset geometry column (e.g. `alert_polygon` before the user draws
            # one) reindexes to NaN floats on a multi-row frame; map those to None
            # so GeoSeries accepts them as missing geometry instead of raising on
            # the float dtype.
            vals = [
                g if g is not None and not (isinstance(g, float) and pd.isna(g)) else None
                for g in out[col].tolist()
            ]
            out[col] = gpd.GeoSeries(vals, index=out.index, crs="EPSG:4326")
    if "geometry" in out.columns:
        out = out.set_geometry("geometry")
    # pyarrow writes a local file; stage it, then ship it to the user's
    # workspace and hand back the REMOTE path that is the store. The staged copy
    # is dropped immediately: this runs once per alert save, so keeping it would
    # pile up a full copy of the store in container tmp per save.
    local = workspace.scratch_file(".parquet")
    try:
        out.to_parquet(local)
        return workspace.upload_from_scratch(local, remote_path)
    finally:
        workspace.discard_scratch(local)


def load_gdf_from_parquet(workspace, remote_path):
    """Read a GeoParquet alerts store out of the user's SEPAL workspace.

    GeoPandas/pyarrow only open local files, so the store is staged first, then
    the staged copy is discarded (the gdf is fully in memory by then);
    `geometry` is made the active geometry on the way back.
    """
    local = workspace.download_to_scratch(remote_path)
    try:
        gdf = gpd.read_parquet(local)
    finally:
        workspace.discard_scratch(local)
    if "geometry" in gdf.columns:
        gdf = gdf.set_geometry("geometry")
    return gdf


def load_alert_store(workspace, recipe_folder):
    """Load the alerts gdf for a REMOTE recipe folder: parquet, then CSV.

    Returns the gdf, or None when neither store exists -- a recipe that has
    never been analyzed is the normal first case, not an error. Old recipes that
    only have `alert_db.csv` decode their WKT geometry columns and backfill the
    polygon `geometry` (D10 back-compat), exactly as the old load path did; the
    CSV is staged locally first because pandas cannot read a remote path.

    Args:
        workspace (Workspace): the session's user-file adapter.
        recipe_folder (str): REMOTE recipe folder (``recipe_folder_path``).
    """
    folder = PurePosixPath(str(recipe_folder))
    parquet = str(folder / "alert_db.parquet")
    if workspace.exists(parquet):
        return load_gdf_from_parquet(workspace, parquet)
    csv = str(folder / "alert_db.csv")
    if workspace.exists(csv):
        local = workspace.download_to_scratch(csv)
        try:
            return backfill_geometry(load_gdf_from_csv(local, ALERT_GEOM_COLS))
        finally:
            workspace.discard_scratch(local)
    return None


def update_saved_dictionary(workspace, json_file_path, key, new_value):
    """Set one key in a recipe JSON in the user's SEPAL workspace and rewrite it.

    Args:
        workspace (Workspace): the session's user-file adapter.
        json_file_path (str): REMOTE path of the recipe JSON.
        key (str): the key to set.
        new_value (any): the value to assign.

    Returns:
        None

    Every failure is swallowed (as before): this runs on alert navigation, and
    losing one ``actual_alert_id`` write must not break the UI. The old
    ``FileNotFoundError`` guard becomes ``pysepal_api.NotFound`` -- the adapter's
    equivalent for "the recipe JSON is not there".
    """
    try:
        # Read the JSON file
        data = workspace.read_json(json_file_path)

        # Ensure the content is a dictionary
        if not isinstance(data, dict):
            raise ValueError("JSON content must be a dictionary.")

        # Update the 'actual_id' key
        data[key] = new_value

        # Write the updated content back to the same file
        workspace.write_json(json_file_path, data)

        #print(f"'key' successfully updated to {new_value}.")
    except NotFound:
        print(f"Error: File '{json_file_path}' not found.")
    except json.JSONDecodeError:
        print(f"Error: File '{json_file_path}' does not contain valid JSON.")
    except Exception as e:
        print(f"An error occurred: {e}")
