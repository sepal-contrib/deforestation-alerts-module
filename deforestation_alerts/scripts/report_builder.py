import math

from docx import Document
from docx.shared import Pt
import pandas as pd
import geopandas as gpd
import rasterio
import matplotlib.pyplot as plt
from matplotlib_scalebar.scalebar import ScaleBar
import cartopy.crs as ccrs
from pyproj import Transformer
from deforestation_alerts.scripts.mosaics_helper import convert_julian_to_date
from datetime import datetime
from pathlib import Path
# The code decoding moved to alert_codes (no report libraries); kept importable from here.
from deforestation_alerts.scripts.alert_codes import format_list, get_unique_alerts, parse_formatted_string  # noqa: F401
from deforestation_alerts.scripts import local_files


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

    # Add scale bar: the extent is in CRS metres, so one data unit is one metre,
    # except Web Mercator, which stretches ground distances by 1/cos(latitude).
    dx = 1.0
    if src.crs.to_epsg() == 3857:
        _, lat = Transformer.from_crs(3857, 4326, always_xy=True).transform(
            (img_extent[0] + img_extent[1]) / 2, (img_extent[2] + img_extent[3]) / 2
        )
        dx = math.cos(math.radians(lat))
    ax.add_artist(
        ScaleBar(
            dx,
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


def _present(row, key) -> bool:
    return key in row and not pd.isna(row[key])


def row_attributes(gdf_row) -> dict:
    """The placeholders the report fills from the alert's own row (the names
    ``project.questions.REPORT_PLACEHOLDERS`` reserves)."""
    return {
        "admin1": gdf_row["admin1"],
        "admin2": gdf_row["admin2"],
        "admin3": gdf_row["admin3"],
        "detection_date1": convert_julian_to_date(gdf_row["alert_date_min"]),
        "detection_date2": convert_julian_to_date(gdf_row["alert_date_max"]),
        "confirmation_date": datetime.now().strftime("%Y-%m-%d"),
        "before_img": gdf_row["before_img"],
        "after_img": gdf_row["after_img"],
        "alert_system": gdf_row["alert_sources"],
        "area_loss": "{:.2f}".format(gdf_row["area_ha"]),
        "group_size": str(int(gdf_row["group_size"])) if _present(gdf_row, "group_size") else "1",
        "group_alerts": str(gdf_row["group_alerts"]) if _present(gdf_row, "group_alerts") else "",
    }


def _fill_placeholders(doc, attributes: dict):
    """Replace every ``{key}`` in the body paragraphs (not tables, headers or
    footers: spec D3 says so in the UI). Returns ``doc``."""
    for para in doc.paragraphs:
        for key, value in attributes.items():
            if f"{{{key}}}" in para.text:
                para.text = para.text.replace(f"{{{key}}}", str(value))
    return doc


def generate_deforestation_report_with_word_template(
    image_path,
    geodataframe_path,
    template_path,
    output_path,
    scale_bar_length=200,
    extra=None,
):
    """Render the Word report onto ``output_path`` and return that path.

    ``extra`` is ``{placeholder: value}`` for the placeholders beyond the
    alert's own (the review's ``{verdict}`` and one ``{key}`` per question);
    ``None`` values read as empty.

    Path contract: all paths are local; the report is built in a private
    scratch folder and copied onto ``output_path``. The six intermediate JPEGs
    and the ``.docx`` live in that per-call scratch folder (two concurrent
    reports never share an ``output_image1.jpg``), which is deleted before
    this function returns.
    """
    # Load Word template
    doc = Document(template_path)
    geodataframe = gpd.read_file(geodataframe_path)

    # The first row's attributes from the GeoDataFrame (one alert per file)
    attributes = row_attributes(geodataframe.iloc[0])
    attributes.update({k: "" if v is None else str(v) for k, v in (extra or {}).items()})

    # Replace placeholders in the Word document
    _fill_placeholders(doc, attributes)

    with local_files.scratch_dir() as folder_temp:
        local_doc = folder_temp / "report.docx"
        _render_report(doc, geodataframe, image_path, folder_temp, local_doc)
        local_files.copy_into(local_doc, output_path)
    return str(output_path)


def _render_report(doc, geodataframe, image_path, folder_temp, local_doc):
    """Plot the six figures into ``folder_temp``, place them in ``doc`` and
    save it as ``local_doc``."""
    folder_temp = str(folder_temp)

    # Process Image 1
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

    # Process Image 3
    img3_path_jpg = folder_temp + "/output_image3.jpg"
    plot_tiff_with_overlay(
        image_path,
        Path(img3_path_jpg),
        bands=(8, 7, 6),
        vector_overlay=geodataframe,
        overlay_color="red",
    )

    # Process Image 4
    img4_path_jpg = folder_temp + "/output_image4.jpg"
    plot_tiff_with_overlay(
        image_path,
        Path(img4_path_jpg),
        bands=(3, 2, 1),
        vector_overlay=None,
        overlay_color="red",
    )

    # Process Image 2
    img5_path_jpg = folder_temp + "/output_image5.jpg"
    plot_tiff_with_overlay(
        image_path,
        Path(img5_path_jpg),
        bands=(7, 6, 5),
        vector_overlay=None,
        overlay_color="red",
    )

    # Process Image 2
    img6_path_jpg = folder_temp + "/output_image6.jpg"
    plot_tiff_with_overlay(
        image_path,
        Path(img6_path_jpg),
        bands=(7, 6, 5),
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

        if "[Placeholder for Image 4]" in para.text:
            para.text = ""  # Clear placeholder text
            run = para.add_run()
            run.add_picture(img4_path_jpg, width=Pt(250))

        elif "[Placeholder for Image 5]" in para.text:
            para.text = ""  # Clear placeholder text
            run = para.add_run()
            run.add_picture(img5_path_jpg, width=Pt(250))

        elif "[Placeholder for Image 6]" in para.text:
            para.text = ""  # Clear placeholder text
            run = para.add_run()
            run.add_picture(img6_path_jpg, width=Pt(250))

    doc.save(str(local_doc))
