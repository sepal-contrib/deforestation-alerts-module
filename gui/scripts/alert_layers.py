"""The alert sets drawn on the shared map: the active set's tiles, the earlier
sets' lightened tiles, and the centroid fallback for either (spec §9.2, §9.4).

Architecture layer: script. Solara-free, and -- unlike the panel view these
builds used to live in -- **no build reads a view flag**. An alert layer is
switched on in Integrate & vectorize once a run finishes and stays on until
the user switches it off or the project changes (``MapLayers`` ruling #10),
so "is the view still open" is not a question a build may ask.

Nothing is left behind by a build that races a switch-off: ``MapLayers._build``
compares the generation it was handed against the key's current one and drops
the layer it just added when they differ, and both ``hide`` and ``show`` bump
that generation.

The tiles are built from the set's store on disk (``set_store.ensure_pmtiles``),
never from the alerts a session holds. When tippecanoe is missing, or the
tiles cannot be built or served, the alerts fall back to a GeoJSON layer of
centroid points under the same key, drawing the same status colours (spec
§9.4). While the project is being written (an Integrate re-running the set, a
review Save), stale tiles are not rebuilt: the centroids stand in and the
build is asked for again once the writer is done, which is what ``Deferred``
and ``deferred_keys`` record.
"""

import logging
import os
import threading
from pathlib import Path
from xml.sax.saxutils import escape

import rasterio
from ipyleaflet import GeoJSON

from deforestation_alerts import gdal_env
from deforestation_alerts.engine.fingerprint import combined_key, earlier_sets
from deforestation_alerts.errors import UserFileMissing
from deforestation_alerts.project import paths
from gui.i18n import t
from gui.scripts import set_store
from gui.scripts.error_text import error_text
from gui.scripts import raster_map
from gui.scripts.legend_specs import state_colormap, status_colors
from gui.scripts.pmtiles_map import add_set_pmtiles
from gui.scripts.source_layers import earlier_set_name
from gui.store.workflow_steps import active_set

#: MapLayers key of the active set's tiles (or their centroid fallback)
SET_LAYER_KEY = "alerts:set"
#: MapLayers key of the active set's not-analysable pixels (combined band 4)
STATES_LAYER_KEY = "alerts:states"
#: map key of the click popup
POPUP_KEY = "alerts:popup"
#: MapLayers key prefix of the earlier sets' layers (spec §4.3)
EARLIER_PREFIX = "alerts:set:"

logger = logging.getLogger("SEPALUI.deforestation_alerts.alert_layers")


class Deferred:
    """A one-slot flag shared between a build and the effect that retries it.

    The same ``.current`` shape as a Solara ref, so the hook that owns it can
    keep one in a ``use_memo`` -- but nothing here depends on solara, which is
    what lets the builds be tested without a render harness.
    """

    __slots__ = ("current",)

    def __init__(self, current: bool = False):
        self.current = current


def combined_artifact(project, alert_set):
    """The set's combined raster record, or None."""
    if project is None or alert_set is None:
        return None
    return project.artifacts.get(combined_key(alert_set.id))


def has_states(project, alert_set) -> bool:
    """True when the set's combined raster has the pixel-state band (a set
    integrated before band 4 existed does not)."""
    art = combined_artifact(project, alert_set)
    return art is not None and bool((art.stats or {}).get("states"))


