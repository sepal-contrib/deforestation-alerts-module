"""AOI step (spec §6.1): pysepal's Solara AoiView, saved when selected.

Picking an AOI with no project open creates one named <aoi>_<yyyymmdd>.
Once the project has an alert set, the AOI is locked (D10): the view is shown
read-only with a hint to duplicate the project instead.

``app_state.aoi_result`` is what downloads and previews clip to, so it only
holds a pick the open project took: the saved AOI replayed on load, or a new
AOI once it is saved. A rejected pick leaves it alone.
"""

import asyncio
import contextlib
import os
from datetime import date

import reacton.ipyvuetify as rv
import solara
from pysepal.solara.components.aoi import AoiView
from pysepal.solara.components.aoi.aoi_spec import AoiSpec
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.project import paths
from gui.i18n import t
from gui.scripts import aoi_io
from gui.store.project_writers import writing
from gui.tile.project_dialog import create_project, names_on_disk


def _open_name(app_state):
    p = app_state.project.value
    return p.name if p is not None else None


@solara.component
def AoiStep(app_state, map_, gee_interface):
    notifications = use_notifications()
    p = app_state.project.value
    signal = app_state.project_loaded_signal.value
    loading = solara.use_reactive(False)
    locked = p is not None and p.locked
    spec = AoiSpec.from_dict(p.aoi.spec) if p is not None and p.aoi and p.aoi.spec else None

    created_here = solara.use_ref(None)  # the name of a project this step just created
    key_ref = solara.use_ref((signal, f"aoi-{signal}"))
    if key_ref.current[0] != signal:
        # A project install remounts the picker (it replays the new project's
        # AOI), except the install of the project this step just created from
        # the pick on screen: remounting would clear and refetch that pick.
        same_pick = p is not None and created_here.current == p.name
        key_ref.current = (signal, key_ref.current[1] if same_pick else f"aoi-{signal}")
        created_here.current = None
    column_key = key_ref.current[1]

    async def _attach(result):
        # The pick belongs to the project open when it was made: a switch while
        # the AOI is measured must not hand it to the next project.
        owner = _open_name(app_state)
        gdf = await result.get_gdf_async()
        current = app_state.project.value
        if gdf is None:
            # Earth Engine AOI: its digest needs no request, so a restore of the
            # saved AOI never asks Earth Engine for its bounds.
            digest = aoi_io.lazy_digest(result)
            if current is not None and current.aoi is not None and digest and current.aoi.digest == digest:
                if _open_name(app_state) == owner:
                    app_state.aoi_result.set(result)
                return
            geom = await gee_interface.get_info_async(result.feature_collection.geometry().bounds())
            record = aoi_io.build_record(result, None, aoi_io.bounds_from_geojson(geom))
        else:
            # the digest hashes every geometry: off the event loop
            record = await asyncio.to_thread(aoi_io.build_record, result, gdf, aoi_io.gdf_bounds(gdf))
        if _open_name(app_state) != owner:
            return
        current = app_state.project.value
        if current is None:
            # Every folder counts, not only listed projects: a manifest-less
            # folder of that name would be overwritten.
            existing = await asyncio.to_thread(names_on_disk)
            if app_state.project.value is not None:
                return  # a project was opened meanwhile
            # Installed once, with its AOI: a second publish would remount the
            # picker in between (outline gone, a second request, two toasts).
            new = create_project(aoi_io.auto_project_name(result.name, existing, date.today()))
            new.aoi = record
            if gdf is not None:
                await asyncio.to_thread(aoi_io.write_sidecar, paths.project_dir(new.name), gdf)
            if app_state.project.value is not None:
                # a project was opened while the sidecar was written: the
                # folder was free when named, so only that file is in it
                sidecar = paths.project_dir(new.name) / paths.AOI_SIDECAR
                sidecar.unlink(missing_ok=True)
                with contextlib.suppress(OSError):
                    sidecar.parent.rmdir()
                return
            created_here.current = new.name  # its install must not remount the picker
            app_state.new_project_state(new)
            app_state.aoi_result.set(result)
            notifications.success(t("steps.aoi.saved", name=record.name))
            return
        if current.aoi is not None and current.aoi.digest == record.digest:
            app_state.aoi_result.set(result)  # a restore replaying the saved AOI
            return
        if current.locked:
            notifications.warning(t("steps.aoi.locked"))
            return
        name = current.name
        pdir = paths.project_dir(name)
        staged = None
        if gdf is not None:
            # Staged, not written: a switch while it is written must leave this
            # project's sidecar alone (it is moved into place under the lock).
            with writing(name):
                staged = await asyncio.to_thread(aoi_io.stage_sidecar, pdir, gdf)

        def _apply(live):
            if staged is not None:
                os.replace(staged, pdir / paths.AOI_SIDECAR)
            else:
                # An Earth Engine AOI has no sidecar. Drop the previous AOI's,
                # or ee_aoi's fallback would clip downloads to the old geometry.
                (pdir / paths.AOI_SIDECAR).unlink(missing_ok=True)
            live.aoi = record
            live.grid = None  # the grid is derived from the AOI bounds
            live.artifacts = {}

        try:
            applied = app_state.update_project(name, _apply)
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)  # refused: the project keeps its own sidecar
        if not applied:
            return  # switched or closed while the sidecar was written
        app_state.aoi_result.set(result)
        notifications.success(t("steps.aoi.saved", name=record.name))

    attach_task = solara.lab.use_task(_attach, dependencies=None, prefer_threaded=False, raise_error=False)

    def _on_value(result):
        if result is not None:
            attach_task(result)

    def _on_error():
        if attach_task.error:
            notifications.error(t("steps.aoi.failed", error=str(attach_task.exception)))

    solara.use_effect(
        _on_error, [attach_task.pending, attach_task.finished, attach_task.error, attach_task.cancelled]
    )

    with solara.Column(style="gap:16px;").key(column_key):
        solara.Markdown(f"### {t('steps.aoi.title')}")
        solara.Text(t("steps.aoi.description"), classes=["text--secondary"])
        if locked:
            # Inline on purpose: a persistent state description, not the outcome
            # of an action (spec §11 routes outcomes through notifications).
            rv.Alert(type="info", dense=True, text=True, children=[t("steps.aoi.locked")])
        # Keyed: the alert appearing above must not remount the picker (its
        # unmount clears the AOI from the map and the remount replays it).
        with rv.Html(tag="div", style_="pointer-events:none;opacity:0.6;" if locked else "").key("aoi-view"):
            AoiView(
                value=app_state.aoi_result.value,
                on_value=_on_value,
                loading=loading,
                methods="ALL",
                map_=map_,
                gee=True,
                spec=spec,
            )
