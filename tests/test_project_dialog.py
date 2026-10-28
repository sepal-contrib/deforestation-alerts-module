import asyncio
import json
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import ipyvuetify as v
import pytest
import reacton
import solara
from pysepal.sepalwidgets.file_input import FileInput
from pysepal.solara.notifications import NotificationProvider
from pysepal.solara.notifications.bus import get_current_bus

from deforestation_alerts.project import io, paths, share
from deforestation_alerts.project.models import AoiRecord, ReviewCopy, add_predefined_source
from gui.i18n import t
from gui.scripts.alert_filter import AlertFilter
from gui.store.autosave import install_autosave
from gui.store.project_writers import writing
from gui.store.state_manager import AppState
from gui.store.workflow_steps import step_index
from gui.tile import project_dialog as pd
from gui.tile.analysis.state import AnalysisState
from gui.widget.enter_key import ENTER_DEFAULT
from tests._render_helpers import run_rendered


def test_create_project_is_empty_and_named():
    p = pd.create_project("north")
    assert p.name == "north" and p.sources == {} and p.aoi is None


def test_duplicate_copies_the_aoi_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    p = pd.create_project("a")
    p.aoi = AoiRecord(spec={}, name="x", method="DRAW", digest="d", bounds=(0, 0, 1, 1),
                      geometry_file=paths.AOI_SIDECAR)
    io.save_project(p)
    (tmp_path / "a" / paths.AOI_SIDECAR).write_text('{"type": "FeatureCollection", "features": []}')
    q = pd.duplicate_with_sidecar(p, "b")
    assert q.name == "b" and q.aoi == p.aoi
    assert (tmp_path / "b" / paths.AOI_SIDECAR).exists()


def test_delete_is_blocked_while_writing():
    assert pd.delete_blocked("x") is None
    with writing("x"):
        assert pd.delete_blocked("x") == "steps.project.delete_busy"


def test_names_on_disk_counts_every_folder(tmp_path):
    io.save_project(pd.create_project("listed"), root=tmp_path)
    (tmp_path / "no_manifest").mkdir()
    (tmp_path / "a_file").write_text("")
    assert sorted(pd.names_on_disk(tmp_path)) == ["listed", "no_manifest"]
    assert pd.names_on_disk(tmp_path / "missing") == []


def test_folder_size_counts_the_files_and_never_follows_a_link(tmp_path):
    folder = tmp_path / "a"
    (folder / "sub").mkdir(parents=True)
    (folder / "x.bin").write_bytes(b"0" * 10)
    (folder / "sub" / "y.bin").write_bytes(b"0" * 5)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "z.bin").write_bytes(b"0" * 1000)
    (folder / "file_link").symlink_to(elsewhere / "z.bin")
    (folder / "dir_link").symlink_to(elsewhere, target_is_directory=True)
    assert pd.folder_size("a", tmp_path) == 15
    assert pd.folder_size("missing", tmp_path) == 0


# ---------------------------------------------------------------------------
# The dialog, rendered: the status block, the list, the name form, open /
# new / duplicate, delete.
# ---------------------------------------------------------------------------
@solara.component
def _Harness(app_state, dismiss, analysis=None):
    NotificationProvider()  # the dialog's use_notifications() needs it rendered first
    pd.ProjectDialog(app_state=app_state, dismiss=dismiss, analysis=analysis)


@pytest.fixture
def dialog(monkeypatch, tmp_path):
    """A real AppState with the auto-save hook; Manage projects is swapped for
    a recorder, so the test calls the callbacks the dialog wires into it (the
    dialog itself is tested in tests/test_manage_projects.py). The delete
    confirmation is the real one."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    ns = SimpleNamespace(manage=[], dismissed=0, root=tmp_path, state=AppState(), analysis=AnalysisState(),
                         filters=[])
    # The start filter's own behaviour is T5's (tests/test_alert_filter_state.py);
    # here: which filter the dialog sets, on which AnalysisState.
    monkeypatch.setattr(pd, "apply_start_filter", lambda analysis, f: ns.filters.append((analysis, f)))

    @solara.component
    def _RecorderManage(open, infos, selected, on_select, on_open, on_delete, on_cancel, current=None,
                        busy=False, covered=False):
        ns.manage.append(dict(open=open, infos=infos, selected=selected, on_select=on_select, on_open=on_open,
                              on_delete=on_delete, on_cancel=on_cancel, current=current, busy=busy,
                              covered=covered))

    monkeypatch.setattr(pd, "ManageProjectsDialog", _RecorderManage)
    unsubscribe = install_autosave(ns.state)

    def _dismiss():
        ns.dismissed += 1

    ns.render = lambda: reacton.render(_Harness(app_state=ns.state, dismiss=_dismiss, analysis=ns.analysis),
                                       handle_error=False)
    yield ns
    unsubscribe()


# Generous: a list read and its re-render run on a worker thread, which a
# loaded machine (parallel suites, a SEPAL sandbox) can hold back for seconds.
SETTLE_SECONDS = 10.0


async def _settle(done, what, timeout=SETTLE_SECONDS, rc=None):
    """Wait until ``done()`` is truthy: poll by wall-clock deadline, not by a
    count of sleeps (a count gives up early when the event loop runs slowly).

    With ``rc``, ``done()`` is only read between two renders of it. A list
    read or a size count renders on its worker thread, and a state set on
    this thread meanwhile is left to that render: the tree is then half
    updated (the recorder has the new list, the confirmation is still open),
    and a field typed into or a button pressed lands on the previous render's
    handlers."""
    deadline = time.monotonic() + timeout
    while True:
        if rc is None:
            if done():
                return
        elif rc.thread_lock.acquire(blocking=False):  # held for a whole render, nested ones too
            try:
                if done():
                    return
            finally:
                rc.thread_lock.release()
        if time.monotonic() > deadline:
            raise AssertionError(f"never happened within {timeout:g} s: {what}")
        await asyncio.sleep(0.01)


def _listed(ns):
    """{name: "sources / periods / sets"} as the dialog hands the list to Manage now."""
    if not ns.manage:
        return None
    return {i.name: f"{i.n_sources} / {i.n_periods} / {i.n_sets}" for i in ns.manage[-1]["infos"]}


def _open_from_manage(ns, name):
    """A row click, then the footer's Open."""
    ns.manage[-1]["on_select"](name)
    ns.manage[-1]["on_open"]()


