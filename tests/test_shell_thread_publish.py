"""Every always-mounted tile of the shell, rendered as the shell renders it,
while worker threads publish the project the way the workers do.

The shell mounts each tile once per session (``render_detached``) and the
tiles follow the project reactive, so a publish re-renders all of them in the
publishing thread. A Prepare row, an Integrate and a review Save publish from
``spawn_in_context`` workers, which have no event loop: a hook that starts
loop-only work there (a ``prefer_threaded=False`` ``use_task`` whose
dependencies change on the save, an ``asyncio.create_task``) raises out of
the publish, as the Project dialog's list did after ``ab1d1e6``. Here every
tile the shell mounts is rendered for real (``AboutTile``, ``ProjectDialog``,
``OverviewTile``, the five step tiles with pysepal's own ``AoiView``, the
workflow header, the Analysis set-status banner) together with the three
leaves ``_AppShell`` renders in its own context (``_NavBridge``,
``_PanelBridge``, ``_ActiveSetLoader``, compiled from gui/solara_app.py as
tests/test_active_set_loader.py does), and a worker thread drives both entry
points, ``AppState.update_project`` and ``AppState.commit``, through the
changes each worker makes: artifacts recorded row by row, a set recorded and
its alerts published, the navigation to Analysis, a Save under the project's
writer mark. Nothing may raise into the worker and no tile may fail its
render (``render_detached`` reports those; the report is recorded here).

Earth Engine is not touched: the catalogue metadata is pre-filled (so the
shared metadata task has nothing to fetch), ``AoiView``'s ``init_ee`` is a
no-op and the maps carry a stand-in interface. ``view_actual_alert`` (raw
widgets, Earth Engine for its bounding box) is not wired.
"""

import ast
import asyncio
import logging
import threading
import traceback
import uuid
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import ipyvuetify as v
import pytest
import reacton
import solara
from pysepal.mapping import SepalMap
from pysepal.solara.components.aoi import aoi_view
from pysepal.solara.notifications import NotificationProvider, use_notifications
from shapely.geometry import box
from traitlets import HasTraits, Int

from deforestation_alerts.engine import catalogue
from deforestation_alerts.engine import grid as gridmod
from deforestation_alerts.engine.fingerprint import harm_key, raw_key
from deforestation_alerts.engine.runner import apply_artifacts
from deforestation_alerts.project import io
from deforestation_alerts.project.models import (
    AlertSet,
    AlertSetParams,
    AoiRecord,
    Artifact,
    Period,
    Project,
    add_predefined_source,
)
from deforestation_alerts.project.models import set_id as make_set_id
from deforestation_alerts.scripts.alert_store import save_gdf_to_parquet
from gui.i18n import t
from gui.scripts import detached, set_store
from gui.scripts.detached import render_detached
from gui.scripts.integrate_jobs import _publish_alerts, apply_result
from gui.scripts.map_layers import MapLayers
from gui.scripts.solara_threads import spawn_in_context
from gui.store.autosave import install_autosave
from gui.store.navigation import apply_navigation, dismiss_dialog, resolve_navigation
from gui.store.project_writers import writing
from gui.store.state_manager import AppState
from gui.store.workflow_steps import RAIL_ITEMS, STEPS, active_set, rail_item_for_step, step_index
from gui.tile.about_tile import AboutTile
from gui.tile.alerts_step import AlertsStep
from gui.tile.analysis.analysis_tile import build_set_status_banner
from gui.tile.aoi_step import AoiStep
from gui.tile.integrate_step import IntegrateStep
from gui.tile.overview_tile import OverviewTile
from gui.tile.period_step import PeriodStep
from gui.tile.prepare_step import PrepareStep
from gui.tile.project_dialog import ProjectDialog
from gui.widget.report_template import ReportTemplateField
from gui.widget.stage_host import StageHost
from gui.widget.workflow_header import WorkflowHeader

ROOT = Path(__file__).resolve().parents[1]
JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
PARAGUAY = (-55.5, -24.5, -55.0, -24.0)
NAME = "h"


class _NavModel(HasTraits):
    current_step = Int(allow_none=True, default_value=None)


