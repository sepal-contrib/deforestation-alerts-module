"""Solara entry for the Forest Cover Loss Alerts app (Phase 4).

Session bootstrap (pysepal solara pattern) + MapApp.element() shell. Tiles are
added to steps_data as they are ported (Tasks 8-9); About is first.
Run: solara run gui/solara_app.py --port 8900 --no-open
"""

import logging
import sys
from pathlib import Path

# `solara run gui/solara_app.py` exec's this file without putting the repo root
# on sys.path, so the first-party imports below (deforestation_alerts, gui)
# would fail. Add the repo root (gui/solara_app.py -> parents[1]) up front so
# the app loads regardless of how solara is invoked or the process CWD.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import ipyvuetify as v
import solara
from traitlets import HasTraits, Int

from pysepal.scripts.utils import init_ee
from pysepal.mapping import SepalMap
from pysepal.sepalwidgets.vue_app import MapApp, LocaleSelect
from pysepal.solara import (
    get_current_gee_interface,
    get_current_sepal_client,
    get_current_theme_state,
    setup_sessions,
    setup_solara_server,
    setup_theme_colors,
    with_sepal_sessions,
)

from deforestation_alerts.message import cm
from deforestation_alerts.model.aoi_date_model import AoiDateModel
from deforestation_alerts.model.alerts_model import AlertFilterModel, SelectedAlertsModel
from deforestation_alerts.model.analysis_model import AlertAnalysisModel
from deforestation_alerts.model.app_model import AppTileModel
from deforestation_alerts.model.aux_model import AuxModel
from gui.store.navigation import (
    active_map_view_for_step,
    apply_navigation,
    page_view_for_step,
    resolve_navigation,
)
from gui.store.state_manager import AppState
from gui.tile.about_tile import AboutTile
from gui.tile.aoi_tile import AoiTile
from gui.tile.alerts_filter_tile import AlertsFilterTile
from gui.tile.analysis import build_analysis_layout
from gui.tile.aux_tile import build_aux_layout
from gui.tile.overview_tile import OverviewTile
from gui.tile.recipe_tile import build_recipe_layout

logger = logging.getLogger("SEPALUI.deforestation_alerts")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class _NavModel(HasTraits):
    """Tiny HasTraits bridge so we can observe MapApp's active step from Python.

    Passed to ``MapApp.element(model=...)``; MapApp links its ``current_step``
    trait to this model's (``_setup_model_binding`` links matching trait names).
    The Vue drawer sets ``MapApp.current_step`` on step activation, which flows
    here, letting the right-panel host swap to the active tile's controls.
    """

    current_step = Int(allow_none=True, default_value=None)


def _render_detached(make_element):
    """Render a Solara tile component to a standalone widget for MapApp.

    WHY THIS EXISTS (the alternative is a duplicate tile stacked over the map):
    reacton auto-parents an ``Element`` into the active render context the moment
    it is *constructed* (``Element.__init__`` calls ``container_adders[-1].add``).
    So the obvious ``solara.render_fixed(SomeTile(...))`` builds ``SomeTile(...)``
    while *Page's* render is active, which adds the tile to Page's OWN output tree
    — every tile then renders twice: once leaked inline into Page (all tiles
    stacked in one tall sheet, bleeding over the fullscreen map — e.g. the Aux
    "Planet Login Data" card floating bottom-right) AND once via render_fixed for
    MapApp's dialog/panel/step slot.

    Building the element with Page's ``container_adders`` temporarily detached
    means the construction does NOT auto-add to Page; only ``render_fixed`` (in
    its own isolated context) renders it, and we hand MapApp that single widget.

    Args:
        make_element: zero-arg callable that constructs the tile Element. Must be
            a thunk (not a pre-built element) so the construction happens here,
            after the adders are detached.
    """
    import reacton.core as _rcore

    rc = _rcore.get_render_context(required=False)
    if rc is None:
        return solara.render_fixed(make_element())[0]
    saved = rc.container_adders[:]
    del rc.container_adders[:]
    try:
        return solara.render_fixed(make_element())[0]
    finally:
        rc.container_adders[:] = saved


