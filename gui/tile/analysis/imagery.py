"""build_imagery sub-component (Task 9.2 / B4 — dual-map Planet/S2/Landsat selectors).

Architecture layer: view (raw-widget builder, no ``@solara.component``). This is the
imagery slice of the OLD ``AnalysisTile`` god-class port. It drives the two
image-selector cards (``imgSelection1/2`` on ``widgets``) and their date-button slide
groups: Planet (synchronous, from the recipe-built monthly dicts) plus Sentinel-2 and
Landsat (fetched lazily off EE).

WHY A RAW BUILDER (not a Solara component + effects/use_task): MapApp renders the
Analysis dialog with ``v-if``, so it DESTROYS the dialog DOM on close and REBUILDS it
on reopen. Solara-component reactivity (``use_effect`` / ``use_task``) + ``solara.display``
content does not survive that rebuild. Like the sibling builders
(``navigator`` / ``review_panel`` / ``build_edit_tools`` / export) this is a plain
function that registers a ``.subscribe`` on the driving reactive and starts daemon
threads for off-thread work — there are no Solara hooks at all. This component renders
nothing visible (the maps + selector cards are built/displayed elsewhere), so
``build_imagery`` returns ``None``; the parent calls it purely for its side effects.

Key port decisions (FIXED by the migration contract; the plan's reference code is
unreliable):

* **Per-alert handoff via ``widgets.imagery_ctx``.** The parent's
  ``view_actual_alert`` computes the alert grid / dates / planet dicts and
  publishes a context dict to ``widgets.imagery_ctx.value`` (a solara reactive).
  This component ``.subscribe``s a single ``_on_ctx`` callback to that reactive, so
  the slide-group builds and the EE fetches only run once the parent has published a
  fresh context — which guarantees the grid/dates exist before any imagery work
  starts. When no alert is active (ctx is ``None``) ``_on_ctx`` no-ops (safe at smoke
  time). Because ``imagery_ctx`` may already hold a value when ``build_imagery`` runs
  (an alert selected before the dialog opened), ``_on_ctx`` is also invoked once at
  the end with the current value (mirroring a ``use_effect`` firing on mount).

* **EE fetches run on daemon threads.** ``getIndividualS2`` /
  ``getIndividualLandsat`` call blocking ``.getInfo()``; ``_on_ctx`` starts TWO
  independent daemon threads (one S2, one Landsat), each doing only the EE fetch in a
  ``try/except/finally`` (on failure: log + clear that source's spinner via the
  "Not available" sentinel; on success: rebuild that source's slide groups). This is
  the same task->daemon-thread conversion the sibling ``edit_tools`` / ``export``
  builders use. ipyvuetify/ipyleaflet trait sets from the worker thread are accepted
  (``recipe_tile`` / ``edit_tools`` / ``export`` do exactly this).

* **Imperative helpers ported as module-level functions** (no ``self``):
  ``image_slider_map_callback`` / ``image_slider_info_callback`` /
  ``create_horizontal_slide_group_v3``. Logic is byte-faithful to the OLD methods
  (the nested-children indexing into the selector tree, the color maps, the
  ``milis`` sort, the cloud-cover icons, the Landsat pansharpen+harmonize).

* The maps + selector cards themselves are built/displayed by
  ``AnalysisWidgets`` / the parent's left panel. This component renders nothing
  visible — it is a pure logic/effect component placed in the parent's left panel.

Props
-----
widgets : AnalysisWidgets
    Shared heavy widgets (maps, imgSelection1/2, map31/32, scratch slots, and the
    ``imagery_ctx`` reactive published by the parent).
app_state : AppState
    Navigation + alerts source of truth (unused directly; kept for contract parity).
selected_alerts_model : SetContext
    ``combined_path``: the set's combined raster, whose band 3 is the
    original-alert overlay layer.
analysis : AnalysisState
    Where a click on a date button stores the chosen image and its info list
    (``before_image`` / ``after_image``, ``before_info`` / ``after_info``).
"""

import logging
from datetime import datetime
from operator import itemgetter

import ee
import ipyvuetify as v
from pysepal import sepalwidgets as sw

