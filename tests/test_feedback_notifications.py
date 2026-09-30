"""User feedback is published through the pysepal notification system.

Task 7 removed every inline feedback surface from the app (``solara.Error`` /
``solara.Info`` progress elements and the ``sw.Alert`` widgets) in favour of the
notification bus. These tests assert at the **module boundary**: the notifier the
``use_notifications()`` hook returns is a silent ``NoopNotifier`` when no
``NotificationProvider`` is mounted, so a test that mounted nothing would pass
against a completely unwired app. Instead each raw-widget module's
``current_notifier`` is pointed at a REAL pysepal ``NotificationBus`` and the real
click handler + worker is driven end to end, so what is asserted is the task and
the toasts a real run actually leaves on the bus — plus the state change they
accompany.

The raw-layout workers run their body under ``notify_bridge.tracked_job``, so a
failure is reported by the bridge: the task is FAILED first, then exactly ONE
error toast (``ERROR_TOAST_TIMEOUT``) is published. pysepal's bus REPLACES earlier
error toasts, so counting ``bus.toasts`` could hide a duplicate — the error count
is asserted on a ``Notifier.error`` spy instead.

Solara-tile coverage (``aoi_tile`` / ``alerts_filter_tile``): DELIBERATELY not
render-based. This suite has no headless reacton/Solara render harness (every
other UI test here is either a pure-function test or a source-contract test in
``test_app_wiring.py``), and standing one up for two toasts is not warranted.
Those two tiles are covered by (a) ``test_solara_tiles_use_the_hook_not_the_globals``
and ``test_task_error_toasts_use_the_exception_not_the_error_flag`` below, which
lock the API boundary and the one publishing bug that is easy to introduce, and
(b) the manual live check recorded in the task report.

No GEE, no browser.
"""

import ast
import logging
from pathlib import Path

import pytest
from pysepal.solara.notifications.bus import NotificationBus
from pysepal.solara.notifications.notifier import Notifier
from pysepal.solara.notifications.state import TaskStatus, ToastType

from deforestation_alerts.message import cm
from deforestation_alerts.model.alerts_model import SelectedAlertsModel
from deforestation_alerts.model.analysis_model import AlertAnalysisModel
from deforestation_alerts.model.app_model import AppTileModel
from deforestation_alerts.model.aux_model import AuxModel
from gui.scripts.notify_bridge import ERROR_TOAST_TIMEOUT
from gui.scripts.solara_threads import spawn_in_context
from gui.store.state_manager import AppState


# ---------------------------------------------------------------------------
# A real bus per test
# ---------------------------------------------------------------------------
class _Feedback:
    """What one real run published on a private ``NotificationBus``."""

    def __init__(self, bus):
        self.bus = bus
        # One entry per Notifier.error call: (message, timeout, statuses of every
        # task on the bus at the moment the toast was published).
        self.errors = []
        self.threads = []

    def join(self):
        for thread in self.threads:
            thread.join(timeout=30)
            assert not thread.is_alive(), "worker thread did not finish"

    def task(self):
        """The one task the run opened."""
        tasks = list(self.bus.tasks.value)
        assert len(tasks) == 1, [t.title for t in tasks]
        return tasks[0]

    def milestones(self):
        return [m.message for m in self.task().milestones]

    def toasts(self, *types):
        """``(type, message)`` of the non-error toasts on the bus (or ``types``)."""
        wanted = types or tuple(t for t in ToastType if t is not ToastType.ERROR)
        return [
            (t.type.value, t.message) for t in self.bus.toasts.value if t.type in wanted
        ]

    def assert_one_error_after_fail(self, startswith=None, equals=None):
        """Exactly one error toast, published after the task was FAILED."""
        assert len(self.errors) == 1, self.errors
        message, timeout, statuses = self.errors[0]
        assert timeout == ERROR_TOAST_TIMEOUT
        # fail() lands BEFORE the toast: the run is already recorded as FAILED
        # (with its reason) in the task log by the time the user reads it.
        assert statuses == (TaskStatus.FAILED,)
        task = self.task()
        assert task.status == TaskStatus.FAILED
        assert task.error_message == message
        assert self.toasts() == []  # and no success/warning next to it
        if equals is not None:
            assert message == str(equals)
        if startswith is not None:
            assert message.startswith(str(startswith))
        return message


def _record_feedback(monkeypatch, module):
    """Point ``module``'s notifier at a real bus and spy the error toasts.

    The real ``spawn_in_context`` still runs (real daemon thread, real kernel-
    context bridge); the wrapper only keeps the handles so the test can join.
    Any ``notify`` global the module still uses is routed to the same bus.
    """
    bus = NotificationBus()
    feedback = _Feedback(bus)

    monkeypatch.setattr(module, "current_notifier", lambda: Notifier(bus))
    monkeypatch.setattr(
        module,
        "notify",
        lambda message, type_="info": getattr(Notifier(bus), type_)(message),
        raising=False,
    )

    real_error = Notifier.error

    def _spy_error(self, message, *, timeout=None):
        feedback.errors.append(
            (message, timeout, tuple(t.status for t in bus.tasks.value))
        )
        real_error(self, message, timeout=timeout)

    monkeypatch.setattr(Notifier, "error", _spy_error)

    def _spawn(target, args=(), **kwargs):
        thread = spawn_in_context(target, args, **kwargs)
        feedback.threads.append(thread)
        return thread

    monkeypatch.setattr(module, "spawn_in_context", _spawn)
    return feedback


