from docx import Document
from docx.shared import Inches, Pt
import geopandas as gpd
import rasterio
from rasterio.plot import show
import matplotlib.pyplot as plt
import numpy as np
from matplotlib_scalebar.scalebar import ScaleBar
import cartopy.crs as ccrs
from cartopy.mpl.ticker import LatitudeFormatter, LongitudeFormatter
from component.scripts.mosaics_helper import *
from datetime import datetime
import cartopy.io.img_tiles as cimgt
from shapely.geometry import box
from pathlib import Path
from component.parameter import directory


def plot_tiff_with_overlay(
    tiff_path, output_path, bands=(1, 2, 3), vector_overlay=None, overlay_color="red"
):
    """
    Plot a multi-band TIFF image with map elements and an optional vector overlay.

    Parameters:
    - tiff_path (str): Path to the input TIFF image.
    - output_path (str): Path to save the output JPEG image.
    - bands (tuple): Tuple of band indices for RGB visualization (e.g., (1, 2, 3) for Red, Green, Blue).
    - vector_overlay (GeoDataFrame): Optional GeoDataFrame for overlaying vector data on the map.
    - overlay_color (str): Color for vector overlay.
    """

    # Load the TIFF image
    with rasterio.open(tiff_path) as src:
        # Read the specified bands
        img_data = src.read(bands)
        img_extent = [
            src.bounds.left,
            src.bounds.right,
            src.bounds.bottom,
            src.bounds.top,
        ]
        crs_proj = src.crs.to_string()

    # Set up the plot
    fig, ax = plt.subplots(
        figsize=(6, 6), subplot_kw={"projection": ccrs.epsg(int(src.crs.to_epsg()))}
    )
    ax.set_extent(img_extent, crs=ccrs.epsg(int(src.crs.to_epsg())))

    # Display image data
    ax.imshow(img_data.transpose(1, 2, 0), extent=img_extent, origin="upper")

    # Add gridlines and ticks
    gl = ax.gridlines(draw_labels=True, color="gray", alpha=0.5, linestyle="--")
    gl.top_labels = True
    gl.bottom_labels = True
    gl.left_labels = True
    gl.right_labels = True

    # Rotate left and right tick labels for vertical orientation
    # gl.xlabel_style = {'rotation': 0}
    # gl.ylabel_style = {'rotation': 90}

    # Add north arrow
    ax.annotate(
        "N",
        xy=(0.95, 0.95),
        xycoords="axes fraction",
        ha="center",
        va="center",
        fontsize=16,
        color="black",
        weight="bold",
        arrowprops=dict(
            facecolor="black", width=4, headwidth=8, headlength=4, shrink=0.4
        ),
    )

    # Add scale bar
    # scale_length_km = 5  # scale length in km (customize as needed)
    # scale_length_deg = scale_length_km / 111  # approximate conversion to degrees
    # x0, y0 = ax.get_xlim()[0] + scale_length_deg * 1, ax.get_ylim()[0] + scale_length_deg * 1
    # ax.plot([x0, x0 + scale_length_deg], [y0, y0], color='black', lw=3)
    # ax.text(x0 + scale_length_deg / 2, y0, f'{scale_length_km} km', ha='center', va='bottom')
    ax.add_artist(
        ScaleBar(
            4.77,
            location="lower left",
            label_loc="bottom",
            scale_loc="top",
            frameon=True,
            length_fraction=0.1,
            width_fraction=0.004,
            box_alpha=0.2,
        )
    )

    # Add vector overlay if provided
    if vector_overlay is not None and not vector_overlay.empty:
        vector_overlay.to_crs(crs_proj, inplace=True)
        vector_overlay.plot(
            ax=ax,
            edgecolor=overlay_color,
            facecolor="none",
            linewidth=1,
            transform=ccrs.epsg(int(src.crs.to_epsg())),
        )

    # Save the output as JPEG
    plt.savefig(output_path, format="jpg", bbox_inches="tight", dpi=150)
    plt.close(fig)


# Check https://coolum001.github.io/cartopylayout.html
def add_north_arrow(ax, x=0.95, y=0.95):
    """Adds a north arrow to a Matplotlib axis."""
    ax.annotate(
        "N",
        xy=(x, y),
        xytext=(x, y - 0.05),
        xycoords="axes fraction",
        arrowprops=dict(
            facecolor="black", width=4, headwidth=8, headlength=4, shrink=0.4
        ),
        ha="center",
        fontsize=10,
    )


def generate_deforestation_report_with_word_template(
    image_path, geodataframe_path, template_path, output_path, scale_bar_length=200
):
    # Load Word template
    doc = Document(template_path)
    geodataframe = gpd.read_file(geodataframe_path)

    # Extract the first row's attributes from the GeoDataFrame
    gdf_row = geodataframe.iloc[0]  # Assuming one row of relevant data
    attributes = {
        "admin1": gdf_row["admin1"],
        "admin2": gdf_row["admin2"],
        "admin3": gdf_row["admin3"],
        "detection_date1": convert_julian_to_date(gdf_row["alert_date_min"]),
        "detection_date2": convert_julian_to_date(gdf_row["alert_date_max"]),
        "confirmation_date": datetime.now().strftime("%Y-%m-%d"),
        "before_img": gdf_row["before_img"],
        "after_img": gdf_row["after_img"],
        # "alert_system_a": gdf_row["AlertSystemA"],
        "alert_system_a": "GLAD Landsat",
        # "alert_system_b": gdf_row["AlertSystemB"],
        "alert_system_b": "CCDC",
        "area_loss": "{:.2f}".format(gdf_row["area_ha"]),
    }

    # Replace placeholders in the Word document
    for para in doc.paragraphs:
        for key, value in attributes.items():
            if f"{{{key}}}" in para.text:
                para.text = para.text.replace(f"{{{key}}}", str(value))

    # Process Image 1
    folder_temp = os.path.abspath(directory.module_dir / "temp")
    img1_path_jpg = folder_temp + "/output_image1.jpg"
    plot_tiff_with_overlay(
        image_path,
        Path(img1_path_jpg),
        bands=(4, 3, 2),
        vector_overlay=None,
        overlay_color="red",
    )

    # Process Image 2
    img2_path_jpg = folder_temp + "/output_image2.jpg"
    plot_tiff_with_overlay(
        image_path,
        Path(img2_path_jpg),
        bands=(8, 7, 6),
        vector_overlay=None,
        overlay_color="red",
    )

    # Process Image 2
    img3_path_jpg = folder_temp + "/output_image3.jpg"
    plot_tiff_with_overlay(
        image_path,
        Path(img3_path_jpg),
        bands=(8, 7, 6),
        vector_overlay=geodataframe,
        overlay_color="red",
    )

    # Insert images in place of placeholders
    for para in doc.paragraphs:
        if "[Placeholder for Image 1]" in para.text:
            para.text = ""  # Clear placeholder text
            run = para.add_run()
            run.add_picture(img1_path_jpg, width=Pt(250))

        elif "[Placeholder for Image 2]" in para.text:
            para.text = ""  # Clear placeholder text
            run = para.add_run()
            run.add_picture(img2_path_jpg, width=Pt(250))

        elif "[Placeholder for Image 3]" in para.text:
            para.text = ""  # Clear placeholder text
            run = para.add_run()
            run.add_picture(img3_path_jpg, width=Pt(250))

    # Save the report as a Word document
    doc.save(output_path)
    print(f"Report generated successfully: {output_path}")