def states_vrt(combined, content_id: str) -> Path:
    """A one-band VRT of the combined raster's band 4 in the app's scratch dir,
    named by the artifact's ``content_id`` so a re-run gets a new file.

    The pixel states 0 (none) and 1 (analysable) map to 0, which is the VRT's
    nodata, so a tile server leaves them transparent; 2 and 3 pass through.
    This goes through a VRT because the combined file's own header says
    ``nodata=0``: overriding it with ``nodata=1`` would let 0 clamp to the
    lowest colour, and a colormap's alpha is ignored by localtileserver.
    """
    combined = Path(combined).resolve()
    out = gdal_env.scratch_dir() / f"states_{content_id}.vrt"
    # always rewritten: a moved project folder leaves a stale SourceFilename
    with rasterio.open(combined) as src:
        w, h, crs, gt = src.width, src.height, src.crs, src.transform.to_gdal()
        dtype = {"int32": "Int32", "uint8": "Byte", "int16": "Int16", "uint16": "UInt16"}.get(src.dtypes[3], "Int32")
    srs = f"<SRS>{escape(crs.to_wkt())}</SRS>" if crs is not None else ""
    xml = f"""<VRTDataset rasterXSize="{w}" rasterYSize="{h}">
  {srs}
  <GeoTransform>{", ".join(repr(float(c)) for c in gt)}</GeoTransform>
  <VRTRasterBand dataType="{dtype}" band="1">
    <NoDataValue>0</NoDataValue>
    <ComplexSource>
      <SourceFilename relativeToVRT="0">{escape(str(combined))}</SourceFilename>
      <SourceBand>4</SourceBand>
      <SrcRect xOff="0" yOff="0" xSize="{w}" ySize="{h}" />
      <DstRect xOff="0" yOff="0" xSize="{w}" ySize="{h}" />
      <LUT>0:0,1:0,2:2,3:3</LUT>
    </ComplexSource>
  </VRTRasterBand>
</VRTDataset>
"""
    part = out.with_name(out.name + f".{os.getpid()}.{threading.get_ident()}.part")
    part.write_text(xml)
    os.replace(part, out)
    return out


def states_build(map_, *, project, alert_set) -> bool:
    """Draw the set's too-small / over-cap pixels under ``STATES_LAYER_KEY``
    from a VRT of band 4 of the combined raster (see ``states_vrt``). Adds
    nothing and returns False for a set without the band."""
    if not has_states(project, alert_set):
        return False
    combined = paths.resolve(paths.project_dir(project.name), alert_set.combined_rel_path)
    vrt = states_vrt(combined, combined_artifact(project, alert_set).content_id)
    raster_map.add_raster_layer(map_, vrt, key=STATES_LAYER_KEY, name=t("legend.states_title"), band=1,
                                colormap=state_colormap(), vmin=2, vmax=3, nodata=0)
    return True


def earlier_key(alert_set) -> str:
    """The MapLayers key an earlier set is drawn under."""
    return f"{EARLIER_PREFIX}{alert_set.id}"


def earlier_of(project, alert_set) -> list:
    """The sets whose footprints ``alert_set`` subtracts (periods that start
    before its own), newest first: what "Show earlier sets" draws."""
    period = project.periods.get(alert_set.period_id) if alert_set is not None else None
    if period is None:
        return []

    def _key(e):
        return (project.periods[e.period_id].start, e.created_at)

    return sorted(earlier_sets(project, period), key=_key, reverse=True)


def centroid_style_for(faded: bool):
    colors = status_colors(faded)

    def _style(feature):
        color = colors.get(feature["properties"].get("status_code"), colors[0])
        return {"color": color, "fillColor": color}

    return _style


#: The centroid layer's own style. It must be non-empty and carry no colour:
#: ipyleaflet's GeoJSON view draws each feature with
#: ``{...feature.properties.style, ...layer.style}``, so the layer's style wins,
#: and ``SepalMap.add_layer`` gives a layer with an empty style pysepal's
#: default (hollow, weight 2, the theme's primary colour), which would paint
#: every point alike. The per-status colours come from ``centroid_style_for``.
CENTROID_LAYER_STYLE = {"stroke": True, "weight": 1, "opacity": 1, "fill": True, "fillOpacity": 0.9}


def add_centroid_layer(map_, alerts, name: str, key: str = SET_LAYER_KEY, faded: bool = False) -> None:
    """Draw ``alerts`` as centroid points coloured by status under ``key``
    (lightened for an earlier set): the fallback when the set's tiles cannot
    be shown (spec §9.2, §12)."""
    layer = GeoJSON(
        data=set_store.centroid_geojson(alerts),
        point_style={"radius": 4},
        style=CENTROID_LAYER_STYLE,
        style_callback=centroid_style_for(faded),
        name=name,
    )
    map_.add_layer(layer, key=key)


