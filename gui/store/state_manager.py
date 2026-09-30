"""Per-session reactive state (spec §5.4). Built once per session in the shell
(``solara.use_memo``) and handed to tiles as an argument.

The Project reactive compares by identity: tiles mutate the live Project and
publish ``p.model_copy()`` (``commit``); value equality would swallow those
updates. Worker threads never replace the project wholesale: they return what
they produced and ``update_project`` applies it to the *live* project, only if
that project is still the open one.

Lock ordering. ``_lock`` makes check-current + mutate + publish atomic against
a project switch (close/new/load take it too). Publishing is not cheap: a
reactive ``set`` runs its subscribers synchronously in the calling thread, so
while ``_lock`` is held the auto-save hook writes the manifest and every
component reading the project re-renders, taking reacton's render lock. Hence:

- call ``commit``, ``update_project`` and the ``*_project_state`` methods from
  event handlers, tasks or worker threads, never from a render body or an
  effect (effects run inside the render lock, so the two locks would be taken
  in opposite orders by a worker and the render thread);
- the auto-save hook runs inside that window and must not call back into these
  methods or wait on another thread.
"""

import contextlib
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import solara

from deforestation_alerts.project import paths
from deforestation_alerts.project.models import Project


class AppState:
    def __init__(self):
        self.project = solara.reactive(None, equals=lambda a, b: a is b)
        self.project_loaded_signal = solara.reactive(0)
        self.last_saved = solara.reactive(None)
        self.save_error = solara.reactive(None)
        # pysepal AoiResult of the current selection (holds ee objects; not saved)
        self.aoi_result = solara.reactive(None)
        # predefined-source catalogue metadata (coverage end, GLAD-L bands),
        # fetched once per session by gui.scripts.catalogue_meta; not saved
        self.catalogue_meta = solara.reactive({})
        self._restoring_depth = solara.reactive(0)
        self._lock = threading.RLock()
        # True only while a project read from disk is being installed: the
        # auto-save hook skips that publish (it would just rewrite the file).
        self._loading = False
        # navigation (unchanged from the workflow-side-panel shell)
        self.current_page_view = solara.reactive("about_tile")
        # index into gui.store.workflow_steps.STEPS; owned by the shell, set by
        # the pipeline header and the navigation bridge
        self.active_step = solara.reactive(0)
        # "workflow" (pipeline header + the active step) or a rail item's key
        self.panel_view = solara.reactive("workflow")
        # mirror of pysepal ThemeState.dark, kept by an observer in Page(): the
        # header cannot read MapApp's theme from Solara's own dark mode
        self.dark = solara.reactive(False)
        # alert review
        self.alerts_gdf = solara.reactive(None)
        self.actual_alert_id = solara.reactive(-1)
        self.loading = solara.reactive(False)
        # LEGACY until Task 22: the old shell and tiles still read these, so the
        # app keeps running on the old flow while the new steps are built.
        # active_map_view: the right-panel tile owning the shared map's layers
        # (MapLayers replaces it, spec §4.3); aoi_name: mirror of
        # aoi_date_model.name kept by a traitlets observer in Page().
        self.active_map_view = solara.reactive("")
        self.aoi_name = solara.reactive(None)
        self.recipe_name = solara.reactive("")
        self.recipe_folder_path = solara.reactive("")
        self.error_message = solara.reactive(None)

    # ---- project lifecycle ---------------------------------------------------
    @property
    def project_dir(self) -> Optional[Path]:
        p = self.project.value
        return paths.project_dir(p.name) if p is not None else None

    def commit(self, p: Project) -> None:
        """Publish an in-place mutation (the auto-save hook saves it)."""
        with self._lock:
            self.project.set(p.model_copy())

    def update_project(self, name: str, fn: Callable[[Project], None]) -> bool:
        """Apply a job's result to the live project and publish it, only if the
        project named ``name`` is still the open one (the spec's
        ``publish_if_current``). Returns False, touching nothing, when it was
        closed, deleted or switched meanwhile."""
        with self._lock:
            live = self.project.value
            if live is None or live.name != name:
                return False
            fn(live)
            self.project.set(live.model_copy())
            return True

    @property
    def restoring(self) -> bool:
        return self._restoring_depth.value > 0

    def restoring_project(self):
        @contextlib.contextmanager
        def _window():
            self._restoring_depth.value += 1
            try:
                yield
            finally:
                self._restoring_depth.value -= 1

        return _window()

    def _install(self, p: Optional[Project], *, loading: bool) -> None:
        with self.restoring_project(), self._lock:
            self.aoi_result.set(None)
            self.alerts_gdf.set(None)
            self.actual_alert_id.set(-1)
            self.save_error.set(None)  # before the set: the auto-save may report into it
            # a new project has never been saved: the auto-save stamps it
            self.last_saved.set(datetime.now() if loading and p is not None else None)
            self._loading = loading
            try:
                self.project.set(p)  # auto-save runs here unless loading
            finally:
                self._loading = False
            self.project_loaded_signal.set(self.project_loaded_signal.value + 1)

    def load_project_state(self, p: Project) -> None:
        self._install(p, loading=True)

    def new_project_state(self, p: Project) -> None:
        self._install(p, loading=False)

    def close_project_state(self) -> None:
        self._install(None, loading=True)

    # ---- alert navigation (unchanged) ------------------------------------
    @property
    def max_alert_id(self):
        gdf = self.alerts_gdf.value
        return (len(gdf) - 1) if gdf is not None else -1

    def go_to(self, idx):
        self.actual_alert_id.value = max(0, min(idx, self.max_alert_id))

    def go_next(self):
        self.go_to(self.actual_alert_id.value + 1)

    def go_prev(self):
        self.go_to(self.actual_alert_id.value - 1)


def aoi_name_from_model(model):
    """The AOI label for the pipeline header gates, or None when no AOI is set.

    The *geometry* gates the label: ``name`` is a traitlet that outlives a
    cleared geometry, so a name without a ``feature_collection`` is a stale
    label rather than a selected AOI.

    Solara-free on purpose (no reactives, no widgets) so the mirror installed by
    ``Page()._mirror_aoi_name`` can be unit-tested without importing
    ``gui.solara_app`` (which calls ``init_ee()`` at import time).
    """
    fc = getattr(model, "feature_collection", None)
    name = getattr(model, "name", None) if fc is not None else None
    return name or None
