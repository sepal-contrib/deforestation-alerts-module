"""AOI step (spec §6.1): pysepal's Solara AoiView, saved when selected.

Picking an AOI with no project open creates one named <aoi>_<yyyymmdd>.
Once the project has an alert set, the AOI is locked (D10): the view is shown
read-only with a hint to duplicate the project instead. Before that, a change
is refused while a job writes into the project (a Download & harmonize or
Integrate run reads the AOI's files and would write old-AOI files after the
change), as the other steps refuse their edits (spec §5.1).

A change drops every artifact record and deletes their files: the layers
drawn from them (``src:`` harmonized files and previews, ``mask:`` aligned
masks, all cut to the old AOI) leave the map first, as a period's do
(``period_step.hide_dropped_layers``).

``app_state.aoi_result`` is what downloads and previews clip to, so it only
holds a pick the open project took: the saved AOI replayed on load, or a new
AOI once it is saved. A rejected pick leaves it alone.

A review copy (spec §6.3) shows no picker: its AOI is its package's sidecar,
never replayed from the creator's admin code or asset.
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

from deforestation_alerts.project import cleanup, paths
from deforestation_alerts.project.review_copy import read_only
from gui.i18n import t
from gui.scripts.error_text import error_text
from gui.scripts import aoi_io
from gui.scripts.setup_guard import banner_text
from gui.store.project_writers import is_writing, writing
from gui.tile.project_dialog import create_project, names_on_disk
from gui.widget.help import StepIntro


class _Busy(Exception):
    """Raised inside ``update_project``'s callback: the AOI is not changed."""


def _open_name(app_state):
    p = app_state.project.value
    return p.name if p is not None else None


AOI_LAYER_PREFIXES = ("src:", "mask:")  # every one is cut to the AOI


def hide_aoi_layers(layers, content_ids) -> None:
    """Take off the map every layer an AOI change leaves wrong, before its
    files are deleted: the ``src:`` and ``mask:`` layers (harmonized files,
    Earth Engine previews and aligned masks, all cut to the old AOI) and any
    other layer drawn from a dropped file."""
    for key in sorted(layers.on.value):
        content = layers.content_of(key)
        if key.startswith(AOI_LAYER_PREFIXES) or (content is not None and content in content_ids):
            layers.hide(key)


@solara.component
def AoiStep(app_state, map_, gee_interface, layers):
    notifications = use_notifications()
    p = app_state.project.value
    signal = app_state.project_loaded_signal.value
    loading = solara.use_reactive(False)
    locked = p is not None and p.locked
    ro = read_only(p)  # a review copy: the header's banner says why (spec §6.3)
    spec = AoiSpec.from_dict(p.aoi.spec) if p is not None and p.aoi and p.aoi.spec and not ro else None

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
            # (the bounds' reprojection too: a national AOI takes a while)
            record = await asyncio.to_thread(lambda: aoi_io.build_record(result, gdf, aoi_io.gdf_bounds(gdf)))
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
        if read_only(current):  # spec §6.3: a review copy's AOI is the creator's
            notifications.warning(banner_text(current))
            return
        if current.locked:
            notifications.warning(t("steps.aoi.locked"))
            return
        name = current.name
        if is_writing(name):  # spec §5.1: checked again under the state lock below
            notifications.warning(t("steps.aoi.busy"))
            return
        pdir = paths.project_dir(name)
        staged = None
        if gdf is not None:
            # Staged, not written: a switch while it is written must leave this
            # project's sidecar alone (it is moved into place under the lock).
            with writing(name):
                staged = await asyncio.to_thread(aoi_io.stage_sidecar, pdir, gdf)

        gone, dropped, refused = [], set(), []

        def _apply(live):
            if is_writing(name):
                # A job set its mark since the check above: it reads this
                # AOI's files. Raised so nothing is published.
                refused.append(True)
                raise _Busy()
            if staged is not None:
                os.replace(staged, pdir / paths.AOI_SIDECAR)
            else:
                # An Earth Engine AOI has no sidecar. Drop the previous AOI's,
                # or ee_aoi's fallback would clip downloads to the old geometry.
                (pdir / paths.AOI_SIDECAR).unlink(missing_ok=True)
            dropped.update(a.content_id for a in live.artifacts.values())
            gone.extend(aoi_io.apply_aoi(live, record))

        try:
            applied = app_state.update_project(name, _apply)
        except _Busy:
            applied = False
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)  # refused: the project keeps its own sidecar
        if refused:
            notifications.warning(t("steps.aoi.busy"))
            return
        if not applied:
            return  # switched or closed while the sidecar was written
        hide_aoi_layers(layers, dropped)  # before the files go (the Task 38 order)
        await asyncio.to_thread(cleanup.delete_files, pdir, gone)
        app_state.aoi_result.set(result)
        notifications.success(t("steps.aoi.saved", name=record.name))

    attach_task = solara.lab.use_task(_attach, dependencies=None, prefer_threaded=False, raise_error=False)

    def _on_value(result):
        if result is not None:
            attach_task(result)

    def _on_error():
        if attach_task.error:
            notifications.error(t("steps.aoi.failed", error=error_text(attach_task.exception)))

    solara.use_effect(
        _on_error, [attach_task.pending, attach_task.finished, attach_task.error, attach_task.cancelled]
    )

    with solara.Column(style="gap:16px;").key(column_key):
        StepIntro(t("steps.aoi.description"), t("steps.aoi.info_header"), t("steps.aoi.info_md"))
        if locked and not ro:
            # Inline on purpose: a persistent state description, not the outcome
            # of an action (spec §11 routes outcomes through notifications).
            rv.Alert(type="info", dense=True, text=True, children=[t("steps.aoi.locked")])
        if ro:
            # A review copy's AOI is its package's sidecar (spec §5.3, §6.3):
            # no picker, so the creator's admin code or asset is never
            # replayed through GAUL or Earth Engine, and ``aoi_result`` stays
            # unset (``aoi_io.ee_aoi`` then clips to the sidecar).
            if p.aoi is not None:
                solara.Text(p.aoi.name, style="font-weight:500;")
            return
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
