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
from deforestation_alerts.scripts import local_files
from gui.i18n import t
from tests._pane_helpers import make_panes, reviewer_edits


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
    """Find the button in ``root`` whose label is ``msg``: an ``sw.Btn`` by
    its ``.msg``, or a plain ``v.Btn`` (the export section's build tile) by a
    text child of its own."""
    import ipyvuetify as v

    for widget in _widgets(root):
        if getattr(widget, "msg", None) == msg:
            return widget
        if isinstance(widget, v.Btn) and msg in (getattr(widget, "children", None) or []):
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
def _export_layout(export, **kwargs):
    """The Export section's body plus the Save button the footer owns.

    ``build_export`` returns the section body and the one ``run_save`` runner;
    the Save button itself lives in the panel's footer (``footer.build_footer``),
    wired to that runner. These tests assert the feedback a real Save
    publishes, so they drive the runner through a stand-in button whose RUNNER
    WIRING is exactly the footer's (``run_save(button)``), and hang it beside
    the body so ``_button(card, ...)`` finds both. Only the wiring matches: the
    footer's buttons and the export body are covered in
    tests/test_analysis_footer.py.
    """
    import ipyvuetify as v
    from pysepal import sepalwidgets as sw

    parts = export.build_export(**kwargs)
    save_btn = sw.Btn(msg=t("analysis_tile.export_labels.save_btn"))
    save_btn.on_event("click", lambda *_: parts.run_save(save_btn))
    return v.Html(tag="div", children=[parts.body, save_btn])


