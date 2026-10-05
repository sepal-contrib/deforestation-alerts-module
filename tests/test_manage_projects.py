"""Manage projects and the delete confirmation (spatial_risk's
gui/widget/manage_projects.py): the list, the search, the Open gate, the
chips, and the gate of a delete (Review Focus 5). Both dialogs are
presentational: every action is a callback, recorded here."""

from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import ipyvuetify as v
import reacton
import solara

from deforestation_alerts.project.io import ProjectInfo
from gui.i18n import t
from gui.widget import manage_projects as mp
from gui.widget.enter_key import ENTER_DEFAULT


def _texts(widget):
    if isinstance(widget, str):
        return [widget]
    out = []
    for child in getattr(widget, "children", None) or []:
        out += _texts(child)
    return out


def _button(finder, label):
    return next(w for w in finder.find(v.Btn).widgets if label in w.children)


def _trash(rc):
    return [b for b in rc.find(v.Btn).widgets
            if any(isinstance(c, v.Icon) and c.children == ["mdi-delete-outline"] for c in b.children)]


def _titles(rc):
    return [w.children[0] for w in rc.find(v.ListItemTitle).widgets]


def _info(name, sources=1, periods=0, sets=0, minutes=5):
    return ProjectInfo(name=name, n_sources=sources, n_periods=periods, n_sets=sets,
                       updated_at=datetime.now() - timedelta(minutes=minutes))


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def test_relative_time():
    now = datetime(2026, 10, 1, 12, 0, 0)
    assert mp.relative_time(now - timedelta(seconds=59), now) == "just now"
    assert mp.relative_time(now + timedelta(seconds=5), now) == "just now"  # a clock a little ahead
    assert mp.relative_time(now - timedelta(minutes=1), now) == "1 minute ago"
    assert mp.relative_time(now - timedelta(minutes=59, seconds=59), now) == "59 minutes ago"
    assert mp.relative_time(now - timedelta(hours=1), now) == "1 hour ago"
    assert mp.relative_time(now - timedelta(hours=23), now) == "23 hours ago"
    assert mp.relative_time(now - timedelta(days=3, hours=5), now) == "3 days ago"


def test_filter_infos():
    infos = [ProjectInfo(name=n, n_sources=0, n_periods=0, n_sets=0, updated_at=None)
             for n in ("North", "north_2", "South")]
    assert [i.name for i in mp.filter_infos(infos, " NOR ")] == ["North", "north_2"]
    assert mp.filter_infos(infos, "") == infos and mp.filter_infos(infos, None) == infos
    assert mp.filter_infos(infos, "zz") == []


def test_count_chips_accent_only_the_alert_sets():
    info = _info("a", sources=2, periods=1, sets=0)
    assert mp.count_chips(info) == [("2 sources", False), ("1 period", False), ("0 alert sets", False)]
    assert mp.count_chips(replace(info, n_sets=3))[2] == ("3 alert sets", True)


# ---------------------------------------------------------------------------
# ManageProjectsDialog
# ---------------------------------------------------------------------------
def _render_manage(infos, *, current=None, busy=False, selected=None):
    ns = SimpleNamespace(calls=[], open=solara.reactive(True), selected=solara.reactive(selected))

    def _select(name):
        ns.calls.append(("select", name))
        ns.selected.set(name)

    @solara.component
    def Page():
        mp.ManageProjectsDialog(
            open=ns.open.value, infos=infos, selected=ns.selected.value, on_select=_select,
            on_open=lambda: ns.calls.append(("open", ns.selected.value)),
            on_delete=lambda name: ns.calls.append(("delete", name)),
            on_cancel=lambda: ns.calls.append(("cancel",)), current=current, busy=busy)

    ns.box, ns.rc = reacton.render(Page(), handle_error=False)
    return ns


def test_the_manage_frame_is_spatial_risks():
    ns = _render_manage([_info("a")])
    try:
        dialog = ns.rc.find(v.Dialog).widget
        assert dialog.max_width == "480px" and dialog.eager and not dialog.persistent
        assert t("steps.project.manage_title") in _texts(dialog)
    finally:
        ns.rc.close()


