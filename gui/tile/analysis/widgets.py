"""Shared heavy widgets + per-alert scratch state for the analysis tile.

Architecture layer: view-support. This is a *plain* (non-component) class built
ONCE by the parent ``AnalysisTile`` via ``solara.use_memo(..., dependencies=[])``.
It holds the imperative ipyvuetify / pysepal widgets the OLD ``AnalysisTile``
god-class kept as ``self.*`` — the two synced ``SepalMap``s, the two
``CustomDrawControl``s, the ``AoiControl``, and the two 3-tab Planet/S2/Landsat
image-selector cards (``imgSelection1/2``).

Why a plain class and not Solara reactives: these are heavy ipyleaflet/ipyvuetify
widgets that must be created once and mutated imperatively (re-creating a map on
every Solara render would discard layers, draw state, and the data-link between
the two maps). Reacton ``rv.*`` elements are rebuilt each render and cannot be
imperatively mutated, so we use raw widgets — same rationale as the Filter and
Overview tile ports.

This object IS the inter-component contract: the future sub-components
(AlertImagery, AlertCnnPanel, AlertEditTools, AlertExport — Tasks 9.2+) receive
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
    * ``imgSelection1``    — left 3-tab image-selector card (Planet/S2/Landsat)
    * ``imgSelection2``    — right 3-tab image-selector card
    * ``map31`` / ``map32``— the OLD map *containers* (title + map + info card);
                             ``create_horizontal_slide_group_v3`` indexes into these
    * ``selected_alert_info`` — the alert-info card body (mutated each alert)

Mutable per-alert scratch slots (set by ``view_actual_alert`` / future sub-cpts):
    * ``actual_bb``                    — ee.Feature bbox (buffer+bounds) of the alert
    * ``actual_alert_grid``            — ee covering-grid bounds for downloads
    * ``selected_img_before`` / ``_after``            — the chosen ee.Image per pane
    * ``selected_img_before_info_list`` / ``_after``  — [source, milis, cloud, value, suffix]
    * ``show_image_collection``        — [planet, s2, landsat] default-load flags
"""

import ipyvuetify as v
from traitlets import link
from pysepal import color, sepalwidgets as sw
from pysepal.mapping import SepalMap
from pysepal.mapping.aoi_control import AoiControl

from deforestation_alerts.message import cm
from gui.widget.custom_sw import CustomDrawControl, CustomSlideGroup


