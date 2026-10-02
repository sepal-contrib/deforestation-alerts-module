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

No GEE, no browser.
"""

import ast
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from pysepal.solara.notifications.bus import NotificationBus
from pysepal.solara.notifications.notifier import Notifier
from pysepal.solara.notifications.state import TaskStatus, ToastType

from gui.scripts.notify_bridge import ERROR_TOAST_TIMEOUT
from gui.scripts.solara_threads import spawn_in_context
from gui.store.state_manager import AppState
from gui.tile.analysis.state import AnalysisState
from gui.i18n import t


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


def _set_context_fake():
    """What the Analysis cards read from their ``SetContext`` (the set's folder
    and the report template), without a project: ``""`` is "no set"."""
    return SimpleNamespace(recipe_folder_path="", custom_report_template=None)


def _button(root, msg):
    """Find the ``sw.Btn`` in ``root`` whose label is ``msg``."""
    for widget in _widgets(root):
        if getattr(widget, "msg", None) == msg:
            return widget
    raise AssertionError(f"no button labelled {msg!r} in the layout")


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
    data_root, monkeypatch, app_logger_at_info
):
    """The point of running the body under ``tracked_job``: an app log line the
    export emits on the worker thread shows up as a milestone of ITS task."""
    import gui.tile.overview_tile as overview_tile

    feedback = _record_feedback(monkeypatch, overview_tile)
    folder = str(data_root / "recipe_a")

    def _export(gdf, folder_, fmt, sources, start, end):
        logging.getLogger("SEPALUI.deforestation_alerts.alert_export").info(
            "Writing 1 alert"
        )
        return f"{folder_}/alerts.{fmt}"

    monkeypatch.setattr(overview_tile, "export_alerts_gdf", _export)
    card = overview_tile.build_export_card(
        _overview_gdf(), folder, ["GLAD-L"], "2026-01-01", "2026-06-12", "d"
    )
    gen_btn = card.children[1].children[1]
    dl_btn = card.children[2].children[1]

    gen_btn.fire_event("click", None)
    feedback.join()

    task = feedback.task()
    assert task.title == t("overview_tile.export_task_title")
    assert task.status == TaskStatus.COMPLETED
    assert feedback.milestones() == [
        t("overview_tile.export_step", fmt="GPKG"),
        "Writing 1 alert",
    ]
    assert dl_btn.disabled is False and dl_btn.viz is True
    assert feedback.toasts() == [
        ("success", t("overview_tile.export_done", fmt="GPKG"))
    ]


def test_overview_export_failure_fails_the_task_with_one_error_toast(
    data_root, monkeypatch
):
    import gui.tile.overview_tile as overview_tile

    feedback = _record_feedback(monkeypatch, overview_tile)

    def _boom(*_a, **_kw):
        raise ValueError("unsupported driver")

    monkeypatch.setattr(overview_tile, "export_alerts_gdf", _boom)
    card = overview_tile.build_export_card(
        _overview_gdf(), str(data_root / "recipe_a"), ["GLAD-L"], "a", "b", "d"
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
def export_card(monkeypatch, data_root):
    import gui.tile.analysis.export as export

    app_state = AppState()
    app_tile_model = _set_context_fake()
    card = export.build_export(
        widgets=None,  # only touched inside _save_attributes / _download_data
        app_state=app_state,
        aux_model=_set_context_fake(),
        analysis=AnalysisState(),
        app_tile_model=app_tile_model,
    )
    return export, card, app_state, app_tile_model


def test_export_save_publishes_success_and_enables_the_download(
    export_card, monkeypatch
):
    export, card, _app_state, _app_tile_model = export_card
    feedback = _record_feedback(monkeypatch, export)
    monkeypatch.setattr(export, "_save_attributes", lambda *a, **k: True)

    download_btn = _button(card, t("analysis_tile.export_labels.download_alert_data_btn"))
    assert download_btn.disabled is True

    save_btn = _button(card, t("analysis_tile.export_labels.save_btn"))
    save_btn.fire_event("click", None)
    feedback.join()

    # the success toast accompanies the real post-save apply step
    assert download_btn.disabled is False
    task = feedback.task()
    assert task.title == t("analysis_tile.export_labels.save_task_title")
    assert task.total_steps is None
    assert t("analysis_tile.export_labels.save_step") in feedback.milestones()
    assert task.status == TaskStatus.COMPLETED
    assert feedback.errors == []
    assert feedback.toasts() == [
        ("success", t("analysis_tile.export_labels.save_done"))
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

    save_btn = _button(card, t("analysis_tile.export_labels.save_btn"))
    download_btn = _button(card, t("analysis_tile.export_labels.download_alert_data_btn"))
    save_btn.fire_event("click", None)
    feedback.join()

    expected = t("analysis_tile.export_labels.save_no_alert")
    task = feedback.task()
    assert task.status == TaskStatus.FAILED
    assert task.error_message == expected
    assert feedback.errors == []
    assert feedback.toasts() == [("warning", expected)]
    # nothing was enabled, and the save button is released
    assert download_btn.disabled is True
    assert save_btn.loading is False and save_btn.disabled is False


@pytest.mark.parametrize(
    "label_key, body",
    [
        ("analysis_tile.export_labels.save_btn", "_save_attributes"),
        ("analysis_tile.export_labels.download_alert_data_btn", "_download_data"),
    ],
    ids=["save", "download"],
)
def test_export_marks_the_open_project_as_being_written(
    export_card, monkeypatch, label_key, body
):
    """Ruling #21: Save and Download write into the active set's folder, so
    the Project dialog's delete guard must see the open project's name while
    they run (the recipe-era key was the recipe name, or "recipe")."""
    from datetime import datetime

    from deforestation_alerts.project.models import Project
    from gui.store.project_writers import is_writing

    export, card, app_state, _app_tile_model = export_card
    feedback = _record_feedback(monkeypatch, export)
    now = datetime.now()
    app_state.new_project_state(Project(name="d", created_at=now, updated_at=now))
    busy = []

    def _write(*_a, **_k):
        busy.append(is_writing("d"))  # returns None: the worker's refusal path

    monkeypatch.setattr(export, body, _write)

    _button(card, t(label_key)).fire_event("click", None)
    feedback.join()

    assert busy == [True] and not is_writing("d")


def test_export_save_of_another_sets_alerts_is_refused_with_a_warning(
    monkeypatch, tmp_path
):
    """While the alerts on hand are another set's (Integrate records a new set
    before it publishes that set's alerts), a Save must not write them into
    the active set's store. Nothing went wrong, the Save just has nowhere to
    go: a FAILED task with the reason and one warning toast, no error toast."""
    from datetime import datetime

    import geopandas as gpd
    from shapely.geometry import box

    import gui.tile.analysis.export as export
    from deforestation_alerts.project.models import (
        AlertSet,
        AlertSetParams,
        Project,
    )
    from gui.scripts.set_context import SetContext

    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    now = datetime.now()
    rel = "periods/per/sets/glad-l"
    p = Project(name="d", created_at=now, updated_at=now)
    p.alert_sets["x"] = AlertSet(
        id="x", period_id="per", sources=["GLAD-L"], digit_map={0: "GLAD-L"},
        params=AlertSetParams(), store_rel_path=f"{rel}/alert_db.parquet",
        combined_rel_path=f"{rel}/c.tif", footprint_rel_path=f"{rel}/footprint.tif",
        fingerprint="f", created_at=now,
    )
    p.active_set = "x"
    app_state = AppState()
    app_state.new_project_state(p)
    store = tmp_path / "d" / rel / "alert_db.parquet"
    store.parent.mkdir(parents=True)
    store.write_bytes(b"set x's store")
    app_state.alerts_gdf.value = gpd.GeoDataFrame(
        {"status": ["Not reviewed"], "set_id": ["y"]},
        geometry=[box(0, 0, 1, 1)], crs="EPSG:4326",
    )
    app_state.actual_alert_id.value = 0
    ctx = SetContext(app_state)
    card = export.build_export(
        widgets=None,  # the refusal comes before any widget is read
        app_state=app_state,
        aux_model=ctx,
        analysis=AnalysisState(),
        app_tile_model=ctx,
    )
    feedback = _record_feedback(monkeypatch, export)

    save_btn = _button(card, t("analysis_tile.export_labels.save_btn"))
    download_btn = _button(card, t("analysis_tile.export_labels.download_alert_data_btn"))
    save_btn.fire_event("click", None)
    feedback.join()

    expected = t("analysis_tile.export_labels.save_other_set")
    task = feedback.task()
    assert task.status == TaskStatus.FAILED and task.error_message == expected
    assert feedback.errors == []
    assert feedback.toasts() == [("warning", expected)]
    assert store.read_bytes() == b"set x's store"
    assert download_btn.disabled is True
    assert save_btn.loading is False and save_btn.disabled is False


def test_export_save_of_a_false_positive_with_a_drawn_polygon_is_refused_with_a_warning(
    monkeypatch, tmp_path
):
    """The OLD guard (a False Positive with a polygon drawn) is a refusal, not
    a failure: a FAILED task with its reason and one warning toast, no error
    toast, and the set's store is not written."""
    from datetime import datetime

    import geopandas as gpd
    from shapely.geometry import box

    import gui.tile.analysis.export as export
    from deforestation_alerts.project.models import AlertSet, AlertSetParams, Project
    from gui.scripts.set_context import SetContext

    class _AdminLookups:  # stands in for ``export.ee``: the FAO/GAUL lookups
        def __getattr__(self, name):
            return lambda *a, **k: self

        def getInfo(self):  # noqa: N802 - Earth Engine's name
            return "Brasil, "

    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(export, "ee", _AdminLookups())
    now = datetime.now()
    rel = "periods/per/sets/glad-l"
    p = Project(name="d", created_at=now, updated_at=now)
    p.alert_sets["x"] = AlertSet(
        id="x", period_id="per", sources=["GLAD-L"], digit_map={0: "GLAD-L"},
        params=AlertSetParams(), store_rel_path=f"{rel}/alert_db.parquet",
        combined_rel_path=f"{rel}/c.tif", footprint_rel_path=f"{rel}/footprint.tif",
        fingerprint="f", created_at=now,
    )
    p.active_set = "x"
    app_state = AppState()
    app_state.new_project_state(p)
    store = tmp_path / "d" / rel / "alert_db.parquet"
    store.parent.mkdir(parents=True)
    store.write_bytes(b"set x's store")
    app_state.alerts_gdf.value = gpd.GeoDataFrame(
        {"status": ["Not reviewed"], "set_id": ["x"]},
        geometry=[box(0, 0, 1, 1)], crs="EPSG:4326",
    )
    app_state.actual_alert_id.value = 0
    analysis = AnalysisState()
    analysis.defo_layer.set(gpd.GeoDataFrame(geometry=[box(0, 0, 0.001, 0.001)], crs="EPSG:4326"))
    widgets = SimpleNamespace(
        confirmation_select=SimpleNamespace(v_model="False Positive"),
        driver_combobox=SimpleNamespace(v_model=[]),
        actual_bb=SimpleNamespace(geometry=lambda: None),
    )
    ctx = SetContext(app_state)
    card = export.build_export(widgets=widgets, app_state=app_state, aux_model=ctx,
                               analysis=analysis, app_tile_model=ctx)
    feedback = _record_feedback(monkeypatch, export)

    save_btn = _button(card, t("analysis_tile.export_labels.save_btn"))
    download_btn = _button(card, t("analysis_tile.export_labels.download_alert_data_btn"))
    save_btn.fire_event("click", None)
    feedback.join()

    expected = t("analysis_tile.questionarie.false_positive_drawn")
    task = feedback.task()
    assert task.status == TaskStatus.FAILED and task.error_message == expected
    assert feedback.errors == []
    assert feedback.toasts() == [("warning", expected)]
    assert store.read_bytes() == b"set x's store"
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

    save_btn = _button(card, t("analysis_tile.export_labels.save_btn"))
    download_btn = _button(card, t("analysis_tile.export_labels.download_alert_data_btn"))
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
    "recipe_folder, with_alerts, expected_key",
    [
        # The active alert set's folder is what gives the files somewhere to
        # live in the project; without a set nothing can be written.
        ("", True, "analysis_tile.export_labels.download_no_recipe"),
        # With a set but no selected alert there is nothing to build.
        ("/home/sepal-user/module_results/deforestation_alerts/r",
         False, "analysis_tile.export_labels.download_no_alert"),
    ],
    ids=["no-recipe", "no-alert"],
)
def test_export_download_refusal_fails_the_task_and_says_why(
    export_card, monkeypatch, recipe_folder, with_alerts, expected_key
):
    """A download that writes nothing must not report success.

    ``_download_data`` runs for REAL here (it refuses before touching
    ``widgets``), so this drives the actual refusal, not a stub. It used to call
    ``task.complete()`` and then silently skip the toast and the reveal: a green
    pill, no files, no explanation. The refusal is a WARNING outcome.
    """
    export, card, app_state, app_tile_model = export_card
    feedback = _record_feedback(monkeypatch, export)
    expected = t(expected_key)  # in the test's locale, not the collection's

    app_tile_model.recipe_folder_path = recipe_folder
    if with_alerts:
        app_state.alerts_gdf.value = _one_row_gdf()
        app_state.actual_alert_id.value = 0

    toolbar = _download_toolbar(card)
    download_btn = _button(
        card, t("analysis_tile.export_labels.download_alert_data_btn")
    )
    download_btn.fire_event("click", None)
    feedback.join()

    task = feedback.task()
    assert task.status == TaskStatus.FAILED
    assert task.error_message == expected
    assert feedback.errors == []
    assert feedback.toasts() == [("warning", expected)]
    # nothing was revealed, and the button is released
    assert toolbar.viz is False
    assert download_btn.loading is False and download_btn.disabled is False


