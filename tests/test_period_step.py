"""Period step (spec §6.4): the period list, its modal and §5.1's rules, rendered.

``PeriodStep`` renders against a real ``AppState``/``Project`` and a real
``MapLayers`` (builds run synchronously). Its ProductTable, PeriodModal and
ConfirmDialog children are recorders, so a test reads the rows and calls the
callbacks the user would click; ``PeriodModal`` is rendered for real in its own
tests below.
"""

import threading
import uuid
from datetime import date, datetime
from types import SimpleNamespace

import ipyvuetify as v
import pytest
import reacton
import solara
from pysepal.solara.notifications import NotificationProvider
from pysepal.solara.notifications.bus import get_current_bus

from deforestation_alerts.engine import fingerprint as fp
from deforestation_alerts.project import periods
from deforestation_alerts.project.models import (
    AlertSet, AlertSetParams, AoiRecord, Artifact, GridSpec, PeriodMask, Project, add_predefined_source,
)
from gui.i18n import t, tn
from gui.scripts import map_layers
from gui.scripts.map_layers import MapLayers
from gui.store.project_writers import writing
from gui.store.state_manager import AppState
from gui.tile import period_step
from gui.widget import period_modal
from gui.widget.enter_key import ENTER_DEFAULT

JAN_ID, FEB_ID = "2026-01-01_2026-01-31", "2026-02-01_2026-02-28"
GRID = GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=8900000, width=40, height=40)


class _Map:
    def add_layer(self, layer, key=""):
        pass

    def remove_layer(self, key, none_ok=False):
        pass


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(map_layers, "spawn_in_context",
                        lambda target, args=(), **kw: target(*args) or threading.current_thread())
    seen = {}
    monkeypatch.setattr(period_step, "ProductTable", lambda **kw: seen.__setitem__("table", kw))
    monkeypatch.setattr(period_step, "PeriodModal", lambda **kw: seen.__setitem__("modal", kw))
    monkeypatch.setattr(period_step, "ConfirmDialog", lambda **kw: seen.__setitem__("confirm", kw))
    return SimpleNamespace(seen=seen, pdir=tmp_path / "per")


def _file(pdir, rel):
    path = pdir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return rel


def _project(pdir, name="per"):
    """January and February (February active), each with a GLAD-L download and
    harmonized file recorded under an old fingerprint (out of date)."""
    p = Project(name=name, created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="d", bounds=(0, 0, 1, 1))
    p.grid = GRID
    add_predefined_source(p, "GLAD-L")
    for a, b in (("2026-01-01", "2026-01-31"), ("2026-02-01", "2026-02-28")):
        assert periods.new_period(p, a, b) is None
    arts = {}
    for pid in (JAN_ID, FEB_ID):
        for key, kind, rel in ((fp.raw_key(pid, "GLAD-L"), "raw_alert", f"periods/{pid}/raw/raw.tif"),
                               (fp.harm_key(pid, "GLAD-L"), "harmonized", f"periods/{pid}/harmonized/h.tif")):
            arts[key] = Artifact(key=key, kind=kind, rel_path=_file(pdir, rel), fingerprint="old", inputs={},
                                 content_id=uuid.uuid4().hex, created_at=datetime.now())
    p.artifacts = arts
    return p


def _add_set(p, pdir, pid):
    sid = f"{pid}__glad-l"
    folder = f"periods/{pid}/sets/glad-l"
    _file(pdir, f"{folder}/alert_db.parquet")
    _file(pdir, f"{folder}/alerts.pmtiles")
    p.alert_sets = {**p.alert_sets, sid: AlertSet(
        id=sid, period_id=pid, sources=["GLAD-L"], digit_map={0: "GLAD-L"}, params=AlertSetParams(),
        store_rel_path=f"{folder}/alert_db.parquet", combined_rel_path=f"{folder}/c.tif",
        footprint_rel_path=f"{folder}/footprint.tif", n_alerts=5, n_reviewed=2, fingerprint="f",
        created_at=datetime.now())}
    return sid


def _state(p):
    state = AppState()
    state.new_project_state(p)
    return state


