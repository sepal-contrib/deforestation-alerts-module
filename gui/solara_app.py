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

import solara

from pysepal.scripts.utils import init_ee
from pysepal.mapping import SepalMap
from pysepal.sepalwidgets.vue_app import MapApp
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
from gui.store.state_manager import AppState
from gui.tile.about_tile import AboutTile
from gui.tile.overview_tile import OverviewTile

logger = logging.getLogger("SEPALUI.deforestation_alerts")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

init_ee()
setup_solara_server()


@solara.lab.on_kernel_start
def on_kernel_start():
    return setup_sessions()


@solara.component
@with_sepal_sessions(module_name="deforestation_alerts")
def Page():
    setup_theme_colors()

    gee_interface = get_current_gee_interface()
    get_current_sepal_client()
    theme_state = get_current_theme_state()

    # Ephemeral UI/nav state (passed to tiles as they are ported in Tasks 8-9).
    app_state = AppState()  # noqa: F841

    map_ = SepalMap(
        gee_interface=gee_interface, fullscreen=True, theme_state=theme_state
    )

    # MapApp step/dialog content is a synced traitlets `Instance(DOMWidget)` slot
    # (MapApp.vue binds `:widget="activeStep.content"`), NOT a reacton child — so a
    # raw @solara.component element is not rendered there. Render each Solara tile to
    # a concrete widget with solara.render_fixed and hand MapApp that single widget.
    # Memoized so the widget is stable across re-renders.
    about_widget = solara.use_memo(
        lambda: solara.render_fixed(AboutTile())[0], dependencies=[]
    )

    # Overview tile: render_fixed so reactivity (use_effect on alerts_gdf) is
    # preserved while handing MapApp a concrete DOMWidget.  render_fixed leaves a
    # stale duplicate widget in the DOM — use_effect side-effects are idempotent so
    # double-mount is safe.
    overview_widget = solara.use_memo(
        lambda: solara.render_fixed(OverviewTile(app_state=app_state))[0],
        dependencies=[],
    )

    steps_data = [
        {
            "id": 1,
            "name": cm.app.drawer_item.about,
            "icon": "mdi-help-circle",
            "display": "dialog",
            "content": about_widget,
            "width": 1300,
        },
        {
            "id": 2,
            "name": cm.app.drawer_item.overview,
            "icon": "mdi-chart-box-outline",
            # MapApp.vue renders step content only when display is "step"
            # (panel over the map) or "dialog"; an empty display renders nowhere.
            "display": "step",
            "content": overview_widget,
        },
    ]

    MapApp.element(
        app_title=cm.app.title,
        app_icon="mdi-tree",
        repo_url="https://github.com/sepal-contrib/deforestation-alerts-module",
        main_map=[map_],
        steps_data=steps_data,
        initial_step=1,
        theme_state=theme_state,
    )