def test_export_download_without_images_is_refused_before_any_file_is_written(
    monkeypatch, data_root, data_files
):
    """The composite needs a before and an after image, and nothing picks them
    by itself (no Planet login, Sentinel-2 needs a manual pick). It used to be
    a raw "'NoneType' object is not subscriptable" error toast, raised after
    the alert's gpkg had already been uploaded: a partial alert_<id>.gpkg in
    the set's folder. Refused like the Save guard: a WARNING outcome, nothing
    written."""
    import gui.tile.analysis.export as export

    app_state = AppState()
    app_tile_model = SimpleNamespace(
        recipe_folder_path=str(data_root / "r"), custom_report_template=None
    )
    widgets = SimpleNamespace(actual_alert_grid=None)  # no image picked: AnalysisState() starts empty
    card = export.build_export(
        widgets=widgets,
        app_state=app_state,
        aux_model=_set_context_fake(),
        analysis=AnalysisState(),
        app_tile_model=app_tile_model,
    )
    feedback = _record_feedback(monkeypatch, export)
    app_state.alerts_gdf.value = _one_row_gdf()
    app_state.actual_alert_id.value = 0

    toolbar = _download_toolbar(card)
    download_btn = _button(card, t("analysis_tile.export_labels.download_alert_data_btn"))
    download_btn.fire_event("click", None)
    feedback.join()

    expected = t("analysis_tile.export_labels.download_no_images")
    task = feedback.task()
    assert task.status == TaskStatus.FAILED
    assert task.error_message == expected
    assert feedback.errors == []
    assert feedback.toasts() == [("warning", expected)]
    assert data_files(data_root) == []  # no alert_0.gpkg left behind
    assert toolbar.viz is False
    assert download_btn.loading is False and download_btn.disabled is False