def _is_subsequence(expected, actual):
    it = iter(actual)
    return all(any(item == got for got in it) for item in expected)


def _widgets(root):
    """Depth-first walk of an ipyvuetify widget tree.

    ``v_slots`` is walked too: ``sw.Tooltip`` parents its target widget there,
    so the DL run buttons are invisible to a plain ``children`` walk.
    """
    yield root
    children = list(getattr(root, "children", None) or [])
    for slot in getattr(root, "v_slots", None) or []:
        slot_children = slot.get("children") if isinstance(slot, dict) else None
        if slot_children is None:
            continue
        if isinstance(slot_children, (list, tuple)):
            children.extend(slot_children)
        else:
            children.append(slot_children)
    for child in children:
        if hasattr(child, "children"):
            yield from _widgets(child)


def _button(root, msg):
    """Find the ``sw.Btn`` in ``root`` whose label is ``msg``."""
    for widget in _widgets(root):
        if getattr(widget, "msg", None) == msg:
            return widget
    raise AssertionError(f"no button labelled {msg!r} in the layout")


class _AoiDateStub:
    """Stands in for AoiDateModel so the test never touches Earth Engine."""

    def __init__(self):
        self.imported = None

    def export_dictionary(self):
        return {"aoi_method": "ADMIN0", "aoi_admin": ["CIV"]}

    def import_from_dictionary(self, params):
        self.imported = params


# ---------------------------------------------------------------------------
# recipe_tile — raw ipyvuetify layout + daemon workers under tracked_job
# ---------------------------------------------------------------------------
@pytest.fixture()
def recipe_layout(ws):
    """Build the real recipe layout against a ``Workspace`` over the fake client.

    The recipe files these workers save and load are USER files, so the assertions
    below read them back out of the workspace (``ws``), not off the container's
    disk — that IS the contract Task 11 moved them onto.
    """
    import gui.tile.recipe_tile as recipe_tile

    selected = SelectedAlertsModel()
    selected.min_area = 1.5  # Float(None) default raises on export
    aoi_date = _AoiDateStub()
    app_tile_model = AppTileModel()
    app_state = AppState()

    layout = recipe_tile.build_recipe_layout(
        AuxModel(),
        aoi_date,
        None,  # alert_filter_model — unused by save/load
        selected,
        AlertAnalysisModel(),
        app_tile_model,
        app_state,
        ws,
    )
    return recipe_tile, layout, app_state, app_tile_model, aoi_date


def test_recipe_save_publishes_a_success_toast(recipe_layout, ws, monkeypatch):
    recipe_tile, layout, app_state, _model, _aoi = recipe_layout
    feedback = _record_feedback(monkeypatch, recipe_tile)
    folder = ws.ensure_dir("recipe_test")
    app_state.recipe_folder_path.value = folder

    save_btn = _button(layout, cm.recipe_tile.button_save_recipe)
    save_btn.fire_event("click", None)
    feedback.join()

    # the toast reports a save that really happened -- in the user's workspace
    assert ws.exists(folder + "/recipe_parameters.json")
    # the save is a tracked task now (pill + task log), completed
    task = feedback.task()
    assert task.title == str(cm.recipe_tile.card_title_save_recipe)
    assert task.status == TaskStatus.COMPLETED
    assert feedback.errors == []
    assert feedback.toasts() == [("success", cm.recipe_tile.alert_save_recipe_done)]
    assert save_btn.loading is False and save_btn.disabled is False


def test_recipe_save_failure_fails_the_task_and_publishes_one_error_toast(
    recipe_layout, ws, monkeypatch, data_files
):
    recipe_tile, layout, app_state, _model, _aoi = recipe_layout
    feedback = _record_feedback(monkeypatch, recipe_tile)
    # a folder that does not exist -> _run_save raises FileNotFoundError
    gone = ws.results_dir("gone")
    app_state.recipe_folder_path.value = gone

    save_btn = _button(layout, cm.recipe_tile.button_save_recipe)
    save_btn.fire_event("click", None)
    feedback.join()

    assert not ws.exists(gone)
    assert data_files(ws) == []  # and nothing was written anyway
    feedback.assert_one_error_after_fail(
        startswith=f"{cm.recipe_tile.input_hint_save_recipe_error}: "
    )
    # the button is released on the failure path too
    assert save_btn.loading is False and save_btn.disabled is False


