"""The two map panes: the timeline's pick, rebuilt as an ee.Image and drawn.

Architecture layer: view (raw-widget builder, no ``@solara.component``).

What stood here was a pair of per-pane Planet/S2/Landsat selector cards that
fetched their own date lists off Earth Engine -- one blocking ``.getInfo()``
per date, per source, per pane, per alert -- and then rebuilt a mosaic from a
*processing* timestamp. The shared timeline
(``gui/tile/analysis/timeline.py``) answers an alert from the project-level
scene index with no round trip at all, so all of that is gone. What is left is
this module's two jobs:

* ``rebuild_image`` -- turn the granule keys of the chosen (collection, date)
  back into one ``ee.Image``, clipped to the alert's grid. The timeline worked
  out *which* granules overlap this alert locally, from the cached index, so
  there is no ``filterBounds``, no cloud filter and no date filter here: the
  choice has already been made, and re-deriving it is how the two could
  disagree.
* ``wire_panes`` -- one subscription per lane, so a pick redraws its pane.

WHY A RAW BUILDER (not a Solara component + effects/use_task): MapApp renders
the Analysis step's content with ``v-if``, so it DESTROYS the DOM on close and
REBUILDS it on reopen. Solara-component reactivity does not survive that
rebuild. Like the sibling builders (``navigator`` / ``review_panel`` /
``build_edit_tools`` / ``export`` / ``timeline``) this is a plain module of
functions that register ``.subscribe`` callbacks and start daemon threads for
off-thread work -- there are no Solara hooks at all.

The alert token (``AnalysisState.alert_token``) runs through everything here:
a rebuild blocks on ``getMapId`` and the tile server, so one that lands after
the reviewer has moved on must drop its image rather than draw the previous
alert's pane over the new one.
"""

import logging
from contextlib import nullcontext

import ee

from deforestation_alerts.scripts.mosaics_helper import (
    SFIM_pan_sharpen,
    harmonizeL8ToS2_scaled,
)
from deforestation_alerts.scripts.scene_index import SOURCE_LANDSAT, SOURCE_PLANET
from gui.scripts import raster_map
from gui.scripts.solara_threads import spawn_in_context

logger = logging.getLogger("SEPALUI.deforestation_alerts.analysis")

#: The surface-reflectance products the scene index is built from, so a
#: Landsat row's key is always one of *these* collections' ``LANDSAT_SCENE_ID``
#: (``scene_index.LANDSAT_COLLECTIONS``).
LANDSAT_SR_COLLECTIONS = ("LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2")
#: ...and the TOA products whose band 8 pan-sharpens them. Named rather than
#: inlined so the live-tier test can point the lookup at a collection that
#: cannot hold the scene, which is the only way to reach the fallback below
#: without fabricating a key the surface product does not hold either.
LANDSAT_TOA_COLLECTIONS = ("LANDSAT/LC08/C02/T1_TOA", "LANDSAT/LC09/C02/T1_TOA")

_SR_BANDS = ["SR_B1", "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"]

#: Per source, the (true colour, false colour) visualizations. Planet's bands
#: are named by colour; the harmonized S2/Landsat ones by number.
_VIS = {
    SOURCE_PLANET: (
        {"min": 0, "max": 1600, "bands": ["R", "G", "B"]},
        {"min": 0, "max": [6500, 1600, 1600], "bands": ["N", "R", "G"]},
    ),
}
_VIS_DEFAULT = (
    {"min": 0, "max": 3000, "bands": ["B4", "B3", "B2"]},
    {"min": 0, "max": [6500, 3000, 3000], "bands": ["B8", "B4", "B3"]},
)


# ---------------------------------------------------------------- the rebuild


def _merged(collection_ids):
    coll = ee.ImageCollection(collection_ids[0])
    for cid in collection_ids[1:]:
        coll = coll.merge(ee.ImageCollection(cid))
    return coll


def _landsat_pair(keys, geom):
    """One Landsat acquisition, pan-sharpened from its own TOA band 8.

    The surface-reflectance and TOA products are two assets of the same
    acquisition, which is why a Landsat row's key is its ``LANDSAT_SCENE_ID``
    rather than an asset id: the id is what finds the pair.
    """
    where = ee.Filter.inList("LANDSAT_SCENE_ID", list(keys))
    sr_img = _merged(LANDSAT_SR_COLLECTIONS).filter(where).mosaic().select(_SR_BANDS)
    toa_match = _merged(LANDSAT_TOA_COLLECTIONS).filter(where)
    # T1_TOA is not a perfect mirror of T1_L2: a scene can be in the surface
    # product and missing from the TOA one. Pan-sharpening against an empty
    # mosaic would leave a dead pane for a scene the reviewer can see on the
    # timeline, so fall back to the unsharpened SR image -- coarser, but drawn
    # (spec §9). The branch is server side, inside ``ee.Algorithms.If``,
    # because asking which it is from Python would cost a ``getInfo`` round
    # trip per pick; ``tests/test_timeline_ee.py`` is what confirms against
    # live Earth Engine that the unused branch's empty-pan graph resolves
    # instead of raising.
    sharpened = harmonizeL8ToS2_scaled(
        SFIM_pan_sharpen(sr_img, toa_match.mosaic().select("B8")))
    plain = harmonizeL8ToS2_scaled(sr_img)
    return ee.Image(
        ee.Algorithms.If(toa_match.size().gt(0), sharpened, plain)
    ).clip(geom)