def test_export_download_success_reveals_the_files(export_card, monkeypatch, data_root):
    export, card, _app_state, _app_tile_model = export_card
    feedback = _record_feedback(monkeypatch, export)
    folder = data_root / "r"
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
        card, t("analysis_tile.export_labels.download_alert_data_btn")
    )
    download_btn.fire_event("click", None)
    feedback.join()

    task = feedback.task()
    assert task.title == t("analysis_tile.export_labels.download_task_title")
    assert task.status == TaskStatus.COMPLETED
    assert t("analysis_tile.export_labels.download_step") in feedback.milestones()
    assert feedback.errors == []
    assert feedback.toasts() == [
        ("success", t("analysis_tile.export_labels.download_done"))
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
        card, t("analysis_tile.export_labels.download_alert_data_btn")
    )
    download_btn.fire_event("click", None)
    feedback.join()

    feedback.assert_one_error_after_fail(equals="disk full")
    assert toolbar.viz is False
    assert download_btn.loading is False and download_btn.disabled is False


def test_export_download_apply_failure_fails_the_task_with_one_error_toast(
    export_card, monkeypatch
):
    """The post-download apply step runs inside the tracked job: a download path
    ``local_files.home_relative`` rejects (outside ``$HOME``) must be a FAILED
    task + one error toast, not a green pill with buttons that never show."""
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

    monkeypatch.setattr(export.local_files, "home_relative", _reject)

    toolbar = _download_toolbar(card)
    download_btn = _button(
        card, t("analysis_tile.export_labels.download_alert_data_btn")
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

    save_btn = _button(card, t("analysis_tile.export_labels.save_btn"))
    save_btn.fire_event("click", None)
    feedback.join()

    feedback.assert_one_error_after_fail(equals="cannot copy the alerts")
    assert save_btn.loading is False and save_btn.disabled is False


# ---------------------------------------------------------------------------
# analysis/edit_tools — CNN run workers under tracked_job (dl_404 mapping)
# ---------------------------------------------------------------------------
@pytest.fixture()
def edit_tools_card():
    import gui.tile.analysis.edit_tools as edit_tools

    analysis = AnalysisState()
    card = edit_tools.build_edit_tools(
        widgets=None,  # only touched inside _run_cnn_model / the edit handlers
        app_state=AppState(),
        analysis=analysis,
        app_tile_model=_set_context_fake(),
    )
    return edit_tools, card, analysis


def _dl_run_button(card, label):
    for widget in _widgets(card):
        if list(getattr(widget, "children", None) or []) == [label]:
            return widget
    raise AssertionError(f"no DL run button {label!r} in the card")


@pytest.mark.parametrize("label, model", [("M 1", 1), ("M 2", 2)])
def test_edit_tools_model_run_publishes_success(
    edit_tools_card, monkeypatch, label, model
):
    edit_tools, card, analysis = edit_tools_card
    feedback = _record_feedback(monkeypatch, edit_tools)
    monkeypatch.setattr(edit_tools, "_run_cnn_model", lambda *a, **k: "/tmp/pred.tif")
    monkeypatch.setattr(edit_tools, "verify_raster", lambda path, threshold: True)

    run_btn = _dl_run_button(card, label)
    run_btn.fire_event("click", None)
    feedback.join()

    # the toast accompanies the real apply step (the prediction is recorded)
    assert analysis.prediction_files.value == {model: "/tmp/pred.tif"}
    task = feedback.task()
    assert task.title == t("analysis_tile.model_labels.dl_task_title")
    assert task.total_steps is None
    assert t("analysis_tile.model_labels.dl_step_run") in feedback.milestones()
    assert task.status == TaskStatus.COMPLETED
    assert feedback.errors == []
    assert feedback.toasts() == [
        ("success", t("analysis_tile.model_labels.dl_done"))
    ]
    assert run_btn.loading is False and run_btn.disabled is False


def test_both_model_runs_keep_their_predictions(edit_tools_card, monkeypatch):
    """Each run records its raster by model number without dropping the
    other's: ``prediction_files`` is replaced, never mutated in place."""
    edit_tools, card, analysis = edit_tools_card
    before = analysis.prediction_files.value
    paths = {"M 1": "/tmp/pred_m1.tif", "M 2": "/tmp/pred_m2.tif"}
    monkeypatch.setattr(edit_tools, "verify_raster", lambda path, threshold: True)
    for label in ("M 1", "M 2"):
        feedback = _record_feedback(monkeypatch, edit_tools)
        monkeypatch.setattr(edit_tools, "_run_cnn_model", lambda *a, _p=paths[label], **k: _p)
        _dl_run_button(card, label).fire_event("click", None)
        feedback.join()

    assert analysis.prediction_files.value == {1: "/tmp/pred_m1.tif", 2: "/tmp/pred_m2.tif"}
    assert before == {}


def _edition_button(card, icon):
    """The DL toolbar's add/remove buttons, by icon (the first of each per
    toolbar is model 1's, the second model 2's)."""
    import gui.tile.analysis.edit_tools as edit_tools

    return [w for w in _widgets(card) if isinstance(w, edit_tools._EditionButton)
            and any(getattr(c, "children", None) == [icon] for c in w.children)]


@pytest.mark.parametrize("label, model", [("M 1", 1), ("M 2", 2)])
def test_a_model_run_for_a_previous_alert_drops_its_result(
    edit_tools_card, monkeypatch, label, model
):
    """Final review B, I2: a CNN run takes a minute; the user moves to the next
    alert meanwhile. Its prediction belongs to the alert it started on, so it is
    neither recorded nor offered ("add" stays disabled) on the alert now on
    screen, whose review Save would store it. The run says so with a warning."""
    edit_tools, card, analysis = edit_tools_card
    feedback = _record_feedback(monkeypatch, edit_tools)

    def _run_while_the_user_moves_on(*_a, **_k):
        analysis.reset_alert()  # view_actual_alert on the next alert
        return "/tmp/pred_of_A.tif"

    monkeypatch.setattr(edit_tools, "_run_cnn_model", _run_while_the_user_moves_on)
    monkeypatch.setattr(edit_tools, "verify_raster", lambda path, threshold: True)

    _dl_run_button(card, label).fire_event("click", None)
    feedback.join()

    assert analysis.prediction_files.value == {}
    add = _edition_button(card, "fa-solid fa-plus")[model - 1]
    assert add.disabled is True
    assert feedback.errors == []
    assert feedback.toasts() == [("warning", t("analysis_tile.model_labels.dl_stale"))]
    assert feedback.task().status == TaskStatus.FAILED


def test_an_alert_change_disables_the_previous_alerts_prediction_buttons(
    edit_tools_card, monkeypatch
):
    """Deferred T3 item 1: "add prediction" stayed enabled after an alert
    change and added the previous alert's raster (now forgotten: a no-op)."""
    edit_tools, card, analysis = edit_tools_card
    feedback = _record_feedback(monkeypatch, edit_tools)
    monkeypatch.setattr(edit_tools, "_run_cnn_model", lambda *a, **k: "/tmp/pred.tif")
    monkeypatch.setattr(edit_tools, "verify_raster", lambda path, threshold: True)
    _dl_run_button(card, "M 1").fire_event("click", None)
    feedback.join()
    add = _edition_button(card, "fa-solid fa-plus")[0]
    assert add.disabled is False

    analysis.reset_alert()

    assert add.disabled is True
    assert all(b.disabled for b in _edition_button(card, "fa-solid fa-minus"))


@pytest.mark.parametrize("label", ["M 1", "M 2"])
def test_edit_tools_model_run_marks_the_open_project_as_being_written(
    monkeypatch, label
):
    """Spec §12: the CNN composite lands in the set's folder, so the Project
    dialog's delete guard must see the open project's name for the whole run
    (without it, a delete mid-download removed the folder and the composite
    upload re-created it as a manifest-less half-project)."""
    from datetime import datetime

    import gui.tile.analysis.edit_tools as edit_tools
    from deforestation_alerts.project.models import Project
    from gui.store.project_writers import is_writing

    app_state = AppState()
    now = datetime.now()
    app_state.new_project_state(Project(name="d", created_at=now, updated_at=now))
    card = edit_tools.build_edit_tools(
        widgets=None,
        app_state=app_state,
        analysis=AnalysisState(),
        app_tile_model=_set_context_fake(),
    )
    feedback = _record_feedback(monkeypatch, edit_tools)
    busy = []

    def _run(*_a, **_k):
        busy.append(is_writing("d"))
        return "/tmp/pred.tif"

    monkeypatch.setattr(edit_tools, "_run_cnn_model", _run)
    monkeypatch.setattr(edit_tools, "verify_raster", lambda path, threshold: True)

    _dl_run_button(card, label).fire_event("click", None)
    feedback.join()

    assert busy == [True] and not is_writing("d")
    assert feedback.errors == []


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
        equals=t("analysis_tile.model_labels.dl_404")
    )


