import asyncio
from types import SimpleNamespace

import ipyvuetify as v
import pytest
import reacton
import solara
from pysepal.solara.notifications import NotificationProvider

from deforestation_alerts.project import io, paths
from deforestation_alerts.project.models import AoiRecord, add_predefined_source
from gui.i18n import t
from gui.store.autosave import install_autosave
from gui.store.project_writers import writing
from gui.store.state_manager import AppState
from gui.tile import project_dialog as pd


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


# ---------------------------------------------------------------------------
# The dialog, rendered: the list, name checks, open/new/duplicate, delete.
# ---------------------------------------------------------------------------
@solara.component
def _Harness(app_state, dismiss):
    NotificationProvider()  # the dialog's use_notifications() needs it rendered first
    pd.ProjectDialog(app_state=app_state, dismiss=dismiss)


@pytest.fixture
def dialog(monkeypatch, tmp_path):
    """A real AppState with the auto-save hook; the projects table is swapped
    for a recorder so the test calls the row actions the dialog wires up."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    ns = SimpleNamespace(rows=[], dismissed=0, root=tmp_path, state=AppState())

    @solara.component
    def _RecorderTable(title, columns, rows, empty_text):
        ns.rows.append(rows)

    monkeypatch.setattr(pd, "ProductTable", _RecorderTable)
    unsubscribe = install_autosave(ns.state)

    def _dismiss():
        ns.dismissed += 1

    ns.render = lambda: reacton.render(_Harness(app_state=ns.state, dismiss=_dismiss), handle_error=False)
    yield ns
    unsubscribe()


async def _settle(done, what, tries=300):
    for _ in range(tries):
        if done():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"never happened: {what}")


def _listed(ns):
    """{name: "sources / periods / sets"} as the table shows it now."""
    return {row["key"]: row["cells"][1]["value"] for row in ns.rows[-1]} if ns.rows else None


def _row_action(ns, name, kind):
    row = next(r for r in ns.rows[-1] if r["key"] == name)
    return next(a["on_click"] for a in row["actions"] if a["kind"] == kind)


def _button(finder, label):
    return next(w for w in finder.find(v.Btn).widgets if label in w.children)


def _open_form(rc):
    """The name form that is open now (New or Duplicate)."""
    return rc.find(v.Dialog, v_model=True)


def _submit_name(rc, name):
    form = _open_form(rc)
    form.find(v.TextField, label=t("steps.project.name_label")).widget.v_model = name
    _button(form, t("common.create")).fire_event("click", None)


def test_the_list_follows_the_open_projects_saves(dialog):
    """The counts and time of the open project are read again on every save,
    not only when a project is installed or closed."""
    async def main():
        box, rc = dialog.render()
        try:
            dialog.state.new_project_state(pd.create_project("a"))
            await _settle(lambda: _listed(dialog) == {"a": "0 / 0 / 0"}, "the new project listed")
            p = dialog.state.project.value
            add_predefined_source(p, "GLAD-L")
            dialog.state.commit(p)  # auto-saved
            await _settle(lambda: _listed(dialog) == {"a": "1 / 0 / 0"}, "the saved source counted")
        finally:
            rc.close()

    asyncio.run(main())


def test_a_name_taken_on_disk_since_the_list_was_read_is_refused(dialog):
    """Another tab created "b" after this dialog read the list: Create must
    check the disk, or the auto-save of a new "b" overwrites that project."""
    async def main():
        box, rc = dialog.render()
        try:
            await _settle(lambda: _listed(dialog) == {}, "the empty list read")
            other = pd.create_project("b")
            other.look_back_days = 99
            io.save_project(other)  # the other tab
            manifest = dialog.root / "b" / paths.MANIFEST
            before = manifest.read_text()

            _button(rc, t("steps.project.new")).fire_event("click", None)
            _submit_name(rc, "b")

            assert _listed(dialog) == {}  # the list on screen did not know "b"
            errors = [w.children for w in _open_form(rc).find(v.Alert).widgets]
            assert errors == [[t("steps.project.name_exists")]]
            assert dialog.state.project.value is None and manifest.read_text() == before
        finally:
            rc.close()

    asyncio.run(main())


@pytest.mark.parametrize("how", ["open", "new", "duplicate"])
def test_a_new_opened_or_duplicated_project_starts_at_aoi_and_closes_the_dialog(dialog, how):
    """Already on AOI the navigation does not move, so it never closed the
    dialog (it did from any other step): the dialog closes itself."""
    async def main():
        io.save_project(pd.create_project("a"))
        box, rc = dialog.render()
        try:
            dialog.state.current_page_view.value = "aoi_tile"  # AOI is showing
            await _settle(lambda: _listed(dialog) == {"a": "0 / 0 / 0"}, "the saved project listed")
            if how in ("open", "duplicate"):
                _row_action(dialog, "a", "open")()
                await _settle(lambda: dialog.state.project.value is not None, "the project opened")
            if how == "new":
                _button(rc, t("steps.project.new")).fire_event("click", None)
                _submit_name(rc, "n")
            if how == "duplicate":
                _button(rc, t("steps.project.duplicate")).fire_event("click", None)
                _submit_name(rc, "c")
            expected = {"open": "a", "new": "n", "duplicate": "c"}[how]
            assert dialog.state.project.value.name == expected
            assert dialog.state.current_page_view.value == "aoi_tile"
            assert dialog.dismissed == (2 if how == "duplicate" else 1)
        finally:
            rc.close()

    asyncio.run(main())
