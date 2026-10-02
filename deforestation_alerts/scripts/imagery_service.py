"""GEE imagery fetch (S2/Landsat/Planet) extracted from mosaics_helper for the
Solara use_task call sites (Task 9/8). Service layer: no solara/ipyvuetify/reacton."""

import ee

from datetime import datetime


def getIndividualS2(geometry, date1, date2):
    s2 = ee.ImageCollection("COPERNICUS/S2_HARMONIZED")
    s2_filtered = (
        s2.filterDate(date1, date2)
        .filterBounds(geometry)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 90))
    )

    # Retrieve all unique Generation time values
    s2_dates_list = s2_filtered.aggregate_array("GENERATION_TIME").distinct().getInfo()
    elements = []

    if len(s2_dates_list) > 0:
        # Process each unique Generation time to get the mosaic

        for s2_date in s2_dates_list:
            # Create image to display
            s2_same_date = s2_filtered.filter(ee.Filter.eq("GENERATION_TIME", s2_date))
            # s2_same_date_clip = s2_same_date.mosaic().clip(geometry)

            # Calculate properties of images
            mean_cloud = (
                s2_same_date.aggregate_mean("CLOUDY_PIXEL_PERCENTAGE")
                .format("%.2f")
                .getInfo()
            )
            date = datetime.fromtimestamp(s2_date / 1000).strftime("%Y-%m-%d")
            name = f"Sentinel 2 {date} "

            dictionary = {
                "value": name,
                "image_id": s2_date,
                "milis": s2_date,
                "source": "Sentinel 2",
                "cloud_cover": mean_cloud,
            }
            elements.append(dictionary)

    return elements


def getIndividualLandsat(geometry, date1, date2):
    l8 = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
    l9 = ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
    landsat = l8.merge(l9)
    landsat_filtered = (
        landsat.filterDate(date1, date2)
        .filterBounds(geometry)
        .filter(ee.Filter.lt("CLOUD_COVER", 90))
    )

    # Retrieve all unique Generation time values
    landsat_dates_list = (
        landsat_filtered.aggregate_array("DATE_PRODUCT_GENERATED").distinct().getInfo()
    )
    elements = []

    if len(landsat_dates_list) > 0:
        # Process each unique Generation time to get the mosaic

        for landsat_date in landsat_dates_list:
            # Create image to display
            landsat_img = landsat_filtered.filter(
                ee.Filter.eq("DATE_PRODUCT_GENERATED", landsat_date)
            ).first()

            # Calculate properties of images
            cloud_cover = (
                ee.Number(landsat_img.get("CLOUD_COVER")).format("%.2f").getInfo()
            )
            scene_id = landsat_img.get("LANDSAT_SCENE_ID")
            date = datetime.fromtimestamp(landsat_date / 1000).strftime("%Y-%m-%d")
            name = f"Landsat {date} "

            dictionary = {
                "value": name,
                "image_id": scene_id,
                "milis": ee.Number(landsat_date).getInfo(),
                "source": "Landsat",
                "cloud_cover": cloud_cover,
            }
            elements.append(dictionary)

    elif len(landsat_dates_list) == 0:
        # Define the no-access result dictionary
        elements = [
            {
                "value": "Not available",
                "image_id": "Not available",
                "milis": "Not available",
                "source": "Planet NICFI",
                "cloud_cover": "Not available",
            }
        ]

    return elements