@pytest.mark.parametrize("label", ["M 1", "M 2"])
def test_edit_tools_model_run_failure_leaves_one_traceback_in_the_server_log(
    edit_tools_card, monkeypatch, server_log, label
):
    """``tracked_job`` logs the failure with its traceback (the raw exception,
    behind the friendly dl_404 toast); the run worker logged it again, so one
    failed run printed two tracebacks."""
    edit_tools, card, _model = edit_tools_card
    feedback = _record_feedback(monkeypatch, edit_tools)

    def _boom(*a, **k):
        raise RuntimeError("model download failed")

    monkeypatch.setattr(edit_tools, "_run_cnn_model", _boom)

    _dl_run_button(card, label).fire_event("click", None)
    feedback.join()

    out = server_log.getvalue()
    assert out.count("Traceback (most recent call last)") == 1
    assert "RuntimeError: model download failed" in out
    assert len(feedback.errors) == 1


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
        equals=t("analysis_tile.model_labels.dl_404")
    )


def test_cnn_composite_download_drives_the_progress_ring(monkeypatch):
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
        return "/tmp/composite.tif"

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
    widgets = type("W", (), {"actual_alert_grid": None})()

    result = edit_tools._run_cnn_model(
        "repo", "file", "_m1", widgets, AnalysisState(), app_state, _set_context_fake(),
        task=_Task(),
    )

    assert result == "/tmp/pred.tif"
    assert order == [0.25, 0.5, 1.0, None, "inference"]