@solara.component
def _NavBridge(app_state, nav_model, mapapp_ref, step_display, step_actions):
    """Programmatic tile navigation (restores the Voila ``current_page_view`` jump).

    Tiles signal navigation by writing ``app_state.current_page_view`` (AOI search
    -> Filter, Filter analyze -> Analysis, Recipe load -> Analysis, etc.). This
    invisible leaf component reads that reactive and, on change, drives the active
    ``MapApp`` step plus the right-panel / dialog state. It is a *leaf* on purpose:
    only this component re-renders on a nav event, so ``Page`` / ``MapApp`` /
    ``steps_data`` are not rebuilt.

    The Vue ``current_step`` watcher only switches content + opens dialogs; unlike a
    drawer click it does NOT apply ``right_panel_action`` or close a lingering
    dialog. So we drive those explicitly via the ``MapApp`` widget, which ``Page``
    resolves (``solara.get_widget`` must run in the component that created the
    element) and hands us through ``mapapp_ref``: open the panel for panel tiles /
    close it otherwise, and dismiss any open dialog when the target is not a dialog.

    Threading: search/analyze run in worker threads; they only set the reactive.
    Solara marshals this component's re-render + effect onto the main thread, so all
    widget mutation here stays main-thread.
    """
    view = app_state.current_page_view.value

    def _drive():
        action = resolve_navigation(view, step_display, step_actions)
        if action is None:
            return
        # Loop-safety: already on the target step (e.g. the step -> view reverse
        # sync just echoed the active page) -> nothing to do.
        if action.step_id == nav_model.current_step:
            return
        apply_navigation(mapapp_ref.get("widget"), nav_model, action)

    solara.use_effect(_drive, [view])


init_ee()
setup_solara_server()


@solara.lab.on_kernel_start
def on_kernel_start():
    return setup_sessions()


