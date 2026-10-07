"""Shared heavy widgets + per-alert scratch state for the analysis tile.

Architecture layer: view-support. This is a *plain* (non-component) class built
ONCE by the parent ``AnalysisTile`` via ``solara.use_memo(..., dependencies=[])``.
It holds the imperative ipyvuetify / pysepal widgets the OLD ``AnalysisTile``
god-class kept as ``self.*`` — the two synced ``SepalMap``s, the two
``CustomDrawControl``s and the ``AoiControl``. The two 3-tab Planet/S2/Landsat
image-selector cards that stood here are retired: one shared timeline under
both panes replaces them (``gui/tile/analysis/timeline.py``).

Why a plain class and not Solara reactives: these are heavy ipyleaflet/ipyvuetify
widgets that must be created once and mutated imperatively (re-creating a map on
every Solara render would discard layers, draw state, and the data-link between
the two maps). Reacton ``rv.*`` elements are rebuilt each render and cannot be
imperatively mutated, so we use raw widgets — same rationale as the Filter and
Overview tile ports.

This object IS the inter-component contract: the future sub-components
(AlertImagery, AlertReviewPanel, AlertEditTools, AlertExport — Tasks 9.2+) receive
this ``AnalysisWidgets`` instance and read/mutate the widgets + scratch slots on
it. The foundation only wires up the widgets and the per-alert scratch slots; the
behavior that drives them lands in the later tasks.

Exposed attributes
------------------
Heavy widgets (created once, data-linked):
    * ``map_before``       — left SepalMap (was OLD ``map_31``)
    * ``map_after``        — right SepalMap (was OLD ``map_32``); center/zoom
                             two-way linked to ``map_before``
    * ``draw_before``      — CustomDrawControl on ``map_before`` (was ``draw_alerts1``)
    * ``draw_after``       — CustomDrawControl on ``map_after`` (was ``draw_alerts2``);
                             ``.data`` two-way linked to ``draw_before``
    * ``aoi_control``      — AoiControl on ``map_after``
    * ``timeline``         — the shared image timeline card; filled in by
                             ``build_analysis_layout``, which has the app and
                             analysis state this object does not
    * ``map31`` / ``map32``— the map *containers* (title + map)

Mutable per-alert scratch slots (set by ``view_actual_alert`` / future sub-cpts;
the chosen before/after images and their info lists live in ``AnalysisState``):
    * ``actual_bb``                    — ee.Feature bbox (buffer+bounds) of the alert
    * ``actual_alert_grid``            — ee covering-grid bounds for downloads
"""

import threading
from contextlib import contextmanager

import ipyvuetify as v
import solara
from ipywidgets import jslink
from pysepal import sepalwidgets as sw
from pysepal.mapping import SepalMap
from pysepal.mapping.aoi_control import AoiControl

from gui.i18n import t
from gui.widget.custom_sw import CustomDrawControl


class LockedMap(SepalMap):
    """A SepalMap whose layer list is rewritten under one per-map lock.

    ipyleaflet's add/remove are read-modify-writes of ``layers``; on the
    Analysis maps the visualization builds add layers on a worker while the
    loop clears the alert layers (``vis_sync.clear_alert_layers``, which takes
    ``layers_lock`` too), so an unlocked add landing mid-clear was dropped.
    """

    def __init__(self, *args, **kwargs):
        # Before SepalMap's own init: it adds the basemap through add_layer.
        self.layers_lock = threading.RLock()
        self._gate = threading.local()
        super().__init__(*args, **kwargs)

    @contextmanager
    def gated(self, gate):
        """Inside the block, this thread's ``add_layer`` calls run as
        ``gate(add)``, which may decline to run ``add``. The imagery worker
        passes ``AnalysisState.apply_if_current``: an ``addLayer`` finishes its
        blocking getMapId, then adds the layer only while its alert is still
        the current one (under the lock ``reset_alert`` takes)."""
        self._gate.fn = gate
        try:
            yield self
        finally:
            self._gate.fn = None

    def add_layer(self, *args, **kwargs):
        def _add():
            with self.layers_lock:
                return super(LockedMap, self).add_layer(*args, **kwargs)

        gate = getattr(getattr(self, "_gate", None), "fn", None)
        return _add() if gate is None else gate(_add)

    def remove_layer(self, *args, **kwargs):
        with self.layers_lock:
            return super().remove_layer(*args, **kwargs)

    def add(self, *args, **kwargs):
        with self.layers_lock:
            return super().add(*args, **kwargs)

    def remove(self, *args, **kwargs):
        with self.layers_lock:
            return super().remove(*args, **kwargs)


def info_button(description: str, header: str, md: str) -> v.Html:
    """Description text + an info icon that opens a Markdown popup (raw-widget
    twin of gui.widget.help.InfoButton for builders that never render via
    reacton). Like InfoButton it closes with a Close button: Escape does not
    reach a raw dialog that never takes the focus."""
    close = v.Btn(text=True, small=True, children=[t("common.close")])
    dialog = v.Dialog(
        v_model=False,
        max_width="640px",
        children=[
            v.Card(children=[
                v.CardTitle(children=[header]),
                v.CardText(children=[sw.Markdown(md)]),
                v.CardActions(style_="justify-content: flex-end;", children=[close]),
            ])
        ],
    )
    close.on_event("click", lambda *_: setattr(dialog, "v_model", False))
    btn = v.Btn(icon=True, small=True, children=[v.Icon(children=["mdi-information-outline"])])
    btn.on_event("click", lambda *_: setattr(dialog, "v_model", True))
    return v.Html(
        tag="div",
        class_="d-flex align-center px-3",
        style_="gap: 4px;",
        children=[v.Html(tag="span", style_="flex: 1 1 auto;", children=[description]), btn, dialog],
    )