def rebuild_image(source, keys, geom):
    """The chosen row as one ee.Image, clipped to the alert's grid.

    ``keys`` are the granules of that (collection, date) that overlap *this*
    alert, which the timeline worked out locally from the cached index. No
    ``filterBounds``, no cloud filter and no date filter: the choice was
    already made, and re-deriving it here is how the two could disagree.

    ``None`` when there are no keys -- a selection cleared by an alert change,
    which the caller draws nothing for.
    """
    if not keys:
        return None
    if source == SOURCE_LANDSAT:
        return _landsat_pair(keys, geom)
    if source == SOURCE_PLANET:
        # A NICFI basemap is already an AOI-wide monthly mosaic, so one key is
        # the whole image; the index never records more than one per month.
        return ee.Image(list(keys)[0]).clip(geom)
    return ee.ImageCollection([ee.Image(k) for k in keys]).mosaic().clip(geom)


# ------------------------------------------------------------------ the panes


def _gated(map_element, analysis, token):
    """``map_element``'s layer adds, each run only while ``token`` is current
    (``LockedMap.gated``); a plain map (tests) adds as before."""
    gated = getattr(map_element, "gated", None)
    if gated is None:
        return nullcontext()
    return gated(lambda add: analysis.apply_if_current(token, add))


def image_slider_map_callback(
    selected_item, map_element, model_att, widgets, analysis, selected_alerts_model,
    token=None,
):
    """Draw the chosen image's True/False-colour + original-alert layers.

    ``selected_item`` is ``{"source": ..., "keys": [...]}``: the collection the
    timeline's pick belongs to and the granules it mosaics for this alert.
    Reads the per-alert grid from ``widgets`` and the set's combined raster
    (``combined_path``) from ``selected_alerts_model`` (a ``SetContext``).
    Stores the rebuilt ee.Image in ``analysis`` per pane (``model_att`` 0 =
    before, 1 = after).

    ``token`` is the alert the pick was published for
    (``AnalysisState.alert_token``): once another alert is current, nothing is
    stored or drawn. Blocking throughout (the rebuild's ``getMapId``, the tile
    server), so it belongs on a worker -- ``wire_panes`` puts it there.
    """
    if not analysis.is_current(token):
        return
    geom = widgets.actual_alert_grid
    img_source = selected_item["source"]
    vis1, vis2 = _VIS.get(img_source, _VIS_DEFAULT)
    img = rebuild_image(img_source, selected_item.get("keys") or (), geom)
    if img is None:
        # No granules: the selection was cleared by an alert change between
        # the publish and this call. Nothing to draw, and nothing to say.
        return

    target = {0: analysis.before_image, 1: analysis.after_image}.get(model_att)
    stored = analysis.apply_if_current(token, (lambda: target.set(img)) if target is not None else (lambda: None))
    if not stored:
        logger.info("Analysis imagery: dropped an image chosen for a previous alert")
        return

    # Each add blocks (getMapId, the tile server) and then lands only while
    # this alert is still current: an alert change meanwhile has cleared the
    # maps for the next one (``vis_sync.clear_alert_layers``, after
    # ``reset_alert``), which must not get this alert's layers.
    with _gated(map_element, analysis, token):
        map_element.addLayer(img, vis1, "True Color", True)
        map_element.addLayer(img, vis2, "False Color", False)
        # "Original Alert": the set's combined raster, band 3 (display), through
        # localtileserver (spec §10). Starting the tile server blocks and can fail;
        # a failure must not take the pane's own layers with it.
        combined = getattr(selected_alerts_model, "combined_path", None)
        if combined is not None and combined.exists() and analysis.is_current(token):
            try:
                raster_map.add_raster_layer(
                    map_element, combined, key="original-alert", name="Original Alert",
                    band=3, opacity=0.5,
                    visible=False,  # off by default, as before; the map's layer control shows it
                )
            except Exception:
                logger.exception("Analysis imagery: failed to add the Original Alert layer")


def wire_panes(widgets, analysis, selected_alerts_model):
    """Redraw a pane whenever the timeline publishes a pick for its lane.

    One subscription per lane, registered once per session by
    ``build_analysis_layout``. The info list is the trigger, not the keys:
    ``timeline._assign`` sets the granule keys *first* and the info list
    second, precisely so a half-set pair is never rebuilt (and because an
    already-empty key list set to ``[]`` again notifies nobody).

    The alert token is read here, on the publishing thread at the publish --
    not inside the worker, where it would be whatever alert is current by the
    time the thread is scheduled. ``spawn_in_context`` carries the kernel
    context so the layer adds reach the browser.

    Returns the two unsubscribe callables. ``build_analysis_layout`` discards
    them, which is correct for a builder that runs once per session and lives
    as long as the kernel (the house pattern; see ``vis_sync.wire``).
    """

    def _wire(pane, info_reactive, keys_reactive, map_element):
        def _on_pick(*_):
            info = info_reactive.value
            if not info:
                return  # an alert change: ``reset_alert`` clears the info
            item = {"source": info[0], "keys": list(keys_reactive.value or ())}
            spawn_in_context(image_slider_map_callback,
                             (item, map_element, pane, widgets, analysis,
                              selected_alerts_model, analysis.alert_token.value))

        return info_reactive.subscribe(_on_pick)

    return (
        _wire(0, analysis.before_info, analysis.before_keys, widgets.map_before),
        _wire(1, analysis.after_info, analysis.after_keys, widgets.map_after),
    )
