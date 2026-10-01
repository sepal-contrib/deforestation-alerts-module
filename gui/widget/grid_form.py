"""The project grid card (spec §7.1): EPSG with auto-UTM, resolution, size."""

import reacton.ipyvuetify as rv
import solara
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine import grid as gridmod
from gui.i18n import t

LARGE_BYTES = 2e9  # spec §7.1: warn (not block) above 2 GB per source


def grid_error_key(epsg, resolution):
    err = gridmod.validate_projection(epsg, resolution)
    return f"steps.prepare.grid_{err}" if err else None


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
        applied = []

        def _apply(live):
            if live.locked:
                return  # a set was recorded since this form rendered (spec D10): the grid is frozen
            live.grid = new
            applied.append(True)

        if not app_state.update_project(name, _apply):
            return  # the project was closed or switched meanwhile
        if not applied:
            notifications.warning(t("steps.prepare.grid_locked"))
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
            size = gridmod.estimate_bytes(preview)
            solara.Text(t("steps.prepare.grid_size", w=preview.width, h=preview.height, gb=f"{size / 1e9:.1f}"),
                        classes=["text--secondary"])
            if size > LARGE_BYTES:
                solara.Text(t("steps.prepare.grid_large"), classes=["warning--text"])
        if locked:
            solara.Text(t("steps.prepare.grid_locked"), classes=["text--secondary"])
        else:
            solara.Button(t("steps.prepare.grid_save"), color="primary", small=True, disabled=bool(err),
                          on_click=_save)
