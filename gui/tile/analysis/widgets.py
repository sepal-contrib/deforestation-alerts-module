"""Shared heavy widgets + per-alert scratch state for the analysis tile.

Architecture layer: view-support. This is a *plain* (non-component) class built
ONCE by ``build_analysis_layout``. It holds the imperative widgets the OLD
``AnalysisTile`` god-class kept as ``self.*``. The two synced ``SepalMap``s,
their draw controls and the ``AoiControl`` that stood here are gone: both
views are now ONE browser component, ``AlertViews`` (``leaflet-multiview``),
whose sync, layer menus and polygon editing run in the browser. The two 3-tab
Planet/S2/Landsat image-selector cards are retired too: one shared timeline
under both views replaces them (``gui/tile/analysis/timeline.py``).

Why a plain class and not Solara reactives: these are heavy widgets that must
be created once and mutated imperatively (re-creating the views on every
Solara render would discard their layers, the drawing and the view). Reacton
``rv.*`` elements are rebuilt each render and cannot be imperatively mutated,
so we use raw widgets -- same rationale as the Filter and Overview tile ports.

This object IS the inter-component contract: the sub-builders (imagery,
review panel, edit tools, export) receive this ``AnalysisWidgets`` instance
and read/mutate the widgets + scratch slots on it.

Exposed attributes
------------------
Heavy widgets (created once):
    * ``panes_widget``  -- the ``AlertViews`` (class ``analysis-views``): the
                           before and after views, side by side
    * ``panes``         -- ``PaneLayers``, the only writer of ``panes_widget``:
                           layers, view, zoom-to-alert bounds, edit mode and
                           the drawing
    * ``pane_before`` / ``pane_after`` -- ``PaneTarget``s (group ``"alert"``)
                           the imagery builders add their layers to
    * ``map_title_before`` / ``map_title_after`` -- the view titles, filled
                           by the timeline
    * ``head_before`` / ``head_after`` -- one header cell per view (class
                           ``analysis-mapbox``), each holding its title
    * ``timeline``      -- the shared image timeline card; filled in by
                           ``build_analysis_layout``, which has the app and
                           analysis state this object does not

Mutable per-alert scratch slots (set by ``view_actual_alert``; the chosen
before/after images and their info lists live in ``AnalysisState``):
    * ``actual_bb``                    -- ee.Feature bbox (buffer+bounds) of the alert
    * ``actual_alert_grid``            -- ee covering-grid bounds for downloads
"""

import ipyvuetify as v
import solara
from pysepal import sepalwidgets as sw

from gui.i18n import t
from gui.tile.analysis.pane_layers import PaneLayers
from gui.widget.alert_views import AlertViews


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

    Built once per session by ``build_analysis_layout``. See the module
    docstring for the full attribute contract handed to the sub-builders.

    Both views are ONE browser component (``AlertViews``): their sync, layer
    menus and polygon editing run in the browser. Python writes to it only
    through ``panes`` (``PaneLayers``); ``pane_before`` / ``pane_after`` are
    the per-view targets the imagery builders add to.

    Parameters
    ----------
    gee_interface : GEEInterface
        Session GEE interface, used for the tile URLs of Earth Engine layers.
    theme_state : ThemeState, optional
        Shared app theme state: the views follow its light/dark theme, as the
        shared main map does.
    """

    def __init__(self, gee_interface=None, theme_state=None):
        # ------------------------------------------------------------------
        # The two views, one widget. ``analysis-views`` is the class the stage
        # CSS sizes it by and the walks find it by.
        # ------------------------------------------------------------------
        self.panes_widget = AlertViews()
        self.panes_widget.add_class("analysis-views")
        self.panes = PaneLayers(self.panes_widget, gee_interface)
        # The imagery tiles report a failed load on their view (``meta.reportErrors``).
        self.pane_before = self.panes.target("before", group="alert", report_errors=True)
        self.pane_after = self.panes.target("after", group="alert", report_errors=True)
        if theme_state is not None:
            theme_state.observe(
                lambda change: setattr(self.panes_widget, "theme", "dark" if change["new"] else "light"),
                "dark",
            )
        self.panes_widget.theme = "dark" if getattr(theme_state, "dark", False) else "light"

        # ------------------------------------------------------------------
        # View titles, one header cell per view.
        # ------------------------------------------------------------------
        # The titles are the timeline's to fill (``build_timeline``): each one
        # names its view's chosen image, offers the same day's other
        # collection, and says when a saved image was not found. They start
        # as the bare view name until the timeline has a pick.
        self.map_title_before = v.CardTitle(
            class_="pa-1 ma-1",
            children=[t("analysis_tile.img_selection.prev_image_title")],
        )
        self.map_title_after = v.CardTitle(
            class_="pa-1 ma-1",
            children=[t("analysis_tile.img_selection.post_image_title")],
        )
        # One header cell per view, lined up over it. ``analysis-mapbox`` is
        # what the walks find the titles by (``tools/walk/phases.map_titles``).
        self.head_before = v.Html(tag="div", class_="analysis-mapbox", children=[self.map_title_before])
        self.head_after = v.Html(tag="div", class_="analysis-mapbox", children=[self.map_title_after])

        # One shared timeline under both views replaces the two per-view
        # selectors; built by the tile, which has the app and analysis state.
        self.timeline = None

        # ------------------------------------------------------------------
        # Mutable per-alert scratch slots (set by view_actual_alert).
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