def _button(finder, label):
    return next(w for w in finder.find(v.Btn).widgets if label in w.children)


def _open_form(rc):
    """The dialog that is open now: the name form (New or Duplicate) or the
    delete confirmation (Manage is a recorder here)."""
    return rc.find(v.Dialog, v_model=True)


def _name_field(rc):
    return _open_form(rc).find(v.TextField, label=t("steps.project.name_label")).widget


async def _submit_name(rc, name):
    await _settle(lambda: _open_form(rc).widgets, "the name form opened")
    _name_field(rc).v_model = name
    _button(_open_form(rc), t("common.create")).fire_event("click", None)


def _toasts(kind):
    return [toast.message for toast in get_current_bus().toasts.value if toast.type.value == kind]


def _texts(widget):
    out = []
    if isinstance(widget, str):
        return [widget]
    for child in getattr(widget, "children", None) or []:
        out += _texts(child)
    return out


def test_with_no_project_open_the_dialog_offers_new_and_manage(dialog):
    async def main():
        box, rc = dialog.render()
        try:
            await _settle(lambda: _listed(dialog) == {}, "the empty list read")
            texts = _texts(box)
            assert t("steps.project.empty_heading") in texts and t("steps.project.none_open") in texts
            new = _button(rc, t("steps.project.new"))
            assert new.block and new.color == "primary" and not new.outlined
            manage = _button(rc, t("steps.project.manage_count", count=0))
            assert manage.block and manage.outlined and manage.disabled  # nothing saved yet
        finally:
            rc.close()
        io.save_project(pd.create_project("a"))
        box, rc = dialog.render()
        try:
            await _settle(lambda: _listed(dialog) == {"a": "0 / 0 / 0"}, "the saved project listed")
            assert not _button(rc, t("steps.project.manage_count", count=1)).disabled
        finally:
            rc.close()

    asyncio.run(main())


def test_an_open_project_shows_its_save_state_and_manage_selects_it(dialog):
    async def main():
        box, rc = dialog.render()
        try:
            dialog.state.new_project_state(pd.create_project("b"))
            await _settle(lambda: t("steps.project.chip_saved") in _texts(box), "the saved chip")
            chip = next(c for c in rc.find(v.Chip).widgets if c.children == [t("steps.project.chip_saved")])
            assert chip.color == "primary"
            for label in (t("steps.project.new"), t("steps.project.manage"), t("steps.project.duplicate")):
                button = _button(rc, label)
                assert button.outlined and button.small and not button.block, label
            dialog.state.save_error.set("disk full")
            await _settle(lambda: t("steps.project.chip_save_failed") in _texts(box), "the failed chip")
            chip = next(c for c in rc.find(v.Chip).widgets if c.children == [t("steps.project.chip_save_failed")])
            assert chip.color == "error" and t("steps.project.chip_saved") not in _texts(box)
            _button(rc, t("steps.project.manage")).fire_event("click", None)
            await _settle(lambda: dialog.manage[-1]["open"], "Manage opened")
            assert dialog.manage[-1]["selected"] == "b" and dialog.manage[-1]["current"] == "b"
            dialog.manage[-1]["on_cancel"]()
            await _settle(lambda: not dialog.manage[-1]["open"], "Manage closed")
        finally:
            rc.close()

    asyncio.run(main())


def test_the_list_follows_the_open_projects_saves(dialog):
    """The counts and time of the open project are read again on every save,
    not only when a project is installed or closed."""
    async def main(rc):
        dialog.state.new_project_state(pd.create_project("a"))
        await _settle(lambda: _listed(dialog) == {"a": "0 / 0 / 0"}, "the new project listed")
        p = dialog.state.project.value
        add_predefined_source(p, "GLAD-L")
        dialog.state.commit(p)  # auto-saved
        await _settle(lambda: _listed(dialog) == {"a": "1 / 0 / 0"}, "the saved source counted")

    run_rendered(lambda: dialog.render(), main)


