"""GEE imagery fetch (S2/Landsat/Planet) extracted from mosaics_helper for the
Solara use_task call sites (Task 9/8). Service layer: no solara/ipyvuetify/reacton."""

import ee

from datetime import datetime

from deforestation_alerts.scripts.mosaics_helper import (
    check_planet_collection_access,
    check_access,
    is_future_date,
    is_after_march_2025,
)


def getPlanetMonthly(geometry, date1, date2):

    # Define the no-access result dictionary
    no_images = [
        {
            "value": "Not available",
            "image_id": "Not available",
            "milis": "Not available",
            "source": "Planet NICFI",
            "cloud_cover": "Not available",
        }
    ]

    access_status = check_planet_collection_access()

    if check_access(access_status) is True:
        return no_images
    else:
        lista2 = [
            "American Samoa",
            "Arunachal Pradesh",
            "Ashmore and Cartier Islands",
            "Baker Island",
            "Bangladesh",
            "Bhutan",
            "British Indian Ocean Territory",
            "Brunei Darussalam",
            "Cambodia",
            "Christmas Island",
            "Cocos (Keeling) Islands",
            "Cook Islands",
            "Fiji",
            "French Polynesia",
            "Guam",
            "Howland Island",
            "India",
            "Indonesia",
            "Jarvis Island",
            "Johnston Atoll",
            "Kingman Reef",
            "Kiribati",
            "Lao People's Democratic Republic",
            "Malaysia",
            "Maldives",
            "Marshall Islands",
            "Micronesia (Federated States of)",
            "Myanmar",
            "Nauru",
            "Nepal",
            "Niue",
            "Northern Mariana Islands",
            "Palau",
            "Palmyra Atoll",
            "Papua New Guinea",
            "Paracel Islands",
            "Philippines",
            "Samoa",
            "Scarborough Reef",
            "Singapore",
            "Solomon Islands",
            "Spratly Islands",
            "Sri Lanka",
            "Thailand",
            "Timor-Leste",
            "Tokelau",
            "Tonga",
            "Tuvalu",
            "Vanuatu",
            "Viet Nam",
            "Wake Island",
            "Wallis and Futuna",
        ]

        # Define the Asia footprint based on countries in lista2
        footprint_asia = (
            ee.FeatureCollection("FAO/GAUL_SIMPLIFIED_500m/2015/level0")
            .filter(ee.Filter.inList("ADM0_NAME", lista2))
            .geometry()
        )

        collections = {
            "americas": "projects/planet-nicfi/assets/basemaps/americas",
            "africa": "projects/planet-nicfi/assets/basemaps/africa",
            "asia": "projects/planet-nicfi/assets/basemaps/asia",
        }
        # Initialize an empty image collection to store the merged results
        intersecting_images = ee.ImageCollection([])

        for region, status in access_status.items():
            if status == "has_access":
                try:
                    img_collection = ee.ImageCollection(collections[region])

                    # Use footprint_asia for Asia and the general geometry for others
                    if region == "asia":
                        # Check intersection with the specified geometry using ee.Algorithms.If
                        intersects_geometry = ee.Algorithms.If(
                            footprint_asia.intersects(geometry, 10),
                            img_collection,
                            ee.ImageCollection([]),
                        )
                    else:
                        # Check intersection with the specified geometry using ee.Algorithms.If
                        intersects_geometry = ee.Algorithms.If(
                            img_collection.first().geometry().intersects(geometry, 10),
                            img_collection,
                            ee.ImageCollection([]),
                        )

                    intersecting_images = intersecting_images.merge(intersects_geometry)
                except ee.EEException as e:
                    if "not found" in str(e):
                        print(
                            f"{region} collection not found or user does not have access."
                        )

        if intersecting_images.size().getInfo() == 0:
            return no_images

        else:
            selected_planet = ee.ImageCollection(intersecting_images)

            planet_filtered_collection = selected_planet.filterDate(
                date1, date2
            ).filterBounds(geometry)

            # Retrieve all image IDs with a single getInfo call
            image_ids = planet_filtered_collection.aggregate_array(
                "system:id"
            ).getInfo()
            elements = []

            if len(image_ids) > 0:
                # Process each image ID to format the name
                for image_id in image_ids:
                    # Split the image ID into parts
                    parts = image_id.split("/")
                    # Extract region and date parts
                    region_part = parts[-2].title()
                    date_part = parts[-1].split("_")[-2]
                    # Parse year and month
                    year = date_part[:4]
                    month = int(date_part[5:7])

                    date_str = date_part + "-01"
                    date_str2 = datetime.strptime(date_str, "%Y-%m-%d").strftime(
                        "%b %Y"
                    )

                    # Format the name
                    name = f"Planet Monthly {region_part} {date_str2}"
                    # Create image to display
                    planet_clip = ee.Image(image_id)  ##.clip(geometry)

                    # t1 = ee.Number(planet_clip.get('system:time_start')).getInfo()
                    t2 = ee.Number(planet_clip.get("system:time_end"))
                    t3 = ee.Date(t2).advance(-1, "days").millis().getInfo()

                    dictionary = {
                        "value": name,
                        "image_id": image_id,
                        "milis": t3,
                        "source": "Planet NICFI",
                        "cloud_cover": "Not available",
                    }
                    elements.append(dictionary)

            elif len(image_ids) == 0 and (
                is_future_date(date2) or is_after_march_2025(date2)
            ):

                last_two_imgs = selected_planet.sort("system:time_end", False).limit(2)
                image_ids_2 = last_two_imgs.aggregate_array("system:id").getInfo()
                # Process each image ID to format the name
                for image_id in image_ids_2:
                    # Split the image ID into parts
                    parts = image_id.split("/")
                    # Extract region and date parts
                    region_part = parts[-2].title()
                    date_part = parts[-1].split("_")[-2]
                    # Parse year and month
                    year = date_part[:4]
                    month = int(date_part[5:7])

                    date_str = date_part + "-01"
                    date_str2 = datetime.strptime(date_str, "%Y-%m-%d").strftime(
                        "%b %Y"
                    )

                    # Format the name
                    name = f"Planet Monthly {region_part} {date_str2}"
                    # Create image to display
                    planet_clip = ee.Image(image_id)  ##.clip(geometry)

                    # t1 = ee.Number(planet_clip.get('system:time_start')).getInfo()
                    t2 = ee.Number(planet_clip.get("system:time_end"))
                    t3 = ee.Date(t2).advance(-1, "days").millis().getInfo()

                    dictionary = {
                        "value": name,
                        "image_id": image_id,
                        "milis": t3,
                        "source": "Planet NICFI",
                        "cloud_cover": "Not available",
                    }
                    elements.append(dictionary)

    return elements


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
