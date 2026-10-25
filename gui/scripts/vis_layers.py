"""Visualization layers (spec §6.3): Earth Engine images and GeoTIFFs, on the
shared map and the Analysis maps. Builders are blocking (map ids, tile
servers): call them from a worker thread (MapLayers does).

What a builder raises ``MapLayers`` shows as the toast (``error_text``), so
it raises keyed errors and never translates on its worker (spec §9.1, §11: a
GeoTIFF in a folder that is not writable is refused with a toast naming the
folder).
"""

import hashlib
import os
import threading
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

from deforestation_alerts import gdal_env
from deforestation_alerts.engine.raster_io import ensure_overviews
from deforestation_alerts.errors import FolderNotWritable, UserFileMissing
from gui.i18n import t
from gui.scripts import raster_map, vis_classes, vis_palettes
from gui.scripts.legend_data import format_number

#: The Planet NICFI basemaps of each region: the timeline's Planet collection
#: (gui/scripts/scene_loader), shown when the Earth Engine account can read them.
NICFI_COLLECTIONS = {
    "americas": "projects/planet-nicfi/assets/basemaps/americas",
    "africa": "projects/planet-nicfi/assets/basemaps/africa",
    "asia": "projects/planet-nicfi/assets/basemaps/asia",
}
KINDS = ("gee", "file")
MAX_NAME = 24


def nicfi_region(bounds) -> str:
    """Americas west of 30°W, Asia east of 60°E, else Africa: from the AOI centroid."""
    lon = (bounds[0] + bounds[2]) / 2
    return "americas" if lon < -30 else "asia" if lon > 60 else "africa"


def ee_vis_params(layer) -> dict:
    vis = {"bands": list(layer.bands), "min": float(layer.min), "max": float(layer.max)}
    if layer.palette and len(layer.bands) == 1:
        vis["palette"] = list(layer.palette)
    return vis


def effective_style(layer) -> str:
    """The layer's drawing style: the saved one, else read from the band count as
    layers saved before styles were drawn."""
    if layer.style is not None:
        return layer.style
    return "rgb" if len(layer.bands) == 3 else "continuous"


def folder_writable(path) -> bool:
    """Whether the ``.ovr`` sidecar of ``path`` can be written next to it."""
    return os.access(Path(path).parent, os.W_OK)


def validate_name(name, project, current: Optional[str] = None) -> Optional[str]:
    """None, or the message key of the first problem with the layer's name."""
    text = str(name or "").strip()
    if not text:
        return "steps.layers.name_empty"
    if len(text) > MAX_NAME:
        return "steps.layers.name_too_long"
    if text.casefold() in {n.casefold() for n in project.vis_layers if n != current}:
        return "steps.layers.name_duplicate"
    return None


def _ramp_label(palette) -> Optional[str]:
    """The name of the colour ramp ``palette`` is (either way round), else None."""
    key, _inverted = vis_palettes.ramp_choice(palette)
    return t(f"steps.layers.ramp_{key}") if key else None


def vis_layers_summary(layer) -> str:
    """The layer's details note: its bands, or its band and colour ramp, and
    its range, or its band and class count."""
    style = effective_style(layer)
    if style == "categorical":
        band = layer.bands[0] if layer.kind == "gee" else layer.band
        count = len(vis_classes.visible_classes(layer.classes))
        return f"{t('steps.layers.summary_band', band=band)} · {t('steps.layers.summary_classes', count=count)}"
    span = f"{format_number(layer.min)}–{format_number(layer.max)}"
    if style == "rgb":
        return f"{t('steps.layers.summary_rgb', bands=', '.join(str(b) for b in layer.bands))} · {span}"
    if layer.kind == "gee":
        return f"{_ramp_label(layer.palette) or ', '.join(layer.bands)} · {span}"
    ramp = _ramp_label(layer.palette) or (layer.colormap if not layer.palette else None)
    parts = [t("steps.layers.summary_band", band=layer.band), ramp, span]
    return " · ".join(p for p in parts if p)


