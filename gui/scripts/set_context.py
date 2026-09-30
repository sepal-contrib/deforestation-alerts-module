"""What Analysis needs from the active alert set, under the attribute names the
recipe-era models had (spec §10), so Analysis's internals are unchanged in
slice 1. The shell passes one ``SetContext`` as ``app_tile_model``
(``recipe_folder_path``, ``remember_position``, ``record_review``),
``aux_model`` (``custom_report_template``) and ``selected_alerts_model``
(``combined_path``).

Always reads the live project: the Analysis layout is built once and outlives
project and set switches. Spec §10 describes a *frozen* context that also
carries the set's ``digit_map``, grid and store path; slice 1 accepts this live
one (ruling #84), and fields are added when slice 2's Analysis needs them.
"""

from pathlib import Path
from typing import Optional

from deforestation_alerts.project import paths
from gui.scripts import set_store
from gui.store.workflow_steps import active_set


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

    def _update_set(self, alerts, field: str, value) -> None:
        """Set ``field`` on the active set (the auto-save writes it), unless
        ``alerts`` are another set's or the value is already there: every
        publish rewrites the manifest."""
        p = self._project
        s = active_set(p)
        if s is None or not set_store.alerts_belong_to(alerts, s) or getattr(s, field) == value:
            return
        sid = s.id

        def _apply(live):
            if sid in live.alert_sets:
                setattr(live.alert_sets[sid], field, value)

        self.app_state.update_project(p.name, _apply)

    def remember_position(self, index: int) -> None:
        """Keep the review position on the set, for the next time it is opened."""
        self._update_set(self.app_state.alerts_gdf.value, "last_alert_index", max(0, int(index)))

    def record_review(self, gdf) -> None:
        """Keep the set's reviewed count (the Analysis badge) in step with a saved store."""
        if gdf is None or "status" not in gdf:
            return
        self._update_set(gdf, "n_reviewed", int((gdf["status"] != "Not reviewed").sum()))