@pytest.fixture()
def export_card(monkeypatch, data_root):
    import gui.tile.analysis.export as export

    app_state = AppState()
    app_tile_model = _set_context_fake()
    card = _export_layout(
        export,
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

    download_btn = _button(card, t("analysis_tile.export_labels.build_tile"))
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
    download_btn = _button(card, t("analysis_tile.export_labels.build_tile"))
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
        ("analysis_tile.export_labels.build_tile", "_download_data"),
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
    card = _export_layout(
        export,
        widgets=None,  # the refusal comes before any widget is read
        app_state=app_state,
        aux_model=ctx,
        analysis=AnalysisState(),
        app_tile_model=ctx,
    )
    feedback = _record_feedback(monkeypatch, export)

    save_btn = _button(card, t("analysis_tile.export_labels.save_btn"))
    download_btn = _button(card, t("analysis_tile.export_labels.build_tile"))
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

    class _AdminLookups:  # stands in for ``review_save.ee``: the FAO/GAUL lookups
        def __getattr__(self, name):
            return lambda *a, **k: self

        def getInfo(self):  # noqa: N802 - Earth Engine's name
            return "Brasil, "

    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("gui.tile.analysis.review_save.ee", _AdminLookups())
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
    analysis.verdict.set("False Positive")
    widgets = SimpleNamespace(actual_bb=SimpleNamespace(geometry=lambda: None))
    ctx = SetContext(app_state)
    card = _export_layout(export, widgets=widgets, app_state=app_state, aux_model=ctx,
                          analysis=analysis, app_tile_model=ctx)
    feedback = _record_feedback(monkeypatch, export)

    save_btn = _button(card, t("analysis_tile.export_labels.save_btn"))
    download_btn = _button(card, t("analysis_tile.export_labels.build_tile"))
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
    download_btn = _button(card, t("analysis_tile.export_labels.build_tile"))
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
    """The container of the S1 file tiles (built hidden, shown on success)."""
    from gui.tile.analysis.file_tile import FileTile

    for widget in _widgets(card):
        if hasattr(widget, "viz") and any(
            isinstance(child, FileTile)
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
        card, t("analysis_tile.export_labels.build_tile")
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
    card = _export_layout(
        export,
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
    download_btn = _button(card, t("analysis_tile.export_labels.build_tile"))
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
        card, t("analysis_tile.export_labels.build_tile")
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
    # The two file tiles link this build's zip and report.
    zip_tile, docx_tile = toolbar.children
    assert zip_tile.disabled is False and zip_tile.attributes["download"] == "alert_0.zip"
    assert docx_tile.disabled is False and docx_tile.attributes["download"] == "alert_0.docx"
    from gui.tile.analysis.file_tile import FileTile

    (gif_tile,) = [w for w in _widgets(card) if isinstance(w, FileTile) and w.kind == "gif"]
    assert gif_tile.disabled is True  # TODO(timelapse): nothing builds it yet


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
        card, t("analysis_tile.export_labels.build_tile")
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

    monkeypatch.setattr(local_files, "home_relative", _reject)

    toolbar = _download_toolbar(card)
    download_btn = _button(
        card, t("analysis_tile.export_labels.build_tile")
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


def test_a_save_that_lands_after_the_reviewer_moved_on_stamps_nobody(
    monkeypatch, data_root
):
    """The save stamp belongs to the alert that was saved (spec §4.4).

    ``last_save_time`` is per-alert UI: ``reset_alert`` clears it so an alert
    nobody saved shows no time at all. A save whose store write lands after
    the reviewer navigated by hand lands after that reset too, so the stamp
    has to be written under the same token guard as ``dirty`` -- written from
    inside ``_save_attributes`` it stamps whatever alert is on screen by then,
    and the new one claims a save nobody made for it.
    """
    import gui.tile.analysis.export as export

    app_state = AppState()
    analysis = AnalysisState()
    card = _export_layout(
        export,
        widgets=None,
        app_state=app_state,
        aux_model=_set_context_fake(),
        analysis=analysis,
        app_tile_model=_set_context_fake(),
    )
    save_btn = _button(card, t("analysis_tile.export_labels.save_btn"))

    # A save of the alert on screen stamps it.
    feedback = _record_feedback(monkeypatch, export)
    monkeypatch.setattr(export, "_save_attributes", lambda *a, **k: True)
    save_btn.fire_event("click", None)
    feedback.join()
    assert analysis.last_save_time.value > 0

    # ... and the next alert starts with no stamp of its own.
    analysis.reset_alert()
    assert analysis.last_save_time.value == 0.0

    feedback = _record_feedback(monkeypatch, export)

    def _navigate_mid_save(*_a, **_k):
        analysis.reset_alert()  # the reviewer clicks the next alert meanwhile
        return True

    monkeypatch.setattr(export, "_save_attributes", _navigate_mid_save)
    save_btn.fire_event("click", None)
    feedback.join()

    assert analysis.last_save_time.value == 0.0, (
        "an alert nobody saved was stamped as saved"
    )


# ---------------------------------------------------------------------------
# analysis/edit_tools — CNN run workers under tracked_job (dl_404 mapping)
# ---------------------------------------------------------------------------
def _images_drawn(analysis):
    """The precondition of every run and every build: a before/after pair
    picked AND rebuilt by its pane.

    The ee.Images are stand-ins (the rebuild and the geedim download are
    stubbed in these tests) but they have to be there: a run or a build
    refuses while a lane is still rebuilding, because the pair's metadata
    would be the new pick's and its pixels the previous one's
    (``edit_tools._images_ready``).
    """
    analysis.before_info.set(["Sentinel 2", 1700000000, None, "S2_before", "_010124"])
    analysis.after_info.set(["Sentinel 2", 1700000001, None, "S2_after", "_020224"])
    analysis.before_image.set(object())
    analysis.after_image.set(object())
    return analysis


@pytest.fixture()
def edit_tools_card():
    import gui.tile.analysis.edit_tools as edit_tools

    analysis = _images_drawn(AnalysisState())
    card = edit_tools.build_edit_tools(
        widgets=None,  # only touched inside _run_cnn_model / the edit handlers
        app_state=AppState(),
        analysis=analysis,
        app_tile_model=_set_context_fake(),
    )
    return edit_tools, card, analysis


def _found(*, patches=2, area=3.9):
    """What ``vectorise`` gives the worker for a prediction with something in
    it, without a raster to read: its features, their count and their area."""
    feature = {"type": "Feature", "properties": {},
               "geometry": {"type": "Polygon",
                            "coordinates": [[[0, 0], [0, 0.01], [0.01, 0.01], [0.01, 0], [0, 0]]]}}
    return ([feature] * patches, patches, area)


def _slots(card):
    """The section body's named widgets: both models' *Run* buttons now carry
    the same label, so a walk by label can no longer tell them apart."""
    import gui.tile.analysis.edit_tools as edit_tools

    return edit_tools.edit_widgets(card)


def _dl_run_button(card, model):
    return _slots(card)[f"dl{model}_run"]


@pytest.mark.parametrize("model", [1, 2])
def test_edit_tools_model_run_publishes_success(
    edit_tools_card, monkeypatch, model
):
    edit_tools, card, analysis = edit_tools_card
    feedback = _record_feedback(monkeypatch, edit_tools)
    monkeypatch.setattr(edit_tools, "_run_cnn_model", lambda *a, **k: "/tmp/pred.tif")
    monkeypatch.setattr(edit_tools, "vectorise", lambda path, *_: _found())

    run_btn = _dl_run_button(card, model)
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
    # and the row reports what it found, counted and measured in the language
    assert analysis.prediction_shapes.value == {model: (2, 3.9)}
    assert _slots(card)[f"dl{model}_found"].children == ["Found 2 patches \u00b7 3.9 ha"]


def _path_to(root, target):
    """The chain of widgets from ``root`` down to ``target``, or ``None``.

    Walks ``v_slots`` like ``_widgets``: ``sw.Tooltip`` parents its target there.
    """
    if root is target:
        return [root]
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
        if not hasattr(child, "children"):
            continue
        found = _path_to(child, target)
        if found is not None:
            return [root] + found
    return None


def _hidden_above(root, target):
    """Every widget on the way down to ``target`` that is hidden -- its own
    ``viz`` included. A button whose ``viz`` is True is still off screen when a
    container above it is the one carrying ``SepalWidget.hide()``'s ``d-none``
    or a ``display: none``, which is exactly how an enabled *Add to map* was
    left unreachable."""
    path = _path_to(root, target)
    assert path is not None, "the widget is not parented in the body at all"

    def _hidden(widget):
        return (getattr(widget, "viz", True) is False
                or "d-none" in str(getattr(widget, "class_", "") or "")
                or "display: none" in str(getattr(widget, "style_", "") or ""))

    return [widget for widget in path if _hidden(widget)]


@pytest.mark.parametrize("model", [1, 2])
def test_a_finished_run_offers_its_prediction_on_screen(
    edit_tools_card, monkeypatch, model
):
    """*Run* sits in the model row, outside the drawing session, so a reviewer
    runs a model on an alert they have not started drawing on -- and the pair
    that puts the prediction on the map has to be reachable then. OLD carried
    *Run* inside the same toolbar as *Add to map*, so a run outside drawing
    mode was impossible; with *Run* moved out, a pair still gated on *Start
    Drawing* leaves a finished prediction with nowhere to go."""
    edit_tools, card, analysis = edit_tools_card
    feedback = _record_feedback(monkeypatch, edit_tools)
    monkeypatch.setattr(edit_tools, "_run_cnn_model", lambda *a, **k: "/tmp/pred.tif")
    monkeypatch.setattr(edit_tools, "vectorise", lambda path, *_: _found())

    _dl_run_button(card, model).fire_event("click", None)
    feedback.join()

    slots = _slots(card)
    add, remove = slots[f"dl{model}_add"], slots[f"dl{model}_remove"]
    assert _hidden_above(card, add) == [], "Add to map is not on screen"
    assert _hidden_above(card, remove) == [], "Remove from map is not on screen"
    assert add.disabled is False


@pytest.mark.parametrize("model", [1, 2])
def test_a_model_run_with_a_pane_still_loading_is_refused(
    edit_tools_card, monkeypatch, model
):
    """The composite is named after the picked pair (``paths.alert_image_name``)
    and built from the two ee.Images. While one lane is still rebuilding, the
    infos are the new pick's and its image is not there yet, so a run would
    stage a composite named for one pick out of the other's pixels -- or fail
    inside geedim on a ``None`` image, which reads as a broken model. It waits
    for both panes instead, and says so.
    """
    edit_tools, card, analysis = edit_tools_card
    feedback = _record_feedback(monkeypatch, edit_tools)
    # the reviewer picks another before image: that lane is pending again
    # until its rebuild lands (``imagery.wire_panes``)
    analysis.before_image.set(None)
    ran = []
    monkeypatch.setattr(edit_tools, "_run_cnn_model", lambda *a, **k: ran.append(1))

    _dl_run_button(card, model).fire_event("click", None)
    feedback.join()

    reason = t("analysis_tile.model_labels.dl_no_images")
    assert ran == [], "the model ran on a pane that had not drawn its pick"
    assert feedback.task().status == TaskStatus.FAILED
    assert feedback.task().error_message == reason
    assert feedback.toasts() == [("warning", reason)]


def test_both_model_runs_keep_their_predictions(edit_tools_card, monkeypatch):
    """Each run records its raster by model number without dropping the
    other's: ``prediction_files`` is replaced, never mutated in place."""
    edit_tools, card, analysis = edit_tools_card
    before = analysis.prediction_files.value
    paths = {1: "/tmp/pred_m1.tif", 2: "/tmp/pred_m2.tif"}
    monkeypatch.setattr(edit_tools, "vectorise", lambda path, *_: _found())
    for model in (1, 2):
        feedback = _record_feedback(monkeypatch, edit_tools)
        monkeypatch.setattr(edit_tools, "_run_cnn_model", lambda *a, _p=paths[model], **k: _p)
        _dl_run_button(card, model).fire_event("click", None)
        feedback.join()

    assert analysis.prediction_files.value == {1: "/tmp/pred_m1.tif", 2: "/tmp/pred_m2.tif"}
    assert before == {}


def _edition_button(card, kind):
    """Both models' add ("add") or remove ("remove") button, model 1's first."""
    return [_slots(card)[f"dl{n}_{kind}"] for n in (1, 2)]


@pytest.mark.parametrize("model", [1, 2])
def test_a_model_run_for_a_previous_alert_drops_its_result(
    edit_tools_card, monkeypatch, model
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
    monkeypatch.setattr(edit_tools, "vectorise", lambda path, *_: _found())

    _dl_run_button(card, model).fire_event("click", None)
    feedback.join()

    assert analysis.prediction_files.value == {}
    assert analysis.prediction_shapes.value == {}
    add = _edition_button(card, "add")[model - 1]
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
    monkeypatch.setattr(edit_tools, "vectorise", lambda path, *_: _found())
    _dl_run_button(card, 1).fire_event("click", None)
    feedback.join()
    add = _edition_button(card, "add")[0]
    assert add.disabled is False

    analysis.reset_alert()

    assert add.disabled is True
    assert all(b.disabled for b in _edition_button(card, "remove"))
    # and the row no longer reports the previous alert's prediction
    assert _slots(card)["dl1_found"].children == []


@pytest.mark.parametrize("model", [1, 2])
def test_edit_tools_model_run_marks_the_open_project_as_being_written(
    monkeypatch, model
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
        analysis=_images_drawn(AnalysisState()),
        app_tile_model=_set_context_fake(),
    )
    feedback = _record_feedback(monkeypatch, edit_tools)
    busy = []

    def _run(*_a, **_k):
        busy.append(is_writing("d"))
        return "/tmp/pred.tif"

    monkeypatch.setattr(edit_tools, "_run_cnn_model", _run)
    monkeypatch.setattr(edit_tools, "vectorise", lambda path, *_: _found())

    _dl_run_button(card, model).fire_event("click", None)
    feedback.join()

    assert busy == [True] and not is_writing("d")
    assert feedback.errors == []


@pytest.mark.parametrize("model", [1, 2])
def test_edit_tools_model_run_failure_fails_the_task_before_the_error_toast(
    edit_tools_card, monkeypatch, model
):
    edit_tools, card, _model = edit_tools_card
    feedback = _record_feedback(monkeypatch, edit_tools)

    def _boom(*a, **k):
        raise RuntimeError("model download failed")

    monkeypatch.setattr(edit_tools, "_run_cnn_model", _boom)

    run_btn = _dl_run_button(card, model)
    run_btn.fire_event("click", None)
    feedback.join()

    assert run_btn.loading is False and run_btn.disabled is False
    # every run failure maps to the friendly dl_404 text, as before
    feedback.assert_one_error_after_fail(
        equals=t("analysis_tile.model_labels.dl_404")
    )


@pytest.mark.parametrize("model", [1, 2])
def test_edit_tools_model_run_failure_leaves_one_traceback_in_the_server_log(
    edit_tools_card, monkeypatch, server_log, model
):
    """``tracked_job`` logs the failure with its traceback (the raw exception,
    behind the friendly dl_404 toast); the run worker logged it again, so one
    failed run printed two tracebacks."""
    edit_tools, card, _model = edit_tools_card
    feedback = _record_feedback(monkeypatch, edit_tools)

    def _boom(*a, **k):
        raise RuntimeError("model download failed")

    monkeypatch.setattr(edit_tools, "_run_cnn_model", _boom)

    _dl_run_button(card, model).fire_event("click", None)
    feedback.join()

    out = server_log.getvalue()
    assert out.count("Traceback (most recent call last)") == 1
    assert "RuntimeError: model download failed" in out
    assert len(feedback.errors) == 1


@pytest.mark.parametrize("model", [1, 2])
def test_a_model_run_vectorises_with_the_projects_crs_and_minimum_size(monkeypatch, model):
    """The patches a run offers follow the alert set's minimum size, measured
    in the project's grid CRS, read at the click."""
    from datetime import datetime

    import gui.tile.analysis.edit_tools as edit_tools
    from deforestation_alerts.project.models import AlertSet, AlertSetParams, GridSpec, Project

    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now(),
                grid=GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=7350000, width=10, height=10))
    p.alert_sets["x"] = AlertSet(id="x", period_id="per", sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                                 params=AlertSetParams(min_size_ha=1.5), store_rel_path="s/alert_db.parquet",
                                 combined_rel_path="s/c.tif", footprint_rel_path="s/footprint.tif",
                                 fingerprint="f", created_at=datetime.now())
    p.active_set = "x"
    app_state = AppState()
    app_state.project.value = p
    card = edit_tools.build_edit_tools(widgets=None, app_state=app_state,
                                       analysis=_images_drawn(AnalysisState()),
                                       app_tile_model=_set_context_fake())
    feedback = _record_feedback(monkeypatch, edit_tools)
    monkeypatch.setattr(edit_tools, "_run_cnn_model", lambda *a, **k: "/tmp/pred.tif")
    calls = []
    monkeypatch.setattr(edit_tools, "vectorise", lambda path, *rule: calls.append(rule) or _found())

    _dl_run_button(card, model).fire_event("click", None)
    feedback.join()

    assert calls == [("EPSG:32721", 1.5)]


@pytest.mark.parametrize("model", [1, 2])
def test_edit_tools_vectorise_failure_is_one_dl_404_error_on_a_failed_task(
    edit_tools_card, monkeypatch, model
):
    """The verify/apply step used to publish its own dl_404 toast and let the
    worker fail the task AFTER it; reading the prediction now fails inside the
    tracked job, so the task is FAILED first and there is still exactly one
    error toast."""
    edit_tools, card, _model = edit_tools_card
    feedback = _record_feedback(monkeypatch, edit_tools)
    monkeypatch.setattr(edit_tools, "_run_cnn_model", lambda *a, **k: "/tmp/pred.tif")

    def _bad_raster(path, *_):
        raise OSError("not a raster")

    monkeypatch.setattr(edit_tools, "vectorise", _bad_raster)

    run_btn = _dl_run_button(card, model)
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


def test_a_cnn_run_records_what_it_found():
    """What a run found is remembered per model and forgotten with the alert,
    so the row can say it and the next alert never inherits it. (That the
    *worker* records it is pinned end to end by the run tests above.)"""
    from gui.tile.analysis import edit_tools

    analysis = AnalysisState()
    edit_tools._record_prediction_shapes(analysis, 1, 2, 3.9)
    assert analysis.prediction_shapes.value == {1: (2, 3.9)}
    analysis.reset_alert()
    assert analysis.prediction_shapes.value == {}


def test_nothing_found_is_recorded_as_nothing_and_shows_as_nothing():
    """An empty prediction records no shapes and renders no result line: the
    row shows the ``dl_404`` warning instead of "Found 0 patches". (The run
    that produces it is pinned by the end-to-end test below.)"""
    from gui.tile.analysis import edit_tools

    analysis = AnalysisState()
    text = edit_tools.found_text(analysis, 1)
    assert text == ""
    edit_tools._record_prediction_shapes(analysis, 1, 0, 0.0)
    assert analysis.prediction_shapes.value == {}


def _prediction_raster(path, prob):
    """A 10 m probability raster in Paraguay's UTM zone, as the CNN writes one."""
    import numpy as np
    import rasterio
    from affine import Affine

    prob = np.asarray(prob, dtype=np.float32)
    with rasterio.open(path, "w", driver="GTiff", width=prob.shape[1], height=prob.shape[0],
                       count=1, dtype="float32", crs="EPSG:32721",
                       transform=Affine(10, 0, 500_000, 0, -10, 7_350_000)) as dst:
        dst.write(prob, 1)
    return str(path)


@pytest.mark.parametrize("empty", ["nothing above the threshold", "only patches under the minimum"])
def test_vectorise_reads_an_empty_prediction_as_nothing_found(tmp_path, empty):
    """The translation the row's "nothing found" rests on, run for real: a
    prediction with no detection, and one whose patches are all smaller than
    the set's minimum size, are both ``([], 0, 0.0)`` -- never a zero-area row
    or a placeholder point."""
    import numpy as np

    from gui.tile.analysis import edit_tools

    prob = np.full((20, 20), 0.1)
    if empty == "only patches under the minimum":
        prob[2:4, 2:4] = 0.9  # 0.04 ha
    path = _prediction_raster(tmp_path / "pred.tif", prob)

    assert edit_tools.vectorise(path, "EPSG:32721", 0.5) == ([], 0, 0.0)


def test_vectorise_offers_only_the_patches_of_the_sets_minimum_size(tmp_path):
    """A 1 ha and a 0.04 ha patch under a 0.5 ha minimum: one patch found, its
    area measured in the project's CRS, its feature in lon/lat for the map."""
    import numpy as np

    from gui.tile.analysis import edit_tools

    prob = np.zeros((30, 30))
    prob[2:12, 2:12] = 0.9
    prob[20:22, 20:22] = 0.9
    path = _prediction_raster(tmp_path / "pred.tif", prob)

    features, n_patches, area_ha = edit_tools.vectorise(path, "EPSG:32721", 0.5)
    assert n_patches == 1 and len(features) == 1
    assert area_ha == pytest.approx(1.0)
    lon, lat = features[0]["geometry"]["coordinates"][0][0]
    assert -60 < lon < -54 and -25 < lat < -23

    assert edit_tools.vectorise(path, "EPSG:32721", 0.0)[1] == 2


# Fix 1: every widget the builder makes must be reachable from the body it
# returns -- see the test's own docstring.
def test_every_widget_the_body_is_built_with_is_on_screen(edit_tools_card):
    """``clear_btn`` was built, wired and never a child of any toolbar, so it
    was unreachable for the life of the module while every test passed. The
    old lookups walked the returned tree and would have said so;
    ``edit_widgets`` reads the builder's own dict, which would not. This is
    what pins reachability now: a slot that is never parented fails here."""
    edit_tools, card, _analysis = edit_tools_card
    on_screen = {id(widget) for widget in _widgets(card)}
    slots = edit_tools.edit_widgets(card)
    assert set(slots) >= {"start_btn", "save_btn", "stop_btn"} | {
        f"dl{n}_{part}" for n in (1, 2) for part in ("run", "add", "remove", "msg", "found")}
    assert sorted(name for name, widget in slots.items() if id(widget) not in on_screen) == []


@pytest.mark.parametrize("model", [1, 2])
def test_a_run_that_finds_nothing_says_so_and_leaves_the_drawing_alone(
    edit_tools_card, monkeypatch, model
):
    """Review Focus: the model returns zero patches. The row says so with the
    dl_404 warning instead of a "Found 0 patches" line, nothing is offered to
    add, and what the reviewer already drew is untouched (not cleared, and not
    marked unsaved by a run the reviewer did not draw)."""
    edit_tools, card, analysis = edit_tools_card
    feedback = _record_feedback(monkeypatch, edit_tools)
    monkeypatch.setattr(edit_tools, "_run_cnn_model", lambda *a, **k: "/tmp/pred.tif")
    monkeypatch.setattr(edit_tools, "vectorise", lambda path, *_: ([], 0, 0.0))
    drawn = object()
    analysis.defo_layer.set(drawn)

    _dl_run_button(card, model).fire_event("click", None)
    feedback.join()

    assert feedback.errors == []
    assert feedback.toasts() == [("warning", t("analysis_tile.model_labels.dl_404"))]
    assert analysis.prediction_shapes.value == {}
    assert _slots(card)[f"dl{model}_found"].children == []
    add, remove = _slots(card)[f"dl{model}_add"], _slots(card)[f"dl{model}_remove"]
    assert add.viz is False and remove.viz is False and _slots(card)[f"dl{model}_msg"].viz is True
    assert analysis.defo_layer.value is drawn and analysis.dirty.value is False


def test_the_refine_badge_follows_the_drawn_polygon(tmp_path):
    import geopandas as gpd
    from shapely.geometry import box as shapely_box

    from gui.tile.analysis import edit_tools

    analysis = AnalysisState()
    seen = []
    edit_tools.wire_refine_badge(analysis, lambda text, color=None: seen.append(text))
    assert seen[-1] == t("analysis_tile.sections.refine_empty")

    gdf = gpd.GeoDataFrame(geometry=[shapely_box(0, 0, 0.01, 0.01),
                                     shapely_box(1, 1, 1.01, 1.01)], crs="EPSG:4326")
    analysis.defo_layer.set(gdf)
    assert "2" in seen[-1] and "ha" in seen[-1]


def test_the_refine_badge_cannot_break_an_alert_change(tmp_path):
    """Ruling 32: this subscriber runs INSIDE ``defo_layer.set()``, so a value
    it cannot measure would raise through ``reset_alert`` and
    ``view_actual_alert`` -- out of a summary line and into navigation. Every
    production writer sets a GeoDataFrame or ``None``, so this is the belt and
    not a live path; it is pinned because Task 10 is what first gives the
    badge a setter and makes the callback run at all."""
    from gui.tile.analysis import edit_tools

    analysis = AnalysisState()
    seen = []
    edit_tools.wire_refine_badge(analysis, lambda text, color=None: seen.append(text))

    analysis.defo_layer.set(object())  # no len(), no area
    assert seen[-1] == t("analysis_tile.sections.refine_empty")  # the badge held
    analysis.reset_alert()  # and the alert change still goes through
    assert analysis.defo_layer.value is None
    assert seen[-1] == t("analysis_tile.sections.refine_empty")


_SQUARE = {"type": "Feature", "properties": {},
           "geometry": {"type": "Polygon", "coordinates": [
               [[0, 0], [0, 0.01], [0.01, 0.01], [0.01, 0], [0, 0]]]}}


def _draw_widgets():
    """``AnalysisWidgets`` reduced to what the edit handlers touch: the views'
    writer, on a headless ``AlertViews``. The reviewer's edits in the browser
    are ``tests._pane_helpers.reviewer_edits``."""
    return SimpleNamespace(panes=make_panes())


def test_a_drawing_edit_marks_the_alert_unsaved_but_seeding_it_does_not(monkeypatch):
    """Ruling 24 / spec §4.4: the reviewer drawing or editing the polygon makes
    the footer read "Unsaved changes on this alert" -- while
    ``view_actual_alert`` *seeding* a freshly opened alert's stored polygon must
    not, which is why the flag hangs off the reviewer's own edits
    (``PaneLayers.on_drawing_edit``) and the edit handlers the reviewer
    operates, never off ``analysis.defo_layer`` or a Python write of the
    drawing."""
    import geopandas as gpd
    from shapely.geometry import box as shapely_box

    import gui.tile.analysis.edit_tools as edit_tools

    analysis = AnalysisState()
    widgets = _draw_widgets()
    body = edit_tools.build_edit_tools(widgets=widgets, app_state=AppState(),
                                       analysis=analysis, app_tile_model=_set_context_fake())
    slots = _slots(body)

    # seeding: view_actual_alert publishing the alert's stored polygon
    analysis.defo_layer.set(gpd.GeoDataFrame(geometry=[shapely_box(0, 0, 0.01, 0.01)],
                                             crs="EPSG:4326"))
    assert analysis.dirty.value is False, "seeding an alert's polygon is not a change"
    # nor is Python writing the drawing (Start Drawing's seeding, Add to map)
    widgets.panes.set_drawing([_SQUARE])
    assert analysis.dirty.value is False, "a Python write of the drawing is not an edit"

    # the reviewer draws (or edits, or deletes) a shape on a view
    reviewer_edits(widgets.panes, [_SQUARE])
    assert analysis.dirty.value is True, "the reviewer's edit is not wired"
    assert analysis.drawing_unapplied.value is True

    # applying the drawing is a change too
    analysis.dirty.set(False)
    slots["save_btn"].fire_event("click", None)
    assert analysis.defo_layer.value is not None and analysis.dirty.value is True

    # and so are adding a model's prediction to the drawing and taking it off
    monkeypatch.setattr(edit_tools, "vectorise", lambda path, *_: _found(patches=1, area=1.0))
    analysis.prediction_files.set({1: "/tmp/pred.tif"})
    for name in ("dl1_add", "dl1_remove"):
        analysis.dirty.set(False)
        slots[name].fire_event("click", None)
        assert analysis.dirty.value is True, name


def test_add_to_map_opens_the_drawing_session_it_needs(monkeypatch):
    """*Run* is in the model row, outside the drawing session, so *Add to map*
    can be the first thing the reviewer clicks. The prediction goes into the
    drawing, and *Start Drawing* seeds the drawing from the stored polygon, so
    one added before the session opened would be replaced by the next *Start
    Drawing*: *Add* opens the session itself, and the panel reads as if the
    reviewer had pressed *Start Drawing* first."""
    import gui.tile.analysis.edit_tools as edit_tools

    analysis = AnalysisState()
    widgets = _draw_widgets()
    # what a run left on the alert: its raster and what it found
    analysis.prediction_files.set({1: "/tmp/pred.tif"})
    analysis.prediction_shapes.set({1: (1, 1.0)})
    body = edit_tools.build_edit_tools(widgets=widgets, app_state=AppState(),
                                       analysis=analysis, app_tile_model=_set_context_fake())
    slots = _slots(body)
    monkeypatch.setattr(edit_tools, "vectorise", lambda path, *_: _found(patches=1, area=1.0))
    views = widgets.panes.widget
    assert views.draw_edit_mode is False, "no drawing session was started"
    assert slots["dl1_add"].disabled is False, "the recorded prediction is on offer"

    slots["dl1_add"].fire_event("click", None)

    # the session is open on every view and the drawing carries the prediction
    assert views.draw_edit_mode is True
    assert len(widgets.panes.drawing_features()) == 1
    assert views.draw["fc"]["features"] == widgets.panes.drawing_features(), "the views got it"
    # and the edit buttons say so
    assert slots["start_btn"].disabled is True
    assert slots["save_btn"].disabled is False and slots["stop_btn"].disabled is False
    # the pair now offers the other direction
    assert slots["dl1_add"].disabled is True and slots["dl1_remove"].disabled is False

    # stopping leaves edit mode and clears the drawing (the views keep a
    # drawing on screen outside edit mode, so the clear is the app's to do),
    # so what the run found is on offer again
    slots["stop_btn"].fire_event("click", None)
    # the added prediction is unsaved work, so Stop asks first
    assert slots["stop_dialog"].v_model is True
    slots["stop_discard"].fire_event("click", None)
    assert views.draw_edit_mode is False
    assert widgets.panes.drawing_features() == [] and views.draw["fc"]["features"] == []
    assert slots["dl1_add"].disabled is False and slots["dl1_remove"].disabled is True


def test_start_drawing_seeds_the_stored_polygon_and_stop_clears_it():
    """The views show the drawing in and out of edit mode; only the toolbar
    follows edit mode. So *Start Drawing* both switches edit mode on and seeds
    the drawing with the alert's stored polygon, and *Stop Drawing* both
    switches it off and empties the drawing -- explicitly, or the polygon
    would stay drawn over the review after the session."""
    import geopandas as gpd
    from shapely.geometry import box as shapely_box

    import gui.tile.analysis.edit_tools as edit_tools

    analysis = AnalysisState()
    widgets = _draw_widgets()
    views = widgets.panes.widget
    body = edit_tools.build_edit_tools(widgets=widgets, app_state=AppState(),
                                       analysis=analysis, app_tile_model=_set_context_fake())
    slots = _slots(body)
    analysis.defo_layer.set(gpd.GeoDataFrame(geometry=[shapely_box(0, 0, 0.01, 0.01)],
                                             crs="EPSG:4326"))
    assert views.draw_edit_mode is False and widgets.panes.drawing_features() == []

    slots["start_btn"].fire_event("click", None)
    assert views.draw_edit_mode is True
    (seeded,) = widgets.panes.drawing_features()
    assert seeded["geometry"]["type"] == "Polygon"
    assert seeded["properties"]["style"]["color"] == "blue"
    assert analysis.dirty.value is False, "opening the session is not an edit"

    slots["stop_btn"].fire_event("click", None)
    assert views.draw_edit_mode is False
    assert widgets.panes.drawing_features() == []

    # with no stored polygon, the session opens on an empty drawing (never a
    # feature without a geometry, which the views would keep in every edit)
    analysis.defo_layer.set(None)
    slots["start_btn"].fire_event("click", None)
    assert views.draw_edit_mode is True and widgets.panes.drawing_features() == []


def test_applying_an_emptied_drawing_clears_the_review_polygon(monkeypatch):
    """A false positive may not keep a drawn polygon and the save refuses one
    (``questionarie.false_positive_drawn``, "delete the polygon or change the
    answer") -- so deleting every shape and pressing *Apply* has to be able to
    clear the stored polygon. An empty *Apply* did nothing at all before,
    which left the reviewer told to delete a polygon they had deleted, with no
    way to resolve it. The layer goes off the views with it."""
    import geopandas as gpd
    from shapely.geometry import box as shapely_box

    import gui.tile.analysis.edit_tools as edit_tools

    from gui.tile.analysis.edit_tools import DEFO_ID

    analysis = AnalysisState()
    widgets = _draw_widgets()
    body = edit_tools.build_edit_tools(widgets=widgets, app_state=AppState(),
                                       analysis=analysis, app_tile_model=_set_context_fake())
    slots = _slots(body)
    # the alert opens on its stored polygon, which went on the views
    analysis.defo_layer.set(gpd.GeoDataFrame(geometry=[shapely_box(0, 0, 0.01, 0.01)],
                                             crs="EPSG:4326"))
    assert widgets.panes.spec(DEFO_ID) is not None
    # the reviewer started drawing and deleted every shape
    slots["start_btn"].fire_event("click", None)
    assert len(widgets.panes.drawing_features()) == 1
    reviewer_edits(widgets.panes, [])
    analysis.dirty.set(False)

    slots["save_btn"].fire_event("click", None)

    assert analysis.defo_layer.value is None, "the emptied drawing did not apply"
    assert widgets.panes.spec(DEFO_ID) is None, "the Defo Layer is still on the views"
    assert analysis.dirty.value is True, "clearing the polygon is a change"
    assert analysis.drawing_unapplied.value is False, "the drawing IS the polygon now"


def test_a_rerun_does_not_strand_the_prediction_already_in_the_drawing(monkeypatch):
    """Run -> Add to map -> Run again. The first prediction's shapes are still
    in the drawing while the second run goes and when it lands, so *Remove from
    map* has to stay live for them -- hidden or dead, those shapes are stranded
    with no button that takes them off. A rerun that finds nothing must not
    leave the previous run's "Found ..." line under its own warning either."""
    import gui.tile.analysis.edit_tools as edit_tools

    analysis = _images_drawn(AnalysisState())
    widgets = _draw_widgets()
    # what the first run left on the alert
    analysis.prediction_files.set({1: "/tmp/pred.tif"})
    analysis.prediction_shapes.set({1: (2, 3.9)})
    body = edit_tools.build_edit_tools(widgets=widgets, app_state=AppState(),
                                       analysis=analysis, app_tile_model=_set_context_fake())
    slots = _slots(body)
    monkeypatch.setattr(edit_tools, "vectorise", lambda path, *_: _found())
    monkeypatch.setattr(edit_tools, "_run_cnn_model", lambda *a, **k: "/tmp/pred.tif")

    slots["dl1_add"].fire_event("click", None)
    assert slots["dl1_remove"].disabled is False
    drawn = widgets.panes.drawing_features()

    # the reviewer runs the model again and it finds something
    feedback = _record_feedback(monkeypatch, edit_tools)
    slots["dl1_run"].fire_event("click", None)
    feedback.join()

    assert widgets.panes.drawing_features() == drawn, "the run left the drawing alone"
    assert slots["dl1_remove"].disabled is False, "the drawn prediction is stranded"

    # and again, finding nothing this time
    feedback = _record_feedback(monkeypatch, edit_tools)
    monkeypatch.setattr(edit_tools, "vectorise", lambda path, *_: ([], 0, 0.0))
    slots["dl1_run"].fire_event("click", None)
    feedback.join()

    assert analysis.prediction_shapes.value == {}, "the previous run's numbers are gone"
    assert slots["dl1_found"].children == [], "no stale Found line under the warning"
    assert slots["dl1_remove"].disabled is False, "the drawn prediction is stranded"
    assert _hidden_above(body, slots["dl1_remove"]) == [], "Remove from map is not on screen"


def test_other_imagery_picked_after_a_run_puts_the_rows_back_to_nothing_run(monkeypatch):
    """A prediction belongs to the composite it ran on.

    Picking other imagery for the alert (``state.imagery_changed``, which the
    timeline's lanes call) invalidates it: the "Found ..." line and *Add to
    map* would otherwise offer a raster of imagery the reviewer is no longer
    looking at. What a previous prediction already put in the drawing is the
    reviewer's own work by then: it stays, and *Remove from map* stays live
    for it.
    """
    import gui.tile.analysis.edit_tools as edit_tools
    from gui.tile.analysis.state import imagery_changed

    analysis = _images_drawn(AnalysisState())
    widgets = _draw_widgets()
    # what a run of both models left on the alert, with the first one's shapes
    # put on the map by the reviewer
    analysis.prediction_files.set({1: "/tmp/pred_m1.tif", 2: "/tmp/pred_m2.tif"})
    analysis.prediction_shapes.set({1: (2, 3.9), 2: (1, 1.0)})
    body = edit_tools.build_edit_tools(widgets=widgets, app_state=AppState(),
                                       analysis=analysis, app_tile_model=_set_context_fake())
    slots = _slots(body)
    monkeypatch.setattr(edit_tools, "vectorise", lambda path, *_: _found())

    slots["dl1_add"].fire_event("click", None)
    drawn = widgets.panes.drawing_features()
    assert drawn, "nothing was added to the drawing"
    assert slots["dl2_add"].disabled is False and slots["dl2_found"].children

    imagery_changed(analysis)

    # model 2: nothing run for this pair, so nothing on offer
    assert slots["dl2_found"].children == [], "a Found line from the other imagery"
    assert slots["dl2_add"].disabled is True, "a prediction of the other imagery on offer"
    assert slots["dl2_remove"].disabled is True
    # model 1: the drawing is untouched and still removable
    assert slots["dl1_found"].children == [], "a Found line from the other imagery"
    assert widgets.panes.drawing_features() == drawn, "the reviewer's drawing was dropped"
    assert slots["dl1_remove"].disabled is False, "the drawn prediction is stranded"
    assert _hidden_above(body, slots["dl1_remove"]) == [], "Remove from map is not on screen"


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


def test_the_form_frame_keeps_field_validation_inline_with_a_justification():
    """The one documented exception, and it must stay documented: every form
    shows its validation error through gui/widget/form_dialog's form_error."""
    rel = "gui/widget/form_dialog.py"
    assert "rv.Alert" in _called(rel), "the field-validation alert disappeared"
    assert "KEPT INLINE ON PURPOSE" in _src(rel), (
        "the retained inline validation lost its justification comment"
    )


# ---------------------------------------------------------------------------
# Final re-review, Minor 3: Save, Download and a CNN run work on the alert
# clicked for, read at the click (UI thread), never on a mix of it and the
# alert shown by the time the worker reads.
# ---------------------------------------------------------------------------
def _alert_a_then_b(app_state, analysis, widgets, polygon=True):
    """Alert A on screen; returns the move to alert B (``view_actual_alert``).
    (No drawn polygon for the edit tools: drawing one needs their maps.)"""
    import geopandas as gpd
    from shapely.geometry import box

    gdf = gpd.GeoDataFrame({"status": ["Not reviewed"] * 2}, geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)],
                           crs="EPSG:4326")
    app_state.alerts_gdf.value = gdf
    app_state.actual_alert_id.value = 0
    if polygon:
        analysis.defo_layer.set("polygon of A")
    analysis.before_info.set(["S2", 1, 0, "before of A"])
    analysis.after_info.set(["S2", 2, 0, "after of A"])
    analysis.before_image.set(object())  # drawn by its pane: a run needs both
    analysis.after_image.set(object())
    widgets.actual_alert_grid = "grid of A"
    analysis.answers.set({"loss_driver": ["logging"]})

    def _move_to_b():
        app_state.actual_alert_id.value = 1
        analysis.reset_alert()
        if polygon:
            analysis.defo_layer.set("polygon of B")
        analysis.before_info.set(["S2", 3, 0, "before of B"])
        analysis.after_info.set(["S2", 4, 0, "after of B"])
        analysis.before_image.set(object())
        analysis.after_image.set(object())
        widgets.actual_alert_grid = "grid of B"
        analysis.answers.set({"loss_driver": ["mining"]})

    return _move_to_b


def _deferred(monkeypatch, module):
    """The click's worker, held until the test runs it."""
    held = []
    monkeypatch.setattr(module, "spawn_in_context", lambda target, args=(), **k: held.append(target))
    return held


@pytest.mark.parametrize("label_key, body", [
    ("analysis_tile.export_labels.save_btn", "_save_attributes"),
    ("analysis_tile.export_labels.build_tile", "_download_data"),
])
def test_save_and_download_work_on_the_alert_clicked_for(monkeypatch, data_root, label_key, body):
    import gui.tile.analysis.export as export

    app_state, analysis = AppState(), AnalysisState()
    widgets = SimpleNamespace()
    card = _export_layout(export, widgets=widgets, app_state=app_state,
                          aux_model=_set_context_fake(), analysis=analysis,
                          app_tile_model=_set_context_fake())
    move_to_b = _alert_a_then_b(app_state, analysis, widgets)
    analysis.verdict.set("Yes")  # A's answer; ``_move_to_b`` forgets it (``reset_alert``)
    _record_feedback(monkeypatch, export)
    held = _deferred(monkeypatch, export)
    seen = []
    monkeypatch.setattr(export, body, lambda *a, **k: seen.append(k.get("alert")))

    _button(card, t(label_key)).fire_event("click", None)
    move_to_b()  # before the worker reads anything
    held[0]()

    alert = seen[0]
    assert alert is not None and alert.alert_id == 0 and alert.grid == "grid of A"
    assert alert.defo_layer == "polygon of A" and alert.before_info[3] == "before of A"
    assert alert.answers == {"loss_driver": ["logging"]} and alert.status == "Yes"


def test_a_model_run_for_a_previous_alert_does_not_start(monkeypatch):
    import gui.tile.analysis.edit_tools as edit_tools

    app_state, analysis = AppState(), AnalysisState()
    widgets = SimpleNamespace()
    card = edit_tools.build_edit_tools(widgets=widgets, app_state=app_state, analysis=analysis,
                                       app_tile_model=_set_context_fake())
    move_to_b = _alert_a_then_b(app_state, analysis, widgets, polygon=False)
    feedback = _record_feedback(monkeypatch, edit_tools)
    held = _deferred(monkeypatch, edit_tools)
    ran = []
    monkeypatch.setattr(edit_tools, "_run_cnn_model", lambda *a, **k: ran.append(k.get("alert")) or "/p.tif")
    monkeypatch.setattr(edit_tools, "vectorise", lambda path, *_: _found())
    run_btn = _dl_run_button(card, 1)

    run_btn.fire_event("click", None)  # a run started while A is shown runs on A's values
    held.pop()()
    assert ran[0].alert_id == 0 and ran[0].grid == "grid of A" and ran[0].after_info[3] == "after of A"

    run_btn.fire_event("click", None)
    move_to_b()  # before the worker starts: nothing runs, nothing is recorded
    held.pop()()
    assert len(ran) == 1
    assert analysis.prediction_files.value == {}
    assert feedback.toasts()[-1] == ("warning", t("analysis_tile.model_labels.dl_stale"))
    assert run_btn.loading is False and run_btn.disabled is False


def test_a_build_that_lands_after_the_alert_change_says_so(monkeypatch):
    """A stale build is a FAILED task + one warning, never a green pill.

    Its files were written into the set's folder, but nothing on screen may
    offer them for the alert now under review — so the reviewer, who waited
    for a slow build, is told, in the shape a stale model run already uses.
    """
    import gui.tile.analysis.export as export

    app_state, analysis = AppState(), AnalysisState()
    widgets = SimpleNamespace()
    card = _export_layout(export, widgets=widgets, app_state=app_state,
                          aux_model=_set_context_fake(), analysis=analysis,
                          app_tile_model=_set_context_fake())
    move_to_b = _alert_a_then_b(app_state, analysis, widgets)
    feedback = _record_feedback(monkeypatch, export)
    held = _deferred(monkeypatch, export)
    monkeypatch.setattr(export, "_download_data", lambda *a, **k: {
        "zip_path": "/set/alert_0.zip", "report_path": "/set/alert_0.docx"})
    build_btn = _button(card, t("analysis_tile.export_labels.build_tile"))

    build_btn.fire_event("click", None)
    move_to_b()  # before the worker's apply: its files belong to A
    held.pop()()

    reason = t("analysis_tile.export_labels.download_stale")
    assert feedback.toasts() == [("warning", reason)]  # and no success beside it
    assert feedback.task().status == TaskStatus.FAILED
    assert feedback.task().error_message == reason
    # The spinner stopped, but the button stays as the alert change left it.
    assert build_btn.loading is False and build_btn.disabled is True


# ---------------------------------------------------------------------------
# analysis/export — the review save as a runner the footer drives
# ---------------------------------------------------------------------------
def _save_harness(tmp_path, monkeypatch):
    """One real review Save, start to finish, on the calling thread.

    A project with one active set (its store lands in ``tmp_path``) and that
    set's alerts, as ``test_set_context`` builds them; the FAO/GAUL admin
    lookups stood in for, the notifier on a private bus, and the worker run
    inline so ``run_save`` has finished by the time it returns.
    Returns ``(app_state, analysis, widgets, app_tile_model, feedback)``.

    The recorder is RETURNED rather than dropped: a caller that wants the
    toasts must not install a second one, because ``_record_feedback`` also
    re-patches ``spawn_in_context`` -- back to a real thread, undoing the
    inline worker this harness promises.
    """
    from datetime import datetime

    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Point, box

    import gui.tile.analysis.export as export
    from deforestation_alerts.project.models import AlertSet, AlertSetParams, Project
    from gui.scripts.set_context import SetContext

    class _AdminLookups:  # stands in for ``review_save.ee``: the FAO/GAUL lookups
        def __getattr__(self, name):
            return lambda *a, **k: self

        def getInfo(self):  # noqa: N802 - Earth Engine's name
            return "Brasil, "

    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("gui.tile.analysis.review_save.ee", _AdminLookups())

    now = datetime.now()
    rel = "periods/per/sets/glad-l"
    project = Project(name="d", created_at=now, updated_at=now)
    project.alert_sets["x"] = AlertSet(
        id="x", period_id="per", sources=["GLAD-L"], digit_map={0: "GLAD-L"},
        params=AlertSetParams(), store_rel_path=f"{rel}/alert_db.parquet",
        combined_rel_path=f"{rel}/c.tif", footprint_rel_path=f"{rel}/footprint.tif",
        fingerprint="f", created_at=now,
    )
    project.active_set = "x"
    app_state = AppState()
    app_state.new_project_state(project)

    poly = box(0, 0, 1, 1)
    alerts = gpd.GeoDataFrame(
        {"status": ["Not reviewed"], "set_id": ["x"]}, geometry=[poly], crs="EPSG:4326"
    )
    alerts["bounding_box"] = gpd.GeoSeries([poly], crs="EPSG:4326")
    alerts["point"] = gpd.GeoSeries([Point(0.5, 0.5)], crs="EPSG:4326")
    for col in ("alert_polygon", "description", "before_img", "after_img",
                "before_img_info", "after_img_info", "admin1", "admin2",
                "admin3", "area_ha"):
        alerts[col] = pd.Series([None], dtype="object")
    app_state.alerts_gdf.value = alerts
    app_state.actual_alert_id.value = 0

    widgets = SimpleNamespace(actual_bb=SimpleNamespace(geometry=lambda: None))
    feedback = _record_feedback(monkeypatch, export)
    # The worker on the calling thread: the assertions below follow the save
    # it started, not a thread that may not have run yet.
    monkeypatch.setattr(
        export, "spawn_in_context", lambda target, args=(), **k: target(*args)
    )
    return app_state, AnalysisState(), widgets, SetContext(app_state), feedback


def test_the_save_runner_advances_only_after_a_save_that_wrote(tmp_path, monkeypatch):
    """Save & next passes ``go_next`` as ``then``: a refused save must leave
    the reviewer on the alert that was not saved.

    ``moved`` alone cannot tell a save that WROTE from one that only ran:
    ``_save_alerts_to_gdf`` returns early on an empty ``_save_target`` while
    ``_save_attributes`` still returns True, so an advance would stay green
    over a store that was never touched. The parquet on disk is asserted on
    both halves, and the refusal's own warning names the reason the reviewer
    is told -- the missing answer, not some later guard tripping by accident.
    """
    import geopandas as gpd

    from gui.tile.analysis import export as export_mod

    app_state, analysis, widgets, model, feedback = _save_harness(tmp_path, monkeypatch)
    store = tmp_path / "d" / "periods" / "per" / "sets" / "glad-l" / "alert_db.parquet"
    moved = []
    run_save = export_mod.make_save_runner(widgets, app_state, analysis, model)

    analysis.verdict.set(None)              # no answer: _save_attributes refuses
    run_save(None, then=lambda: moved.append(1))
    assert moved == []
    assert ("warning", t("analysis_tile.questionarie.confirmation_missing")) \
        in feedback.toasts()
    assert not store.exists(), "a refused save must write nothing"

    analysis.verdict.set("Confirmed")
    run_save(None, then=lambda: moved.append(1))
    assert moved == [1]
    assert ("success", t("analysis_tile.export_labels.save_done")) in feedback.toasts()
    # The store, not just the advance: the verdict reached the parquet.
    assert store.exists(), "the save advanced without writing the alert store"
    assert list(gpd.read_parquet(store)["status"]) == ["Confirmed"]


def test_an_edit_made_while_the_save_runs_stays_unsaved_and_does_not_advance(
    tmp_path, monkeypatch
):
    """A save writes the values captured at the click (``capture_alert``).

    A verdict or driver change made while the GEE admin lookups block is not
    in what was written, so completion must not clear "Unsaved changes" -- and
    Save & next must not navigate off it, which loses the change with nothing
    on screen to notice it by. The store write itself stands: it is the
    earlier review, and it landed.
    """
    import geopandas as gpd

    from gui.tile.analysis import export as export_mod
    from gui.tile.analysis.state import touch

    app_state, analysis, widgets, model, feedback = _save_harness(tmp_path, monkeypatch)
    store = tmp_path / "d" / "periods" / "per" / "sets" / "glad-l" / "alert_db.parquet"
    moved = []
    run_save = export_mod.make_save_runner(widgets, app_state, analysis, model)
    analysis.verdict.set("Confirmed")

    real_save = export_mod._save_attributes

    def _save_while_the_reviewer_edits(*a, **k):
        wrote = real_save(*a, **k)
        touch(analysis)  # the reviewer picks another driver meanwhile
        return wrote

    monkeypatch.setattr(export_mod, "_save_attributes", _save_while_the_reviewer_edits)
    run_save(None, then=lambda: moved.append(1))

    assert list(gpd.read_parquet(store)["status"]) == ["Confirmed"], "nothing was written"
    assert analysis.dirty.value is True, "the later edit was reported as saved"
    assert moved == [], "Save & next navigated off an unsaved change"
    # ...and the reviewer is told, or the refused advance after pressing
    # *Save & next* has nothing on screen that explains it.
    assert ("warning", t("analysis_tile.export_labels.save_outpaced")) in feedback.toasts()


def test_a_second_save_cannot_start_while_one_is_running(tmp_path, monkeypatch):
    """One writer at a time, and both save buttons say so.

    The footer has two buttons on the same runner, and only the clicked
    one used to be disabled: the other could start a second writer over the
    same store and the same row while the first was still in its admin
    lookups, with the later writer's row values winning by luck of timing.
    """
    from pysepal import sepalwidgets as sw

    from gui.tile.analysis import export as export_mod

    app_state, analysis, widgets, model, feedback = _save_harness(tmp_path, monkeypatch)
    held = []
    monkeypatch.setattr(export_mod, "spawn_in_context",
                        lambda target, args=(), **k: held.append(lambda: target(*args)))
    run_save = export_mod.make_save_runner(widgets, app_state, analysis, model)
    save = sw.Btn(msg="Save")
    save_next = sw.Btn(msg="Save & next")
    run_save.guard(save_next, save)
    analysis.verdict.set("Confirmed")

    run_save(save)
    assert save.disabled is True
    assert save_next.disabled is True, "the other save button stayed live"

    run_save(save_next)  # ...and pressing it anyway starts nothing
    assert len(held) == 1, "a second writer started over the first"

    held.pop()()  # the first save finishes
    assert save.disabled is False and save.loading is False
    assert save_next.disabled is False


def test_a_save_that_lands_after_the_alert_change_does_not_advance(tmp_path, monkeypatch):
    """A stale *Save & next* must not move the reviewer on from where they are.

    The save is slow (GEE admin lookups plus the store write), so the reviewer
    can navigate by hand while it runs. ``then`` is ``app_state.go_next``: left
    unguarded, the late save advances from the alert the reviewer chose, so they
    land one alert FURTHER than where they went, with nothing to say why. The
    write itself still happened — the success toast below proves this is not a
    refusal passing for a guard.
    """
    import gui.tile.analysis.export as export

    app_state, analysis, widgets, model, _ = _save_harness(tmp_path, monkeypatch)
    feedback = _record_feedback(monkeypatch, export)
    held = _deferred(monkeypatch, export)
    moved = []
    run_save = export.make_save_runner(widgets, app_state, analysis, model)

    analysis.verdict.set("Confirmed")
    run_save(None, then=lambda: moved.append(1))  # the save starts here...
    analysis.reset_alert()                        # ...the reviewer moves on...
    held.pop()()                                  # ...and only now does it land

    assert ("success", t("analysis_tile.export_labels.save_done")) in feedback.toasts()
    assert moved == []


def test_model_rows_hold_run_add_remove_on_one_line(edit_tools_card):
    _edit_tools, card, _analysis = edit_tools_card
    slots = _slots(card)
    for n in (1, 2):
        first_line = slots[f"dl{n}_row"].children[0]
        assert slots[f"dl{n}_run"] in first_line.children
        assert slots[f"dl{n}_add"] in first_line.children
        assert slots[f"dl{n}_remove"] in first_line.children
    assert t("analysis_tile.model_labels.dl_button1") == "Model 1"
    assert t("analysis_tile.edition_labels.apply_edit_button") == "Save drawing"


def _drawing_session_with_an_edit():
    """A drawing session whose drawing the reviewer edited and did not save."""
    import geopandas as gpd
    from shapely.geometry import box as shapely_box

    import gui.tile.analysis.edit_tools as edit_tools
    from gui.tile.analysis.state import drawing_changed

    analysis = AnalysisState()
    widgets = _draw_widgets()
    body = edit_tools.build_edit_tools(widgets=widgets, app_state=AppState(),
                                       analysis=analysis, app_tile_model=_set_context_fake())
    slots = _slots(body)
    analysis.defo_layer.set(gpd.GeoDataFrame(geometry=[shapely_box(0, 0, 0.01, 0.01)], crs="EPSG:4326"))
    slots["start_btn"].fire_event("click", None)
    drawing_changed(analysis)  # the reviewer's edit, as ``on_drawing_edit`` reports it
    return analysis, widgets, slots


def test_stop_drawing_asks_before_discarding_an_unsaved_drawing():
    analysis, widgets, slots = _drawing_session_with_an_edit()
    slots["stop_btn"].fire_event("click", None)
    assert slots["stop_dialog"].v_model is True
    assert widgets.panes.widget.draw_edit_mode is True, "nothing is stopped before the answer"

    slots["stop_keep"].fire_event("click", None)
    assert slots["stop_dialog"].v_model is False
    assert widgets.panes.widget.draw_edit_mode is True and widgets.panes.drawing_features()


def test_discard_stops_and_drops_the_unsaved_drawing():
    analysis, widgets, slots = _drawing_session_with_an_edit()
    before = analysis.defo_layer.value
    slots["stop_btn"].fire_event("click", None)
    slots["stop_discard"].fire_event("click", None)
    assert widgets.panes.widget.draw_edit_mode is False and widgets.panes.drawing_features() == []
    assert analysis.defo_layer.value is before and analysis.drawing_unapplied.value is False


def test_save_and_stop_stores_the_drawing_then_stops():
    analysis, widgets, slots = _drawing_session_with_an_edit()
    widgets.panes.set_drawing([])  # the reviewer deleted the polygon
    slots["stop_btn"].fire_event("click", None)
    slots["stop_save"].fire_event("click", None)
    assert analysis.defo_layer.value is None, "the emptied drawing was applied"
    assert widgets.panes.widget.draw_edit_mode is False


def test_stop_drawing_with_nothing_unsaved_does_not_ask():
    import geopandas as gpd
    from shapely.geometry import box as shapely_box

    import gui.tile.analysis.edit_tools as edit_tools

    analysis = AnalysisState()
    widgets = _draw_widgets()
    body = edit_tools.build_edit_tools(widgets=widgets, app_state=AppState(),
                                       analysis=analysis, app_tile_model=_set_context_fake())
    slots = _slots(body)
    analysis.defo_layer.set(gpd.GeoDataFrame(geometry=[shapely_box(0, 0, 0.01, 0.01)], crs="EPSG:4326"))
    slots["start_btn"].fire_event("click", None)
    slots["stop_btn"].fire_event("click", None)
    assert slots["stop_dialog"].v_model is False
    assert widgets.panes.widget.draw_edit_mode is False
