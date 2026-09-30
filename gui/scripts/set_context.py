"""What Analysis needs from the active alert set, under the attribute names the
recipe-era models had (spec §10), so Analysis's internals are unchanged in
slice 1. The shell passes one ``SetContext`` as ``app_tile_model``
(``recipe_folder_path``, ``pin``, ``remember_position``, ``record_review``),
``aux_model`` (``custom_report_template``) and ``selected_alerts_model``
(``combined_path``).

Always reads the live project: the Analysis layout is built once and outlives
project and set switches. Spec §10 describes a *frozen* context that also
carries the set's ``digit_map``, grid and store path; slice 1 accepts this live
one (ruling #84), and fields are added when slice 2's Analysis needs them. A
write that blocks first (a review Save) pins its set with ``pin`` before it
starts, so an Integrate that activates a new set, or a project switch,
meanwhile does not redirect it.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from deforestation_alerts.project import paths
from deforestation_alerts.project.models import AlertSet
from gui.scripts import set_store
from gui.store.workflow_steps import active_set


@dataclass(frozen=True)
class SetTarget:
    """The active set, its project and its folder, read once (``pin``).

    A write that blocks for a while (a review Save's admin lookups) goes where
    it started, whatever the live context points at by the time it writes.
    """

    project: str
    alert_set: AlertSet
    folder: Path


class SetContext:
    def __init__(self, app_state):
        self.app_state = app_state

    @property
    def _project(self):
        return self.app_state.project.value

    @property
    def alert_set(self):
        return active_set(self._project)

    @property
    def set_folder(self) -> Optional[Path]:
        p = self._project
        s = active_set(p)
        return set_store.set_folder(p, s) if s is not None else None

    @property
    def recipe_folder_path(self) -> str:
        """The set's folder: review store, CNN composites, exports and reports
        land there, next to its ``alert_db.parquet``. ``""`` without a set."""
        folder = self.set_folder
        return str(folder) if folder is not None else ""

    @property
    def custom_report_template(self) -> Optional[str]:
        p = self._project
        return p.report_template if p is not None else None

    @property
    def combined_path(self) -> Optional[Path]:
        """The set's combined raster (band 3 is the ``display`` class)."""
        p = self._project
        s = active_set(p)
        if s is None:
            return None
        return paths.resolve(paths.project_dir(p.name), s.combined_rel_path)

    def pin(self) -> Optional[SetTarget]:
        """The active set as it is now, or None without one."""
        p = self._project
        s = active_set(p)
        if s is None:
            return None
        return SetTarget(project=p.name, alert_set=s, folder=set_store.set_folder(p, s))

    def _update_set(self, alerts, field: str, value, target: Optional[SetTarget] = None) -> None:
        """Set ``field`` on ``target``'s set (the active set without one; the
        auto-save writes it). Nothing happens when ``alerts`` are another
        set's, when that project is no longer the open one (set ids repeat
        across projects), or when the value is already there: every publish
        rewrites the manifest."""
        target = target if target is not None else self.pin()
        if target is None or not set_store.alerts_belong_to(alerts, target.alert_set):
            return
        p = self._project
        sid = target.alert_set.id
        s = p.alert_sets.get(sid) if p is not None and p.name == target.project else None
        if s is None or getattr(s, field) == value:
            return

        def _apply(live):
            if sid in live.alert_sets:
                setattr(live.alert_sets[sid], field, value)

        self.app_state.update_project(target.project, _apply)

    def remember_position(self, index: int) -> None:
        """Keep the review position on the set, for the next time it is opened."""
        self._update_set(self.app_state.alerts_gdf.value, "last_alert_index", max(0, int(index)))

    def record_review(self, gdf, target: Optional[SetTarget] = None) -> None:
        """Keep a set's reviewed count (the Analysis badge) in step with its
        saved store. ``target`` is the set the store was written for, pinned
        when the Save started; without it, the active set."""
        if gdf is None or "status" not in gdf:
            return
        self._update_set(gdf, "n_reviewed", int((gdf["status"] != "Not reviewed").sum()), target)