@solara.component
def _Harness(app_state, layers):
    NotificationProvider()  # use_notifications() needs the provider rendered first
    period_step.PeriodStep(app_state=app_state, layers=layers)


def _render(state):
    layers = MapLayers(_Map())
    _, rc = reacton.render(_Harness(app_state=state, layers=layers), handle_error=False)
    return layers, rc


def _toasts(kind):
    return [x.message for x in get_current_bus().toasts.value if x.type.value == kind]


def _action(seen, pid, kind):
    row = next(r for r in seen["table"]["rows"] if r["key"] == pid)
    return next(a for a in row["actions"] if a["kind"] == kind)


def _button(rc, label):
    return next(b for b in rc.find(v.Btn).widgets if label in b.children)


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------
def test_rows_are_newest_first_with_the_window_the_sets_and_the_badges(env):
    p = _project(env.pdir)
    p.periods = {k: v.model_copy(update={"look_back_days": 30}) for k, v in p.periods.items()}
    _add_set(p, env.pdir, JAN_ID)
    rows = {r["key"]: r for r in period_step.period_rows(p, env.pdir, select=None, edit=None, delete=None)}
    assert list(rows) == [FEB_ID, JAN_ID]
    jan, feb = rows[JAN_ID], rows[FEB_ID]
    assert [c["value"] for c in jan["cells"]] == ["2026-01-01 → 2026-01-31", "1", t("steps.period.state_stale")]
    texts = [n["text"] for n in jan["notes"]]
    assert texts == [
        t("steps.period.window", start="2025-12-02", end="2026-01-31"),
        t("steps.period.note_no_earlier"),
        t("steps.period.note_sources_stale", count=1),
        t("steps.period.note_sets_stale", count=1),
        t("steps.period.note_frozen"),
    ]
    assert t("steps.period.note_no_earlier") not in [n["text"] for n in feb["notes"]]  # January's set is earlier
    assert feb["cells"][1]["value"] == "0" and t("steps.period.note_frozen") not in [n["text"] for n in feb["notes"]]
    assert [a["kind"] for a in feb["actions"]] == ["select", "edit", "delete"]
    assert len(feb["actions"]) == period_step.PERIOD_ACTIONS
    assert feb["actions"][0]["is_on"] and not jan["actions"][0]["is_on"]


def test_a_period_with_nothing_out_of_date_shows_no_badge(env):
    p = _project(env.pdir)
    p.artifacts = {}
    row = period_step.period_rows(p, env.pdir, select=None, edit=None, delete=None)[0]
    assert row["cells"][2] == {"value": "—", "muted": True} and len(row["notes"]) == 2  # the window and "no earlier set"


def test_selecting_a_row_sets_the_active_period(env):
    state = _state(_project(env.pdir))
    layers, rc = _render(state)
    try:
        _action(env.seen, JAN_ID, "select")["on_click"]()
        assert state.project.value.active_period == JAN_ID
        assert _action(env.seen, JAN_ID, "select")["is_on"]
    finally:
        rc.close()


# ---------------------------------------------------------------------------
# New period, edit
# ---------------------------------------------------------------------------
def test_a_new_period_copies_the_mask_choices_of_the_latest_period(env):
    p = _project(env.pdir)
    p.periods = {**p.periods, FEB_ID: p.periods[FEB_ID].model_copy(
        update={"masks": {"forest": PeriodMask(on=True, mode="exclude")}})}
    state = _state(p)
    layers, rc = _render(state)
    try:
        assert env.seen["modal"]["open_"].value is False
        _button(rc, t("steps.period.new")).fire_event("click", None)
        modal = env.seen["modal"]
        assert modal["open_"].value is True and modal["editing"] is None
        assert modal["on_submit"]("2026-03-01", "2026-03-31", "90") is None
        live = state.project.value
        assert live.active_period == "2026-03-01_2026-03-31"
        assert live.periods["2026-03-01_2026-03-31"].masks == {"forest": PeriodMask(on=True, mode="exclude")}
        assert _toasts("success") == [t("steps.period.created")]
        before = state.project.value
        assert modal["on_submit"]("2026-03-01", "2026-03-31", "90") == "steps.period.error_exists"
        assert state.project.value is before  # a refused change neither publishes nor saves
    finally:
        rc.close()


