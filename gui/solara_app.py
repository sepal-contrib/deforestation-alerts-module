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
from gui.i18n import t
from gui.scripts.log_bridge import install_log_console_handler
from gui.store.state_manager import AppState
from gui.store.workflow_steps import STEPS
from gui.tile.about_tile import AboutTile
from gui.tile.aoi_tile import AoiTile
from gui.tile.alerts_filter_tile import AlertsFilterTile
from gui.tile.analysis import build_analysis_layout
from gui.tile.aux_tile import build_aux_layout
from gui.tile.overview_tile import OverviewTile
from gui.tile.recipe_tile import build_recipe_layout
from gui.widget.log_console import LogConsole

logger = logging.getLogger("SEPALUI.deforestation_alerts")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Bridge the app logger tree into the on-map LogConsole (idempotent; survives
# Solara hot-reload). Worker-thread emits reach the browser once the console
# binds its kernel context on mount.
install_log_console_handler()


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
        ".defo-map-hidden { display: none !important; }\n"
        # The Analysis step-content panel above is lifted to z-index 801, which sits
        # over MapApp's drawer collapse/expand arrow (`.sidebar-controls`, z-index 5)
        # docked at the drawer edge — burying that control so the left nav drawer
        # can't be collapsed while on the Analysis step. Lift the control above the
        # step panel (still below the dialog scrim ~1005, so dialogs cover it). The
        # `.v-application` prefix raises specificity above MapApp's own
        # `.sidebar-controls { z-index: 5 !important }` (equal specificity would lose
        # on source order).
        ".v-application .sidebar-controls { z-index: 802 !important; }\n"
        # Center modal dialogs over the MAP area (viewport minus the left nav
        # drawer), not the whole viewport — otherwise a wide dialog (About is
        # 1300px) centers across the drawer and its left edge sits under the
        # drawer's collapse/expand handle (`.sidebar-controls`) docked at the
        # drawer edge. The card is centered by `.v-dialog__content` — a FIXED,
        # full-viewport flex box (`justify-content: center`); the `dialog-container`
        # class from `content-class` lands on the inner `.v-dialog` (position:
        # static), so size/offset must go on the `.v-dialog__content` parent, not
        # `.dialog-container` (overriding the latter's width would force the card
        # wider, the opposite of what we want). Shrink that fixed flex box by the
        # live drawer width (`--sepal-drawer-width`, pushed onto :root by MapApp)
        # and push its left edge in, so the card centers over the map only. Scoped
        # with `:has` to MapApp's dialogs (the ones using `dialog-container`).
        ".v-dialog__content:has(> .dialog-container) {\n"
        "  left: var(--sepal-drawer-width, 0px) !important;\n"
        "  width: calc(100% - var(--sepal-drawer-width, 0px)) !important;\n"
        "}\n"
        # Justify the About prose inside its dialog (scoped via AboutTile's
        # `.about-content` wrapper so the form dialogs are untouched).
        ".about-content { text-align: justify; }"
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
            theme_state=theme_state,
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

    # Each right-panel tile's controls wrapped in a div we SHOW/HIDE. ALL
    # wrappers stay mounted in panel_host at all times; on step change we toggle
    # their visibility instead of swapping panel_host.children.
    # WHY (not a children swap): swapping detaches the previous widget, and a
    # tile's `solara.display`-captured controls live in an `Output` whose view is
    # NOT recreated when the widget is reattached — so AOI/Filter cards vanished
    # after navigating away and back (same class of bug as the Recipe/Aux dialogs
    # on reopen). `display:none` keeps the DOM — and the widget views — alive, so
    # the controls survive. Which steps have panel controls — and their ids —
    # derives from the STEPS registry; AOI is the default-visible one (the
    # initial step is the About dialog, which leaves the panel closed).
    panel_controls = {
        "aoi": aoi_widget,
        "filter": filter_widget,
        "overview": overview_widget,
        "analysis": analysis_controls_widget,
    }
    panel_wrappers = solara.use_memo(
        lambda: {
            s.step_id: v.Html(
                tag="div",
                children=[panel_controls[s.key]],
                style_="" if s.key == "aoi" else "display: none;",
            )
            for s in STEPS
            if s.key in panel_controls
        },
        dependencies=[],
    )
    panel_host = solara.use_memo(
        lambda: v.Container(
            fluid=True,
            class_="pa-0 ma-0",
            children=[
                panel_wrappers[s.step_id]
                for s in STEPS
                if s.step_id in panel_wrappers
            ],
        ),
        dependencies=[],
    )

    # Derived from the STEPS registry (single source of truth): the right-panel
    # steps that OWN layers on the shared map, and the full-stage steps that
    # hide the shared map while active.
    map_owner_step_ids = tuple(s.step_id for s in STEPS if s.owns_map)
    map_hidden_step_ids = {s.step_id for s in STEPS if s.hides_shared_map}

    def _wire_panel_swap():
        def _on_step(change):
            sid = change["new"]
            if sid in panel_wrappers:
                for step_id, wrap in panel_wrappers.items():
                    wrap.style_ = "" if step_id == sid else "display: none;"
            # Hide the shared map while a full-stage workspace (Analysis) owns
            # the stage, so its leaflet layer-control menu can't bleed over the
            # step panel (the bug that originally forced Analysis to be a
            # dialog). The shared map + other tiles' layers stay mounted and
            # return on leave. Which steps hide the map derives from STEPS.
            if sid in map_hidden_step_ids:
                map_.add_class("defo-map-hidden")
            else:
                map_.remove_class("defo-map-hidden")
            # Right-panel tiles that OWN layers on the shared map (owns_map in
            # STEPS: AOI/Filter/Overview). Analysis renders its controls in the
            # panel too but owns its OWN maps, so it is excluded — activating it
            # must NOT change the active map view (else it would clear the
            # underlying tile's layers).
            map_view = active_map_view_for_step(sid, map_owner_step_ids)
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

    # MapApp drawer entries derive from the STEPS registry (gui/store/
    # workflow_steps.py) — the single source of truth for step order, ids,
    # labels, icons, display types and right-panel actions. Only each step's
    # CONTENT widget is supplied here: dialogs get their rendered tile, panel
    # steps get the throwaway sentinel (their controls live in panel_host),
    # and the Analysis full-stage step gets the two-map stage widget (its
    # controls render in the right panel like the panel steps).
    step_content = {
        "about": about_widget,
        "recipes": recipe_widget,
        "aux": aux_widget,
        "aoi": panel_sentinel,
        "filter": panel_sentinel,
        "overview": panel_sentinel,
        "analysis": analysis_stage_widget,
    }
    steps_data = [
        {
            "id": s.step_id,
            "name": t(s.label_key),
            "icon": s.icon,
            "display": s.display,
            "content": step_content[s.key],
            "right_panel_action": s.panel_action,
            **({"width": s.dialog_width} if s.dialog_width else {}),
        }
        for s in STEPS
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

    # Floating, collapsible process-log console pinned to the map's lower-right
    # corner (Tier-1 scaffold widget). Renders the records captured by the
    # ReactiveLogHandler installed at boot; mounting it binds this session's
    # kernel context so worker-thread log emits reach the browser.
    LogConsole()