def test_rows_show_the_open_chip_the_counts_and_the_age():
    ns = _render_manage([_info("north", sources=2, sets=1), _info("south", minutes=0)], current="north")
    try:
        assert _titles(ns.rc) == ["north", "south"]
        chips = ns.rc.find(v.Chip).widgets
        open_chip = [c for c in chips if c.children == [t("steps.project.chip_open")]]
        assert len(open_chip) == 1 and open_chip[0].outlined
        assert open_chip[0] in ns.rc.find(v.ListItemTitle).widgets[0].children  # on north's title
        accented = [c.children[0] for c in chips if c.color == "primary" and not c.outlined]
        assert accented == ["1 alert set"]
        texts = _texts(ns.rc.find(v.Dialog).widget)
        assert {"2 sources", "0 periods", "0 alert sets"} <= set(texts)
        assert t("steps.project.modified", time_ago="5 minutes ago") in texts
        assert t("steps.project.modified", time_ago="just now") in texts
    finally:
        ns.rc.close()


def test_the_search_filters_and_is_cleared_on_every_opening():
    ns = _render_manage([_info("north"), _info("north_2"), _info("south")])
    try:
        search = ns.rc.find(v.TextField, label=t("steps.project.search")).widget
        search.v_model = "NOR"
        assert _titles(ns.rc) == ["north", "north_2"]
        search.v_model = "zz"
        assert _titles(ns.rc) == []
        assert t("steps.project.no_match", query="zz") in _texts(ns.rc.find(v.Dialog).widget)
        search.v_model = None  # the field's clear button
        assert _titles(ns.rc) == ["north", "north_2", "south"]
        search.v_model = "sou"
        ns.open.set(False)
        ns.open.set(True)
        assert search.v_model == "" and _titles(ns.rc) == ["north", "north_2", "south"]
    finally:
        ns.rc.close()


def test_no_saved_project_reads_the_empty_text_without_a_search():
    ns = _render_manage([])
    try:
        assert not ns.rc.find(v.TextField).widgets and not ns.rc.find(v.ListItem).widgets
        assert t("steps.project.list_empty") in _texts(ns.rc.find(v.Dialog).widget)
    finally:
        ns.rc.close()


def test_open_is_the_enter_default_and_needs_a_visible_selection():
    ns = _render_manage([_info("north"), _info("south")])
    try:
        def open_btn():
            return _button(ns.rc, t("steps.project.open_selected"))

        assert ENTER_DEFAULT in open_btn().class_.split() and open_btn().disabled  # nothing selected
        ns.rc.find(v.ListItemGroup).widget.v_model = "south"
        assert ns.calls == [("select", "south")] and not open_btn().disabled
        search = ns.rc.find(v.TextField, label=t("steps.project.search")).widget
        search.v_model = "nor"
        assert open_btn().disabled  # the selection is filtered out of view
        search.v_model = ""
        assert not open_btn().disabled
        open_btn().fire_event("click", None)
        assert ns.calls[-1] == ("open", "south")
        assert len([b for b in ns.rc.find(v.Btn).widgets if ENTER_DEFAULT in (b.class_ or "").split()]) == 1
    finally:
        ns.rc.close()


def test_open_is_off_while_a_load_or_a_delete_runs():
    ns = _render_manage([_info("a")], selected="a", busy=True)
    try:
        assert _button(ns.rc, t("steps.project.open_selected")).disabled
    finally:
        ns.rc.close()


def test_the_trash_button_cancel_and_esc_report_to_the_caller():
    ns = _render_manage([_info("north"), _info("south")])
    try:
        _trash(ns.rc)[1].fire_event("click", None)
        _button(ns.rc, t("common.cancel")).fire_event("click", None)
        ns.rc.find(v.Dialog).widget.v_model = False  # ESC or a click outside
        assert ns.calls == [("delete", "south"), ("cancel",), ("cancel",)]
    finally:
        ns.rc.close()