def test_recipe_load_tracks_its_phases_and_publishes_success(
    recipe_layout, ws, monkeypatch
):
    recipe_tile, layout, app_state, app_tile_model, aoi_date = recipe_layout
    feedback = _record_feedback(monkeypatch, recipe_tile)

    params = {"recipe_name": "recipe_from_disk", "actual_alert_id": 0}
    folder = ws.results_dir("recipe_from_disk")
    recipe_file = folder + "/recipe_parameters.json"
    ws.write_json(recipe_file, params)
    _find_load_input(layout).v_model = recipe_file

    load_btn = _button(layout, cm.recipe_tile.button_load_recipe)
    load_btn.fire_event("click", None)
    feedback.join()

    # the load really ran: models imported and the folder published
    assert aoi_date.imported == params
    assert app_tile_model.recipe_name == "recipe_from_disk"
    assert app_state.recipe_folder_path.value == folder
    # and it was reported as a 2-phase tracked task followed by one success toast
    task = feedback.task()
    assert task.title == str(cm.recipe_tile.load_task_title)
    assert task.total_steps == 2
    assert task.status == TaskStatus.COMPLETED
    assert _is_subsequence(
        [cm.recipe_tile.load_step_parameters, cm.recipe_tile.loader_loading_alert_db],
        feedback.milestones(),
    )
    assert feedback.errors == []
    assert feedback.toasts() == [("success", cm.recipe_tile.alert_load_recipe_done)]
    assert load_btn.loading is False and load_btn.disabled is False


@pytest.mark.parametrize("shape", ["home_relative", "root_relative"])
def test_recipe_load_normalizes_a_non_absolute_picked_path(
    recipe_layout, ws, monkeypatch, shape
):
    """picker -> adapter boundary: ``FileInput`` fills ``v_model`` from the path
    the SERVER echoes, which is not built here and is not trusted. A path that
    is not absolute must still resolve to the right remote recipe folder rather
    than failing the load or anchoring the alert store somewhere else."""
    recipe_tile, layout, app_state, app_tile_model, aoi_date = recipe_layout
    feedback = _record_feedback(monkeypatch, recipe_tile)

    params = {"recipe_name": "recipe_from_disk", "actual_alert_id": 0}
    folder = ws.results_dir("recipe_from_disk")
    recipe_file = folder + "/recipe_parameters.json"
    ws.write_json(recipe_file, params)
    picked = (
        ws.home_relative(recipe_file)  # module_results/deforestation_alerts/...
        if shape == "home_relative"
        else "recipe_from_disk/recipe_parameters.json"
    )
    assert not picked.startswith("/")
    _find_load_input(layout).v_model = picked

    _button(layout, cm.recipe_tile.button_load_recipe).fire_event("click", None)
    feedback.join()

    assert aoi_date.imported == params
    # the store is anchored on the ABSOLUTE remote folder, not on the echo
    assert app_state.recipe_folder_path.value == folder
    assert app_tile_model.recipe_folder_path == folder
    assert feedback.toasts() == [("success", cm.recipe_tile.alert_load_recipe_done)]


def test_recipe_load_refuses_a_picked_path_outside_the_workspace(
    recipe_layout, ws, monkeypatch
):
    """An echoed path pointing somewhere else entirely must fail visibly instead
    of silently pointing the alert store at another folder."""
    recipe_tile, layout, app_state, _model, _aoi = recipe_layout
    feedback = _record_feedback(monkeypatch, recipe_tile)
    _find_load_input(layout).v_model = "/home/other-user/recipe_parameters.json"

    _button(layout, cm.recipe_tile.button_load_recipe).fire_event("click", None)
    feedback.join()

    assert app_state.recipe_folder_path.value in (None, "")
    feedback.assert_one_error_after_fail(
        startswith=cm.recipe_tile.input_hint_load_recipe_name
    )


def test_recipe_load_failure_fails_the_task_before_the_error_toast(
    recipe_layout, monkeypatch
):
    recipe_tile, layout, _app_state, _model, _aoi = recipe_layout
    feedback = _record_feedback(monkeypatch, recipe_tile)
    _find_load_input(layout).v_model = ""  # nothing selected -> FileNotFoundError

    load_btn = _button(layout, cm.recipe_tile.button_load_recipe)
    load_btn.fire_event("click", None)
    feedback.join()

    # same text as before the migration: "<hint>: <exception>"
    feedback.assert_one_error_after_fail(
        equals=(
            f"{cm.recipe_tile.input_hint_load_recipe_name}: "
            f"{cm.recipe_tile.input_hint_load_recipe_name}"
        )
    )
    assert load_btn.loading is False and load_btn.disabled is False


def _find_load_input(layout):
    """The recipe picker: pysepal's REMOTE-capable FileInput (not ``sw.FileInput``,
    which is a different class that browses the container's disk)."""
    from pysepal.sepalwidgets.file_input import FileInput

    for widget in _widgets(layout):
        if isinstance(widget, FileInput):
            return widget
    raise AssertionError("no FileInput in the recipe layout")