from deforestation_alerts.scripts.imagery_service import (
    getIndividualLandsat,
    getIndividualS2,
)
from deforestation_alerts.scripts.mosaics_helper import (
    SFIM_pan_sharpen,
    get_sentinel2_dates,
    harmonizeL8ToS2_scaled,
)
from gui.i18n import t
from gui.scripts import raster_map
from gui.scripts.solara_threads import spawn_in_context

logger = logging.getLogger("SEPALUI.deforestation_alerts.analysis")

# Map source -> the selector card's main tab index (Planet/S2/Landsat order).
_SOURCE_TO_INDEX = {"Planet NICFI": 0, "Sentinel 2": 1, "Landsat": 2}

_COLOR_MAP = {
    "Sentinel 2": "blue",
    "Planet NICFI": "green",
    "Landsat": "blue-grey",
    "selected": "orange",
}


# ---------------------------------------------------------------------------
# Imperative helpers — ported verbatim from the OLD god-class as module-level
# functions (no ``self``); explicit args replace the former instance attributes.
# ---------------------------------------------------------------------------
def image_slider_map_callback(
    selected_item, map_element, model_att, widgets, analysis, selected_alerts_model,
    token=None,
):
    """Add the chosen image's True/False-color + original-alert layers to a map.

    Ports OLD ``AnalysisTile.image_slider_map_callback``. Reads the per-alert
    grid from ``widgets`` and the set's combined raster (``combined_path``) from
    ``selected_alerts_model`` (a ``SetContext``). Stores the chosen ee.Image in
    ``analysis`` per pane (``model_att`` 0 = before, 1 = after).

    ``token`` is the alert the image was listed for (``AnalysisState.alert_token``):
    once another alert is current, nothing is stored or drawn.
    """
    if not analysis.is_current(token):
        return
    widgets.imgSelection1.disabled = True
    widgets.imgSelection2.disabled = True
    geom = widgets.actual_alert_grid
    image_id = selected_item["image_id"]
    img_source = selected_item["source"]
    vis1p = {"min": 0, "max": 1600, "bands": ["R", "G", "B"]}
    vis2p = {"min": 0, "max": [6500, 1600, 1600], "bands": ["N", "R", "G"]}

    vis1s = {"min": 0, "max": 3000, "bands": ["B4", "B3", "B2"]}
    vis2s = {"min": 0, "max": [6500, 3000, 3000], "bands": ["B8", "B4", "B3"]}

    if img_source == "Sentinel 2":
        vis1 = vis1s
        vis2 = vis2s
        s2 = (
            ee.ImageCollection("COPERNICUS/S2_HARMONIZED")
            .filterBounds(geom)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 90))
        )
        s2_same_date = s2.filter(ee.Filter.eq("GENERATION_TIME", image_id))
        img = s2_same_date.mosaic().clip(geom)

    elif img_source == "Planet NICFI":
        vis1 = vis1p
        vis2 = vis2p
        img = ee.Image(image_id).clip(geom)

    elif img_source == "Landsat":
        vis1 = vis1s
        vis2 = vis2s

        # Filter Landsat SR
        l8 = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        l9 = ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
        landsat = l8.merge(l9)
        landsat_filtered = (
            landsat.filterBounds(geom)
            .filter(ee.Filter.lt("CLOUD_COVER", 90))
            .filter(ee.Filter.eq("LANDSAT_SCENE_ID", image_id))
        ).first()

        # Filter Landsat TOA
        l8_toa = ee.ImageCollection("LANDSAT/LC08/C02/T1_TOA")
        l9_toa = ee.ImageCollection("LANDSAT/LC09/C02/T1_TOA")
        landsat_toa = l8_toa.merge(l9_toa)
        landsat_toa_filtered = (
            landsat_toa.filterBounds(geom)
            .filter(ee.Filter.lt("CLOUD_COVER", 90))
            .filter(ee.Filter.eq("LANDSAT_SCENE_ID", image_id))
        ).first()

        # Pansharpening
        landsat_sr_img = landsat_filtered.select(
            ["SR_B1", "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"]
        )
        landsat_toa_img = landsat_toa_filtered.select("B8")
        pan_sharp = SFIM_pan_sharpen(landsat_sr_img, landsat_toa_img)
        # Harmonize
        harmonized_img = harmonizeL8ToS2_scaled(pan_sharp).clip(geom)
        img = harmonized_img

    target = {0: analysis.before_image, 1: analysis.after_image}.get(model_att)
    stored = analysis.apply_if_current(token, (lambda: target.set(img)) if target is not None else (lambda: None))
    if not stored:
        logger.info("Analysis imagery: dropped an image listed for a previous alert")
        widgets.imgSelection1.disabled = False
        widgets.imgSelection2.disabled = False
        return

    map_element.addLayer(img, vis1, "True Color", True)
    map_element.addLayer(img, vis2, "False Color", False)
    # "Original Alert": the set's combined raster, band 3 (display), through
    # localtileserver (spec §10). Starting the tile server blocks and can fail;
    # a failure must not leave the image selectors disabled.
    combined = getattr(selected_alerts_model, "combined_path", None)
    if combined is not None and combined.exists():
        try:
            raster_map.add_raster_layer(
                map_element, combined, key="original-alert", name="Original Alert",
                band=3, opacity=0.5,
                visible=False,  # off by default, as before; the map's layer control shows it
            )
        except Exception:
            logger.exception("Analysis imagery: failed to add the Original Alert layer")
    widgets.imgSelection1.disabled = False
    widgets.imgSelection2.disabled = False


