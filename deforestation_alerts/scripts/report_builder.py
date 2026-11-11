import math
import re

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt
from docx.text.paragraph import Paragraph
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


def _review_date(row) -> str:
    """The date part of ``reviewed_at``; today for alerts reviewed before it existed."""
    at = row["reviewed_at"] if _present(row, "reviewed_at") else ""
    return str(at)[:10] if str(at).strip() else datetime.now().strftime("%Y-%m-%d")


def row_attributes(gdf_row) -> dict:
    """The placeholders the report fills from the alert's own row (the names
    ``project.questions.REPORT_PLACEHOLDERS`` reserves)."""
    return {
        "admin1": gdf_row["admin1"],
        "admin2": gdf_row["admin2"],
        "admin3": gdf_row["admin3"],
        "detection_date1": convert_julian_to_date(gdf_row["alert_date_min"]),
        "detection_date2": convert_julian_to_date(gdf_row["alert_date_max"]),
        "confirmation_date": _review_date(gdf_row),
        "reviewed_by": str(gdf_row["reviewed_by"]) if _present(gdf_row, "reviewed_by") else "",
        "before_img": gdf_row["before_img"],
        "after_img": gdf_row["after_img"],
        "alert_system": gdf_row["alert_sources"],
        "area_loss": "{:.2f}".format(gdf_row["area_ha"]),
        "group_size": str(int(gdf_row["group_size"])) if _present(gdf_row, "group_size") else "1",
        "group_alerts": str(gdf_row["group_alerts"]) if _present(gdf_row, "group_alerts") else "",
        **_centroid(gdf_row.get("geometry")),
        # set by the build when a pick is radar (``extra``); empty otherwise
        "before_radar": "",
        "after_radar": "",
        "image_note": "",
        "radar_summary": "",
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
    radar_path=None,
    missing=None,
):
    """Render the Word report onto ``output_path`` and return that path.

    ``extra`` is ``{placeholder: value}`` for the placeholders beyond the
    alert's own (the review's ``{verdict}`` and one ``{key}`` per question,
    and the radar ones); ``None`` values read as empty, and a key of the
    alert's own (``before_img``) is overridden.

    ``image_path`` is the optical composite (``None``: none was built) and
    ``radar_path`` the radar one: without it the template's radar block is
    removed (``_drop_radar_block``). ``missing`` is ``{marker: note}`` for the
    figure slots with no image: the note takes the picture's place.

    Path contract: all paths are local; the report is built in a private
    scratch folder and copied onto ``output_path``. The intermediate JPEGs
    and the ``.docx`` live in that per-call scratch folder (two concurrent
    reports never share an ``output_image1.jpg``), which is deleted before
    this function returns.
    """
    # Load Word template
    doc = Document(template_path)
    geodataframe = gpd.read_file(geodataframe_path, layer=0)  # the reviewed layer; ``_detected`` follows it

    # The first row's attributes from the GeoDataFrame (one alert per file)
    attributes = row_attributes(geodataframe.iloc[0])
    attributes.update({k: "" if v is None else str(v) for k, v in (extra or {}).items()})

    if not attributes["image_note"]:
        _drop_paragraphs(doc, lambda text: text.strip() == "{image_note}")
    if radar_path is None:
        _drop_radar_block(doc)

    # Replace placeholders in the Word document
    _fill_placeholders(doc, attributes)

    figures = {}
    if image_path is not None:
        figures.update({f"[Placeholder for Image {n}]": (image_path, *spec) for n, spec in _FIGURES.items()})
    if radar_path is not None:
        figures.update({f"{RADAR_MARKER} {n}]": (radar_path, *spec) for n, spec in _RADAR_FIGURES.items()})
    for marker in missing or {}:
        figures.pop(marker, None)

    with local_files.scratch_dir() as folder_temp:
        local_doc = folder_temp / "report.docx"
        _place_qr(doc, attributes, folder_temp)
        _render_report(doc, geodataframe, figures, missing or {}, folder_temp, local_doc)
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

#: The radar figures (``[Placeholder for Radar N]``), one look each: the radar
#: composite packs R = VV, G = VH, B = VV/VH ratio (``mosaics_helper.scaleS1``).
RADAR_MARKER = "[Placeholder for Radar"
_RADAR_FIGURES = {
    1: ((3, 2, 1), False),  # before
    2: ((7, 6, 5), False),  # after
    3: ((7, 6, 5), True),   # after, alert outline
}


def _drop_paragraphs(doc, test):
    """Remove the body and table-cell paragraphs whose text passes ``test``."""
    for para in list(_paragraphs(doc)):
        if test(para.text):
            parent = para._element.getparent()
            if parent.tag == qn("w:tc") and len(parent.findall(qn("w:p"))) == 1:
                para.text = ""  # a table cell must keep one paragraph
            else:
                parent.remove(para._element)


def _drop_radar_block(doc):
    """A report without radar: the default templates' radar block goes -- the
    table holding the radar markers, with the section heading just before it
    and the caption line just after it (styled as those templates style them).
    A radar marker anywhere else (a custom template) is just cleared."""
    edges = ((lambda el: el.getprevious(), "Report Section"), (lambda el: el.getnext(), "Small Print"))
    for table in list(doc.tables):
        if not any(RADAR_MARKER in p.text for row in table.rows for cell in row.cells for p in cell.paragraphs):
            continue
        tbl = table._tbl
        for neighbour, style in edges:
            el = neighbour(tbl)
            if el is not None and el.tag == qn("w:p") and Paragraph(el, table._parent).style.name == style:
                el.getparent().remove(el)
        tbl.getparent().remove(tbl)
    for para in _paragraphs(doc):
        if RADAR_MARKER in para.text or "{before_radar}" in para.text or "{after_radar}" in para.text:
            para.text = re.sub(r"\[Placeholder for Radar \d\]|\{(before|after)_radar\}", "", para.text)


def _render_report(doc, geodataframe, figures, missing, folder_temp, local_doc):
    """Plot ``figures`` (``{marker: (tif, bands, outline)}``) into
    ``folder_temp``, place each where the template has its marker, write the
    ``missing`` notes in theirs, and save ``doc`` as ``local_doc``."""
    jpgs = {}
    for i, (marker, (tif, bands, outline)) in enumerate(figures.items(), 1):
        jpgs[marker] = Path(folder_temp) / f"output_image{i}.jpg"
        plot_tiff_with_overlay(tif, jpgs[marker], bands=bands,
                               vector_overlay=geodataframe if outline else None, overlay_color="red")
    for para in _paragraphs(doc):
        for marker, jpg in jpgs.items():
            if marker in para.text:
                _place_picture(para, jpg, IMAGE_WIDTH)
                break
        else:
            for marker, note in missing.items():
                if marker in para.text:
                    para.text = para.text.replace(marker, note)
    doc.save(str(local_doc))