# ---------------------------------------------------------------------------
# overview_tile export card — raw widget builder, worker under tracked_job
# ---------------------------------------------------------------------------
def _overview_gdf():
    import geopandas as gpd
    from shapely.geometry import box

    gdf = gpd.GeoDataFrame(
        {"area_ha": [1.0], "alert_polygon": [None]},
        geometry=[box(0, 0, 1, 1)],
        crs="EPSG:4326",
    )
    gdf["bounding_box"] = gdf.geometry.envelope
    gdf["point"] = gdf.geometry.representative_point()
    return gdf


@pytest.fixture()
def app_logger_at_info():
    """Let INFO records through the app logger (restored afterwards)."""
    app_logger = logging.getLogger("SEPALUI.deforestation_alerts")
    previous = app_logger.level
    app_logger.setLevel(logging.INFO)
    yield app_logger
    app_logger.setLevel(previous)


def test_overview_export_worker_log_lines_become_task_milestones(
    ws, monkeypatch, app_logger_at_info
):
    """The point of running the body under ``tracked_job``: an app log line the
    export emits on the worker thread shows up as a milestone of ITS task."""
    import gui.tile.overview_tile as overview_tile

    feedback = _record_feedback(monkeypatch, overview_tile)
    folder = ws.ensure_dir("recipe_a")

    def _export(gdf, workspace, folder_, fmt, sources, start, end):
        logging.getLogger("SEPALUI.deforestation_alerts.alert_export").info(
            "Writing 1 alert"
        )
        return f"{folder_}/alerts.{fmt}"

    monkeypatch.setattr(overview_tile, "export_alerts_gdf", _export)
    card = overview_tile.build_export_card(
        _overview_gdf(), ws, folder, ["GLAD-L"], "2026-01-01", "2026-06-12"
    )
    gen_btn = card.children[1].children[1]
    dl_btn = card.children[2].children[1]

    gen_btn.fire_event("click", None)
    feedback.join()

    task = feedback.task()
    assert task.title == str(cm.overview_tile.export_task_title)
    assert task.status == TaskStatus.COMPLETED
    assert feedback.milestones() == [
        str(cm.overview_tile.export_step).format(fmt="GPKG"),
        "Writing 1 alert",
    ]
    assert dl_btn.disabled is False and dl_btn.viz is True
    assert feedback.toasts() == [
        ("success", str(cm.overview_tile.export_done).format(fmt="GPKG"))
    ]


def test_overview_export_failure_fails_the_task_with_one_error_toast(
    ws, monkeypatch
):
    import gui.tile.overview_tile as overview_tile

    feedback = _record_feedback(monkeypatch, overview_tile)

    def _boom(*_a, **_kw):
        raise ValueError("unsupported driver")

    monkeypatch.setattr(overview_tile, "export_alerts_gdf", _boom)
    card = overview_tile.build_export_card(
        _overview_gdf(), ws, ws.results_dir("recipe_a"), ["GLAD-L"], "a", "b"
    )
    gen_btn = card.children[1].children[0]
    dl_btn = card.children[2].children[0]

    gen_btn.fire_event("click", None)
    feedback.join()

    feedback.assert_one_error_after_fail(equals="unsupported driver")
    assert dl_btn.disabled is True and dl_btn.viz is False
    assert gen_btn.loading is False and gen_btn.disabled is False


# ---------------------------------------------------------------------------
# analysis/export — raw layout + daemon workers under tracked_job
# ---------------------------------------------------------------------------
@pytest.fixture()
def export_card(monkeypatch, ws):
    import gui.tile.analysis.export as export

    app_state = AppState()
    app_tile_model = AppTileModel()
    card = export.build_export(
        widgets=None,  # only touched inside _save_attributes / _download_data
        app_state=app_state,
        aux_model=AuxModel(),
        analyzed_alerts_model=AlertAnalysisModel(),
        app_tile_model=app_tile_model,
        workspace=ws,
    )
    return export, card, app_state, app_tile_model


def test_export_save_publishes_success_and_enables_the_download(
    export_card, monkeypatch
):
    export, card, _app_state, _app_tile_model = export_card
    feedback = _record_feedback(monkeypatch, export)
    monkeypatch.setattr(export, "_save_attributes", lambda *a, **k: True)

    download_btn = _button(card, cm.analysis_tile.export_labels.download_alert_data_btn)
    assert download_btn.disabled is True

    save_btn = _button(card, cm.analysis_tile.export_labels.save_btn)
    save_btn.fire_event("click", None)
    feedback.join()

    # the success toast accompanies the real post-save apply step
    assert download_btn.disabled is False
    task = feedback.task()
    assert task.title == str(cm.analysis_tile.export_labels.save_task_title)
    assert task.total_steps is None
    assert str(cm.analysis_tile.export_labels.save_step) in feedback.milestones()
    assert task.status == TaskStatus.COMPLETED
    assert feedback.errors == []
    assert feedback.toasts() == [
        ("success", cm.analysis_tile.export_labels.save_done)
    ]
    assert save_btn.loading is False and save_btn.disabled is False


