import geopandas as gpd
import matplotlib.pyplot as plt
import rasterio
from rasterio.plot import show
from fpdf import FPDF
from PIL import Image
from datetime import datetime


def generate_report(
    tiff1_path,
    tiff2_path,
    geodataframe,
    district,
    alert_system_a,
    alert_system_b,
    detection_date,
    confirmation_date,
    area_loss,
    output_path,
    bands1=(1, 2, 3),
    bands2=(1, 2, 3),
    scale_length=100,
):
    # Initialize PDF
    pdf = FPDF()
    pdf.add_page()

    # Title
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Deforestation Alert Report", 0, 1, "C")

    # Introduction
    intro_text = (
        f"In the district {district} on {detection_date}, a deforestation alert was detected using "
        f"{alert_system_a} and {alert_system_b} alert systems. This was confirmed on {confirmation_date}, "
        f"with a total of {area_loss} hectares of tree cover loss confirmed using the provided images."
    )

    pdf.set_font("Arial", "", 12)
    pdf.multi_cell(0, 10, intro_text)

    # Load, process, and save Image 1 with bands and visualization elements
    with rasterio.open(tiff1_path) as src:
        fig, ax = plt.subplots(figsize=(8, 8))
        show((src, *bands1), ax=ax)

        # Adding GeoDataFrame overlay
        geodataframe.plot(ax=ax, facecolor="none", edgecolor="red", linewidth=1)

        # Adding map elements
        add_north_arrow(ax)
        add_scale_bar(ax, scale_length, src)

        img1_path = "/mnt/data/image1_overlay.jpg"
        plt.savefig(img1_path)
        plt.close()

    pdf.ln(10)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, "Image 1 with Map Elements:", 0, 1)
    pdf.image(img1_path, x=10, w=100)

    # Load, process, and save Image 2 with bands and visualization elements
    with rasterio.open(tiff2_path) as src:
        fig, ax = plt.subplots(figsize=(8, 8))
        show((src, *bands2), ax=ax)

        # Adding GeoDataFrame overlay
        geodataframe.plot(ax=ax, facecolor="none", edgecolor="red", linewidth=1)

        # Adding map elements
        add_north_arrow(ax)
        add_scale_bar(ax, scale_length, src)

        img2_path = "/mnt/data/image2_overlay.jpg"
        plt.savefig(img2_path)
        plt.close()

    pdf.ln(10)
    pdf.cell(0, 10, "Image 2 with Map Elements and Overlay:", 0, 1)
    pdf.image(img2_path, x=10, w=100)

    # Save the PDF
    pdf.output(output_path)
    print(f"Report generated successfully: {output_path}")


def add_north_arrow(ax, x=0.9, y=0.1):
    """Adds a north arrow to a Matplotlib axis."""
    ax.annotate(
        "N",
        xy=(x, y),
        xytext=(x, y - 0.05),
        xycoords="axes fraction",
        arrowprops=dict(facecolor="black", width=5, headwidth=15),
        ha="center",
        fontsize=12,
    )


def add_scale_bar(ax, length, src, location=(0.1, 0.1), linewidth=3):
    """Adds a scale bar to the map, where 'length' is in meters."""
    # Get the spatial resolution of the raster in meters
    scale_bar_length = length / src.res[0]  # Convert length to the raster's pixel scale
    x0, x1, y0, y1 = ax.get_extent()

    ax.plot([x0, x0 + scale_bar_length], [y0, y0], color="k", linewidth=linewidth)
    ax.text(
        x0, y0 - (y1 - y0) * 0.02, f"{length} m", va="top", ha="center", fontsize=10
    )


# Example usage:
# generate_report("image1.tiff", "image2.tiff", geodataframe, "Amazonas", "Alert System A", "Alert System B", "2023-01-01", "2023-01-15", 100, "report.pdf")


import json
import geopandas as gpd
import matplotlib.pyplot as plt
import rasterio
from rasterio.plot import show
from fpdf import FPDF
from PIL import Image