def active_build(map_, *, project, alert_set, alerts, notifier, warned, deferred):
    """The ``MapLayers`` build for the active set's layer.

    ``warned`` is the set of ``(project name, set id)`` pairs already told that
    tippecanoe is missing -- a review Save re-publishes the alerts and redraws
    the centroids, and saying so twice is noise. ``deferred`` is the flag the
    retry effect watches.
    """
    name = t("steps.integrate.layer_name")

    def _defer():
        # Called by ensure_pmtiles right before it raises StoreBusy, while its
        # own writer mark is still held: releasing that mark is what re-runs
        # the retry effect, and it happens before the exception reaches the
        # except below, which would note the retry too late.
        deferred.current = True

    def build():
        deferred.current = False
        try:
            pm = set_store.ensure_pmtiles(project, alert_set, on_wait=_defer)
            once = (project.name, alert_set.id)
            warning = None if pm is not None or once in warned else t("steps.integrate.no_tippecanoe")
            if pm is None:
                warned.add(once)
        except set_store.StoreBusy as exc:
            # Stale tiles while the project is being written (an Integrate
            # swapping this set's store in, a review Save), or a store
            # rewritten under the build: the centroids stand in, silently,
            # until the writer is done.
            logger.info("tiles of set %s not rebuilt now: %s", alert_set.id, exc)
            deferred.current = True  # already noted by _defer when a mark was involved
            pm, warning = None, None
        except Exception as exc:  # noqa: BLE001 - the centroids stand in for the tiles
            logger.exception("could not build the tiles of set %s", alert_set.id)
            pm, warning = None, t("steps.integrate.tiles_failed", error=error_text(exc))
        if pm is not None:
            try:
                add_set_pmtiles(map_, pm, key=SET_LAYER_KEY, name=name)
                return
            except Exception as exc:  # noqa: BLE001 - the centroids stand in for the tiles
                logger.exception("could not show the tiles of set %s", alert_set.id)
                warning = t("steps.integrate.tiles_failed", error=error_text(exc))
        if warning is not None and notifier is not None:
            notifier.warning(warning)
        add_centroid_layer(map_, alerts, name)

    return build


def earlier_build(map_, *, project, alert_set, loaded, deferred_keys):
    """The ``MapLayers`` build for one earlier set's lightened layer.

    It loads that set's alerts into ``loaded`` -- keyed by (project name, set
    id, fingerprint), so the alerts of another project (set ids repeat across
    projects) or of a rebuilt set are never hit-tested -- then draws its tiles,
    or its centroids when the tiles cannot be built. Silently: the active set's
    build has already warned about the same missing tippecanoe.
    """
    key = earlier_key(alert_set)
    name = earlier_set_name(alert_set)

    def _defer():
        # As the active set's _defer: noted while the writer mark that blocked
        # the build is still held, before its release re-runs the retry effect.
        deferred_keys.add(key)

    def build():
        deferred_keys.discard(key)
        alerts = set_store.load_set_alerts(project, alert_set)
        if alerts is None:
            raise UserFileMissing(f"earlier set {name}: its store is missing",
                                  key="steps.integrate.earlier_missing", set=name)
        loaded[(project.name, alert_set.id, alert_set.fingerprint)] = alerts
        pm = None
        if len(alerts):  # spec §9.2: an empty set has no tiles
            try:
                pm = set_store.ensure_pmtiles(project, alert_set, on_wait=_defer)
            except set_store.StoreBusy as exc:
                # The project is being written (an Integrate, a Save, a sibling
                # set's tile build): the points stand in, silently, and the
                # tiles are built once the writer is done.
                logger.info("tiles of earlier set %s not rebuilt now: %s", alert_set.id, exc)
                _defer()
            except Exception:  # noqa: BLE001 - the points stand in for the tiles
                logger.exception("could not build the tiles of earlier set %s", alert_set.id)
        if pm is not None:
            add_set_pmtiles(map_, pm, key=key, name=name, faded=True)
        else:
            add_centroid_layer(map_, alerts, name, key=key, faded=True)

    return build


def hit_in(project, current, lon, lat, *, loaded, is_shown):
    """(set, alerts, row position) of the alert under the click, or None: the
    active set's alerts first, then the earlier sets shown, newest first.

    ``current`` may be None (the published alerts are still another set's) and
    the project may have no active set at all; either way the earlier sets are
    still searched, and a click that hits nothing returns None quietly.
    """
    if project is None:
        return None
    s_now = active_set(project)
    i = set_store.hit_alert(current, lon, lat) if current is not None else None
    if s_now is not None and i is not None:
        return s_now, current, i
    for e in earlier_of(project, s_now):
        alerts = loaded.get((project.name, e.id, e.fingerprint))
        if alerts is None or not is_shown(earlier_key(e)):
            continue
        i = set_store.hit_alert(alerts, lon, lat)
        if i is not None:
            return e, alerts, i
    return None