@pytest.mark.parametrize(
    "with_alerts, alert_id",
    [
        # Nothing loaded at all (a Save pressed before any analyze/recipe load).
        (False, None),
        # An alerts gdf, but the selected index is past its end.
        (True, 5),
    ],
    ids=["no-alerts", "id-out-of-range"],
)
def test_export_save_refusal_fails_the_task_and_says_why(
    export_card, monkeypatch, with_alerts, alert_id
):
    """A Save that writes nothing must not record a DONE run.

    ``_save_attributes`` runs for REAL here (it refuses on its id guards before
    touching ``widgets``). It used to call ``task.complete()`` unconditionally
    and then skip the toast and the button enable: a DONE run, no file, no
    explanation, and a download button still disabled for no stated reason.
    The refusal is a WARNING outcome (not an error toast).
    """
    export, card, app_state, _app_tile_model = export_card
    feedback = _record_feedback(monkeypatch, export)

    if with_alerts:
        app_state.alerts_gdf.value = _one_row_gdf()
    app_state.actual_alert_id.value = alert_id

    save_btn = _button(card, cm.analysis_tile.export_labels.save_btn)
    download_btn = _button(card, cm.analysis_tile.export_labels.download_alert_data_btn)
    save_btn.fire_event("click", None)
    feedback.join()

    expected = str(cm.analysis_tile.export_labels.save_no_alert)
    task = feedback.task()
    assert task.status == TaskStatus.FAILED
    assert task.error_message == expected
    assert feedback.errors == []
    assert feedback.toasts() == [("warning", expected)]
    # nothing was enabled, and the save button is released
    assert download_btn.disabled is True
    assert save_btn.loading is False and save_btn.disabled is False


def test_export_save_failure_fails_the_task_before_the_error_toast(
    export_card, monkeypatch
):
    export, card, _app_state, _app_tile_model = export_card
    feedback = _record_feedback(monkeypatch, export)

    def _boom(*a, **k):
        raise ValueError("no alert selected")

    monkeypatch.setattr(export, "_save_attributes", _boom)

    save_btn = _button(card, cm.analysis_tile.export_labels.save_btn)
    download_btn = _button(card, cm.analysis_tile.export_labels.download_alert_data_btn)
    save_btn.fire_event("click", None)
    feedback.join()

    # the button is released even on the failure path
    assert save_btn.loading is False and save_btn.disabled is False
    assert download_btn.disabled is True
    # the exception text is what reaches the user, not a bool/placeholder
    feedback.assert_one_error_after_fail(equals="no alert selected")


def _one_row_gdf():
    """Enough of an alerts gdf to clear ``_download_data``'s id guards."""
    import geopandas as gpd
    from shapely.geometry import box

    gdf = gpd.GeoDataFrame(
        {"alert_polygon": [None]}, geometry=[box(0, 0, 1, 1)], crs="EPSG:4326"
    )
    gdf["bounding_box"] = gdf.geometry.envelope
    return gdf


def _download_toolbar(card):
    """The toolbar holding the two DownloadBtns (built hidden, shown on success)."""
    from pysepal import sepalwidgets as sw

    for widget in _widgets(card):
        if isinstance(widget, sw.Toolbar) and any(
            isinstance(child, sw.DownloadBtn)
            for child in (getattr(widget, "children", None) or [])
        ):
            return widget
    raise AssertionError("no downloads toolbar in the export card")


@pytest.mark.parametrize(
    "recipe_folder, with_alerts, expected",
    [
        # A recipe is what gives the files somewhere to live in the user's
        # workspace; without one nothing can be written.
        ("", True, cm.analysis_tile.export_labels.download_no_recipe),
        # With a recipe but no selected alert there is nothing to build.
        ("/home/sepal-user/module_results/deforestation_alerts/r",
         False, cm.analysis_tile.export_labels.download_no_alert),
    ],
    ids=["no-recipe", "no-alert"],
)
def test_export_download_refusal_fails_the_task_and_says_why(
    export_card, monkeypatch, recipe_folder, with_alerts, expected
):
    """A download that writes nothing must not report success.

    ``_download_data`` runs for REAL here (it refuses before touching
    ``widgets``), so this drives the actual refusal, not a stub. It used to call
    ``task.complete()`` and then silently skip the toast and the reveal: a green
    pill, no files, no explanation. The refusal is a WARNING outcome.
    """
    export, card, app_state, app_tile_model = export_card
    feedback = _record_feedback(monkeypatch, export)

    app_tile_model.recipe_folder_path = recipe_folder
    if with_alerts:
        app_state.alerts_gdf.value = _one_row_gdf()
        app_state.actual_alert_id.value = 0

    toolbar = _download_toolbar(card)
    download_btn = _button(
        card, cm.analysis_tile.export_labels.download_alert_data_btn
    )
    download_btn.fire_event("click", None)
    feedback.join()

    task = feedback.task()
    assert task.status == TaskStatus.FAILED
    assert task.error_message == str(expected)
    assert feedback.errors == []
    assert feedback.toasts() == [("warning", str(expected))]
    # nothing was revealed, and the button is released
    assert toolbar.viz is False
    assert download_btn.loading is False and download_btn.disabled is False


