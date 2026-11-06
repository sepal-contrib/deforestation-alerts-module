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
        **_centroid(gdf_row.get("geometry")),
    }


def _centroid(geom) -> dict:
    """``centroid`` ("lat, lon"), ``centroid_lat`` and ``centroid_lon`` of the
    geometry's centroid, 6 decimals (~0.1 m); empty without a geometry. The
    alert file is EPSG:4326; a planar centroid in degrees is exact enough at alert size."""
    if geom is None or geom.is_empty:
        return {"centroid": "", "centroid_lat": "", "centroid_lon": ""}
    lat, lon = f"{geom.centroid.y:.6f}", f"{geom.centroid.x:.6f}"
    return {"centroid": f"{lat}, {lon}", "centroid_lat": lat, "centroid_lon": lon}


def _paragraphs(doc):
    """The body paragraphs, then those of every table cell (nested tables
    too). Headers and footers are left as they are."""
    def walk(tables):
        for table in tables:
            for row in table.rows:
                for cell in row.cells:
                    yield from cell.paragraphs
                    yield from walk(cell.tables)

    yield from doc.paragraphs
    yield from walk(doc.tables)


def _fill_placeholders(doc, attributes: dict):
    """Replace every ``{key}`` in the body paragraphs and table cells (not
    headers or footers). Returns ``doc``."""
    for para in _paragraphs(doc):
        for key, value in attributes.items():
            if f"{{{key}}}" in para.text:
                para.text = para.text.replace(f"{{{key}}}", str(value))
    return doc


#: The widest a figure is drawn, and the QR code; narrower in a smaller table cell.
IMAGE_WIDTH = Pt(250)
QR_WIDTH = Pt(80)
#: What a cell's own left + right margins take (Word's default is 2 x 5.4 pt).
_CELL_PADDING = Pt(14)
QR_MARKER = "[Placeholder for Centroid QR]"


def _place_picture(para, path, widest):
    """Replace the marker paragraph's text with the picture at ``path``,
    ``widest`` wide or the width of its table cell when that is narrower."""
    cell_width = getattr(para._parent, "width", None)  # a _Cell has one, the body does not
    width = min(widest, cell_width - _CELL_PADDING) if cell_width else widest
    para.text = ""
    para.add_run().add_picture(str(path), width=width)


def maps_url(lat, lon) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"


def _place_qr(doc, attributes, folder_temp):
    """Draw the centroid's Google Maps QR code where the template has
    ``QR_MARKER``; without a centroid the marker is just cleared."""
    markers = [p for p in _paragraphs(doc) if QR_MARKER in p.text]
    if not markers:
        return
    if not attributes.get("centroid_lat"):
        for para in markers:
            para.text = para.text.replace(QR_MARKER, "")
        return
    import segno

    png = Path(folder_temp) / "centroid_qr.png"
    segno.make(maps_url(attributes["centroid_lat"], attributes["centroid_lon"]), error="m").save(
        str(png), scale=10, border=2)
    for para in markers:
        _place_picture(para, png, QR_WIDTH)


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
        _place_qr(doc, attributes, folder_temp)
        _render_report(doc, geodataframe, image_path, folder_temp, local_doc)
        local_files.copy_into(local_doc, output_path)
    return str(output_path)


#: Marker number -> (bands, outline drawn): the composite is the before
#: image's B, G, R, NIR then the after image's.
_FIGURES = {
    1: ((4, 3, 2), False),  # before, false colour
    2: ((8, 7, 6), False),  # after, false colour
    3: ((8, 7, 6), True),   # after, false colour, alert outline
    4: ((3, 2, 1), False),  # before, true colour
    5: ((7, 6, 5), False),  # after, true colour
    6: ((7, 6, 5), True),   # after, true colour, alert outline
}


def _render_report(doc, geodataframe, image_path, folder_temp, local_doc):
    """Plot the six figures into ``folder_temp``, place them in ``doc`` and
    save it as ``local_doc``."""
    jpgs = {}
    for n, (bands, outline) in _FIGURES.items():
        jpgs[n] = Path(folder_temp) / f"output_image{n}.jpg"
        plot_tiff_with_overlay(image_path, jpgs[n], bands=bands,
                               vector_overlay=geodataframe if outline else None, overlay_color="red")
    for para in _paragraphs(doc):
        for n, jpg in jpgs.items():
            if f"[Placeholder for Image {n}]" in para.text:
                _place_picture(para, jpg, IMAGE_WIDTH)
                break
    doc.save(str(local_doc))