def image_slider_info_callback(selected_item, info_element, model_att, widgets, analysis, token=None):
    """Update the selected-image info card + store the per-pane info list.

    Ports OLD ``AnalysisTile.image_slider_info_callback`` byte-faithfully; the
    per-pane info list lands in ``analysis.before_info`` / ``after_info``.
    Nothing changes once ``token``'s alert is no longer current.
    """
    if not analysis.is_current(token):
        return
    info_element.loading = True
    info1 = selected_item["source"]
    info2 = selected_item["milis"]
    info3 = selected_item["cloud_cover"]
    info4 = selected_item["value"]
    info5 = datetime.utcfromtimestamp(selected_item["milis"] / 1000).strftime("_%d%m%y")
    lista = [info1, info2, info3, info4, info5]
    target = {0: analysis.before_info, 1: analysis.after_info}.get(model_att)
    stored = analysis.apply_if_current(token, (lambda: target.set(lista)) if target is not None else (lambda: None))
    info_element.loading = False
    if not stored:
        return
    info_element.children = [
        v.Html(
            tag="strong",
            children=[t("analysis_tile.img_selection.selected_img_info_source")],
        ),
        info1,
        v.Html(
            tag="strong",
            children=[", " + t("analysis_tile.img_selection.selected_img_info_date")],
        ),
        datetime.utcfromtimestamp(info2 / 1000).strftime("%Y-%m-%d"),
        v.Html(
            tag="strong",
            children=[", " + t("analysis_tile.img_selection.selected_img_info_cloud")],
        ),
        info3,
    ]