def test_a_period_with_sets_opens_the_dialog_and_a_look_back_only_save_keeps_its_files(env):
    p = _project(env.pdir)
    _add_set(p, env.pdir, JAN_ID)
    state = _state(p)
    layers, rc = _render(state)
    try:
        _action(env.seen, JAN_ID, "edit")["on_click"]()
        modal = env.seen["modal"]
        assert modal["open_"].value is True and modal["editing"] == JAN_ID
        assert modal["on_submit"]("2026-01-01", "2026-01-31", "10") is None
        live = state.project.value
        assert live.periods[JAN_ID].look_back_days == 10 and live.alert_sets and live.artifacts
        assert (env.pdir / "periods" / JAN_ID / "raw" / "raw.tif").exists()
        assert _toasts("success") == [t("steps.period.saved")]
        # changed dates on a period with sets are still refused
        assert modal["on_submit"]("2026-01-02", "2026-01-31", "10") == "steps.period.error_locked"
    finally:
        rc.close()


def test_new_dates_give_a_new_id_and_delete_the_old_windows_files_and_layers(env):
    p = _project(env.pdir)
    state = _state(p)
    layers, rc = _render(state)
    try:
        layers.show("src:GLAD-L", lambda: None, content_id=p.artifacts[fp.harm_key(FEB_ID, "GLAD-L")].content_id)
        _action(env.seen, FEB_ID, "edit")["on_click"]()
        modal = env.seen["modal"]
        assert modal["editing"] == FEB_ID and modal["open_"].value is True
        assert modal["on_submit"]("2026-02-01", "2026-02-15", "90") is None
        live = state.project.value
        assert live.active_period == "2026-02-01_2026-02-15" and FEB_ID not in live.periods
        assert not (env.pdir / "periods" / FEB_ID).exists() and (env.pdir / "periods" / JAN_ID).exists()
        assert not layers.is_on("src:GLAD-L")
    finally:
        rc.close()


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
def test_a_period_with_sets_that_is_not_the_latest_cannot_be_deleted(env):
    p = _project(env.pdir)
    _add_set(p, env.pdir, JAN_ID)
    state = _state(p)
    layers, rc = _render(state)
    try:
        _action(env.seen, JAN_ID, "delete")["on_click"]()
        assert env.seen["confirm"]["open"] is False
        assert _toasts("warning") == [t("steps.period.error_not_latest")]
        assert JAN_ID in state.project.value.periods
    finally:
        rc.close()


def test_deleting_the_latest_period_removes_its_sets_and_every_file(env):
    p = _project(env.pdir)
    jan = _add_set(p, env.pdir, JAN_ID)
    feb = _add_set(p, env.pdir, FEB_ID)
    p.active_set = feb
    state = _state(p)
    layers, rc = _render(state)
    try:
        layers.show(f"alerts:set:{feb}", lambda: None, content_id="tiles")
        _action(env.seen, FEB_ID, "delete")["on_click"]()
        confirm = env.seen["confirm"]
        assert confirm["open"] is True
        assert confirm["message"] == t("steps.period.delete_sets", count=1, reviewed=2)
        confirm["on_confirm"]()
        live = state.project.value
        assert list(live.periods) == [JAN_ID] and list(live.alert_sets) == [jan]
        assert live.active_period == JAN_ID and live.active_set == jan
        assert not (env.pdir / "periods" / FEB_ID).exists()  # tiles, store and downloads alike
        assert len([x for x in (env.pdir / "periods" / JAN_ID).rglob("*") if x.is_file()]) == 4
        assert not layers.is_on(f"alerts:set:{feb}")
        assert _toasts("success") == [t("steps.period.deleted")]
    finally:
        rc.close()


def test_the_delete_confirmation_lists_the_sets_that_go(env):
    p = _project(env.pdir)
    feb = _add_set(p, env.pdir, FEB_ID)
    state = _state(p)
    layers, rc = _render(state)
    try:
        _action(env.seen, FEB_ID, "delete")["on_click"]()
        assert env.seen["confirm"]["details"] == [
            t("steps.period.delete_set_line", sources="GLAD-L", alerts=tn("steps.sets.alerts", 5),
              reviewed=tn("steps.sets.reviewed", 2))]
        assert feb in state.project.value.alert_sets  # nothing goes before the confirmation
    finally:
        rc.close()