@solara.component
@with_sepal_sessions(module_name="deforestation_alerts")
def Page():
    setup_theme_colors()

    # MapApp renders the main map with `fullscreen=True`, which gives its leaflet
    # container `position: fixed; z-index: 800` over the whole viewport. The
    # step-content panel (where "step" tiles render) sits at z-index 0, so the
    # map bleeds through any gaps in a form tile (e.g. Aux). The panel only exists
    # while a step is active, so lifting it above the map is safe and fixes every
    # step tile at once; map-backed tiles render their own map inside the panel.
    #
    # Embedded-widget height fix: a tile renders raw ipyvuetify widgets via
    # `solara.display(card)`, and ipyvuetify wraps each standalone display in its
    # OWN Vuetify `<v-application>` whose `.v-application--wrap` carries
    # `min-height: 100vh`. So every card reserves a full viewport height — in the
    # Recipe dialog this pushed the Create/Load buttons ~1000px below their cards
    # (cards visible, buttons off-screen); Aux/Overview/AOI/Filter had the same
    # latent over-tall layout. Collapse that min-height for embedded mini-apps
    # INSIDE dialog cards and the right panel (form tiles only — these never hold
    # the shared map, so nothing that needs viewport height is affected; the
    # Analysis "step" maps live outside both selectors and keep their height).
    solara.Style(
        ".step-content-container { z-index: 801 !important; }\n"
        ".dialog-card .v-application--wrap,\n"
        ".right-panel .v-application--wrap { min-height: 0 !important; }\n"
        # Hide the shared map while the Analysis workspace owns the step stage so
        # its leaflet layer-control menu can't bleed over the panel.
        ".defo-map-hidden { display: none !important; }"
    )

    gee_interface = get_current_gee_interface()
    get_current_sepal_client()
    theme_state = get_current_theme_state()

    # Ephemeral UI/nav state — memoized so it is not re-created on every
    # Page re-render (which would discard all reactive values mid-session).
    app_state = solara.use_memo(lambda: AppState(), dependencies=[])

    # -----------------------------------------------------------------------
    # Domain model creation — session-stable, shared across tiles.
    # This is the shared-domain-model creation point: later tiles (AOI,
    # Filter, Recipe) will add their own domain models here in the same
    # use_memo pattern.
    # -----------------------------------------------------------------------
    aux_model = solara.use_memo(lambda: AuxModel(), dependencies=[])
    aoi_date_model = solara.use_memo(lambda: AoiDateModel(), dependencies=[])
    alert_filter_model = solara.use_memo(lambda: AlertFilterModel(), dependencies=[])
    selected_alerts_model = solara.use_memo(
        lambda: SelectedAlertsModel(), dependencies=[]
    )
    analyzed_alerts_model = solara.use_memo(
        lambda: AlertAnalysisModel(), dependencies=[]
    )
    app_tile_model = solara.use_memo(lambda: AppTileModel(), dependencies=[])

    # The ONE shared map. Memoized so the SAME instance is handed to MapApp AND
    # to every map-bearing tile (Overview/AOI/Filter draw their layers + popups
    # on it). Re-creating it per render would desync the tiles (which capture it
    # once via use_memo) from the map MapApp renders. The SATELLITE basemap is
    # added once here (the OLD per-tile maps each added it) so every tile shares
    # the satellite context.
    def _make_shared_map():
        m = SepalMap(
            gee_interface=gee_interface, fullscreen=True, theme_state=theme_state
        )
        try:
            m.add_basemap("SATELLITE")
        except Exception:
            logger.exception("Failed to add SATELLITE basemap to the shared map")
        return m

    map_ = solara.use_memo(_make_shared_map, dependencies=[])

    # MapApp step/dialog content is a synced traitlets `Instance(DOMWidget)` slot
    # (MapApp.vue binds `:widget="activeStep.content"`), NOT a reacton child — so a
    # raw @solara.component element is not rendered there. Render each Solara tile to
    # a concrete widget with solara.render_fixed and hand MapApp that single widget.
    # Memoized so the widget is stable across re-renders.
    about_widget = solara.use_memo(
        lambda: _render_detached(lambda: AboutTile()), dependencies=[]
    )

    # Overview tile: render_fixed so reactivity (use_effect on alerts_gdf) is
    # preserved while handing MapApp a concrete DOMWidget.  render_fixed leaves a
    # stale duplicate widget in the DOM — use_effect side-effects are idempotent so
    # double-mount is safe.
    overview_widget = solara.use_memo(
        lambda: _render_detached(
            lambda: OverviewTile(
                app_state=app_state, map_=map_, aoi_date_model=aoi_date_model,
                selected_alerts_model=selected_alerts_model,
            )
        ),
        dependencies=[],
    )

    # Aux tile: build the RAW ipyvuetify layout (memoized; .bind() chain created
    # once) and hand it straight to MapApp. NOT via render_fixed + solara.display:
    # a solara.display-captured widget lives in an Output whose view is not
    # recreated when MapApp destroys the dialog DOM on close and rebuilds it on
    # reopen, so the cards vanish on the 2nd open. A raw widget survives reopen.
    aux_widget = solara.use_memo(
        lambda: build_aux_layout(aux_model),
        dependencies=[],
    )

    # AOI tile: render_fixed so its hooks (memoized map/AoiView, ccdc observer,
    # search use_task) are preserved while MapApp gets a concrete DOMWidget.
    aoi_widget = solara.use_memo(
        lambda: _render_detached(
            lambda: AoiTile(
                aoi_date_model=aoi_date_model,
                alert_filter_model=alert_filter_model,
                aux_model=aux_model,
                app_state=app_state,
                gee_interface=gee_interface,
                map_=map_,
            )
        ),
        dependencies=[],
    )

    # Filter tile: render_fixed so its hooks (memoized map/draw control/form
    # widgets, the available_alerts_raster_list observer, the analyze use_task)
    # are preserved while MapApp gets a concrete DOMWidget.
    filter_widget = solara.use_memo(
        lambda: _render_detached(
            lambda: AlertsFilterTile(
                aoi_date_model=aoi_date_model,
                aux_model=aux_model,
                alert_filter_model=alert_filter_model,
                selected_alerts_model=selected_alerts_model,
                analyzed_alerts_model=analyzed_alerts_model,
                app_state=app_state,
                gee_interface=gee_interface,
                map_=map_,
            )
        ),
        dependencies=[],
    )

    # Recipe tile: build the RAW ipyvuetify layout (memoized; .bind() chain +
    # button handlers wired once) and hand it straight to MapApp. NOT via
    # render_fixed + solara.display — the Recipes dialog is the case the user hit:
    # a solara.display-captured widget's view is not recreated when MapApp
    # rebuilds the dialog DOM on reopen, so the New/Load cards vanished on the 2nd
    # open (only the reacton-managed buttons survived). A raw widget survives
    # reopen; the load runs on a worker thread inside build_recipe_layout.
    recipe_widget = solara.use_memo(
        lambda: build_recipe_layout(
            aux_model=aux_model,
            aoi_date_model=aoi_date_model,
            alert_filter_model=alert_filter_model,
            selected_alerts_model=selected_alerts_model,
            analyzed_alerts_model=analyzed_alerts_model,
            app_tile_model=app_tile_model,
            app_state=app_state,
            gee_interface=gee_interface,
        ),
        dependencies=[],
    )

    # Analysis tile: build the raw stage + controls once. The stage (two-map
    # workspace) renders in MapApp's step content; the controls render in the
    # docked right panel (panel wrapper 7). Raw widgets survive the step
    # container's v-if rebuild, same as the other raw-built tiles.
    analysis_stage_widget, analysis_controls_widget = solara.use_memo(
        lambda: build_analysis_layout(
            aux_model=aux_model,
            selected_alerts_model=selected_alerts_model,
            analyzed_alerts_model=analyzed_alerts_model,
            app_tile_model=app_tile_model,
            app_state=app_state,
            gee_interface=gee_interface,
        ),
        dependencies=[],
    )

    # -----------------------------------------------------------------------
    # Right-panel foundation (pysepal single-map model).
    #
    # Map-bearing tiles (Overview now; AOI/Filter as they are migrated) render
    # their CONTROLS in MapApp's docked right panel — NOT as opaque "step" panels
    # — so the one shared map stays visible + interactive behind a side card,
    # with per-feature info shown as map popups. ``panel_host`` is a stable
    # container whose single child is swapped to the active tile's controls;
    # ``nav_model.current_step`` (linked to ``MapApp.current_step`` via ``model=``)
    # tells us which tile's drawer step is active.
    #
    # A right-panel tile's drawer step uses ``display:"panel"`` (a value MapApp.vue
    # does not render as a step/dialog → nothing covers the map) + a throwaway
    # ``content`` (so ``current_step`` updates on activation) + ``right_panel_action``
    # ("open" to reveal the panel; "close" on non-panel steps to hide it).
    # -----------------------------------------------------------------------
    nav_model = solara.use_memo(lambda: _NavModel(), dependencies=[])
    # Throwaway content for "panel" steps: never rendered (display != step/dialog),
    # but a non-empty content list is what makes MapApp set current_step on click.
    panel_sentinel = solara.use_memo(lambda: v.Html(tag="div"), dependencies=[])

    # Each right-panel tile's controls wrapped in a div we SHOW/HIDE. ALL three
    # wrappers stay mounted in panel_host at all times; on step change we toggle
    # their visibility instead of swapping panel_host.children.
    # WHY (not a children swap): swapping detaches the previous widget, and a
    # tile's `solara.display`-captured controls live in an `Output` whose view is
    # NOT recreated when the widget is reattached — so AOI/Filter cards vanished
    # after navigating away and back (same class of bug as the Recipe/Aux dialogs
    # on reopen). `display:none` keeps the DOM — and the widget views — alive, so
    # the controls survive. (step ids match steps_data below: AOI 4, Filter 5,
    # Overview 6; Overview is the default-visible one.)
    panel_wrappers = solara.use_memo(
        lambda: {
            6: v.Html(tag="div", children=[overview_widget]),
            4: v.Html(tag="div", children=[aoi_widget], style_="display: none;"),
            5: v.Html(tag="div", children=[filter_widget], style_="display: none;"),
            7: v.Html(
                tag="div",
                children=[analysis_controls_widget],
                style_="display: none;",
            ),
        },
        dependencies=[],
    )
    panel_host = solara.use_memo(
        lambda: v.Container(
            fluid=True,
            class_="pa-0 ma-0",
            children=[
                panel_wrappers[6],
                panel_wrappers[4],
                panel_wrappers[5],
                panel_wrappers[7],
            ],
        ),
        dependencies=[],
    )

    def _wire_panel_swap():
        def _on_step(change):
            sid = change["new"]
            if sid in panel_wrappers:
                for step_id, wrap in panel_wrappers.items():
                    wrap.style_ = "" if step_id == sid else "display: none;"
            # Hide the shared map while the Analysis workspace (step 7) owns the
            # stage, so its leaflet layer-control menu can't bleed over the step
            # panel (the bug that originally forced Analysis to be a dialog). The
            # shared map + other tiles' layers stay mounted and return on leave.
            if sid == 7:
                map_.add_class("defo-map-hidden")
            else:
                map_.remove_class("defo-map-hidden")
            # Right-panel tiles that OWN layers on the shared map (AOI/Filter/
            # Overview). Analysis (7) renders its controls in the panel too but owns
            # its OWN maps, so it is excluded — activating it must NOT change the
            # active map view (else it would clear the underlying tile's layers).
            map_view = active_map_view_for_step(sid, (4, 5, 6))
            if map_view is not None and app_state.active_map_view.value != map_view:
                app_state.active_map_view.value = map_view
            # Reverse sync: keep current_page_view a faithful mirror of the active
            # step (so a manual drawer click updates it, and a *repeated* trigger
            # — e.g. searching twice — still changes the value and re-fires the
            # nav effect). The _NavBridge guard makes the resulting effect run a
            # no-op when this only echoes the current step.
            view = page_view_for_step(sid)
            if view is not None and app_state.current_page_view.value != view:
                app_state.current_page_view.value = view

        nav_model.observe(_on_step, "current_step")
        return lambda: nav_model.unobserve(_on_step, "current_step")

    solara.use_effect(_wire_panel_swap, dependencies=[])

    right_panel_config = {
        "title": cm.app.title,
        "icon": "mdi-tune",
        "width": 460,
        "description": "",
    }
    right_panel_content = [
        {
            "title": "",
            "icon": "",
            "content": [panel_host],
            "divider": False,
            "description": "",
        }
    ]

    # Drawer order matches the ORIGINAL Voila app (ui.ipynb): About, Recipes,
    # Optional files (Aux), AOI/Date, Filter, Overview, Analysis. Drawer order is
    # the list order; ids are sequential so they read top-to-bottom.
    #
    # display types under the single-map model:
    #   "dialog" → modal popup over the (dimmed) main map. Config/IO forms (About,
    #              Recipes, Optional files) AND Analysis — the wide before/after
    #              dual-map review popup (width/height "95%"/"90%"). The dialog
    #              scrim hides the shared main map + its layer-control menu, so
    #              nothing bleeds through.
    #   "panel"  → right-panel card over the visible+interactive map (AOI, Filter,
    #              Overview — the map-interactive workflow tiles).
    #   "step"   → opaque full panel. No tile uses this now; the lifted-z-index
    #              rule in Page()'s solara.Style stays for any future step tile.
    steps_data = [
        {
            "id": 1,
            "name": cm.app.drawer_item.about,
            "icon": "mdi-help-circle",
            "display": "dialog",
            "content": about_widget,
            "width": 1300,
            "right_panel_action": "close",
        },
        {
            "id": 2,
            "name": cm.app.drawer_item.recipes,
            "icon": "mdi-folder-open",
            "display": "dialog",
            "content": recipe_widget,
            "width": 900,
            "right_panel_action": "close",
        },
        {
            "id": 3,
            "name": cm.app.drawer_item.aux,
            "icon": "mdi-layers-plus",
            "display": "dialog",
            "content": aux_widget,
            "width": 900,
            "right_panel_action": "close",
        },
        {
            "id": 4,
            "name": cm.app.drawer_item.aoi,
            "icon": "mdi-map-marker",
            # right-panel tile (single-map model): controls in the panel, AOI
            # selection drawn on the shared map.
            "display": "panel",
            "content": panel_sentinel,
            "right_panel_action": "open",
        },
        {
            "id": 5,
            "name": cm.app.drawer_item.filter,
            "icon": "mdi-filter",
            # right-panel tile (single-map model): form cards in the panel, alert
            # / AOI / aux layers drawn on the shared map.
            "display": "panel",
            "content": panel_sentinel,
            "right_panel_action": "open",
        },
        {
            "id": 6,
            "name": cm.app.drawer_item.overview,
            # mdi-chart-box-outline is absent from the bundled MDI font version
            # (it rendered as a blank gap next to "Overview"); mdi-chart-bar is a
            # core glyph present in every MDI release, so the symbol shows.
            "icon": "mdi-chart-bar",
            # right-panel tile: stats card in the panel, alert points on the map.
            "display": "panel",
            "content": panel_sentinel,
            "right_panel_action": "open",
        },
        {
            "id": 7,
            "name": cm.app.drawer_item.analysis,
            "icon": "mdi-image-search",
            # Full-stage workspace (Option C): the two-map stage renders in the
            # step content container (pinned between the drawer and the docked
            # right panel); the controls render in the right panel (wrapper 7).
            # The shared map is hidden while active (see _on_step) so its layer
            # menu can't bleed over the stage.
            "display": "step",
            "content": analysis_stage_widget,
            "right_panel_action": "open",
        },
    ]

    app_element = MapApp.element(
        app_title=cm.app.title,
        app_icon="mdi-tree",
        repo_url="https://github.com/sepal-contrib/deforestation-alerts-module",
        main_map=[map_],
        steps_data=steps_data,
        initial_step=1,
        theme_state=theme_state,
        model=nav_model,
        right_panel_config=right_panel_config,
        right_panel_content=right_panel_content,
        # Without a translator, MapApp's default LocaleSelect() falls back to
        # English-only. Pass our translator so es-ES and fr are also offered.
        language_selector=[LocaleSelect(translator=cm)],
    )

    # Programmatic tile navigation: translate tiles' current_page_view writes into
    # MapApp step changes (+ right-panel / dialog handling). step_display is derived
    # from steps_data so there is a single source of truth for tile display types.
    step_display = {s["id"]: s["display"] for s in steps_data}
    # Per-step right-panel action, the single source of truth resolve_navigation
    # uses so the Analysis "step" tile opens the panel (a display-derived rule
    # would resolve "step" to "close").
    step_actions = {s["id"]: s.get("right_panel_action") for s in steps_data}

    # Resolve the rendered MapApp widget ONCE here, in Page's own context — where
    # ``app_element`` is valid (``solara.get_widget`` must be called from the
    # component that created the element; calling it from _NavBridge raises
    # KeyError). _NavBridge reads it from this holder to open/close the right panel
    # + dismiss dialogs (the part the Vue current_step watcher skips).
    mapapp_ref = solara.use_memo(lambda: {}, dependencies=[])

    def _resolve_mapapp():
        try:
            mapapp_ref["widget"] = solara.get_widget(app_element)
        except Exception:
            logger.exception("Navigation: could not resolve the MapApp widget")

    solara.use_effect(_resolve_mapapp, dependencies=[])

    _NavBridge(
        app_state=app_state,
        nav_model=nav_model,
        mapapp_ref=mapapp_ref,
        step_display=step_display,
        step_actions=step_actions,
    )
