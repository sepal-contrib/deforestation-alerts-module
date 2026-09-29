"""User feedback is published through the pysepal notification system.

Task 7 removed every inline feedback surface from the app (``solara.Error`` /
``solara.Info`` progress elements and the ``sw.Alert`` widgets) in favour of the
notification bus. These tests assert at the **module boundary**: the notifier the
``use_notifications()`` hook returns is a silent ``NoopNotifier`` when no
``NotificationProvider`` is mounted, so a test that mounted nothing would pass
against a completely unwired app. Instead each raw-widget module's ``notify`` /
``track_task`` globals are replaced with recorders and the real click handler +
worker is driven end to end, so what is asserted is the message that a real run
actually publishes — plus the state change it accompanies.

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
from pathlib import Path

import pytest

from deforestation_alerts.message import cm
from deforestation_alerts.model.alerts_model import SelectedAlertsModel
from deforestation_alerts.model.analysis_model import AlertAnalysisModel
from deforestation_alerts.model.app_model import AppTileModel
from deforestation_alerts.model.aux_model import AuxModel
from gui.scripts.solara_threads import spawn_in_context
from gui.store.state_manager import AppState


# ---------------------------------------------------------------------------
# Recorders
# ---------------------------------------------------------------------------
class _RecordingTask:
    """Stand-in for a pysepal ``TaskTracker``.

    Appends into the SAME event list the ``notify`` recorder writes to, so a test
    can assert the *ordering* between a tracker transition and a toast (the
    contract is: ``fail()`` first, so the run is already recorded as FAILED —
    with its reason — by the time the error toast appears).
    """

    def __init__(self, events, title, total_steps=None):
        self._events = events
        events.append(("task:start", title, total_steps))

    def step(self, message):
        self._events.append(("task:step", message))

    def complete(self, message=None):
        self._events.append(("task:complete", message))

    def fail(self, message):
        self._events.append(("task:fail", message))

    def cancel(self):
        self._events.append(("task:cancel", None))

    def set_progress(self, value, detail=None):
        pass

    def update(self, title):
        pass


def _record_feedback(monkeypatch, module):
    """Replace ``module``'s notification globals with recorders.

    The real ``spawn_in_context`` still runs (real daemon thread, real kernel-
    context bridge); the wrapper only keeps the handles so the test can join.
    Returns ``(events, threads)``.
    """
    events = []
    threads = []

    monkeypatch.setattr(
        module,
        "notify",
        lambda message, type_="info", **kw: events.append(("toast", type_, message)),
    )
    monkeypatch.setattr(
        module,
        "track_task",
        lambda title, total_steps=None: _RecordingTask(events, title, total_steps),
    )

    def _spawn(target, args=(), **kwargs):
        thread = spawn_in_context(target, args, **kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(module, "spawn_in_context", _spawn)
    return events, threads


def _join(threads):
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "worker thread did not finish"


def _toasts(events):
    return [(type_, message) for kind, type_, message in
            (e for e in events if e[0] == "toast")]


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
# recipe_tile — raw ipyvuetify layout + daemon workers -> notify()/track_task()
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
    events, threads = _record_feedback(monkeypatch, recipe_tile)
    folder = ws.ensure_dir("recipe_test")
    app_state.recipe_folder_path.value = folder

    _button(layout, cm.recipe_tile.button_save_recipe).fire_event("click", None)
    _join(threads)

    # the toast reports a save that really happened -- in the user's workspace
    assert ws.exists(folder + "/recipe_parameters.json")
    assert _toasts(events) == [("success", cm.recipe_tile.alert_save_recipe_done)]


def test_recipe_save_failure_publishes_an_error_toast(
    recipe_layout, ws, monkeypatch
):
    recipe_tile, layout, app_state, _model, _aoi = recipe_layout
    events, threads = _record_feedback(monkeypatch, recipe_tile)
    # a folder that does not exist -> _run_save raises FileNotFoundError
    gone = ws.results_dir("gone")
    app_state.recipe_folder_path.value = gone

    _button(layout, cm.recipe_tile.button_save_recipe).fire_event("click", None)
    _join(threads)

    assert not ws.exists(gone)
    assert ws.client.files.files == {}  # and nothing was written anyway
    (type_, message), = _toasts(events)
    assert type_ == "error"
    assert message.startswith(cm.recipe_tile.input_hint_save_recipe_error)


def test_recipe_load_tracks_its_phases_and_publishes_success(
    recipe_layout, ws, monkeypatch
):
    recipe_tile, layout, app_state, app_tile_model, aoi_date = recipe_layout
    events, threads = _record_feedback(monkeypatch, recipe_tile)

    params = {"recipe_name": "recipe_from_disk", "actual_alert_id": 0}
    folder = ws.results_dir("recipe_from_disk")
    recipe_file = folder + "/recipe_parameters.json"
    ws.write_json(recipe_file, params)
    _find_load_input(layout).v_model = recipe_file

    _button(layout, cm.recipe_tile.button_load_recipe).fire_event("click", None)
    _join(threads)

    # the load really ran: models imported and the folder published
    assert aoi_date.imported == params
    assert app_tile_model.recipe_name == "recipe_from_disk"
    assert app_state.recipe_folder_path.value == folder
    # and it was reported as a 2-phase tracked task followed by one success toast
    assert events[0] == ("task:start", cm.recipe_tile.load_task_title, 2)
    assert [e for e in events if e[0] == "task:step"] == [
        ("task:step", cm.recipe_tile.load_step_parameters),
        ("task:step", cm.recipe_tile.loader_loading_alert_db),
    ]
    assert events[-2] == ("task:complete", None)
    assert _toasts(events) == [("success", cm.recipe_tile.alert_load_recipe_done)]


@pytest.mark.parametrize("shape", ["home_relative", "root_relative"])
def test_recipe_load_normalizes_a_non_absolute_picked_path(
    recipe_layout, ws, monkeypatch, shape
):
    """picker -> adapter boundary: ``FileInput`` fills ``v_model`` from the path
    the SERVER echoes, which is not built here and is not trusted. A path that
    is not absolute must still resolve to the right remote recipe folder rather
    than failing the load or anchoring the alert store somewhere else."""
    recipe_tile, layout, app_state, app_tile_model, aoi_date = recipe_layout
    events, threads = _record_feedback(monkeypatch, recipe_tile)

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
    _join(threads)

    assert aoi_date.imported == params
    # the store is anchored on the ABSOLUTE remote folder, not on the echo
    assert app_state.recipe_folder_path.value == folder
    assert app_tile_model.recipe_folder_path == folder
    assert _toasts(events) == [("success", cm.recipe_tile.alert_load_recipe_done)]


def test_recipe_load_refuses_a_picked_path_outside_the_workspace(
    recipe_layout, ws, monkeypatch
):
    """An echoed path pointing somewhere else entirely must fail visibly instead
    of silently pointing the alert store at another folder."""
    recipe_tile, layout, app_state, _model, _aoi = recipe_layout
    events, threads = _record_feedback(monkeypatch, recipe_tile)
    _find_load_input(layout).v_model = "/home/other-user/recipe_parameters.json"

    _button(layout, cm.recipe_tile.button_load_recipe).fire_event("click", None)
    _join(threads)

    assert app_state.recipe_folder_path.value in (None, "")
    (type_, message), = _toasts(events)
    assert type_ == "error"
    assert message.startswith(cm.recipe_tile.input_hint_load_recipe_name)


def test_recipe_load_failure_fails_the_task_before_the_error_toast(
    recipe_layout, monkeypatch
):
    recipe_tile, layout, _app_state, _model, _aoi = recipe_layout
    events, threads = _record_feedback(monkeypatch, recipe_tile)
    _find_load_input(layout).v_model = ""  # nothing selected -> FileNotFoundError

    _button(layout, cm.recipe_tile.button_load_recipe).fire_event("click", None)
    _join(threads)

    kinds = [e[0] for e in events]
    assert "task:complete" not in kinds
    # fail() lands BEFORE the toast: the pill is already FAILED (and so stays on
    # screen) by the time the user reads the message.
    assert kinds.index("task:fail") < kinds.index("toast")
    (type_, message), = _toasts(events)
    assert type_ == "error"
    assert message.startswith(cm.recipe_tile.input_hint_load_recipe_name)


def _find_load_input(layout):
    """The recipe picker: pysepal's REMOTE-capable FileInput (not ``sw.FileInput``,
    which is a different class that browses the container's disk)."""
    from pysepal.sepalwidgets.file_input import FileInput

    for widget in _widgets(layout):
        if isinstance(widget, FileInput):
            return widget
    raise AssertionError("no FileInput in the recipe layout")