def categorical_vrt(path, band: int, classes) -> Path:
    """A one-band Byte VRT of ``band`` of ``path`` in the app's scratch dir that
    maps the visible classes to 1..m and every other value to 0, its nodata
    (``vis_classes.lut_text``): localtileserver then draws it with a
    ``ListedColormap`` over 1..m and leaves the rest transparent. Named by the
    file, its mtime, the band and the LUT; always rewritten, as
    ``alert_layers.states_vrt`` is (a moved file leaves a stale SourceFilename).
    """
    import rasterio

    path = Path(path).resolve()
    lut = vis_classes.lut_text(classes)
    digest = hashlib.sha1(f"{path}|{path.stat().st_mtime_ns}|{band}|{lut}".encode()).hexdigest()[:16]
    out = gdal_env.scratch_dir() / f"vis_classes_{digest}.vrt"
    with rasterio.open(path) as src:
        w, h, crs, gt = src.width, src.height, src.crs, src.transform.to_gdal()
    srs = f"<SRS>{escape(crs.to_wkt())}</SRS>" if crs is not None else ""
    xml = f"""<VRTDataset rasterXSize="{w}" rasterYSize="{h}">
  {srs}
  <GeoTransform>{", ".join(repr(float(c)) for c in gt)}</GeoTransform>
  <VRTRasterBand dataType="Byte" band="1">
    <NoDataValue>0</NoDataValue>
    <ComplexSource>
      <SourceFilename relativeToVRT="0">{escape(str(path))}</SourceFilename>
      <SourceBand>{int(band)}</SourceBand>
      <SrcRect xOff="0" yOff="0" xSize="{w}" ySize="{h}" />
      <DstRect xOff="0" yOff="0" xSize="{w}" ySize="{h}" />
      <LUT>{lut}</LUT>
    </ComplexSource>
  </VRTRasterBand>
</VRTDataset>
"""
    part = out.with_name(out.name + f".{os.getpid()}.{threading.get_ident()}.part")
    part.write_text(xml)
    os.replace(part, out)
    return out


def builder(map_, layer, key: str, *, project, aoi_bounds, shown: bool = True):
    """The blocking build of ``layer`` under ``key`` (MapLayers runs it on a
    worker). ``shown=False`` adds it unticked (the Analysis maps)."""

    def build():
        import ee
        from matplotlib import colormaps

        style = effective_style(layer)
        if layer.kind == "gee" and style == "categorical":
            src, dst, vis = vis_classes.ee_remap(layer.classes)
            if not src:  # no visible class: nothing to draw
                return
            img = ee.Image(layer.asset).select([layer.bands[0]]).remap(src, dst)  # unlisted values masked
            map_.add_ee_layer(img, vis, name=layer.name, key=key, shown=shown)
        elif layer.kind == "gee":
            img = ee.Image(layer.asset).select(list(layer.bands))
            map_.add_ee_layer(img, ee_vis_params(layer), name=layer.name, key=key, shown=shown)
        else:
            if not Path(layer.path).exists():
                raise UserFileMissing(f"file not found: {layer.path}", key="steps.layers.err_missing", path=layer.path)
            if not folder_writable(layer.path):
                folder = str(Path(layer.path).parent)
                raise FolderNotWritable(f"the folder {folder} is not writable", key="steps.layers.err_folder",
                                        folder=folder)
            ensure_overviews(layer.path, resampling="nearest")
            if style == "categorical":
                m = len(vis_classes.visible_classes(layer.classes))
                if not m:  # no visible class: nothing to draw
                    return
                # vmax at least 2: a lone class would rescale over an empty range
                raster_map.add_raster_layer(map_, categorical_vrt(layer.path, layer.band, layer.classes), key=key,
                                            name=layer.name, band=1, colormap=vis_classes.class_colormap(layer.classes),
                                            vmin=1, vmax=max(m, 2), nodata=0, visible=shown)
                return
            if style == "rgb":  # an RGB composite: three bands, no colormap
                band, colormap = list(layer.bands), None
            else:  # a ramp, or the colormap name of a layer saved before the ramps
                band = int(layer.band)
                colormap = (vis_palettes.ramp_colormap(layer.palette, layer.name) if layer.palette
                            else colormaps[layer.colormap])
            raster_map.add_raster_layer(map_, layer.path, key=key, name=layer.name, band=band, colormap=colormap,
                                        vmin=layer.min, vmax=layer.max, nodata=None, visible=shown)

    return build