def test_the_name_is_checked_as_it_is_typed(dialog):
    async def main():
        io.save_project(pd.create_project("north"))
        _box, rc = dialog.render()
        try:
            await _settle(lambda: _listed(dialog) == {"north": "0 / 0 / 0"}, "the list read")
            _button(rc, t("steps.project.new")).fire_event("click", None)
            await _settle(lambda: _open_form(rc).widgets, "the name form opened")
            frame = _open_form(rc).widget
            assert frame.max_width == "420px" and frame.persistent
            field = _name_field(rc)
            assert field.autofocus and field.hint == t("steps.project.name_hint") and field.persistent_hint

            def create():
                return _button(_open_form(rc), t("common.create"))

            assert ENTER_DEFAULT in create().class_.split()
            assert not field.error_messages and create().disabled  # nothing typed: no message, no Create
            field.v_model = "bad/name"
            assert field.error_messages == [t("steps.project.name_chars")] and create().disabled
            field.v_model = "NORTH"
            assert field.error_messages == [t("steps.project.name_exists")] and create().disabled
            field.v_model = "south"
            assert not field.error_messages and not create().disabled
            _button(_open_form(rc), t("common.cancel")).fire_event("click", None)
            assert not _open_form(rc).widgets and dialog.state.project.value is None
        finally:
            rc.close()

    asyncio.run(main())


def test_a_listed_name_offers_to_open_that_project_instead(dialog):
    async def main():
        io.save_project(pd.create_project("north"))
        _box, rc = dialog.render()
        try:
            await _settle(lambda: _listed(dialog) == {"north": "0 / 0 / 0"}, "the list read")
            _button(rc, t("steps.project.new")).fire_event("click", None)
            await _settle(lambda: _open_form(rc).widgets, "the name form opened")
            label = t("steps.project.open_instead", name="north")
            assert not any(label in w.children for w in _open_form(rc).find(v.Btn).widgets)
            _name_field(rc).v_model = "North "
            instead = _button(_open_form(rc), label)
            assert ENTER_DEFAULT not in (instead.class_ or "").split()  # Create stays the one default
            instead.fire_event("click", None)
            await _settle(lambda: getattr(dialog.state.project.value, "name", None) == "north", "north opened")
            await _settle(lambda: dialog.dismissed == 1, "the dialog closed")
            assert not _open_form(rc).widgets
        finally:
            rc.close()

    asyncio.run(main())


def test_a_name_taken_on_disk_after_the_form_opened_is_refused(dialog):
    """Review Focus 4. The live check uses the folders read when the form
    opened; another tab creates "b" after that. Create must check the disk
    again, or the auto-save of a new "b" overwrites that project."""
    async def main():
        io.save_project(pd.create_project("a"))
        _box, rc = dialog.render()
        try:
            await _settle(lambda: _listed(dialog) == {"a": "0 / 0 / 0"}, "the list read")
            _button(rc, t("steps.project.new")).fire_event("click", None)
            await _settle(lambda: _open_form(rc).widgets, "the name form opened")
            other = pd.create_project("b")
            other.look_back_days = 99
            io.save_project(other)  # the other tab
            manifest = dialog.root / "b" / paths.MANIFEST
            before = manifest.read_text()

            field = _name_field(rc)
            field.v_model = "b"
            assert not field.error_messages  # the live check read the disk before "b" existed
            create = _button(_open_form(rc), t("common.create"))
            assert not create.disabled
            create.fire_event("click", None)

            await _settle(lambda: _open_form(rc).find(v.Alert).widgets, "the name refused")
            alerts = _open_form(rc).find(v.Alert).widgets
            assert [_texts(a) for a in alerts] == [[t("steps.project.name_exists")]]
            assert alerts[0].type == "error"
            assert dialog.state.project.value is None and manifest.read_text() == before
            assert dialog.dismissed == 0
            field.v_model = "c"  # typing again clears the refusal
            assert not _open_form(rc).find(v.Alert).widgets
        finally:
            rc.close()

    asyncio.run(main())


@pytest.mark.parametrize("how", ["open", "new", "duplicate"])
def test_a_new_opened_or_duplicated_project_starts_at_aoi_and_closes_the_dialog(dialog, how):
    """Already on AOI the navigation does not move, so it never closed the
    dialog (it did from any other step): the dialog closes itself."""
    async def main():
        io.save_project(pd.create_project("a"))
        _box, rc = dialog.render()
        try:
            dialog.state.current_page_view.value = "aoi_tile"  # AOI is showing
            await _settle(lambda: _listed(dialog) == {"a": "0 / 0 / 0"}, "the saved project listed")
            if how in ("open", "duplicate"):
                _button(rc, t("steps.project.manage_count", count=1)).fire_event("click", None)
                await _settle(lambda: dialog.manage[-1]["open"], "Manage opened")
                assert dialog.manage[-1]["selected"] is None  # no project open to preselect
                _open_from_manage(dialog, "a")
                await _settle(lambda: dialog.state.project.value is not None, "the project opened")
                await _settle(lambda: not dialog.manage[-1]["open"], "Manage closed on the load")
            if how == "new":
                _button(rc, t("steps.project.new")).fire_event("click", None)
                await _submit_name(rc, "n")
            if how == "duplicate":
                label = t("steps.project.duplicate")
                await _settle(lambda: any(label in w.children for w in rc.find(v.Btn).widgets),
                              "the Duplicate button")
                _button(rc, label).fire_event("click", None)
                await _settle(lambda: _open_form(rc).widgets, "the name form opened")
                assert _name_field(rc).hint == t("steps.project.duplicate_hint")
                await _submit_name(rc, "c")
            expected = {"open": "a", "new": "n", "duplicate": "c"}[how]
            await _settle(lambda: getattr(dialog.state.project.value, "name", None) == expected,
                          f"{expected} installed")
            assert dialog.state.project.value.name == expected
            assert dialog.state.current_page_view.value == "aoi_tile"
            await _settle(lambda: dialog.dismissed >= (2 if how == "duplicate" else 1), "the dialog closed")
            assert dialog.dismissed == (2 if how == "duplicate" else 1)
            assert not _open_form(rc).widgets  # the name form closed too
        finally:
            rc.close()

    asyncio.run(main())