def test_edit_and_delete_wait_for_the_projects_writers(env):
    state = _state(_project(env.pdir))
    layers, rc = _render(state)
    try:
        with writing("per"):
            _action(env.seen, FEB_ID, "delete")["on_click"]()
            _action(env.seen, FEB_ID, "edit")["on_click"]()
        # both refused: the bus folds the two identical warnings into one toast counting 2
        assert [(x.message, x.count) for x in get_current_bus().toasts.value
                if x.type.value == "warning"] == [(t("steps.period.busy"), 2)]
        assert env.seen["confirm"]["open"] is False and env.seen["modal"]["open_"].value is False
        assert FEB_ID in state.project.value.periods
    finally:
        rc.close()


# ---------------------------------------------------------------------------
# Look-back, remounts
# ---------------------------------------------------------------------------
def test_the_step_no_longer_renders_the_look_back_field(env):
    state = _state(_project(env.pdir))
    layers, rc = _render(state)
    try:
        assert [w for w in rc.find(v.TextField).widgets if w.label == t("steps.period.look_back")] == []
        labels = [c for b in rc.find(v.Btn).widgets for c in b.children if isinstance(c, str)]
        # the look-back "Apply" button (and its steps.period.apply key) is gone: only New period / Close remain
        assert t("steps.period.new") in labels and not [x for x in labels if x not in (t("steps.period.new"), "Close")]
    finally:
        rc.close()


def test_closing_the_open_project_unmounts_the_list_without_reading_it(env):
    state = _state(_project(env.pdir))
    layers, rc = _render(state)
    try:
        assert "table" in env.seen
        env.seen.clear()
        state.close_project_state()
        assert rc.find(v.Btn).widgets == [] and "table" not in env.seen
        assert state.project_loaded_signal.value == 2 and state.active_step.value == 0
    finally:
        rc.close()


def test_without_sources_the_step_is_locked(env):
    state = _state(Project(name="e", created_at=datetime.now(), updated_at=datetime.now()))
    layers, rc = _render(state)
    try:
        assert rc.find(v.TextField).widgets == [] and "table" not in env.seen
    finally:
        rc.close()


# ---------------------------------------------------------------------------
# PeriodModal (rendered for real)
# ---------------------------------------------------------------------------
@solara.component
def _ModalHarness(app_state, open_, on_submit, editing=None):
    period_modal.PeriodModal(open_=open_, app_state=app_state, on_submit=on_submit, editing=editing,
                             today=date(2026, 9, 30))


def _fields(rc):
    return {w.label: w for w in rc.find(v.TextField).widgets}


def test_the_modal_is_prefilled_from_the_latest_period_and_checks_the_order(env):
    state = _state(_project(env.pdir))
    open_, submitted = solara.reactive(True), []
    _, rc = reacton.render(_ModalHarness(app_state=state, open_=open_,
                                         on_submit=lambda a, b, c: submitted.append((a, b, c))), handle_error=False)
    try:
        fields = _fields(rc)
        assert fields[t("steps.period.start")].v_model == "2026-03-01"
        assert fields[t("steps.period.end")].v_model == "2026-09-30"
        fields[t("steps.period.end")].v_model = "2026-02-15"
        assert _button(rc, t("steps.period.add")).disabled
        assert _fields(rc)[t("steps.period.end")].error_messages == [t("steps.period.error_order")]
        _fields(rc)[t("steps.period.end")].v_model = "2026-03-31"
        _button(rc, t("steps.period.add")).fire_event("click", None)
        assert submitted == [("2026-03-01", "2026-03-31", "90")] and open_.value is False
    finally:
        rc.close()