def generate_report_from_form(form_path, geodataframe):
    # Load JSON report form
    with open(form_path, "r") as f:
        form = json.load(f)

    # Extract parameters from form
    title = form["report_title"]
    district = form["district"]
    alert_system_a = form["alert_system_a"]
    alert_system_b = form["alert_system_b"]
    detection_date = form["detection_date"]
    confirmation_date = form["confirmation_date"]
    area_loss = form["area_loss"]
    scale_length = form["scale_length"]
    image1_path = form["image1"]["path"]
    image1_bands = tuple(form["image1"]["bands"])
    image2_path = form["image2"]["path"]
    image2_bands = tuple(form["image2"]["bands"])
    output_path = form["output_path"]

    # Initialize PDF
    pdf = FPDF()
    pdf.add_page()

    # Title
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, title, 0, 1, "C")

    # Introduction
    intro_text = (
        f"In the district {district} on {detection_date}, a deforestation alert was detected using "
        f"{alert_system_a} and {alert_system_b} alert systems. This was confirmed on {confirmation_date}, "
        f"with a total of {area_loss} hectares of tree cover loss confirmed using the provided images."
    )

    pdf.set_font("Arial", "", 12)
    pdf.multi_cell(0, 10, intro_text)

    # Load, process, and save Image 1 with bands and visualization elements
    with rasterio.open(image1_path) as src:
        fig, ax = plt.subplots(figsize=(8, 8))
        show((src, *image1_bands), ax=ax)

        # Adding GeoDataFrame overlay
        geodataframe.plot(ax=ax, facecolor="none", edgecolor="red", linewidth=1)

        # Adding map elements
        add_north_arrow(ax)
        add_scale_bar(ax, scale_length, src)

        img1_path = "/mnt/data/image1_overlay.jpg"
        plt.savefig(img1_path)
        plt.close()

    pdf.ln(10)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, "Image 1 with Map Elements:", 0, 1)
    pdf.image(img1_path, x=10, w=100)

    # Load, process, and save Image 2 with bands and visualization elements
    with rasterio.open(image2_path) as src:
        fig, ax = plt.subplots(figsize=(8, 8))
        show((src, *image2_bands), ax=ax)

        # Adding GeoDataFrame overlay
        geodataframe.plot(ax=ax, facecolor="none", edgecolor="red", linewidth=1)

        # Adding map elements
        add_north_arrow(ax)
        add_scale_bar(ax, scale_length, src)

        img2_path = "/mnt/data/image2_overlay.jpg"
        plt.savefig(img2_path)
        plt.close()

    pdf.ln(10)
    pdf.cell(0, 10, "Image 2 with Map Elements and Overlay:", 0, 1)
    pdf.image(img2_path, x=10, w=100)

    # Save the PDF
    pdf.output(output_path)
    print(f"Report generated successfully: {output_path}")


def add_north_arrow(ax, x=0.9, y=0.1):
    """Adds a north arrow to a Matplotlib axis."""
    ax.annotate(
        "N",
        xy=(x, y),
        xytext=(x, y - 0.05),
        xycoords="axes fraction",
        arrowprops=dict(facecolor="black", width=5, headwidth=15),
        ha="center",
        fontsize=12,
    )


def add_scale_bar(ax, length, src, location=(0.1, 0.1), linewidth=3):
    """Adds a scale bar to the map, where 'length' is in meters."""
    # Get the spatial resolution of the raster in meters
    scale_bar_length = length / src.res[0]  # Convert length to the raster's pixel scale
    x0, x1, y0, y1 = ax.get_extent()

    ax.plot([x0, x0 + scale_bar_length], [y0, y0], color="k", linewidth=linewidth)
    ax.text(
        x0, y0 - (y1 - y0) * 0.02, f"{length} m", va="top", ha="center", fontsize=10
    )


# Example usage:
# generate_report_from_form("report_form.json", geodataframe)