# ---------------------------------------------------------------------------
# analysis/export — raw layout + daemon workers -> notify()/track_task()
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
    events, threads = _record_feedback(monkeypatch, export)
    monkeypatch.setattr(export, "_save_attributes", lambda *a, **k: True)

    download_btn = _button(card, cm.analysis_tile.export_labels.download_alert_data_btn)
    assert download_btn.disabled is True

    _button(card, cm.analysis_tile.export_labels.save_btn).fire_event("click", None)
    _join(threads)

    # the success toast accompanies the real post-save apply step
    assert download_btn.disabled is False
    assert events[0] == ("task:start", cm.analysis_tile.export_labels.save_task_title, None)
    assert ("task:step", cm.analysis_tile.export_labels.save_step) in events
    assert ("task:complete", None) in events
    assert _toasts(events) == [
        ("success", cm.analysis_tile.export_labels.save_done)
    ]


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
    """
    export, card, app_state, _app_tile_model = export_card
    events, threads = _record_feedback(monkeypatch, export)

    if with_alerts:
        app_state.alerts_gdf.value = _one_row_gdf()
    app_state.actual_alert_id.value = alert_id

    save_btn = _button(card, cm.analysis_tile.export_labels.save_btn)
    download_btn = _button(card, cm.analysis_tile.export_labels.download_alert_data_btn)
    save_btn.fire_event("click", None)
    _join(threads)

    expected = str(cm.analysis_tile.export_labels.save_no_alert)
    kinds = [e[0] for e in events]
    assert "task:complete" not in kinds
    # fail() before the toast, so the run is logged as FAILED before the message
    assert kinds.index("task:fail") < kinds.index("toast")
    assert ("task:fail", expected) in events
    assert _toasts(events) == [("warning", expected)]
    # nothing was enabled, and the save button is released
    assert download_btn.disabled is True
    assert save_btn.loading is False and save_btn.disabled is False


def test_export_save_failure_fails_the_task_before_the_error_toast(
    export_card, monkeypatch
):
    export, card, _app_state, _app_tile_model = export_card
    events, threads = _record_feedback(monkeypatch, export)

    def _boom(*a, **k):
        raise ValueError("no alert selected")

    monkeypatch.setattr(export, "_save_attributes", _boom)

    save_btn = _button(card, cm.analysis_tile.export_labels.save_btn)
    save_btn.fire_event("click", None)
    _join(threads)

    # the button is released even on the failure path
    assert save_btn.loading is False and save_btn.disabled is False
    kinds = [e[0] for e in events]
    assert "task:complete" not in kinds
    assert kinds.index("task:fail") < kinds.index("toast")
    # the exception text is what reaches the user, not a bool/placeholder
    assert _toasts(events) == [("error", "no alert selected")]


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
    pill, no files, no explanation.
    """
    export, card, app_state, app_tile_model = export_card
    events, threads = _record_feedback(monkeypatch, export)

    app_tile_model.recipe_folder_path = recipe_folder
    if with_alerts:
        app_state.alerts_gdf.value = _one_row_gdf()
        app_state.actual_alert_id.value = 0

    toolbar = _download_toolbar(card)
    download_btn = _button(
        card, cm.analysis_tile.export_labels.download_alert_data_btn
    )
    download_btn.fire_event("click", None)
    _join(threads)

    kinds = [e[0] for e in events]
    assert "task:complete" not in kinds
    # fail() before the toast, so the pill is already FAILED when it appears
    assert kinds.index("task:fail") < kinds.index("toast")
    assert ("task:fail", str(expected)) in events
    assert _toasts(events) == [("warning", str(expected))]
    # nothing was revealed, and the button is released
    assert toolbar.viz is False
    assert download_btn.loading is False and download_btn.disabled is False