async def _delete(rc, dialog, name):
    dialog.manage[-1]["on_delete"](name)  # the row's trash button
    await _settle(lambda: _open_form(rc).widgets, "the confirm dialog opened", rc=rc)
    await _type_and_delete(rc, name)


async def _type_and_delete(rc, name):
    """The name typed, then Delete pressed once the render that took the name
    is done: the press runs that render's handler, not one that saw no name."""
    _open_form(rc).find(v.TextField).widget.v_model = name
    await _settle(lambda: not _button(_open_form(rc), t("common.delete")).disabled, "the name typed", rc=rc)
    _button(_open_form(rc), t("common.delete")).fire_event("click", None)


def test_the_confirmation_shows_the_folder_size_counted_off_thread(dialog):
    async def main():
        io.save_project(pd.create_project("a"))
        (dialog.root / "a" / "data.bin").write_bytes(b"0" * 5_400_000)
        _box, rc = dialog.render()
        try:
            await _settle(lambda: _listed(dialog) == {"a": "0 / 0 / 0"}, "the list read")
            dialog.manage[-1]["on_delete"]("a")
            await _settle(lambda: _open_form(rc).widgets, "the confirm dialog opened")
            await _settle(lambda: t("steps.project.delete_size", size="5.4 MB") in _texts(_open_form(rc).widget),
                          "the size counted")
            assert not _open_form(rc).find(v.Alert).widgets  # not open, no writer
            _button(_open_form(rc), t("common.cancel")).fire_event("click", None)
            await _settle(lambda: not _open_form(rc).widgets, "the confirm dialog closed")
            assert (dialog.root / "a").exists()
        finally:
            rc.close()

    asyncio.run(main())


def test_manage_is_covered_while_the_delete_confirmation_is_open(dialog):
    # Manage releases its focus trap under the confirmation (see
    # test_manage_projects), so the confirmation's name field keeps the focus.
    async def main():
        io.save_project(pd.create_project("a"))
        _box, rc = dialog.render()
        try:
            await _settle(lambda: _listed(dialog) == {"a": "0 / 0 / 0"}, "the list read")
            assert dialog.manage[-1]["covered"] is False
            dialog.manage[-1]["on_delete"]("a")
            await _settle(lambda: _open_form(rc).widgets, "the confirm dialog opened")
            assert dialog.manage[-1]["covered"] is True
            _button(_open_form(rc), t("common.cancel")).fire_event("click", None)
            await _settle(lambda: not _open_form(rc).widgets, "the confirm dialog closed")
            assert dialog.manage[-1]["covered"] is False
        finally:
            rc.close()

    asyncio.run(main())


def test_deleting_the_open_project_closes_it_before_the_folder_goes(dialog, monkeypatch):
    """Closed first, no auto-save can write the manifest back into the folder
    being deleted; the workflow returns to AOI and the dialog stays open."""
    seen = []
    real_delete = io.delete_project

    def spy(name, root=None):
        seen.append(dialog.state.project.peek())
        real_delete(name, root)

    monkeypatch.setattr(io, "delete_project", spy)

    async def main(rc):
        dialog.state.new_project_state(pd.create_project("a"))
        dialog.state.active_step.set(step_index("period"))
        await _settle(lambda: _listed(dialog) == {"a": "0 / 0 / 0"}, "the open project listed")
        dialog.manage[-1]["on_delete"]("a")
        await _settle(lambda: _open_form(rc).widgets, "the confirm dialog opened", rc=rc)
        alerts = _open_form(rc).find(v.Alert).widgets
        assert [(a.type, _texts(a)) for a in alerts] == [("warning", [t("steps.project.delete_open_warning")])]
        await _type_and_delete(rc, "a")
        await _settle(lambda: _toasts("success"), "the delete finished")
        assert seen == [None]
        assert not (dialog.root / "a").exists()
        assert dialog.state.project.value is None and dialog.state.active_step.value == 0
        assert _toasts("success") == [t("steps.project.deleted", name="a")] and dialog.dismissed == 0
        # The read the close started can render the empty list on its
        # worker while the confirmation's close waits for that render.
        await _settle(lambda: _listed(dialog) == {}, "the list emptied", rc=rc)
        assert not _open_form(rc).widgets  # the confirmation closed

    run_rendered(lambda: dialog.render(), main)


def test_a_failed_delete_of_the_open_project_opens_it_again(dialog, monkeypatch):
    def denied(name, root=None):
        raise PermissionError("denied")

    monkeypatch.setattr(io, "delete_project", denied)

    async def main(rc):
        p = pd.create_project("a")
        add_predefined_source(p, "RADD")
        dialog.state.new_project_state(p)
        await _settle(lambda: _listed(dialog) == {"a": "1 / 0 / 0"}, "the open project listed")
        await _delete(rc, dialog, "a")
        await _settle(lambda: _toasts("error"), "the delete failed")
        assert _toasts("error") == [t("steps.project.delete_failed", name="a", error="denied")]
        live = dialog.state.project.value
        assert live is not None and live.name == "a" and list(live.sources) == ["RADD"]
        assert _open_form(rc).find(v.TextField).widget.v_model == "a"  # still asking

    run_rendered(lambda: dialog.render(), main)


