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
rebuild. Like the sibling builders (``context`` / ``review_panel`` /
``build_edit_tools`` / ``export`` / ``timeline``) this is a plain module of
functions that register ``.subscribe`` callbacks and start daemon threads for
off-thread work -- there are no Solara hooks at all.

The alert token (``AnalysisState.alert_token``) runs through everything here:
a rebuild blocks on ``getMapId`` and the tile server, so one that lands after
the reviewer has moved on must drop its image rather than draw the previous
alert's pane over the new one. Beside it, each lane carries its own request
generation (``wire_panes``), because two picks in the SAME lane for the same
alert share the token: without it a slow first rebuild landing after the
second one draws the scene the timeline no longer says is chosen.
"""

import logging
from contextlib import nullcontext

import ee

from deforestation_alerts.scripts.mosaics_helper import (
    SFIM_pan_sharpen,
    harmonizeL8ToS2_scaled,
)
from deforestation_alerts.scripts.scene_index import SOURCE_LANDSAT, SOURCE_PLANET
from gui.i18n import t
from gui.scripts import raster_map
from gui.scripts.error_text import error_text
from gui.scripts.notify_bridge import current_notifier
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


def true_colour_vis(source) -> dict:
    """The true-colour visualization the panes draw ``source`` with."""
    return _VIS.get(source, _VIS_DEFAULT)[0]

#: per pane, its side and what a failed rebuild says. Keyed per side because
#: a reviewer whose left pane went dead has to know *which* pane failed.
_SIDE = {0: "before", 1: "after"}
_FAILED = {0: "analysis.timeline.image_failed_before",
           1: "analysis.timeline.image_failed_after"}


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
    sr_match = _merged(LANDSAT_SR_COLLECTIONS).filter(where)
    toa_match = _merged(LANDSAT_TOA_COLLECTIONS).filter(where)
    # A mosaic is a *computed* image, and a computed image carries no fixed
    # projection: ``.projection()`` on one answers the WGS84 default at one
    # degree -- 111 km per pixel. ``SFIM_pan_sharpen`` sizes its kernel from
    # both projections and ends on ``.reproject(pan.projection())``, so bare
    # mosaics pin the sharpened result to that 111 km grid: a whole alert grid
    # lands inside a single pixel and the pane draws one flat colour. (This is
    # what a row's keys becoming a *set* of granules cost us -- the single
    # scene's ``.first()`` this replaced carried the scene's own projection.)
    # So put that grid back: the surface product is 30 m and its panchromatic
    # twin is the same CRS at 15 m. Taken from the SR side alone, because the
    # TOA collection is empty on the fallback path below and ``.first()`` on an
    # empty collection is null -- and ``ee.Algorithms.If`` resolves both
    # branches, so the unused one has to stay buildable.
    sr_proj = sr_match.first().select(_SR_BANDS[0]).projection()
    sr_img = sr_match.mosaic().select(_SR_BANDS).setDefaultProjection(sr_proj)
    pan = toa_match.mosaic().select("B8").setDefaultProjection(sr_proj.atScale(15))
    # T1_TOA is not a perfect mirror of T1_L2: a scene can be in the surface
    # product and missing from the TOA one. Pan-sharpening against an empty
    # mosaic would leave a dead pane for a scene the reviewer can see on the
    # timeline, so fall back to the unsharpened SR image -- coarser, but drawn
    # (spec §9). The branch is server side, inside ``ee.Algorithms.If``,
    # because asking which it is from Python would cost a ``getInfo`` round
    # trip per pick; ``tests/test_timeline_ee.py`` is what confirms against
    # live Earth Engine that the unused branch's empty-pan graph resolves
    # instead of raising.
    sharpened = harmonizeL8ToS2_scaled(SFIM_pan_sharpen(sr_img, pan))
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


def _wanted(analysis, token, fresh) -> bool:
    """Whether a rebuild is still the one its pane is waiting for.

    Two questions, and the alert token answers only the first: the alert has
    not changed since the pick (``token``), AND this lane has not been picked
    again since (``fresh``, the lane's request generation from
    ``wire_panes``). Two picks in the same lane for the same alert share the
    token, so without the second question a slow first rebuild landing after
    the second one stores its image and redraws the pane -- the map then shows
    one scene while the timeline, the readout and the review's metadata say
    the other.
    """
    return analysis.is_current(token) and (fresh is None or bool(fresh()))


def _gated(map_element, analysis, token, fresh=None):
    """``map_element``'s layer adds, each run only while this rebuild is still
    the one its pane waits for (``PaneTarget.gated``, ``_wanted``); a plain
    stand-in (tests) adds as before."""
    gated = getattr(map_element, "gated", None)
    if gated is None:
        return nullcontext()

    def _add_if_wanted(write):
        analysis.apply_if_current(
            token, lambda: write() if (fresh is None or fresh()) else None
        )

    return gated(_add_if_wanted)


def image_slider_map_callback(
    selected_item, map_element, model_att, widgets, analysis, selected_alerts_model,
    token=None, fresh=None,
):
    """Draw the chosen image's True/False-colour + original-alert layers.

    ``selected_item`` is ``{"source": ..., "keys": [...]}``: the collection the
    timeline's pick belongs to and the granules it mosaics for this alert.
    Reads the per-alert grid from ``widgets`` and the set's combined raster
    (``combined_path``) from ``selected_alerts_model`` (a ``SetContext``).
    Stores the rebuilt ee.Image in ``analysis`` per pane (``model_att`` 0 =
    before, 1 = after).

    ``token`` is the alert the pick was published for
    (``AnalysisState.alert_token``) and ``fresh`` is the lane's request
    generation (``wire_panes``): once another alert is current, or this lane
    has been picked again, nothing is stored or drawn (``_wanted``). Blocking
    throughout (the rebuild's ``getMapId``, the tile server), so it belongs on
    a worker -- ``wire_panes`` puts it there.
    """
    if not _wanted(analysis, token, fresh):
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
    stored = []

    def _store():
        # Under ``_alert_lock``, and re-asking both questions there: the
        # publish is what the panes, a Run and an export all read.
        if not _wanted(analysis, token, fresh):
            return
        if target is not None:
            target.set(img)
        stored.append(True)

    analysis.apply_if_current(token, _store)
    if not stored:
        # Routine, not a failure: the reviewer moved on, or picked again in
        # this lane, while this was rebuilding -- and that pick's own rebuild
        # is already on its way. It stays at INFO (so it reaches no
        # destination in production) precisely so that the only thing that
        # does surface from this worker is a *failed* rebuild -- which
        # ``wire_panes`` logs at ERROR and toasts.
        logger.info("Analysis imagery: dropped an image the pane no longer waits for")
        return

    # Each add blocks (getMapId, the tile server) and then lands only while
    # this alert is still current: an alert change meanwhile has cleared the
    # views for the next one (``PaneLayers.clear("alert")``, after
    # ``reset_alert``), which must not get this alert's layers.
    with _gated(map_element, analysis, token, fresh):
        map_element.addLayer(img, vis1, "True Color", True)
        map_element.addLayer(img, vis2, "False Color", False)
        # "Original Alert": the set's combined raster, band 3 (display), through
        # localtileserver (spec §10). Starting the tile server blocks and can fail;
        # a failure must not take the pane's own layers with it.
        combined = getattr(selected_alerts_model, "combined_path", None)
        if combined is not None and combined.exists() and _wanted(analysis, token, fresh):
            try:
                raster_map.add_raster_layer(
                    map_element, combined, key="original-alert", name="Original Alert",
                    band=3, opacity=0.5,
                    visible=False,  # off by default, as before; the view's layer menu shows it
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

    Each worker body is guarded: an unhandled exception on a bare daemon
    thread reaches only ``sys.stderr``, which is the kernel stream Voila never
    shows, so a failed rebuild would leave nothing but a pane that did not
    change. ``logger.exception`` + ``current_notifier().error(...)`` is the
    same pair ``vis_sync._build`` uses for a failed visualization layer.

    Returns the two unsubscribe callables. ``build_analysis_layout`` discards
    them, which is correct for a builder that runs once per session and lives
    as long as the kernel (the house pattern; see ``vis_sync.wire``).
    """

    def _wire(pane, info_reactive, keys_reactive, map_element):
        # This lane's request generation: bumped here, on the publishing
        # thread, for every pick. The alert token cannot stand in for it --
        # two picks in the same lane for the same alert share it (``_wanted``).
        picks = {"n": 0}
        image_reactive = {0: analysis.before_image, 1: analysis.after_image}[pane]

        def _on_pick(*_):
            info = info_reactive.value
            if not info:
                return  # an alert change: ``reset_alert`` clears the info
            item = {"source": info[0], "keys": list(keys_reactive.value or ())}
            token = analysis.alert_token.value
            picks["n"] += 1
            mine = picks["n"]

            def fresh():
                return picks["n"] == mine

            # The lane is pending from here until its rebuild lands: the info
            # above is already this pick's, so leaving the previous pick's
            # ee.Image in place is a pair whose metadata and pixels are of
            # different scenes, which a Run would stage as one composite
            # (``edit_tools._images_ready`` / ``export._images_picked`` refuse
            # while it is None).
            image_reactive.set(None)

            def _work():
                # The guard is HERE, at the thread boundary, because that is
                # where the one destination this work used to have was lost:
                # the retired selectors called the draw on the ipyvuetify event
                # thread, where ipykernel logs a traceback. On a bare daemon
                # thread an unhandled exception reaches only
                # ``threading.excepthook`` -> ``sys.stderr``, which under Voila
                # nobody ever sees, and the reviewer is left with a pane that
                # simply did not change. ``logger.exception`` reaches
                # ``ServerLogHandler`` (WARNING+) and the toast reaches the
                # reviewer -- the same pair ``vis_sync._build`` uses for a
                # failed visualization layer.
                try:
                    image_slider_map_callback(item, map_element, pane, widgets, analysis,
                                              selected_alerts_model, token, fresh=fresh)
                except Exception as exc:  # noqa: BLE001 - a dead pane must not be silent
                    logger.exception("Analysis imagery: the %s image could not be drawn",
                                     _SIDE[pane])
                    current_notifier().error(t(_FAILED[pane], error=error_text(exc)))

            spawn_in_context(_work)

        return info_reactive.subscribe(_on_pick)

    return (
        _wire(0, analysis.before_info, analysis.before_keys, widgets.pane_before),
        _wire(1, analysis.after_info, analysis.after_keys, widgets.pane_after),
    )
