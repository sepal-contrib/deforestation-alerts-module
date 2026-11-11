"""Manage projects: search, open, rename and delete saved projects (spatial_risk's
``gui/widget/manage_projects.py``, spec D5).

Both dialogs are presentational: every action is a callback of
``ProjectDialog`` (``gui/tile/project_dialog.py``), which owns the state, the
disk and the notifications. Both render at that dialog's top level, never
inside a list row: nested button -> Dialog toggles have proved unreliable
(``gui/widget/confirm_dialog.py``).

The shared ``ConfirmDialog`` is not reused for the delete: deleting a project
(irreversible, up to several GB) is gated on typing its name.

Neither dialog is a form, so neither sits on the form frame
(``gui/widget/form_dialog.py``): Manage is a list that closes on a click
outside, as in spatial_risk, and the confirmation is persistent only while the
delete runs.
"""

from datetime import datetime

import reacton.ipyvuetify as rv
import solara

from gui.i18n import size_text, t
from gui.widget.enter_key import ENTER_DEFAULT
from gui.widget.text_style import MUTED


def relative_time(when: datetime, now: datetime) -> str:
    """'just now', 'N minutes ago', 'N hours ago', 'N days ago' (spatial_risk's
    ``format_relative``). A time a little ahead of ``now`` reads 'just now'."""
    seconds = max(0, int((now - when).total_seconds()))
    if seconds < 60:
        return t("steps.project.ago_now")
    if seconds < 3600:
        return t("steps.project.ago_minutes", count=seconds // 60)
    if seconds < 86400:
        return t("steps.project.ago_hours", count=seconds // 3600)
    return t("steps.project.ago_days", count=seconds // 86400)


def filter_infos(infos: list, query) -> list:
    """The projects whose name contains ``query``, ignoring case; every project
    for a blank query (spatial_risk's ``filter_project_infos``). Order kept."""
    needle = (query or "").strip().lower()
    return list(infos) if not needle else [i for i in infos if needle in i.name.lower()]


def count_chips(info) -> list[tuple[str, bool]]:
    """``(label, accent)`` for each count chip of a listed project, in display
    order: sources, periods, alert sets. Only the alert sets are accented, and
    only when there is at least one: the project has results to review."""
    return [
        (t("steps.project.count_sources", count=info.n_sources), False),
        (t("steps.project.count_periods", count=info.n_periods), False),
        (t("steps.project.count_sets", count=info.n_sets), info.n_sets > 0),
    ]


def ChipRow(info):
    """A project's count chips (``count_chips``), as Manage's rows and the
    Project dialog's cards show them; an unconverted version 1 recipe is
    marked by an outlined chip first."""
    with rv.Html(tag="div", style_="display:flex;flex-wrap:wrap;gap:4px;margin:2px 0;"):
        if info.legacy:
            rv.Chip(children=[t("steps.project.v1_mark")], x_small=True, outlined=True,
                    attributes={"title": t("steps.project.v1_hint")})
        for label, accent in count_chips(info):
            rv.Chip(children=[label], x_small=True, color="primary" if accent else None,
                    text_color="white" if accent else None)


#: A row's (or a Project card's) last line: the AOI and the age, side by
#: side, wrapping on a narrow dialog.
META = "display:flex;flex-wrap:wrap;gap:0 12px;align-items:center;"
#: Right-aligned with tabular digits, so the sizes line up from row to row.
_SIZE = "font-size:13px;font-weight:500;font-variant-numeric:tabular-nums;min-width:56px;text-align:right;"


def icon_button(icon, label, on_click, color=None, disabled=False):
    """A small icon button; ``label`` is its tooltip and its accessible name."""
    solara.Button(icon_name=icon, icon=True, small=True, color=color, on_click=on_click, disabled=disabled,
                  attributes={"title": label, "aria-label": label})


def aoi_item(name):
    """A marker and the AOI's name, or "no AOI yet"."""
    with rv.Html(tag="span", style_="display:inline-flex;align-items:center;gap:3px;"):
        rv.Icon(x_small=True, children=["mdi-map-marker-outline"])
        solara.Text(name or t("steps.project.no_aoi"))


def size_label(size_bytes) -> str:
    """A row's size on disk; "…" while it is measured (None)."""
    return "…" if size_bytes is None else size_text(max(size_bytes, 1))


@solara.component
def ManageProjectsDialog(open, infos, selected, on_select, on_open, on_delete, on_cancel, current=None,
                         busy=False, covered=False, on_rename=lambda name: None, sizes=None, converting=None):
    """Search, open, rename and delete the saved projects.

    ``infos``: the ``io.list_projects()`` read the caller holds. ``selected``:
    the row clicked in this dialog; ``current``: the project open in the app,
    badged "open". The two answer different questions, so they are shown by
    different things (the list selection, a chip). ``on_select(name | None)``,
    ``on_open()`` (the footer), ``on_rename(name)`` and ``on_delete(name)`` (a
    row's pencil and trash buttons; the caller opens the form or the
    confirmation), ``on_cancel()`` (Cancel, ESC, a click outside). ``sizes``:
    ``{name: bytes}`` measured so far; a row not in it reads "…". ``busy``: a
    load or a delete runs. ``covered``: a dialog opened from this one (the
    rename form, the delete confirmation) is on top. This dialog then lets
    go of the focus: two open dialogs that both retain focus pull it back and
    forth (VDialog's ``onFocusin``), so the one on top never keeps it, and the
    browser's call stack overflows. ``converting``: the version 1 recipe
    being converted for its open, whose row says so.

    Scrollable: only the list scrolls, so the search and the footer's Open
    stay in view however long the list is.
    """
    sizes = sizes or {}
    query, set_query = solara.use_state("")

    # A filter left from the last time would hide rows on this opening.
    def _reset_query():
        if open:
            set_query("")

    solara.use_effect(_reset_query, [open])

    shown = filter_infos(infos, query)
    now = datetime.now()
    needle = (query or "").strip()
    # Open never fires on nothing, nor on a selection the search hides: it
    # would open a project the user cannot see.
    open_disabled = busy or not any(i.name == selected for i in shown)

    with rv.Dialog(v_model=open, on_v_model=lambda v: None if v else on_cancel(), max_width="520px",
                   eager=True, retain_focus=not covered, scrollable=True):
        with rv.Card():
            with rv.CardTitle():
                solara.Text(t("steps.project.manage_title"))
            # Outside the card text, which is what a scrollable dialog scrolls.
            with rv.Html(tag="div", class_="px-6 pb-3", style_="flex:0 0 auto;"):
                if infos:
                    rv.TextField(label=t("steps.project.search"), v_model=query,
                                 on_v_model=lambda v: set_query(v or ""), prepend_inner_icon="mdi-magnify",
                                 dense=True, outlined=True, clearable=True, hide_details=True)
            rv.Divider()
            with rv.CardText(style_="max-height:60vh;"):
                if not infos:
                    solara.Text(t("steps.project.list_empty"), style=MUTED)
                elif not shown:
                    solara.Text(t("steps.project.no_match", query=needle), style=MUTED)
                else:
                    with rv.List(three_line=True):
                        with rv.ListItemGroup(v_model=selected, on_v_model=on_select, color="primary"):
                            for info in shown:
                                with rv.ListItem(value=info.name):
                                    with rv.ListItemContent():
                                        # Outlined, so the badge cannot be taken for
                                        # one of the filled count chips under it.
                                        title = [info.name]
                                        if current and info.name == current:
                                            title.append(rv.Chip(children=[t("steps.project.chip_open")],
                                                                 x_small=True, outlined=True, color="primary",
                                                                 class_="ml-2"))
                                        rv.ListItemTitle(children=title)
                                        ChipRow(info=info)
                                        with rv.ListItemSubtitle(style_=META):
                                            aoi_item(info.aoi_name)
                                            if info.name == converting:
                                                solara.Text(t("steps.project.v1_converting", name=info.name))
                                            elif info.updated_at is not None:
                                                solara.Text(t("steps.project.modified",
                                                              time_ago=relative_time(info.updated_at, now)))
                                    with rv.ListItemAction(style_="flex-direction:row;align-items:center;gap:2px;"):
                                        size = sizes.get(info.name)
                                        rv.Html(tag="span", children=[size_label(size)],
                                                style_=_SIZE + ("opacity:0.5;" if size is None else ""),
                                                attributes={"title": t("steps.project.size_on_disk")})
                                        # a v1 folder is renamed once converted (an open converts it)
                                        icon_button("mdi-pencil-outline",
                                                    t("steps.project.v1_convert_first" if info.legacy
                                                      else "steps.project.rename"),
                                                    lambda n=info.name: on_rename(n), disabled=info.legacy)
                                        icon_button("mdi-delete-outline", t("common.delete"),
                                                    lambda n=info.name: on_delete(n), color="error")
            rv.Divider()
            with rv.CardActions(style_="justify-content:flex-end;gap:8px;"):
                solara.Button(t("common.cancel"), text=True, small=True, on_click=on_cancel)
                solara.Button(t("steps.project.open_selected"), icon_name="mdi-folder-open-outline",
                              color="primary", small=True, disabled=open_disabled,
                              # Enter presses it; a disabled button ignores the press.
                              classes=[ENTER_DEFAULT], on_click=on_open)


@solara.component
def ConfirmDeleteDialog(open, name, size_bytes, on_cancel, on_confirm, is_open_project=False,
                        writer_active=False, busy=False):
    """Type-the-name confirmation for deleting a project's whole folder.

    ``size_bytes``: the folder's size, so the user sees what goes; None while
    it is counted. ``is_open_project``: the project is open; it is closed
    first. ``writer_active``: a task is still writing into the folder, so the
    delete is refused, since its auto-save would create the folder again
    (``gui/store/project_writers.py``). ``busy``: the delete runs. The caller
    closes the dialog (``on_cancel``, ``on_confirm``).
    """
    typed, set_typed = solara.use_state("")

    # Never carry a name typed for one target into the next confirmation.
    def _reset_typed():
        if open:
            set_typed("")

    solara.use_effect(_reset_typed, [open, name])

    # Exact and case-sensitive (the folder goes); only blanks around it are forgiven.
    can_delete = bool(name) and (typed or "").strip() == name and not writer_active and not busy

    def _confirm_if_typed():
        # The disabled button is the browser's copy of this gate; a press sent
        # before the last keystroke reached the kernel still lands here.
        if can_delete:
            on_confirm()

    size = (t("steps.project.size_counting") if size_bytes is None
            else t("steps.project.delete_size", size=size_text(max(size_bytes, 1))))

    with rv.Dialog(v_model=open, on_v_model=lambda v: None if v else on_cancel(), max_width="420px",
                   eager=True,
                   # The delete cannot be called back once it runs: ESC or a click
                   # outside must not hand back a live Manage dialog over a folder
                   # that is being erased.
                   persistent=busy):
        with rv.Card():
            with rv.CardTitle():
                solara.Text(t("steps.project.delete_title", name=name or ""))
            with rv.CardText():
                solara.Text(size)
                # The confirmation's gating text (Global Constraints): why Delete
                # is off, or what it will do besides.
                if writer_active:
                    rv.Alert(type="error", dense=True, text=True, class_="mt-4 mb-0",
                             children=[t("steps.project.delete_busy")])
                elif is_open_project:
                    rv.Alert(type="warning", dense=True, text=True, class_="mt-4 mb-0",
                             children=[t("steps.project.delete_open_warning")])
                if not writer_active:
                    rv.TextField(label=t("steps.project.delete_type_label", name=name or ""), v_model=typed,
                                 on_v_model=lambda v: set_typed(v or ""), dense=True, outlined=True,
                                 autofocus=True, hide_details=True,
                                 # Vuetify's spacing helper (24px, !important): the
                                 # outlined label sits on the top border and needs air
                                 # under the text above it.
                                 class_="mt-6")
                if busy:
                    rv.ProgressLinear(indeterminate=True, class_="mt-4")
            with rv.CardActions(style_="justify-content:flex-end;gap:8px;"):
                # Cancel cannot stop the delete, so it does not offer to.
                solara.Button(t("common.cancel"), text=True, small=True, disabled=busy, on_click=on_cancel)
                solara.Button(t("common.delete"), color="error", small=True, disabled=not can_delete,
                              loading=busy,
                              # Enter in the name field deletes, once the name is typed.
                              classes=[ENTER_DEFAULT], on_click=_confirm_if_typed)