def test_a_project_being_written_cannot_be_deleted(dialog):
    """Review Focus 5: while a task writes into the folder, the confirmation
    shows the busy error, no name field and a disabled Delete; they come back
    once the writer is done."""
    async def main():
        io.save_project(pd.create_project("a"))
        _box, rc = dialog.render()
        try:
            await _settle(lambda: _listed(dialog) == {"a": "0 / 0 / 0"}, "the list read")
            with writing("a"):
                dialog.manage[-1]["on_delete"]("a")
                await _settle(lambda: _open_form(rc).widgets, "the confirm dialog opened")
                confirm = _open_form(rc)
                alerts = confirm.find(v.Alert).widgets
                assert [(a.type, _texts(a)) for a in alerts] == [("error", [t("steps.project.delete_busy")])]
                assert not confirm.find(v.TextField).widgets
                delete = _button(confirm, t("common.delete"))
                assert delete.disabled
                delete.fire_event("click", None)
            await _settle(lambda: _open_form(rc).find(v.TextField).widgets, "the name field back")
            assert not _open_form(rc).find(v.Alert).widgets
            assert (dialog.root / "a").exists() and not _toasts("success")
        finally:
            rc.close()

    asyncio.run(main())


def test_the_list_follows_saves_made_on_worker_threads(dialog, monkeypatch):
    """A finished Prepare row, an Integrate and a review Save publish the
    project (``update_project``, ``commit``) from worker threads with no event
    loop; the auto-save then sets ``last_saved`` there and the always-mounted
    dialog re-renders on that thread. Rendered as the shell renders it, the
    dialog must neither raise into those publishers nor fail its render, and
    its list must still follow each save."""
    import threading
    import traceback

    from gui.scripts import detached
    from gui.scripts.detached import render_detached

    reports = []
    monkeypatch.setattr(detached, "_report", lambda name, text, notify: reports.append(text))

    async def main():
        _container, rc = render_detached(lambda: _Harness(app_state=dialog.state, dismiss=lambda: None), "Project")
        try:
            dialog.state.new_project_state(pd.create_project("a"))
            await _settle(lambda: _listed(dialog) == {"a": "0 / 0 / 0"}, "the new project listed")
            errors = []

            def commit(source):
                try:
                    p = dialog.state.project.value
                    add_predefined_source(p, source)
                    dialog.state.commit(p)
                except BaseException:  # noqa: BLE001 - reported below
                    errors.append(traceback.format_exc())

            def update(source):
                try:
                    dialog.state.update_project("a", lambda live: add_predefined_source(live, source))
                except BaseException:  # noqa: BLE001 - reported below
                    errors.append(traceback.format_exc())

            for n, (publish, source) in enumerate([(commit, "GLAD-L"), (update, "RADD")], start=1):
                worker = threading.Thread(target=publish, args=(source,))
                worker.start()
                while worker.is_alive():
                    await asyncio.sleep(0.01)
                assert errors == [], errors
                await _settle(lambda: _listed(dialog) == {"a": f"{n} / 0 / 0"}, f"save {n} listed")
            assert reports == []
            assert dialog.state.last_saved.value is not None and dialog.state.save_error.value is None
        finally:
            rc.close()

    asyncio.run(main())


def test_saved_ago_follows_the_manifest_and_every_save(dialog):
    async def main():
        old = pd.create_project("a")
        io.save_project(old)
        manifest = dialog.root / "a" / paths.MANIFEST
        raw = json.loads(manifest.read_text())
        raw["updated_at"] = (datetime.now() - timedelta(days=3, minutes=1)).isoformat()
        manifest.write_text(json.dumps(raw))
        box, rc = dialog.render()
        try:
            dialog.state.load_project_state(io.load_project("a"))
            await _settle(lambda: t("steps.project.saved_ago", time_ago="3 days ago") in _texts(box),
                          "the manifest's save time")
            p = dialog.state.project.value
            add_predefined_source(p, "GLAD-L")
            dialog.state.commit(p)  # auto-saved
            await _settle(lambda: t("steps.project.saved_ago", time_ago="just now") in _texts(box),
                          "the new save")
        finally:
            rc.close()

    asyncio.run(main())


def test_saved_ago_moves_on_while_the_dialog_stays_open(dialog, monkeypatch):
    monkeypatch.setattr(pd, "TICK_SECONDS", 0.05)

    async def main():
        box, rc = dialog.render()
        try:
            dialog.state.new_project_state(pd.create_project("a"))
            dialog.state.last_saved.set(datetime.now() - timedelta(seconds=59.8))
            assert t("steps.project.saved_ago", time_ago="just now") in _texts(box)
            await _settle(lambda: t("steps.project.saved_ago", time_ago="1 minute ago") in _texts(box),
                          "a tick re-rendered the line")
        finally:
            rc.close()

    asyncio.run(main())