def test_export_download_success_reveals_the_files(export_card, monkeypatch, ws):
    export, card, _app_state, _app_tile_model = export_card
    feedback = _record_feedback(monkeypatch, export)
    folder = ws.results_dir("r")
    monkeypatch.setattr(
        export,
        "_download_data",
        lambda *a, **k: {
            "zip_path": f"{folder}/alert_0.zip",
            "report_path": f"{folder}/alert_0.docx",
        },
    )

    toolbar = _download_toolbar(card)
    download_btn = _button(
        card, cm.analysis_tile.export_labels.download_alert_data_btn
    )
    download_btn.fire_event("click", None)
    feedback.join()

    task = feedback.task()
    assert task.title == str(cm.analysis_tile.export_labels.download_task_title)
    assert task.status == TaskStatus.COMPLETED
    assert str(cm.analysis_tile.export_labels.download_step) in feedback.milestones()
    assert feedback.errors == []
    assert feedback.toasts() == [
        ("success", str(cm.analysis_tile.export_labels.download_done))
    ]
    assert toolbar.viz is True
    assert download_btn.loading is False and download_btn.disabled is False


def test_export_download_failure_fails_the_task_with_one_error_toast(
    export_card, monkeypatch
):
    export, card, _app_state, _app_tile_model = export_card
    feedback = _record_feedback(monkeypatch, export)

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(export, "_download_data", _boom)

    toolbar = _download_toolbar(card)
    download_btn = _button(
        card, cm.analysis_tile.export_labels.download_alert_data_btn
    )
    download_btn.fire_event("click", None)
    feedback.join()

    feedback.assert_one_error_after_fail(equals="disk full")
    assert toolbar.viz is False
    assert download_btn.loading is False and download_btn.disabled is False


def test_export_download_apply_failure_fails_the_task_with_one_error_toast(
    export_card, monkeypatch, ws
):
    """The post-download apply step runs inside the tracked job: a download path
    ``workspace.home_relative`` rejects (outside the SEPAL home / ``..``) must be a
    FAILED task + one error toast, not a green pill with buttons that never show."""
    export, card, _app_state, _app_tile_model = export_card
    feedback = _record_feedback(monkeypatch, export)
    monkeypatch.setattr(
        export,
        "_download_data",
        lambda *a, **k: {
            "zip_path": "/elsewhere/alert_0.zip",
            "report_path": "/elsewhere/alert_0.docx",
        },
    )

    def _reject(path):
        raise ValueError(f"not under the SEPAL home: {path}")

    monkeypatch.setattr(ws, "home_relative", _reject)

    toolbar = _download_toolbar(card)
    download_btn = _button(
        card, cm.analysis_tile.export_labels.download_alert_data_btn
    )
    download_btn.fire_event("click", None)
    feedback.join()

    feedback.assert_one_error_after_fail(
        equals="not under the SEPAL home: /elsewhere/alert_0.zip"
    )
    assert toolbar.viz is False
    assert download_btn.loading is False and download_btn.disabled is False


def test_export_save_apply_failure_fails_the_task_with_one_error_toast(
    export_card, monkeypatch
):
    """Same for the post-save apply step (enable download + alerts_gdf rebind)."""
    export, card, app_state, _app_tile_model = export_card
    feedback = _record_feedback(monkeypatch, export)
    monkeypatch.setattr(export, "_save_attributes", lambda *a, **k: True)

    class _Uncopyable:
        def copy(self):
            raise MemoryError("cannot copy the alerts")

    app_state.alerts_gdf.value = _Uncopyable()

    save_btn = _button(card, cm.analysis_tile.export_labels.save_btn)
    save_btn.fire_event("click", None)
    feedback.join()

    feedback.assert_one_error_after_fail(equals="cannot copy the alerts")
    assert save_btn.loading is False and save_btn.disabled is False


# ---------------------------------------------------------------------------
# analysis/edit_tools — CNN run workers under tracked_job (dl_404 mapping)
# ---------------------------------------------------------------------------
@pytest.fixture()
def edit_tools_card(ws):
    import gui.tile.analysis.edit_tools as edit_tools

    analyzed_alerts_model = AlertAnalysisModel()
    card = edit_tools.build_edit_tools(
        widgets=None,  # only touched inside _run_cnn_model / the edit handlers
        app_state=AppState(),
        analyzed_alerts_model=analyzed_alerts_model,
        app_tile_model=AppTileModel(),
        workspace=ws,
    )
    return edit_tools, card, analyzed_alerts_model


def _dl_run_button(card, label):
    for widget in _widgets(card):
        if list(getattr(widget, "children", None) or []) == [label]:
            return widget
    raise AssertionError(f"no DL run button {label!r} in the card")


@pytest.mark.parametrize("label, attr", [("M 1", "model1_prediction_file"),
                                         ("M 2", "model2_prediction_file")])