class AnalysisWidgets:
    """Container for the analysis tile's shared heavy widgets + per-alert scratch.

    Built once per session by ``AnalysisTile`` via ``use_memo``. See the module
    docstring for the full attribute contract handed to the sub-components.

    Two-map sync contract: ``map_before``/``map_after`` (center + zoom) and
    ``draw_before``/``draw_after`` (data) are wired with ``ipywidgets.jslink``,
    a browser-side (frontend) link — user pans/zooms/draws on one map mirror to
    the other with no kernel round-trip, and the kernel still receives the
    normal trait-change notification afterwards so Python observers keep
    working. The cost is that jslink propagates NOTHING headless (no browser,
    no mirroring), so any Python-originated mutation of these traits MUST go
    through ``set_synced_view``/``set_synced_draw_data`` instead of writing a
    single widget directly — a one-sided write would silently diverge outside
    a live frontend.

    Parameters
    ----------
    gee_interface : GEEInterface
        Session GEE interface forwarded to both SepalMaps.
    theme_state : ThemeState, optional
        Shared app theme state forwarded to both SepalMaps so the analysis maps
        track the light/dark app theme exactly as the shared main map does.
    """

    def __init__(self, gee_interface=None, theme_state=None):
        # ------------------------------------------------------------------
        # Two maps, data-linked (center + zoom), each with the SATELLITE
        # basemap. Ports OLD ``initialize_layout`` map_31 / map_32. Both bind
        # the shared ``theme_state`` (like the main map) so they follow the
        # app's light/dark theme instead of defaulting to the standalone theme.
        # ------------------------------------------------------------------
        self.map_before = LockedMap(gee_interface=gee_interface, theme_state=theme_state)
        self.map_before.add_class("custom-map-class2")
        self.map_before.add_basemap("SATELLITE")

        self.map_after = LockedMap(gee_interface=gee_interface, theme_state=theme_state)
        self.map_after.add_class("custom-map-class2")
        self.map_after.add_basemap("SATELLITE")

        # Browser-side two-way links: user pans/zooms/draws mirror to the
        # other map client-side, with no kernel round-trip (the old
        # traitlets.link lagged a full browser->python->browser cycle per
        # event). jslink propagates NOTHING headless: Python-originated
        # mutations must use set_synced_view/set_synced_draw_data below.
        jslink((self.map_before, "center"), (self.map_after, "center"))
        jslink((self.map_before, "zoom"), (self.map_after, "zoom"))

        # ------------------------------------------------------------------
        # Map titles + selected-image info cards (mutated by Task 9.2).
        # ------------------------------------------------------------------
        map1_title = v.CardTitle(
            class_="pa-1 ma-1",
            children=[t("analysis_tile.img_selection.prev_image_title")],
        )
        map2_title = v.CardTitle(
            class_="pa-1 ma-1",
            children=[t("analysis_tile.img_selection.post_image_title")],
        )
        # The map *containers* (title + map). The per-pane "selected image"
        # info line under each map is gone with the selectors: the timeline's
        # own readout names both chosen images in one place, beside the gap
        # between them. The ``analysis-mapbox`` class (styled in
        # analysis_tile.py) marks the map container for the full-stage
        # workspace layout (the map height is set by the
        # ``.custom-map-class2`` rule in analysis_tile.py).
        self.map31 = v.Container(
            class_="pa-1 ma-1 analysis-mapbox",
            children=[map1_title, self.map_before],
        )
        self.map32 = v.Container(
            class_="pa-1 ma-1 analysis-mapbox",
            children=[map2_title, self.map_after],
        )

        # One shared timeline under both panes replaces the two per-pane
        # selectors; built by the tile, which has the app and analysis state.
        self.timeline = None

        # ------------------------------------------------------------------
        # Draw controls (data-linked) + AOI control.
        # ------------------------------------------------------------------
        self.draw_before = CustomDrawControl(self.map_before)
        self.draw_after = CustomDrawControl(self.map_after)
        jslink((self.draw_before, "data"), (self.draw_after, "data"))

        self.aoi_control = AoiControl(self.map_after)
        self.map_after.add(self.aoi_control)

        # ------------------------------------------------------------------
        # Mutable per-alert scratch slots (set by view_actual_alert / Task 9.2+).
        # ------------------------------------------------------------------
        self.actual_bb = None
        self.actual_alert_grid = None

        # ------------------------------------------------------------------
        # Per-alert imagery handoff. ``view_actual_alert`` publishes a context
        # dict here -- alert_id, token, bbox, date_min, date_max, saved_before,
        # saved_after, and nothing else, because a dead key sends the next
        # reader hunting for a consumer that does not exist -- and the shared
        # timeline renders the alert from it, against the cached scene index,
        # so no EE call.
        # ``None`` while no alert is active (dormant at smoke time).
        # ------------------------------------------------------------------
        self.imagery_ctx = solara.reactive(None)
        # The timeline's collection toggles and cloud limit, as
        # ``(frozenset(sources_on), max_cloud)``: the GIF export picks its
        # neighbouring frames under the same filter the reviewer sees (spec D6).
        # Published by ``build_timeline``; ``None`` until it is built.
        self.timeline_filter = solara.reactive(None)

    def set_synced_view(self, center, zoom):
        """Python-originated view change: write BOTH maps explicitly (the
        jslink mirror exists only in a live frontend)."""
        self.map_before.center = center
        self.map_after.center = center
        self.map_before.zoom = zoom
        self.map_after.zoom = zoom

    def set_synced_draw_data(self, features):
        """Python-originated draw change: write BOTH controls, with distinct
        list objects so later in-place edits can't alias."""
        self.draw_before.data = list(features)
        self.draw_after.data = list(features)