def test_a_failed_duplicate_toasts_and_leaves_create_working(dialog, monkeypatch):
    """Copying the AOI sidecar can fail on disk trouble: a toast, the form
    stays open on the name, nothing is installed, no half-made folder blocks
    the name, and Create works again."""
    real_copy = pd.shutil.copy2
    failures = [OSError("disk full")]

    def flaky_copy(src, dst, *args, **kwargs):
        if failures:
            raise failures.pop()
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(pd.shutil, "copy2", flaky_copy)

    async def main():
        p = pd.create_project("a")
        p.aoi = AoiRecord(spec={}, name="x", method="DRAW", digest="d", bounds=(0, 0, 1, 1),
                          geometry_file=paths.AOI_SIDECAR)
        io.save_project(p)
        (dialog.root / "a" / paths.AOI_SIDECAR).write_text('{"type": "FeatureCollection", "features": []}')
        _box, rc = dialog.render()
        try:
            dialog.state.load_project_state(io.load_project("a"))
            label = t("steps.project.duplicate")
            await _settle(lambda: any(label in w.children for w in rc.find(v.Btn).widgets), "the Duplicate button")
            _button(rc, label).fire_event("click", None)
            await _submit_name(rc, "c")
            await _settle(lambda: _toasts("error"), "the failure toasted")
            assert _toasts("error") == [t("steps.project.duplicate_failed", name="a", error="disk full")]
            assert dialog.state.project.value.name == "a" and dialog.dismissed == 0
            assert not (dialog.root / "c").exists()  # the half-made folder is gone
            assert _name_field(rc).v_model == "c"  # the form stays open on the name
            create = _button(_open_form(rc), t("common.create"))
            assert not create.disabled
            create.fire_event("click", None)  # the retry
            await _settle(lambda: getattr(dialog.state.project.value, "name", None) == "c", "c installed")
            assert (dialog.root / "c" / paths.AOI_SIDECAR).exists()
            await _settle(lambda: not _open_form(rc).widgets, "the name form closed")
        finally:
            rc.close()

    asyncio.run(main())


def test_opening_the_open_project_closes_the_dialogs_and_keeps_it_as_it_is(dialog, monkeypatch):
    """Manage opens on the open project with Open as Enter's default. Open
    there must not read it again from disk: after a failed auto-save that
    would silently drop what is only in memory."""
    loads = []

    async def main(rc):
        dialog.state.new_project_state(pd.create_project("a"))
        await _settle(lambda: _listed(dialog) == {"a": "0 / 0 / 0"}, "the open project listed")
        live = dialog.state.project.value
        dialog.state.current_page_view.value = "period_tile"  # wherever the user was
        monkeypatch.setattr(dialog.state, "load_project_state", lambda p: loads.append(p))
        _button(rc, t("steps.project.manage")).fire_event("click", None)
        await _settle(lambda: dialog.manage[-1]["open"], "Manage opened")
        assert dialog.manage[-1]["selected"] == "a"
        dialog.manage[-1]["on_open"]()  # Enter, or a click on Open
        await _settle(lambda: not dialog.manage[-1]["open"], "Manage closed")
        await asyncio.sleep(0.05)
        assert loads == [] and dialog.state.project.value is live
        assert dialog.dismissed == 1 and dialog.state.current_page_view.value == "period_tile"
        assert not _toasts("success")

    run_rendered(lambda: dialog.render(), main)


def test_the_open_projects_own_name_is_taken_but_not_offered_to_open(dialog):
    async def main():
        io.save_project(pd.create_project("north"))
        _box, rc = dialog.render()
        try:
            dialog.state.new_project_state(pd.create_project("a"))
            await _settle(lambda: _listed(dialog) == {"a": "0 / 0 / 0", "north": "0 / 0 / 0"}, "the list read")
            for form in (t("steps.project.new"), t("steps.project.duplicate")):
                _button(rc, form).fire_event("click", None)
                await _settle(lambda: _open_form(rc).widgets, "the name form opened")
                field = _name_field(rc)
                field.v_model = "A"
                assert field.error_messages == [t("steps.project.name_exists")], form
                labels = [w.children for w in _open_form(rc).find(v.Btn).widgets]
                assert not any(t("steps.project.open_instead", name="a") in c for c in labels), form
                field.v_model = "north"  # another listed project is still offered
                _button(_open_form(rc), t("steps.project.open_instead", name="north"))
                _button(_open_form(rc), t("common.cancel")).fire_event("click", None)
                await _settle(lambda: not _open_form(rc).widgets, "the name form closed")
        finally:
            rc.close()

    asyncio.run(main())


def test_a_review_copy_offers_no_duplicate(dialog):
    """Spec §6.3: Duplicate is hidden for a review copy (New and Manage stay)."""
    async def main(rc):
        p = pd.create_project("c")
        p.review_copy = ReviewCopy(evaluator="Ana", evaluator_index=1, set_id="x", split_id="sp",
                                   set_fingerprint="f", origin_project="orig", imported_at=datetime.now())
        dialog.state.new_project_state(p)
        await _settle(lambda: any(t("steps.project.manage") in w.children for w in rc.find(v.Btn).widgets),
                      "the open project's buttons")
        labels = [c for w in rc.find(v.Btn).widgets for c in w.children if isinstance(c, str)]
        assert t("steps.project.new") in labels and t("steps.project.duplicate") not in labels

    run_rendered(lambda: dialog.render(), main)


# ---------------------------------------------------------------------------
# Import (spec 2026-10-10 §6.1, §6.2): a review package becomes a review
# copy, which opens on Analysis filtered to its evaluator.
# ---------------------------------------------------------------------------
def _review_copy(name="c__e01-ana", evaluator="Ana"):
    p = pd.create_project(name)
    p.review_copy = ReviewCopy(evaluator=evaluator, evaluator_index=1, set_id="s", split_id="sp",
                               set_fingerprint="fp", origin_project="c", imported_at=datetime.now())
    return p