def test_edit_tools_model_run_publishes_success(
    edit_tools_card, monkeypatch, label, attr
):
    edit_tools, card, analyzed_alerts_model = edit_tools_card
    feedback = _record_feedback(monkeypatch, edit_tools)
    monkeypatch.setattr(edit_tools, "_run_cnn_model", lambda *a, **k: "/tmp/pred.tif")
    monkeypatch.setattr(edit_tools, "verify_raster", lambda path, threshold: True)

    run_btn = _dl_run_button(card, label)
    run_btn.fire_event("click", None)
    feedback.join()

    # the toast accompanies the real apply step (the prediction is on the model)
    assert getattr(analyzed_alerts_model, attr) == "/tmp/pred.tif"
    task = feedback.task()
    assert task.title == str(cm.analysis_tile.model_labels.dl_task_title)
    assert task.total_steps is None
    assert str(cm.analysis_tile.model_labels.dl_step_run) in feedback.milestones()
    assert task.status == TaskStatus.COMPLETED
    assert feedback.errors == []
    assert feedback.toasts() == [
        ("success", str(cm.analysis_tile.model_labels.dl_done))
    ]
    assert run_btn.loading is False and run_btn.disabled is False


@pytest.mark.parametrize("label", ["M 1", "M 2"])
def test_edit_tools_model_run_failure_fails_the_task_before_the_error_toast(
    edit_tools_card, monkeypatch, label
):
    edit_tools, card, _model = edit_tools_card
    feedback = _record_feedback(monkeypatch, edit_tools)

    def _boom(*a, **k):
        raise RuntimeError("model download failed")

    monkeypatch.setattr(edit_tools, "_run_cnn_model", _boom)

    run_btn = _dl_run_button(card, label)
    run_btn.fire_event("click", None)
    feedback.join()

    assert run_btn.loading is False and run_btn.disabled is False
    # every run failure maps to the friendly dl_404 text, as before
    feedback.assert_one_error_after_fail(
        equals=cm.analysis_tile.model_labels.dl_404
    )


@pytest.mark.parametrize("label", ["M 1", "M 2"])
def test_edit_tools_verify_failure_is_one_dl_404_error_on_a_failed_task(
    edit_tools_card, monkeypatch, label
):
    """The verify/apply step used to publish its own dl_404 toast and let the
    worker fail the task AFTER it; it now fails inside the tracked job, so the
    task is FAILED first and there is still exactly one error toast."""
    edit_tools, card, _model = edit_tools_card
    feedback = _record_feedback(monkeypatch, edit_tools)
    monkeypatch.setattr(edit_tools, "_run_cnn_model", lambda *a, **k: "/tmp/pred.tif")

    def _bad_raster(path, threshold):
        raise OSError("not a raster")

    monkeypatch.setattr(edit_tools, "verify_raster", _bad_raster)

    run_btn = _dl_run_button(card, label)
    run_btn.fire_event("click", None)
    feedback.join()

    assert run_btn.loading is False and run_btn.disabled is False
    feedback.assert_one_error_after_fail(
        equals=cm.analysis_tile.model_labels.dl_404
    )


def test_cnn_composite_download_drives_the_progress_ring(monkeypatch, ws):
    """The before/after composite download reports geedim tile ticks as task
    progress, then hands the ring back to indeterminate for the inference."""
    import huggingface_hub

    import gui.tile.analysis.edit_tools as edit_tools
    from deforestation_alerts.scripts import gee_progress

    def _fake_stage(*_a, **_k):
        callback = gee_progress._local.callback
        callback(1, 4)
        callback(2, 4)
        callback(4, 4)
        return "/tmp/composite.tif", None

    order = []
    monkeypatch.setattr(edit_tools, "stage_alert_image", _fake_stage)
    monkeypatch.setattr(
        huggingface_hub, "hf_hub_download", lambda **kw: "/tmp/model.h5"
    )
    monkeypatch.setattr(
        edit_tools,
        "apply_dl_model",
        lambda image, model, suffix: order.append("inference") or "/tmp/pred.tif",
    )

    class _Task:
        def set_progress(self, value):
            order.append(value)

    app_state = AppState()
    app_state.actual_alert_id.value = 0
    widgets = type("W", (), {
        "selected_img_before_info_list": None,
        "selected_img_after_info_list": None,
        "selected_img_before": None,
        "selected_img_after": None,
        "actual_alert_grid": None,
    })()

    result = edit_tools._run_cnn_model(
        "repo", "file", "_m1", widgets, app_state, AppTileModel(), ws, task=_Task()
    )

    assert result == "/tmp/pred.tif"
    assert order == [0.25, 0.5, 1.0, None, "inference"]


# ---------------------------------------------------------------------------
# The API boundary + the "no inline feedback left" invariant.
#
# Source-contract tests, in the style of tests/test_app_wiring.py, but read from
# the parsed AST rather than raw text: a prose mention of ``use_notifications()``
# in a module docstring must not count as a call. They cover the two Solara
# tiles, which this suite cannot render (see the module docstring), and they
# catch the two mistakes this migration makes easy — reaching for the globals
# inside a component, and str()-ing the boolean ``task.error`` instead of
# ``task.exception``.
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]