def create_horizontal_slide_group_v3(
    data_list,
    map_component,
    image_slider_component,
    default_v_model,
    map_cb,
    model_att1,
    info_cb,
    model_att2,
    fire_callback,
    widgets,
    analysis,
    selected_alerts_model,
    token=None,
):
    """Build the date-button slide group inside a selector tab + wire clicks.

    Ports OLD ``AnalysisTile.create_horizontal_slide_group_v3`` byte-faithfully.
    ``map_cb`` / ``info_cb`` are the (now module-level) map/info callbacks; they are
    invoked with the extra ``widgets`` + ``analysis`` (and ``selected_alerts_model``
    for the map callback) explicit args the OLD ``self.``-bound versions carried
    implicitly.
    The nested-children indexing into ``map_component`` / ``image_slider_component``
    matches the ``AnalysisWidgets`` selector tree exactly.

    ``token`` is the alert the dates were fetched for: the buttons keep it, so
    neither the default pick nor a later click stores an image for another alert.
    """
    map_element = map_component.children[1]
    info_element = map_component.children[2].children[0]

    # Determine which tab + child tabs to use based on the first item's source.
    if data_list:
        source_type = data_list[0].get("source")
        if source_type in _SOURCE_TO_INDEX:
            index = _SOURCE_TO_INDEX[source_type]
            main_tab_index = index
            child_tabs_indices = [(index + i) % 3 for i in range(1, 3)]

            main_tab = image_slider_component.children[1].children[main_tab_index]
            child_tabs = [
                image_slider_component.children[1].children[i]
                for i in child_tabs_indices
            ]
        else:
            raise ValueError(f"Unknown source type: {source_type}")
    else:
        raise ValueError("data_list is empty")

    slide_group = main_tab.children[1].children[1]
    child_slide1_group = child_tabs[0].children[1].children[1]
    child_slide2_group = child_tabs[1].children[1].children[1]

    if data_list[0].get("value") == "Not available":
        logger.info("No images for %s", data_list[0].get("source"))
        slide_group.set_loading_state(False)
        main_tab.children[1].hide()
        main_tab.children[0].show()
        return

    # Sort data by 'milis' attribute.
    sorted_data = sorted(data_list, key=itemgetter("milis"), reverse=False)
    date_indices = {i: item for i, item in enumerate(sorted_data)}

    # Determine default v-model index.
    default_v_model = (
        len(sorted_data) - 1 if default_v_model == 1 else len(sorted_data) - 3
    )

    slide_group.defaul_child_color = _COLOR_MAP.get(
        data_list[0].get("source"), "lightgray"
    )

    def create_and_configure_slide_button(i, item):
        """Creates and configures a slide button."""
        img_source = item["source"]
        button_color = _COLOR_MAP.get(img_source, "lightgray")

        if img_source == "Planet NICFI":
            date_string = datetime.utcfromtimestamp(item["milis"] / 1000).strftime("%b")
        elif img_source != "Planet NICFI":
            date_string = datetime.utcfromtimestamp(item["milis"] / 1000).strftime(
                "%b %d"
            )

        icon = ""  # Initialize with no icon
        if img_source != "Planet NICFI" and "cloud_cover" in item:
            cloud_cover = float(item["cloud_cover"])
            if 0 <= cloud_cover <= 30:
                icon = "mdi-weather-sunny"
            elif 30 < cloud_cover <= 60:
                icon = "mdi-cloud-outline"
            elif 60 < cloud_cover <= 100:
                icon = "mdi-cloud"

        button = sw.Btn(
            text=date_string,
            gliph=icon,  # Use the icon if it's available
            color=button_color,
            class_="ma-1",
            value=i,
            style_="min-width: 40px; min-height: 40px;",
        )
        button.on_event("click", on_slide_button_click)
        return button

    def on_slide_button_click(widget, event, data):
        """Handles the click event for a slide button."""
        widget.loading = True
        widget.disabled = True

        selected_item = date_indices[widget.value]

        slide_group.reset_default_color()
        child_slide1_group.reset_default_color()
        child_slide2_group.reset_default_color()

        widget.color = _COLOR_MAP.get("selected")

        map_cb(selected_item, map_element, model_att1, widgets, analysis, selected_alerts_model, token=token)
        info_cb(selected_item, info_element, model_att2, widgets, analysis, token=token)

        widget.loading = False
        widget.disabled = False

    slides = [
        create_and_configure_slide_button(i, item)
        for i, item in enumerate(sorted_data)
    ]
    slide_group.slide_group.children = slides  # Assign directly to children

    slide_group.set_loading_state(False)
    main_tab.children[0].hide()
    main_tab.children[1].show()

    if fire_callback:
        slide_group.slide_group.children[default_v_model].color = _COLOR_MAP.get(
            "selected"
        )
        selected_item = date_indices[
            slide_group.slide_group.children[default_v_model].value
        ]
        map_cb(selected_item, map_element, model_att1, widgets, analysis, selected_alerts_model, token=token)
        info_cb(selected_item, info_element, model_att2, widgets, analysis, token=token)


def _set_all_loading(widgets):
    """Show the per-source slide-group spinners on both selector cards.

    Mirrors the OLD ``view_actual_alert`` block that called ``set_loading_state(True)``
    on every Planet/S2/Landsat slide group of both cards before the rebuilds. The
    selector-tree indexing (``children[1].children[<tab>].children[1].children[1]``)
    matches the ``AnalysisWidgets`` layout.
    """
    for selector in (widgets.imgSelection1, widgets.imgSelection2):
        for tab in range(3):
            try:
                selector.children[1].children[tab].children[1].children[
                    1
                ].set_loading_state(True)
            except Exception:
                logger.exception("Analysis imagery: failed to set loading state")