def _header(kind=None, evaluator="Ana"):
    return SimpleNamespace(kind=kind or share.KIND_PACKAGE, evaluator=evaluator, split_id="sp", origin_project="c")


async def _import_zip(rc, path):
    _button(rc, t("steps.project.import")).fire_event("click", None)
    await _settle(lambda: _open_form(rc).widgets, "the import form opened")
    _open_form(rc).find(FileInput).widget.v_model = str(path)


def _ana_filter():
    return AlertFilter("evaluator", frozenset({"Ana"}))


def test_import_is_offered_with_and_without_an_open_project(dialog):
    async def main(rc):
        assert _button(rc, t("steps.project.import")).block  # the empty state's column of buttons
        dialog.state.new_project_state(pd.create_project("b"))
        await _settle(lambda: any(t("steps.project.import") in w.children and not w.block
                                  for w in rc.find(v.Btn).widgets), "Import beside New and Manage")

    run_rendered(lambda: dialog.render(), main)


def test_import_refuses_a_reviewed_copy(dialog, monkeypatch):
    monkeypatch.setattr(pd.share, "peek_header", lambda path: _header(kind=share.KIND_RETURN))
    imported = []
    monkeypatch.setattr(pd.share, "import_package", lambda path, root=None: imported.append(path))

    async def main(rc):
        await _import_zip(rc, dialog.root / "c__x__e01-ana__reviewed.zip")
        await _settle(lambda: t("share.err.is_return") in _toasts("error"), "the refusal toast")
        assert imported == [] and dialog.state.project.value is None and dialog.dismissed == 0

    run_rendered(lambda: dialog.render(), main)


def test_an_imported_package_opens_on_analysis_filtered_to_its_evaluator(dialog, monkeypatch):
    def _import(path, root=None):
        made = _review_copy()
        io.save_project(made)
        return made

    monkeypatch.setattr(pd.share, "peek_header", lambda path: _header())
    monkeypatch.setattr(pd.share, "find_existing_copy", lambda header, root=None: None)
    monkeypatch.setattr(pd.share, "import_package", _import)

    async def main(rc):
        await _import_zip(rc, dialog.root / "c__x__e01-ana__review.zip")
        await _settle(lambda: getattr(dialog.state.project.value, "name", None) == "c__e01-ana",
                      "the copy installed")
        assert dialog.state.current_page_view.value == "analysis_tile"
        assert dialog.filters == [(dialog.analysis, _ana_filter())]
        await _settle(lambda: dialog.dismissed == 1, "the dialog closed")
        assert t("steps.project.imported", name="c__e01-ana") in _toasts("success")
        assert not _open_form(rc).widgets  # the import form closed too

    run_rendered(lambda: dialog.render(), main)


@pytest.mark.parametrize("answer", ["open", "new"])
def test_a_package_imported_before_asks_to_open_that_copy_or_import_a_new_one(dialog, monkeypatch, answer):
    io.save_project(_review_copy())
    made = []

    def _import(path, root=None):
        p = _review_copy("c__e01-ana-2")
        io.save_project(p)
        made.append(p.name)
        return p

    monkeypatch.setattr(pd.share, "peek_header", lambda path: _header())
    monkeypatch.setattr(pd.share, "find_existing_copy", lambda header, root=None: "c__e01-ana")
    monkeypatch.setattr(pd.share, "import_package", _import)

    async def main(rc):
        await _import_zip(rc, dialog.root / "c__x__e01-ana__review.zip")
        message = t("steps.project.import_existing_message", name="c__e01-ana", evaluator="Ana")
        await _settle(lambda: any(message in _texts(w) for w in _open_form(rc).widgets), "the re-import question")
        assert made == []  # nothing imported before the answer
        label = t("steps.project.import_open_existing") if answer == "open" else t("steps.project.import_as_new")
        _button(_open_form(rc), label).fire_event("click", None)
        expected = "c__e01-ana" if answer == "open" else "c__e01-ana-2"
        await _settle(lambda: getattr(dialog.state.project.value, "name", None) == expected, f"{expected} open")
        assert made == ([] if answer == "open" else ["c__e01-ana-2"])
        assert dialog.state.current_page_view.value == "analysis_tile"
        assert dialog.filters[-1] == (dialog.analysis, _ana_filter())

    run_rendered(lambda: dialog.render(), main)


def test_open_existing_copy_on_the_open_copy_lands_again_without_a_reload(dialog, monkeypatch):
    """Spec §6.2: the copy is open, but its user left Analysis (or cleared the
    filter); re-picking its package and choosing Open existing copy lands it
    again, from memory, not read back from disk."""
    loads = []
    monkeypatch.setattr(pd.share, "peek_header", lambda path: _header())
    monkeypatch.setattr(pd.share, "find_existing_copy", lambda header, root=None: "c__e01-ana")
    monkeypatch.setattr(pd.share, "import_package", lambda path, root=None: pytest.fail("imported"))
    monkeypatch.setattr(pd.io, "load_project", lambda name, *a, **k: loads.append(name))

    async def main(rc):
        dialog.state.new_project_state(_review_copy())
        dialog.state.current_page_view.value = "aoi_tile"
        await _settle(lambda: any(t("steps.project.import") in w.children and not w.block
                                  for w in rc.find(v.Btn).widgets), "the open copy's buttons")
        assert dialog.filters == []
        await _import_zip(rc, dialog.root / "c__x__e01-ana__review.zip")
        message = t("steps.project.import_existing_message", name="c__e01-ana", evaluator="Ana")
        await _settle(lambda: any(message in _texts(w) for w in _open_form(rc).widgets), "the re-import question")
        _button(_open_form(rc), t("steps.project.import_open_existing")).fire_event("click", None)
        await _settle(lambda: dialog.state.current_page_view.value == "analysis_tile", "back on Analysis")
        assert dialog.filters == [(dialog.analysis, _ana_filter())]
        assert loads == [] and dialog.dismissed == 1
        assert dialog.state.project.value.name == "c__e01-ana"

    run_rendered(lambda: dialog.render(), main)