SOLARA_TILES = ["gui/tile/aoi_tile.py", "gui/tile/alerts_filter_tile.py"]
RAW_LAYOUTS = [
    "gui/tile/recipe_tile.py",
    "gui/tile/analysis/edit_tools.py",
    "gui/tile/analysis/export.py",
    # Hybrid: a Solara component, but its export card is a module-level raw
    # widget builder whose worker publishes from a daemon thread, where no hook
    # can run — so it belongs on the raw-layout side of this boundary.
    "gui/tile/overview_tile.py",
]


def _src(rel):
    return (ROOT / rel).read_text()


def _dotted(node):
    """``a.b.c`` for an attribute/name expression, else ""."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _called(rel):
    """Every callee actually invoked in ``rel``, as dotted names."""
    return {
        _dotted(node.func)
        for node in ast.walk(ast.parse(_src(rel)))
        if isinstance(node, ast.Call)
    }


def _imported_names(rel):
    names = set()
    for node in ast.walk(ast.parse(_src(rel))):
        if isinstance(node, ast.ImportFrom):
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


def _str_of_attributes(rel):
    """Attribute names appearing as ``str(<something>.<attr>)`` in ``rel``."""
    attrs = set()
    for node in ast.walk(ast.parse(_src(rel))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "str"
            and node.args
            and isinstance(node.args[0], ast.Attribute)
        ):
            attrs.add(node.args[0].attr)
    return attrs


def test_solara_tiles_use_the_hook_not_the_globals():
    """Components must bind the notifier to the rendering kernel via the hook."""
    for rel in SOLARA_TILES:
        called = _called(rel)
        assert "use_notifications" in called, f"{rel} does not call the hook"
        assert "notifications.error" in called, f"{rel} publishes no error toast"
        assert "notifications.success" in called, f"{rel} publishes no success toast"
        for global_call in ("notify", "track_task"):
            assert global_call not in called, (
                f"{rel} is a Solara component: it must not call the "
                f"{global_call}() global"
            )


def test_raw_layouts_publish_through_the_bridge_not_the_hook():
    """Raw widget layouts publish from worker threads, where no hook can run.

    They resolve a notifier from the worker's kernel context
    (notify_bridge.current_notifier) and run their body under
    tracked_job, which owns the task + the one error toast.
    """
    for rel in RAW_LAYOUTS:
        called = _called(rel)
        imported = _imported_names(rel)
        assert "gui.scripts.notify_bridge.tracked_job" in imported, (
            f"{rel} does not import tracked_job"
        )
        assert "gui.scripts.notify_bridge.current_notifier" in imported, (
            f"{rel} does not import current_notifier"
        )
        assert "tracked_job" in called, f"{rel} tracks no long-running work"
        assert "current_notifier" in called, f"{rel} resolves no notifier"
        assert "notifier.success" in called, f"{rel} publishes no success toast"
        assert "pysepal.solara.notifications.track_task" not in imported, (
            f"{rel} still imports the track_task() global"
        )
        assert "use_notifications" not in called, (
            f"{rel} is a raw layout: it must not call the use_notifications() hook"
        )


def test_raw_layout_workers_use_tracked_job():
    for rel in (
        "gui/tile/recipe_tile.py",
        "gui/tile/overview_tile.py",
        "gui/tile/analysis/export.py",
        "gui/tile/analysis/edit_tools.py",
    ):
        src = _src(rel)
        assert "tracked_job(" in src, rel
        assert "track_task(" not in src, rel


def test_task_error_toasts_use_the_exception_not_the_error_flag():
    """``task.error`` is a bool — str()-ing it would publish "True"."""
    for rel in SOLARA_TILES:
        attrs = _str_of_attributes(rel)
        assert "error" not in attrs, (
            f"{rel} str()s a task's boolean .error flag instead of .exception"
        )
        assert "exception" in attrs, f"{rel} does not report the exception"


def test_no_inline_feedback_surfaces_are_left():
    """The notification system is the app's single user-feedback channel.

    ``sw.Alert`` / ``solara.Error`` / ``solara.Info`` are all gone from the tiles.
    """
    for rel in SOLARA_TILES + RAW_LAYOUTS:
        called = _called(rel)
        for banned in ("sw.Alert", "solara.Error", "solara.Info", "solara.Warning"):
            assert banned not in called, (
                f"{rel} still renders inline feedback: {banned}()"
            )


def test_creation_dialog_keeps_field_validation_inline_with_a_justification():
    """The one documented exception — and it must stay documented."""
    rel = "gui/widget/creation_dialog.py"
    assert "rv.Alert" in _called(rel), "the field-validation alert disappeared"
    assert "KEPT INLINE ON PURPOSE" in _src(rel), (
        "the retained inline validation lost its justification comment"
    )