def test_the_modal_shows_a_refusal_inline_and_stays_open(env):
    state = _state(_project(env.pdir))
    open_ = solara.reactive(True)
    _, rc = reacton.render(_ModalHarness(app_state=state, open_=open_, editing=FEB_ID,
                                         on_submit=lambda a, b, c: "steps.period.error_exists"), handle_error=False)
    try:
        assert _fields(rc)[t("steps.period.start")].v_model == "2026-02-01"  # an edit starts from its dates
        _button(rc, t("steps.period.save")).fire_event("click", None)
        assert open_.value is True
        # rc.find(v.Html) stops at the frame's outer div: look for the error's own span
        assert rc.find(v.Html, tag="span", children=[t("steps.period.error_exists")]).widgets
    finally:
        rc.close()


@pytest.mark.parametrize("editing, label, icon", [
    (None, "steps.period.add", "mdi-plus"), (FEB_ID, "steps.period.save", "mdi-content-save-outline")])
def test_the_period_form_sits_on_the_form_frame(env, editing, label, icon):
    state = _state(_project(env.pdir))
    open_ = solara.reactive(True)
    _, rc = reacton.render(_ModalHarness(app_state=state, open_=open_, on_submit=lambda a, b, c: None,
                                         editing=editing), handle_error=False)
    try:
        dialog = rc.find(v.Dialog).widget
        assert (dialog.max_width, dialog.persistent, dialog.eager) == ("420px", True, True)
        # Enter presses it, from a date field too (gui/widget/enter_key)
        [primary] = [b for b in rc.find(v.Btn).widgets if ENTER_DEFAULT in (b.class_ or "").split()]
        assert t(label) in primary.children
        assert [c.children[0] for c in primary.children if isinstance(c, v.Icon)] == [icon]
        dialog.fire_event("keydown.esc", None)
        assert open_.value is False
    finally:
        rc.close()


def test_the_dialog_has_a_look_back_field_with_a_live_window(env):
    p = _project(env.pdir)
    p.look_back_days = 30
    state = _state(p)
    open_ = solara.reactive(True)
    _, rc = reacton.render(_ModalHarness(app_state=state, open_=open_, on_submit=lambda a, b, c: None),
                           handle_error=False)
    try:
        assert _fields(rc)[t("steps.period.look_back")].v_model == "30"  # a new period starts from the project's
        _fields(rc)[t("steps.period.look_back")].v_model = "10"
        assert rc.find(v.Html, tag="div", children=[t("steps.period.window", start="2026-02-19", end="2026-09-30")]).widgets
        assert not _button(rc, t("steps.period.add")).disabled
        _fields(rc)[t("steps.period.look_back")].v_model = "-1"
        assert _button(rc, t("steps.period.add")).disabled
        assert _fields(rc)[t("steps.period.look_back")].error_messages == [t("steps.period.error_bad_look_back")]
    finally:
        rc.close()


def test_an_edit_starts_from_the_periods_look_back_and_locks_the_dates_when_it_has_sets(env):
    p = _project(env.pdir)
    p.periods = {**p.periods, JAN_ID: p.periods[JAN_ID].model_copy(update={"look_back_days": 45})}
    _add_set(p, env.pdir, JAN_ID)
    state = _state(p)
    open_, submitted = solara.reactive(True), []
    _, rc = reacton.render(_ModalHarness(app_state=state, open_=open_, editing=JAN_ID,
                                         on_submit=lambda a, b, c: submitted.append((a, b, c))), handle_error=False)
    try:
        fields = _fields(rc)
        assert fields[t("steps.period.look_back")].v_model == "45"
        assert fields[t("steps.period.start")].disabled and fields[t("steps.period.end")].disabled
        assert not _button(rc, t("steps.period.save")).disabled
        fields[t("steps.period.look_back")].v_model = "10"
        _button(rc, t("steps.period.save")).fire_event("click", None)
        assert submitted == [("2026-01-01", "2026-01-31", "10")]
    finally:
        rc.close()


def test_a_period_without_sets_keeps_its_date_fields_enabled(env):
    state = _state(_project(env.pdir))
    _, rc = reacton.render(_ModalHarness(app_state=state, open_=solara.reactive(True), editing=FEB_ID,
                                         on_submit=lambda a, b, c: None), handle_error=False)
    try:
        assert not _fields(rc)[t("steps.period.start")].disabled
    finally:
        rc.close()
