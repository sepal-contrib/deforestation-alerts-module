"""The project grid card (spec §7.1): EPSG with auto-UTM, resolution, size.

A Save is refused once the project has a set (D10), and while a job writes
into the project (a Download & harmonize or Integrate run reads the grid:
spec §5.1, as the other steps refuse their edits).
"""

import reacton.ipyvuetify as rv
import solara
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine import grid as gridmod
from gui.i18n import format_number, t
from gui.store.project_writers import is_writing
from gui.widget.text_style import MUTED

LARGE_BYTES = 2e9  # spec §7.1: warn (not block) above 2 GB per source


def grid_error_key(epsg, resolution):
    err = gridmod.validate_projection(epsg, resolution)
    return f"steps.prepare.grid_{err}" if err else None


def grid_size_text(grid) -> str:
    """The grid's pixels and its uncompressed per-source size (spec §7.1):
    in GB from 1 GB up, in MB below it (a small AOI read "about 0.0 GB")."""
    size = gridmod.estimate_bytes(grid)
    w, h = format_number(grid.width), format_number(grid.height)
    if size >= 1e9:
        return t("steps.prepare.grid_size", w=w, h=h, gb=format_number(size / 1e9, 1))
    return t("steps.prepare.grid_size_mb", w=w, h=h, mb=format_number(max(size / 1e6, 0.1), 1))


def _metres(value) -> str:
    """A resolution as the field shows it: 10.0 -> "10", 2.5 -> "2.5"."""
    value = float(value)
    return str(int(value)) if value.is_integer() else str(value)


@solara.component
def GridForm(app_state):
    """Hook-free gate over the project reactive (ruling #18).

    The fields are local state and the step is mounted for the whole session,
    so they live in a child keyed by the project load, the AOI and the default
    resolution: opening, reopening or switching a project, or changing its AOI
    or sources, remounts them instead of keeping one project's typed grid (or
    UTM zone) for another, which a later Save would write into it.

    Two reacton details shape this:

    - The key sits on this component's own child, not on an element inside
      the caller's ``solara.Column``: reacton compares elements without their
      keys, so a Column whose children look unchanged is not re-rendered and a
      new key given to one of them is never seen.
    - Only this gate reads the project; the child gets plain values. A child
      that read the reactive itself would re-render on its own when the
      project closes, before this gate unmounts it, and fail on ``None``.
    """
    p = app_state.project.value
    if p is None or p.aoi is None:
        return  # being unmounted: the step shows its lock message instead
    default_res = gridmod.default_resolution(p)
    _GridFields(
        app_state=app_state, name=p.name, bounds=tuple(p.aoi.bounds), grid=p.grid, locked=p.locked,
        default_res=default_res,
    ).key(f"grid-{app_state.project_loaded_signal.value}-{p.aoi.digest}-{default_res}")


@solara.component
def _GridFields(app_state, name, bounds, grid, locked, default_res):
    notifications = use_notifications()
    epsg, set_epsg = solara.use_state(grid.epsg if grid else gridmod.auto_utm_epsg(bounds))
    res, set_res = solara.use_state(_metres(grid.resolution if grid else default_res))
    err = grid_error_key(epsg, res)
    res_err = err == "steps.prepare.grid_bad_resolution"  # shown next to its own field

    def _auto():
        set_epsg(gridmod.auto_utm_epsg(bounds))

    def _save():
        if err:
            return
        new = gridmod.build_grid(bounds, epsg, float(res))
        refused = []

        def _apply(live):
            if live.locked:
                refused.append("steps.prepare.grid_locked")  # a set was recorded since this form rendered (D10)
            elif is_writing(name):
                refused.append("steps.prepare.grid_busy")  # a job reads the grid; its own record re-checks it
            else:
                live.grid = new

        if not app_state.update_project(name, _apply):
            return  # the project was closed or switched meanwhile
        if refused:
            notifications.warning(t(refused[0]))
            return
        notifications.success(t("steps.prepare.grid_saved", w=new.width, h=new.height))

    preview = None if err else gridmod.build_grid(bounds, epsg, float(res))
    with rv.Card(outlined=True, class_="pa-3"):
        solara.Text(t("steps.prepare.grid_title"), style="font-weight:600;")
        with rv.Html(tag="div", style_="display:flex;gap:8px;align-items:flex-start;margin-top:8px;"):
            epsg_field = rv.TextField(label=t("steps.prepare.grid_epsg"), v_model=epsg, on_v_model=set_epsg,
                                      dense=True, outlined=True, append_icon="mdi-crosshairs-gps",
                                      disabled=locked, error_messages=[t(err)] if err and not res_err else [])
            rv.TextField(label=t("steps.prepare.grid_resolution"), v_model=res, on_v_model=set_res,
                         type="number", suffix="m", dense=True, outlined=True, disabled=locked,
                         error_messages=[t(err)] if res_err else [])
        # rv.use_event is a hook: called unconditionally (spec §11)
        rv.use_event(epsg_field, "click:append", lambda *_: None if locked else _auto())
        if preview is not None:
            solara.Text(grid_size_text(preview), style=MUTED)
            if gridmod.estimate_bytes(preview) > LARGE_BYTES:
                solara.Text(t("steps.prepare.grid_large"), classes=["warning--text"])
        if locked:
            solara.Text(t("steps.prepare.grid_locked"), style=MUTED)
        else:
            solara.Button(t("steps.prepare.grid_save"), color="primary", small=True, disabled=bool(err),
                          on_click=_save)