# ---------------------------------------------------------------------------
# The API boundary + the "no inline feedback left" invariant.
#
# Source-contract tests, in the style of tests/test_app_wiring.py, but read from
# the parsed AST rather than raw text: a prose mention of ``use_notifications()``
# in a module docstring must not count as a call. They catch the mistake this
# migration makes easy: reaching for the hook from a worker thread, where no
# hook can run.
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]

RAW_LAYOUTS = [
    "gui/tile/analysis/edit_tools.py",
    "gui/tile/analysis/export.py",
    # Hybrid: a Solara component, but its export card is a module-level raw
    # widget builder whose worker publishes from a daemon thread, where no hook
    # can run — so it belongs on the raw-layout side of this boundary.
    "gui/tile/overview_tile.py",
]
# Raw layouts whose component also reports through the hook: the Overview's
# map-layer warnings come from its render context (``use_notifications()``),
# while its export card still publishes through the bridge.
HYBRID_LAYOUTS = ["gui/tile/overview_tile.py"]


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
        if rel not in HYBRID_LAYOUTS:
            assert "use_notifications" not in called, (
                f"{rel} is a raw layout: it must not call the use_notifications() hook"
            )


def test_raw_layout_workers_use_tracked_job():
    for rel in (
        "gui/tile/overview_tile.py",
        "gui/tile/analysis/export.py",
        "gui/tile/analysis/edit_tools.py",
    ):
        src = _src(rel)
        assert "tracked_job(" in src, rel
        assert "track_task(" not in src, rel


def test_no_inline_feedback_surfaces_are_left():
    """The notification system is the app's single user-feedback channel.

    ``sw.Alert`` / ``solara.Error`` / ``solara.Info`` are all gone from the tiles.
    """
    for rel in RAW_LAYOUTS:
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
