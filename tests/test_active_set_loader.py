"""The shell's ``_ActiveSetLoader``, rendered for real.

``gui/solara_app.py`` cannot be imported here (it initialises Earth Engine and
the tile transport at import), so the component's own source is compiled on its
own, against the names it uses. Its stores are real GeoParquet files; the map
and the layer registry are stand-ins that record the calls.
"""

import ast
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import pytest
import reacton
import solara
from pysepal.solara.notifications import NotificationProvider
from shapely.geometry import box

from deforestation_alerts.project.models import AlertSet, AlertSetParams, AoiRecord, Project
from deforestation_alerts.scripts.alert_store import save_gdf_to_parquet
from gui.scripts import set_store
from gui.scripts.integrate_jobs import _publish_alerts, apply_result
from gui.store.state_manager import AppState

ROOT = Path(__file__).resolve().parents[1]
SET = "per__glad-l"


def _shell_component(name, threads):
    """Compile ``name`` from gui/solara_app.py with the names it reads."""
    import logging

    from pysepal.solara.notifications import use_notifications

    from gui.i18n import t
    from gui.scripts.solara_threads import spawn_in_context
    from gui.store.workflow_steps import active_set

    tree = ast.parse((ROOT / "gui" / "solara_app.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)

    def _spawn(target, *args, **kwargs):
        thread = spawn_in_context(target, *args, **kwargs)
        threads.append(thread)
        return thread

    ns = {"solara": solara, "use_notifications": use_notifications, "active_set": active_set,
          "set_store": set_store, "spawn_in_context": _spawn, "t": t,
          "logger": logging.getLogger("SEPALUI.deforestation_alerts")}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "gui/solara_app.py", "exec"), ns)
    return ns[name]


class _Layers:
    def __init__(self):
        self.cleared = 0

    def clear(self):
        self.cleared += 1


class _Map:
    def __init__(self):
        self.zoomed = []

    def zoom_bounds(self, bounds):
        self.zoomed.append(tuple(bounds))


def _alerts(n, x0=0.0):
    return gpd.GeoDataFrame({"status": ["Not reviewed"] * n, "alert_id": list(range(n)), "set_id": [SET] * n},
                            geometry=[box(x0 + i, 0, x0 + i + 0.5, 0.5) for i in range(n)], crs="EPSG:4326")


def _alert_set(last=0):
    rel = "periods/per/sets/glad-l"
    return AlertSet(id=SET, period_id="per", sources=["GLAD-L"], digit_map={0: "GLAD-L"}, params=AlertSetParams(),
                    store_rel_path=f"{rel}/alert_db.parquet", combined_rel_path=f"{rel}/c.tif",
                    footprint_rel_path=f"{rel}/footprint.tif", n_alerts=3, last_alert_index=last,
                    fingerprint="f", created_at=datetime.now())


def _project(name, ws, n=None, last=0, x0=0.0):
    """A project; with ``n``, its active set and that set's store of ``n`` alerts."""
    p = Project(name=name, created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name=name, method="DRAW", digest=name, bounds=(x0, 0.0, x0 + 1, 1.0))
    if n is not None:
        p.alert_sets[SET] = _alert_set(last)
        p.active_set = SET
        store = Path(ws.root) / name / p.alert_sets[SET].store_rel_path
        save_gdf_to_parquet(_alerts(n, x0), ws, str(store))
    return p


@pytest.fixture
def loader(ws, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(ws.root))
    threads = []
    component = _shell_component("_ActiveSetLoader", threads)
    state, layers, map_ = AppState(), _Layers(), _Map()

    @solara.component
    def Harness():
        NotificationProvider()
        component(app_state=state, workspace=ws, layers=layers, map_=map_)

    _, rc = reacton.render(Harness(), handle_error=False)

    def settle():
        for _ in range(100):
            alive = [th for th in threads if th.is_alive()]
            if not alive:
                return
            for th in alive:
                th.join(timeout=0.1)
        raise AssertionError("a set load never finished")

    yield SimpleNamespace(state=state, layers=layers, map_=map_, settle=settle, ws=ws)
    rc.close()


def test_an_installed_project_loads_its_set_at_the_saved_position_and_resets_the_map(loader):
    loader.state.load_project_state(_project("a", loader.ws, n=3, last=2, x0=10.0))
    loader.settle()

    assert len(loader.state.alerts_gdf.value) == 3
    assert loader.state.actual_alert_id.value == 2
    assert loader.map_.zoomed == [(10.0, 0.0, 11.0, 1.0)]
    assert loader.layers.cleared == 2  # at mount, then on the install


def test_an_install_reads_the_store_once(loader, monkeypatch):
    """``_install`` publishes the project before it bumps the install signal:
    the render in between must not start a second read of the same store."""
    reads = []
    real = set_store.load_active_alerts
    monkeypatch.setattr(set_store, "load_active_alerts", lambda p, ws: reads.append(p.name) or real(p, ws))

    loader.state.load_project_state(_project("a", loader.ws, n=3))
    loader.settle()
    loader.state.load_project_state(_project("b", loader.ws, n=5))
    loader.settle()
    loader.state.load_project_state(loader.state.project.value.model_copy())  # the same project, reopened
    loader.settle()

    assert reads == ["a", "b", "b"]
    assert len(loader.state.alerts_gdf.value) == 5


def test_another_project_with_the_same_set_id_is_read_from_its_own_store(loader):
    """Set ids repeat across projects (period + sources): the alerts on hand
    must never stand in for the newly installed project's set."""
    loader.state.load_project_state(_project("a", loader.ws, n=3))
    loader.settle()
    loader.state.load_project_state(_project("b", loader.ws, n=5, last=4))
    loader.settle()

    assert len(loader.state.alerts_gdf.value) == 5
    assert loader.state.actual_alert_id.value == 4


def test_integrates_first_set_is_kept_as_published_and_the_render_happens_on_its_worker(loader):
    """Integrate records a project's first set through ``update_project`` on
    its worker thread, then publishes that set's alerts itself. The loader
    re-renders on that thread (no event loop there), and leaves the published
    alerts and the position Analysis took alone (ruling: skip a set already
    on hand)."""
    loader.state.new_project_state(_project("b", loader.ws))
    loader.settle()
    store_path = Path(loader.ws.root) / "b" / _alert_set().store_rel_path
    save_gdf_to_parquet(_alerts(3), loader.ws, str(store_path))
    published = _alerts(3)
    result = SimpleNamespace(artifacts={}, alert_set=_alert_set(), alerts=published)
    errors = []

    def _integrate():
        try:
            assert loader.state.update_project("b", lambda p: apply_result(p, result))
            assert _publish_alerts(loader.state, "b", result)
            loader.state.actual_alert_id.set(1)  # Analysis selected an alert meanwhile
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    worker = threading.Thread(target=_integrate)
    worker.start()
    worker.join(timeout=30)
    loader.settle()

    assert errors == []
    assert loader.state.alerts_gdf.value is published
    assert loader.state.actual_alert_id.value == 1


def test_a_set_whose_store_cannot_be_read_is_reported(loader, monkeypatch):
    toasts = []
    from pysepal.solara.notifications.notifier import Notifier

    monkeypatch.setattr(Notifier, "error", lambda self, message, **kw: toasts.append(message))
    monkeypatch.setattr(set_store, "load_active_alerts", lambda p, ws: (_ for _ in ()).throw(OSError("disk gone")))

    loader.state.load_project_state(_project("a", loader.ws, n=3))
    loader.settle()

    assert loader.state.alerts_gdf.value is None
    assert len(toasts) == 1 and "disk gone" in toasts[0]