def _show_tab(selector, index):
    """Show tab ``index`` of an image-selector card: its Tabs and TabsItems."""
    selector.children[0].v_model = index
    selector.children[1].v_model = index


def _normalize_source_data(data, source):
    """Coerce an empty / missing fetch result into the "Not available" sentinel.

    ``getIndividualS2`` returns a bare ``[]`` when no scenes exist (and the Planet
    dicts are ``None`` until the recipe builds them), but
    ``create_horizontal_slide_group_v3`` needs ``data_list[0]["source"]`` to resolve
    which selector tab to clear. Without this, an empty list raised
    ``ValueError("data_list is empty")`` (swallowed upstream) and left the spinner
    stuck. Returning the source-tagged "Not available" sentinel routes empties
    through the existing branch that clears loading + shows the no-images card.
    """
    if data:
        return data
    return [
        {
            "value": "Not available",
            "source": source,
            "milis": 0,
            "cloud_cover": "Not available",
        }
    ]


def _rebuild_groups(
    data_before,
    data_after,
    default_before,
    default_after,
    widgets,
    analysis,
    selected_alerts_model,
    token=None,
):
    """Rebuild the before/after slide groups for one source.

    Wraps the two ``create_horizontal_slide_group_v3`` calls (left/right pane) in a
    try/except so a transient EE/geometry failure does not break the render.
    Does nothing once ``token``'s alert is no longer current: a fetch that ends
    after the user moved on must not list its dates, or pick its images, for
    the alert now on screen (whose own fetch rebuilds the groups).
    """
    if not analysis.is_current(token):
        logger.info("Analysis imagery: dropped the slide groups of a previous alert")
        return
    try:
        create_horizontal_slide_group_v3(
            data_before,
            widgets.map31,
            widgets.imgSelection1,
            0,
            image_slider_map_callback,
            0,
            image_slider_info_callback,
            0,
            default_before,
            widgets,
            analysis,
            selected_alerts_model,
            token=token,
        )
    except Exception:
        logger.exception("Analysis imagery: before slide group rebuild failed")
    try:
        create_horizontal_slide_group_v3(
            data_after,
            widgets.map32,
            widgets.imgSelection2,
            1,
            image_slider_map_callback,
            1,
            image_slider_info_callback,
            1,
            default_after,
            widgets,
            analysis,
            selected_alerts_model,
            token=token,
        )
    except Exception:
        logger.exception("Analysis imagery: after slide group rebuild failed")