# ---------------------------------------------------------------------------
# analysis/edit_tools — raw layout + daemon workers -> notify()/track_task()
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


def test_edit_tools_model_run_publishes_success(edit_tools_card, monkeypatch):
    edit_tools, card, analyzed_alerts_model = edit_tools_card
    events, threads = _record_feedback(monkeypatch, edit_tools)
    monkeypatch.setattr(edit_tools, "_run_cnn_model", lambda *a, **k: "/tmp/pred_m1.tif")
    monkeypatch.setattr(edit_tools, "verify_raster", lambda path, threshold: True)

    _dl_run_button(card, "M 1").fire_event("click", None)
    _join(threads)

    # the toast accompanies the real apply step (the prediction is on the model)
    assert analyzed_alerts_model.model1_prediction_file == "/tmp/pred_m1.tif"
    assert events[0] == (
        "task:start", cm.analysis_tile.model_labels.dl_task_title, None
    )
    assert ("task:step", str(cm.analysis_tile.model_labels.dl_step_run)) in events
    assert ("task:complete", None) in events
    assert _toasts(events) == [
        ("success", str(cm.analysis_tile.model_labels.dl_done))
    ]


def test_edit_tools_model_run_failure_fails_the_task_before_the_error_toast(
    edit_tools_card, monkeypatch
):
    edit_tools, card, _model = edit_tools_card
    events, threads = _record_feedback(monkeypatch, edit_tools)

    def _boom(*a, **k):
        raise RuntimeError("model download failed")

    monkeypatch.setattr(edit_tools, "_run_cnn_model", _boom)

    run_btn = _dl_run_button(card, "M 1")
    run_btn.fire_event("click", None)
    _join(threads)

    assert run_btn.loading is False and run_btn.disabled is False
    kinds = [e[0] for e in events]
    assert "task:complete" not in kinds
    assert kinds.index("task:fail") < kinds.index("toast")
    assert _toasts(events) == [
        ("error", str(cm.analysis_tile.model_labels.dl_404))
    ]


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
    # can run — so it belongs on the globals side of this boundary.
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


def test_raw_layouts_use_the_globals_not_the_hook():
    """Raw widget layouts publish from worker threads, where no hook can run."""
    for rel in RAW_LAYOUTS:
        called = _called(rel)
        imported = _imported_names(rel)
        assert "pysepal.solara.notifications.notify" in imported, (
            f"{rel} does not import the notify() global"
        )
        assert "pysepal.solara.notifications.track_task" in imported, (
            f"{rel} does not import the track_task() global"
        )
        assert "notify" in called, f"{rel} publishes no toast"
        assert "track_task" in called, f"{rel} tracks no long-running work"
        assert "use_notifications" not in called, (
            f"{rel} is a raw layout: it must not call the use_notifications() hook"
        )


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