# ---------------------------------------------------------------------------
# ConfirmDeleteDialog (Review Focus 5: the gate)
# ---------------------------------------------------------------------------
def _render_confirm(name="north", size_bytes=5_400_000, **kwargs):
    ns = SimpleNamespace(calls=[], open=solara.reactive(True), name=solara.reactive(name))

    @solara.component
    def Page():
        mp.ConfirmDeleteDialog(open=ns.open.value, name=ns.name.value, size_bytes=size_bytes,
                               on_cancel=lambda: ns.calls.append("cancel"),
                               on_confirm=lambda: ns.calls.append("confirm"), **kwargs)

    ns.box, ns.rc = reacton.render(Page(), handle_error=False)
    return ns


def _delete_btn(ns):
    return _button(ns.rc, t("common.delete"))


def test_delete_waits_for_the_exact_name():
    ns = _render_confirm()
    try:
        dialog = ns.rc.find(v.Dialog).widget
        assert dialog.max_width == "420px" and dialog.eager and not dialog.persistent
        texts = _texts(dialog)
        assert t("steps.project.delete_title", name="north") in texts
        assert t("steps.project.delete_size", size="5.4 MB") in texts
        assert not ns.rc.find(v.Alert).widgets
        field = ns.rc.find(v.TextField).widget
        assert field.label == t("steps.project.delete_type_label", name="north") and field.autofocus
        assert "mt-6" in field.class_.split()
        btn = _delete_btn(ns)
        assert btn.color == "error" and ENTER_DEFAULT in btn.class_.split() and btn.disabled
        field.v_model = "North"  # exact, case and all
        assert _delete_btn(ns).disabled
        _delete_btn(ns).fire_event("click", None)  # a press that beat the last keystroke
        assert ns.calls == []
        field.v_model = " north "  # surrounding blanks are forgiven
        assert not _delete_btn(ns).disabled
        _delete_btn(ns).fire_event("click", None)
        assert ns.calls == ["confirm"]
    finally:
        ns.rc.close()


def test_the_size_reads_measuring_until_it_is_counted():
    ns = _render_confirm(size_bytes=None)
    try:
        assert t("steps.project.size_counting") in _texts(ns.rc.find(v.Dialog).widget)
    finally:
        ns.rc.close()
    ns = _render_confirm(size_bytes=0)  # never "()" nor "0.0 MB"
    try:
        assert t("steps.project.delete_size", size="0.1 MB") in _texts(ns.rc.find(v.Dialog).widget)
    finally:
        ns.rc.close()


def test_a_running_writer_refuses_the_delete():
    ns = _render_confirm(writer_active=True, is_open_project=True)
    try:
        alerts = ns.rc.find(v.Alert).widgets
        assert [(a.type, _texts(a)) for a in alerts] == [("error", [t("steps.project.delete_busy")])]
        assert not ns.rc.find(v.TextField).widgets  # nothing to type
        assert _delete_btn(ns).disabled
        _delete_btn(ns).fire_event("click", None)
        assert ns.calls == []
    finally:
        ns.rc.close()


def test_the_open_project_is_warned_about():
    ns = _render_confirm(is_open_project=True)
    try:
        alerts = ns.rc.find(v.Alert).widgets
        assert [(a.type, _texts(a)) for a in alerts] == [("warning", [t("steps.project.delete_open_warning")])]
        assert ns.rc.find(v.TextField).widgets
    finally:
        ns.rc.close()


def test_while_the_delete_runs_the_dialog_holds():
    ns = _render_confirm(busy=True)
    try:
        assert ns.rc.find(v.Dialog).widget.persistent is True  # no ESC, no click outside
        assert ns.rc.find(v.ProgressLinear).widgets
        assert _button(ns.rc, t("common.cancel")).disabled
        ns.rc.find(v.TextField).widget.v_model = "north"
        assert _delete_btn(ns).disabled
    finally:
        ns.rc.close()


def test_the_typed_name_is_cleared_for_the_next_target():
    ns = _render_confirm()
    try:
        field = ns.rc.find(v.TextField).widget
        field.v_model = "north"
        ns.name.set("south")
        assert field.v_model == "" and _delete_btn(ns).disabled
        field.v_model = "south"
        ns.open.set(False)
        ns.open.set(True)
        assert field.v_model == ""
        _button(ns.rc, t("common.cancel")).fire_event("click", None)
        assert ns.calls == ["cancel"]
    finally:
        ns.rc.close()