def build_imagery(
    widgets,
    app_state,
    selected_alerts_model,
    analysis,
):
    """Imagery slice: Planet (sync) + S2/Landsat (daemon threads) slide-group drivers.

    Renders nothing visible — returns ``None``; the parent calls it purely for its
    side effects. Subscribes a single ``_on_ctx`` callback to ``widgets.imagery_ctx``.
    On a fresh ctx, ``_on_ctx``:

    * shows the slide-group spinners + builds the Planet slide groups synchronously
      from ``ctx["before/after_planet"]`` and restores the OLD auto-switch of the
      visible tab to Sentinel-2 when Planet is "Not available" (former ``_build_planet``);
    * starts a daemon thread that fetches the S2 dicts off ``ctx["grid"]`` + the four
      ``get_sentinel2_dates`` windows and, on success, rebuilds the S2 slide groups
      (former ``s2_task`` + ``_rebuild_s2``);
    * starts a daemon thread that does the same for Landsat (former ``landsat_task`` +
      ``_rebuild_landsat``).

    Each fetch thread wraps the EE call in ``try/except/finally``: on failure it logs
    and routes the empty result through the "Not available" sentinel so that source's
    spinner is cleared / the no-images card shown.

    ``_on_ctx`` no-ops while ctx is ``None`` (no alert active), so it is dormant at
    smoke time and the EE fetches never fire on mount. Because ``imagery_ctx`` may
    already hold a value when ``build_imagery`` runs, ``_on_ctx`` is also invoked once
    at the end with the current value (self-guards on ``None``).
    """

    def _token_of(ctx):
        # The alert the context was published for (``view_actual_alert`` puts
        # the token in after its ``reset_alert``).
        return ctx.get("token")

    # ------------------------------------------------------------------
    # Sentinel-2 fetch + rebuild: runs on a daemon thread (blocking .getInfo()).
    # On success rebuilds the S2 slide groups; on failure logs + routes the empty
    # result through the "Not available" sentinel so the spinner is cleared.
    # S2 fires its default callback when Planet was unavailable (show[1]).
    # ------------------------------------------------------------------
    def _fetch_and_rebuild_s2(ctx):
        before = after = None
        try:
            dates = get_sentinel2_dates(ctx["fecha1"], ctx["fecha2"])
            grid = ctx["grid"]
            before = getIndividualS2(grid, dates[0], dates[1])
            after = getIndividualS2(grid, dates[2], dates[3])
        except Exception:
            logger.exception("Analysis imagery: Sentinel-2 fetch failed")
        finally:
            show = ctx.get("show") or [True, False, False]
            _rebuild_groups(
                _normalize_source_data(before, "Sentinel 2"),
                _normalize_source_data(after, "Sentinel 2"),
                show[1],
                show[1],
                widgets,
                analysis,
                selected_alerts_model,
                token=_token_of(ctx),
            )

    # ------------------------------------------------------------------
    # Landsat fetch + rebuild: same shape as S2. Landsat never fires its default
    # callback (OLD slider_landsat_* passed False).
    # ------------------------------------------------------------------
    def _fetch_and_rebuild_landsat(ctx):
        before = after = None
        try:
            dates = get_sentinel2_dates(ctx["fecha1"], ctx["fecha2"])
            grid = ctx["grid"]
            before = getIndividualLandsat(grid, dates[0], dates[1])
            after = getIndividualLandsat(grid, dates[2], dates[3])
        except Exception:
            logger.exception("Analysis imagery: Landsat fetch failed")
        finally:
            _rebuild_groups(
                _normalize_source_data(before, "Landsat"),
                _normalize_source_data(after, "Landsat"),
                False,
                False,
                widgets,
                analysis,
                selected_alerts_model,
                token=_token_of(ctx),
            )

    # ------------------------------------------------------------------
    # Single ctx-driven callback. Runs the former _build_planet synchronously
    # (Planet slide groups are built from the recipe-built monthly dicts), then
    # starts the two independent EE-fetch daemon threads (S2 + Landsat).
    # ------------------------------------------------------------------
    def _on_ctx(ctx):
        if ctx is None:
            return

        _set_all_loading(widgets)

        before_planet = ctx.get("before_planet")
        after_planet = ctx.get("after_planet")
        show = ctx.get("show") or [True, False, False]

        # OLD auto-switch: when Planet is "Not available", flip the visible tab to
        # Sentinel-2 (children[0] is the Tabs widget, children[1] its TabsItems;
        # v_model is the active tab). The two are jslink'd, which mirrors only in
        # a live browser, so a Python-originated switch writes both.
        try:
            if before_planet and before_planet[0].get("value") == "Not available":
                _show_tab(widgets.imgSelection1, 1)
            if after_planet and after_planet[0].get("value") == "Not available":
                _show_tab(widgets.imgSelection2, 1)
        except Exception:
            logger.exception("Analysis imagery: failed to auto-switch to S2 tab")

        # When the Planet monthly dicts are absent (the recipe has not built them
        # yet), route the empties through the "Not available" branch so the Planet
        # spinner is cleared + the no-images card shown, instead of spinning forever.
        _rebuild_groups(
            _normalize_source_data(before_planet, "Planet NICFI"),
            _normalize_source_data(after_planet, "Planet NICFI"),
            show[0],
            show[0],
            widgets,
            analysis,
            selected_alerts_model,
            token=_token_of(ctx),
        )

        # EE fetches off the main thread (blocking .getInfo()). Independent
        # sources; spawn_in_context so any reactive writes reach the browser.
        spawn_in_context(_fetch_and_rebuild_s2, (ctx,))
        spawn_in_context(_fetch_and_rebuild_landsat, (ctx,))

    widgets.imagery_ctx.subscribe(_on_ctx)

    # imagery_ctx may already hold a value (an alert selected before the dialog was
    # opened); fire once on "mount" (self-guards on None).
    _on_ctx(widgets.imagery_ctx.value)

    return None