class SepalCard(sw.SepalWidget, v.Card):
    """SepalWidget + v.Card mix (so the image-selector sub-cards keep .show/.hide).

    Mirrors the OLD ``analysis_tile.SepalCard`` so the 3-tab selector construction
    (and ``create_horizontal_slide_group_v3``'s ``.show()/.hide()`` calls on the
    per-source cards) ports over unchanged.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


def _build_image_selector():
    """Build one 3-tab (Planet / Sentinel-2 / Landsat) image-selector card.

    Ports the OLD ``initialize_layout`` ``imgSelection1``/``imgSelection2``
    construction verbatim (the two are identical, so this is factored out). Each
    tab has a "no images" card + a hidden ``CustomSlideGroup`` card; the future
    ``create_horizontal_slide_group_v3`` (Task 9.2) indexes into this exact tree
    (``card.children[1].children[<tab>].children[1].children[1]`` is the slide
    group), so the structure must match the OLD layout precisely.
    """
    tabs = v.Tabs(
        v_model=0,
        children=[
            v.Tab(children=["Planet"]),
            v.Tab(children=["Sentinel-2"]),
            v.Tab(children=["Landsat"]),
        ],
        background_color=color.main,
        dark=True,
    )
    tab_items = v.TabsItems(
        v_model=0,
        children=[
            v.TabItem(
                children=[
                    SepalCard(
                        children=[
                            v.CardTitle(children=[source]),
                            v.CardText(
                                children=[cm.analysis_tile.img_selection.no_images_msg]
                            ),
                        ],
                        class_="ma-0",
                    ),
                    SepalCard(
                        children=[
                            v.CardTitle(children=[source]),
                            CustomSlideGroup(),
                        ]
                    ).hide(),
                ]
            )
            for source in ("Planet", "Sentinel-2", "Landsat")
        ],
    )
    link((tabs, "v_model"), (tab_items, "v_model"))
    return v.Card(class_="pa-1 ma-1", flat=True, children=[tabs, tab_items])


class AnalysisWidgets:
    """Container for the analysis tile's shared heavy widgets + per-alert scratch.

    Built once per session by ``AnalysisTile`` via ``use_memo``. See the module
    docstring for the full attribute contract handed to the sub-components.

    Parameters
    ----------
    gee_interface : GEEInterface
        Session GEE interface forwarded to both SepalMaps.
    """

    def __init__(self, gee_interface=None):
        # ------------------------------------------------------------------
        # Two maps, data-linked (center + zoom), each with the SATELLITE
        # basemap. Ports OLD ``initialize_layout`` map_31 / map_32.
        # ------------------------------------------------------------------
        self.map_before = SepalMap(gee_interface=gee_interface)
        self.map_before.add_class("custom-map-class2")
        self.map_before.add_basemap("SATELLITE")

        self.map_after = SepalMap(gee_interface=gee_interface)
        self.map_after.add_class("custom-map-class2")
        self.map_after.add_basemap("SATELLITE")

        # two-way link center + zoom so panning one pans the other
        link((self.map_before, "center"), (self.map_after, "center"))
        link((self.map_before, "zoom"), (self.map_after, "zoom"))

        # ------------------------------------------------------------------
        # Map titles + selected-image info cards (mutated by Task 9.2).
        # ------------------------------------------------------------------
        map1_title = v.CardTitle(
            class_="pa-1 ma-1",
            children=[cm.analysis_tile.img_selection.prev_image_title],
        )
        map2_title = v.CardTitle(
            class_="pa-1 ma-1",
            children=[cm.analysis_tile.img_selection.post_image_title],
        )
        selected_img_before_info = v.Html(
            tag="div",
            class_="pa-1 ma-2",
            children=[cm.analysis_tile.img_selection.selected_img_info],
        )
        selected_img_after_info = v.Html(
            tag="div",
            class_="pa-1 ma-2",
            children=[cm.analysis_tile.img_selection.selected_img_info],
        )
        imgInfo1 = v.Card(children=[selected_img_before_info])
        imgInfo2 = v.Card(children=[selected_img_after_info])

        # OLD map *containers* (title + map + info). The slide-group builder
        # indexes children[1] (map) / children[2] (info), so keep this shape.
        self.map31 = v.Container(
            class_="pa-1 ma-1", children=[map1_title, self.map_before, imgInfo1]
        )
        self.map32 = v.Container(
            class_="pa-1 ma-1", children=[map2_title, self.map_after, imgInfo2]
        )

        # ------------------------------------------------------------------
        # Two 3-tab image-selector cards.
        # ------------------------------------------------------------------
        self.imgSelection1 = _build_image_selector()
        self.imgSelection2 = _build_image_selector()

        # ------------------------------------------------------------------
        # Draw controls (data-linked) + AOI control.
        # ------------------------------------------------------------------
        self.draw_before = CustomDrawControl(self.map_before)
        self.draw_after = CustomDrawControl(self.map_after)
        link((self.draw_before, "data"), (self.draw_after, "data"))

        self.aoi_control = AoiControl(self.map_after)
        self.map_after.add(self.aoi_control)

        # ------------------------------------------------------------------
        # Alert-info card body — the always-rendered text block updated each
        # alert by ``view_actual_alert``. Its card title ("Alert info") is the
        # smoke-test anchor.
        # ------------------------------------------------------------------
        self.selected_alert_info = v.Html(
            tag="div",
            children=[
                v.Html(tag="strong", children=[cm.analysis_tile.alert_info.first_date]),
                "",
                v.Html(tag="strong", children=[cm.analysis_tile.alert_info.last_date]),
                "",
                v.Html(tag="strong", children=[cm.analysis_tile.alert_info.status]),
                "",
            ],
        )

        # ------------------------------------------------------------------
        # Mutable per-alert scratch slots (set by view_actual_alert / Task 9.2+).
        # ------------------------------------------------------------------
        self.actual_bb = None
        self.actual_alert_grid = None
        self.selected_img_before = None
        self.selected_img_after = None
        self.selected_img_before_info_list = None
        self.selected_img_after_info_list = None
        self.show_image_collection = [True, False, False]