def _shell_component(name, threads):
    """Compile ``name`` from gui/solara_app.py with the names it reads (the
    module itself initialises Earth Engine at import)."""
    tree = ast.parse((ROOT / "gui" / "solara_app.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)

    def _spawn(target, *args, **kwargs):
        thread = spawn_in_context(target, *args, **kwargs)
        threads.append(thread)
        return thread

    ns = {
        "solara": solara, "use_notifications": use_notifications, "active_set": active_set,
        "set_store": set_store, "spawn_in_context": _spawn, "t": t,
        "logger": logging.getLogger("SEPALUI.deforestation_alerts"),
        "resolve_navigation": resolve_navigation, "apply_navigation": apply_navigation,
        "STEPS": STEPS, "RAIL_ITEMS": RAIL_ITEMS, "rail_item_for_step": rail_item_for_step,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "gui/solara_app.py", "exec"), ns)
    return ns[name]


def _artifact(key, kind):
    return Artifact(key=key, kind=kind, rel_path=f"periods/{key.replace(':', '_')}.tif", fingerprint="f",
                    inputs={}, content_id=uuid.uuid4().hex, created_at=datetime.now())


def _row_artifacts(source):
    rkey, hkey = raw_key(JAN.id, source), harm_key(JAN.id, source)
    return {rkey: _artifact(rkey, "raw_alert"), hkey: _artifact(hkey, "harmonized")}


def _alerts(n, set_id):
    return gpd.GeoDataFrame(
        {"status": ["Not reviewed"] * n, "alert_id": list(range(n)), "set_id": [set_id] * n,
         "area_ha": [1.0] * n},
        geometry=[box(i * 0.01, 0, i * 0.01 + 0.005, 0.005) for i in range(n)], crs="EPSG:4326")


def _project():
    """A project at the Download & harmonize step: AOI, two sources, the
    period and the grid, so every step tile renders its real form."""
    now = datetime.now()
    p = Project(name=NAME, created_at=now, updated_at=now)
    p.aoi = AoiRecord(spec={}, name="paraguay", method="DRAW", digest="d1", bounds=PARAGUAY)
    add_predefined_source(p, "GLAD-L")
    add_predefined_source(p, "RADD")
    p.periods[JAN.id] = JAN
    p.active_period = JAN.id
    p.grid = gridmod.build_grid(PARAGUAY, gridmod.auto_utm_epsg(PARAGUAY), 30.0)
    return p


def _alert_set(p, sources):
    sid = make_set_id(JAN.id, sources)
    rel = f"periods/{JAN.id}/sets/{'-'.join(s.lower() for s in sources)}"
    return AlertSet(id=sid, period_id=JAN.id, sources=list(sources), digit_map={i: s for i, s in enumerate(sources)},
                    params=AlertSetParams(), store_rel_path=f"{rel}/alert_db.parquet",
                    combined_rel_path=f"{rel}/c.tif", footprint_rel_path=f"{rel}/footprint.tif",
                    n_alerts=3, fingerprint="f", created_at=datetime.now())


@pytest.fixture
def shell(data_root, monkeypatch):
    """The shell's always-mounted tiles and leaves on a real ``AppState`` with
    the auto-save hook, as ``_AppShell`` builds them."""
    gee = SimpleNamespace()  # never asked anything: no metadata is missing, no AOI is processed
    monkeypatch.setattr(aoi_view, "get_current_gee_interface", lambda: gee)
    monkeypatch.setattr(aoi_view.su, "init_ee", lambda *a, **k: None)
    reports = []
    monkeypatch.setattr(detached, "_report", lambda name, text, notify: reports.append((name, text)))

    state = AppState()
    state.catalogue_meta.value = {k: {"end": "2026-09-20"} for k in catalogue.KEYS}
    unsubscribe = install_autosave(state)
    threads = []
    nav_bridge = _shell_component("_NavBridge", threads)
    panel_bridge = _shell_component("_PanelBridge", threads)
    set_loader = _shell_component("_ActiveSetLoader", threads)
    nav_model = _NavModel()
    mapapp_ref = {}
    contexts = []
    tiles = {}

    def _detached(name, make_element):
        container, rc = render_detached(make_element, name)
        contexts.append(rc)
        tiles[name] = container
        return container

    @solara.component
    def Page():
        NotificationProvider()  # rendered before the tiles are built, as in gui/solara_app.py
        Shell()

    @solara.component
    def Shell():
        map_ = solara.use_memo(lambda: SepalMap(gee=True, gee_interface=gee), [])
        layers = solara.use_memo(lambda: MapLayers(map_), [])
        stage_host = solara.use_memo(lambda: StageHost(content=[]), [])

        def _build():
            _detached("about", lambda: AboutTile())
            _detached("project", lambda: ProjectDialog(
                app_state=state, dismiss=lambda: dismiss_dialog(mapapp_ref.get("widget"), nav_model)))
            _detached("overview", lambda: OverviewTile(app_state=state, map_=map_, layers=layers))
            _detached("aoi", lambda: AoiStep(app_state=state, map_=map_, gee_interface=gee))
            _detached("alerts", lambda: AlertsStep(app_state=state, map_=map_, gee_interface=gee, layers=layers))
            _detached("period", lambda: PeriodStep(app_state=state))
            _detached("prepare", lambda: PrepareStep(app_state=state, map_=map_, gee_interface=gee, layers=layers))
            _detached("integrate", lambda: IntegrateStep(app_state=state, map_=map_, gee_interface=gee))
            _detached("header", lambda: WorkflowHeader(app_state=state))
            _detached("report_template", lambda: ReportTemplateField(app_state=state))
            tiles["banner"] = build_set_status_banner(state)
            wrappers = {key: v.Html(tag="div", children=[tiles[key]])
                        for key in ("aoi", "alerts", "period", "prepare", "integrate", "overview")}
            wrappers["analysis"] = v.Html(tag="div")
            return wrappers

        wrappers = solara.use_memo(_build, [])
        header_wrapper = solara.use_memo(lambda: v.Html(tag="div", children=[tiles["header"]]), [])
        nav_bridge(app_state=state, nav_model=nav_model, mapapp_ref=mapapp_ref)
        panel_bridge(app_state=state, panel_wrappers=wrappers, header_wrapper=header_wrapper,
                     stage_host=stage_host, map_=map_, mapapp_ref=mapapp_ref,
                     workflow_config={"title": "Workflow", "icon": "mdi-tune", "width": 480})
        set_loader(app_state=state, layers=layers, map_=map_)
        tiles["stage_host"] = stage_host

    holder = {}

    async def mount():
        holder["box"], holder["rc"] = reacton.render(Page(), handle_error=False)

    def settle():
        for _ in range(200):
            alive = [th for th in threads if th.is_alive()]
            if not alive:
                return
            for th in alive:
                th.join(timeout=0.05)
        raise AssertionError("a set load never finished")

    yield SimpleNamespace(state=state, tiles=tiles, reports=reports, mount=mount, settle=settle, data_root=data_root,
                          nav_model=nav_model)
    for rc in contexts:
        rc.close()
    if "rc" in holder:
        holder["rc"].close()
    unsubscribe()


def _publish(fn, errors):
    """Run ``fn`` on a plain worker thread, as the workers do, collecting its traceback."""

    def body():
        try:
            fn()
        except BaseException:  # noqa: BLE001 - reported by the test
            errors.append(traceback.format_exc())

    thread = threading.Thread(target=body, name="worker")
    thread.start()
    return thread


async def _wait(thread):
    while thread.is_alive():
        await asyncio.sleep(0.01)


def _roots(shell):
    return {name: type(c.children[0]).__name__ for name, c in shell.tiles.items() if isinstance(c, v.Html)
            and name not in ("banner", "stage_host") and c.children}


def test_every_mounted_tile_survives_the_workers_publishes(shell, caplog):
    state = shell.state
    p = _project()
    io.save_project(p)
    store = shell.data_root / NAME / _alert_set(p, ["GLAD-L", "RADD"]).store_rel_path
    sid = _alert_set(p, ["GLAD-L", "RADD"]).id
    save_gdf_to_parquet(_alerts(3, sid), str(store))
    errors = []

    async def main():
        await shell.mount()
        state.load_project_state(p)  # on the loop, as the Project dialog opens it
        await asyncio.sleep(0.2)
        shell.settle()
        before = _roots(shell)
        assert len(before) == 10 and shell.reports == []

        # Download & harmonize: a row's artifacts, applied by run_rows on its worker
        for source in ("GLAD-L", "RADD"):
            arts = _row_artifacts(source)
            await _wait(_publish(lambda a=arts: state.update_project(NAME, lambda live: apply_artifacts(live, a)),
                                 errors))
            assert errors == [], errors[-1]
        assert set(state.project.value.artifacts) == set(_row_artifacts("GLAD-L")) | set(_row_artifacts("RADD"))

        # Integrate: the set recorded through update_project, its alerts published,
        # then the navigation to Analysis, all from the worker
        s = _alert_set(p, ["GLAD-L", "RADD"])
        result = SimpleNamespace(artifacts={}, alert_set=s, alerts=_alerts(3, sid))
        set_store.stamp_generation(result.alerts, store)

        def integrate():
            assert state.update_project(NAME, lambda live: apply_result(live, result))
            assert _publish_alerts(state, NAME, result)
            state.current_page_view.value = "analysis_tile"

        await _wait(_publish(integrate, errors))
        assert errors == [], errors[-1]
        await asyncio.sleep(0.2)
        shell.settle()
        assert state.project.value.active_set == sid and state.project.value.locked
        assert state.active_step.value == step_index("analysis") and state.panel_view.value == "workflow"
        assert shell.tiles["stage_host"].visible is True
        assert state.alerts_gdf.value is result.alerts and state.actual_alert_id.value == -1

        # A review Save: commit under the project's writer mark (record_review)
        def save():
            with writing(NAME):
                live = state.project.value
                live.alert_sets[sid].n_reviewed = 1
                state.commit(live)
                state.alerts_gdf.value = state.alerts_gdf.value.copy()  # _apply_save's rebind

        await _wait(_publish(save, errors))
        assert errors == [], errors[-1]
        await asyncio.sleep(0.2)
        shell.settle()

        assert shell.reports == [], shell.reports[0][1]
        assert _roots(shell) == before  # no tile was replaced by a traceback
        assert state.save_error.value is None and state.last_saved.value is not None
        saved = io.load_project(NAME)
        assert saved.active_set == sid and saved.alert_sets[sid].n_reviewed == 1
        failures = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert failures == [], [r.getMessage() for r in failures]

    asyncio.run(main())