def test_opening_a_review_copy_from_manage_lands_on_analysis(dialog):
    io.save_project(_review_copy())

    async def main(rc):
        await _settle(lambda: "c__e01-ana" in (_listed(dialog) or {}), "the copy listed")
        _button(rc, t("steps.project.manage_count", count=1)).fire_event("click", None)
        await _settle(lambda: dialog.manage[-1]["open"], "Manage opened")
        _open_from_manage(dialog, "c__e01-ana")
        await _settle(lambda: getattr(dialog.state.project.value, "name", None) == "c__e01-ana",
                      "the copy opened")
        assert dialog.state.current_page_view.value == "analysis_tile"
        assert dialog.filters == [(dialog.analysis, _ana_filter())]
        await _settle(lambda: dialog.dismissed == 1, "the dialog closed")

    run_rendered(lambda: dialog.render(), main)


def test_a_failed_import_toasts_and_opens_nothing(dialog, monkeypatch):
    def _fail(path, root=None):
        raise OSError("disk full")

    peeked = []

    def _peek(path):
        peeked.append(path)
        return _header()

    monkeypatch.setattr(pd.share, "peek_header", _peek)
    monkeypatch.setattr(pd.share, "find_existing_copy", lambda header, root=None: None)
    monkeypatch.setattr(pd.share, "import_package", _fail)

    async def main(rc):
        await _import_zip(rc, dialog.root / "p.zip")
        await _settle(lambda: t("steps.project.import_failed", file="p.zip", error="disk full")
                      in _toasts("error"), "the failure toast")
        assert dialog.state.project.value is None and dialog.dismissed == 0 and dialog.filters == []
        # Still usable: the form stays open on a fresh picker, and the
        # next pick is read (the in-flight guard was released).
        await _settle(lambda: _open_form(rc).find(FileInput).widgets, "the picker, empty again")
        _open_form(rc).find(FileInput).widget.v_model = str(dialog.root / "q.zip")
        await _settle(lambda: len(peeked) == 2, "the second pick read")
        assert peeked == [str(dialog.root / "p.zip"), str(dialog.root / "q.zip")]

    run_rendered(lambda: dialog.render(), main)


@pytest.mark.parametrize("stage", ["peek", "import"])
def test_a_refused_package_toasts_its_own_reason_alone(dialog, monkeypatch, stage):
    """R4: a ShareError is its own text, never wrapped in ``import_failed``."""
    refusal = share.ShareError("share.err.import_failed", error="no manifest")

    def _raise(*args, **kwargs):
        raise refusal

    monkeypatch.setattr(pd.share, "peek_header", _raise if stage == "peek" else (lambda path: _header()))
    monkeypatch.setattr(pd.share, "find_existing_copy", lambda header, root=None: None)
    monkeypatch.setattr(pd.share, "import_package", _raise)

    async def main(rc):
        await _import_zip(rc, dialog.root / "p.zip")
        text = t("share.err.import_failed", error="no manifest")
        await _settle(lambda: text in _toasts("error"), "the refusal toast")
        assert not any("p.zip" in m for m in _toasts("error"))  # not wrapped in steps.project.import_failed
        assert dialog.state.project.value is None and dialog.dismissed == 0 and dialog.filters == []

    run_rendered(lambda: dialog.render(), main)


def test_a_second_pick_while_an_import_runs_is_dropped(dialog, monkeypatch):
    """Solara runs on_click/on_value whatever the last render disabled: the
    in-flight guard keeps a second pick from starting a second import."""
    release = threading.Event()
    peeked = []

    def _peek(path):
        peeked.append(path)
        release.wait(SETTLE_SECONDS)
        return _header()

    def _import(path, root=None):
        made = _review_copy()
        io.save_project(made)
        return made

    monkeypatch.setattr(pd.share, "peek_header", _peek)
    monkeypatch.setattr(pd.share, "find_existing_copy", lambda header, root=None: None)
    monkeypatch.setattr(pd.share, "import_package", _import)

    async def main():
        _box, rc = dialog.render()
        try:
            await _import_zip(rc, dialog.root / "a.zip")
            await _settle(lambda: peeked, "the first pick read")
            await _settle(lambda: _open_form(rc).find(FileInput).widgets, "the picker remounted")
            _open_form(rc).find(FileInput).widget.v_model = str(dialog.root / "b.zip")
            release.set()
            await _settle(lambda: getattr(dialog.state.project.value, "name", None) == "c__e01-ana",
                          "the copy installed")
            await asyncio.sleep(0.2)
            assert peeked == [str(dialog.root / "a.zip")]
        finally:
            release.set()
            rc.close()

    asyncio.run(main())
