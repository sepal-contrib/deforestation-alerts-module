"""Per-session reactive state (spec §5.4). Built once per session in the shell
(``solara.use_memo``) and handed to tiles as an argument.

The Project reactive compares by identity: tiles mutate the live Project and
publish ``p.model_copy()`` (``commit``); value equality would swallow those
updates. Worker threads never replace the project wholesale: they return what
they produced and ``update_project`` applies it to the *live* project, only if
that project is still the open one.

The copy is shallow (spec §5.4), so every published snapshot shares the live
project's dict fields (``sources``, ``periods``, ``artifacts``, ``alert_sets``,
``masks``). A callback handed to ``commit`` or ``update_project`` therefore
replaces a container it changes (``p.alert_sets = {**p.alert_sets, sid: s}``)
and never mutates it in place: the publish runs on the caller's thread while a
render on another thread may be iterating the same dict, and an in-place
``[...] =`` or ``pop`` there is a "dictionary changed size during iteration"
in that render. Setting a plain attribute, or a field of one entry, is fine.

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

import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import solara

from deforestation_alerts.project import paths
from deforestation_alerts.project.models import Project
from gui.scripts import alert_filter as af


def _set_id_of(gdf) -> Optional[str]:
    """The set the alerts belong to (their ``set_id``), or None: no alerts,
    no rows, or a store written before the column."""
    if gdf is None or not len(gdf) or "set_id" not in gdf.columns:
        return None
    return str(gdf["set_id"].iloc[0])


class AppState:
    def __init__(self):
        self.project = solara.reactive(None, equals=lambda a, b: a is b)
        self.project_loaded_signal = solara.reactive(0)
        self.last_saved = solara.reactive(None)
        self.save_error = solara.reactive(None)
        # pysepal AoiResult of the current selection (holds ee objects; not saved)
        self.aoi_result = solara.reactive(None)
        # GLAD-L's band list (its builder needs it), fetched once per session
        # by gui.scripts.remote_facts.RemoteFactsLoader; not saved
        self.catalogue_meta = solara.reactive({})
        # Earth Engine facts fetched by gui.scripts.remote_facts.RemoteFactsLoader
        # (not saved; the coverage itself is saved in the project)
        self.coverage_pending = solara.reactive(False)
        self.coverage_error = solara.reactive(None)
        self.coverage_retry = solara.reactive(0)  # "Check again" bumps it
        self.coverage_refreshed = solara.reactive(frozenset())  # {(aoi_digest, key)} refreshed this session
        self.sidecar_pending = solara.reactive(False)
        # The project's scene index, fetched by gui.scripts.scene_loader.
        # SceneIndexLoader (holds DataFrames; cached on disk, not in the
        # project document). The timeline reads it for every alert, so it is
        # compared by identity: a merged copy is a new index.
        self.scene_index = solara.reactive(None, equals=lambda a, b: a is b)
        self.scene_manifest = solara.reactive(None, equals=lambda a, b: a is b)
        self.scene_index_pending = solara.reactive(False)
        self.scene_index_error = solara.reactive(None)
        self.scene_index_retry = solara.reactive(0)  # the timeline's "Try again" bumps it
        # The window the strip is showing for the alert on screen, published by
        # its "+1 month" buttons and replaced -- not accumulated -- when
        # another alert opens. The loader unions it with the default ±2 months,
        # so a month outside the cache is fetched rather than drawn empty. A
        # tuple of two dates, or None for "the default is enough".
        self.scene_window_request = solara.reactive(None)
        self._lock = threading.RLock()
        # True only while a project read from disk is being installed: the
        # auto-save hook skips that publish (it would just rewrite the file).
        self._loading = False
        # navigation (unchanged from the workflow-side-panel shell)
        self.current_page_view = solara.reactive("about_tile")
        # index into gui.store.workflow_steps.STEPS; owned by the shell, set by
        # the pipeline header and the navigation bridge
        self.active_step = solara.reactive(0)
        # Which screen shows: the workflow (MapApp, the steps in its right
        # panel) or the analysis screen, its sibling (gui.solara_app's
        # _ScreenBridge). Entering Analysis leaves ``active_step`` alone, so
        # "← Workflow" returns to the step it was entered from.
        self.screen = solara.reactive("workflow")
        # mirror of pysepal ThemeState.dark, kept by an observer in Page(): the
        # header cannot read MapApp's theme from Solara's own dark mode
        self.dark = solara.reactive(False)
        # The alert layers on the shared map, switched from Integrate &
        # vectorize once a run has produced a set (spec §9.2). Intent, not
        # mechanism: the shell's _AlertLayers leaf owns every MapLayers call,
        # so a step never has to hold a build closure. A layer switched on
        # stays on while the user moves between steps (MapLayers ruling #10).
        self.show_alerts = solara.reactive(False)
        self.show_earlier_sets = solara.reactive(False)
        # the set's too-small / over-cap pixels (combined band 4), likewise
        self.show_unanalysable = solara.reactive(False)
        # alert review
        self.alerts_gdf = solara.reactive(None)
        self.actual_alert_id = solara.reactive(-1)
        # Overview's Go into another set (spec §9.3): (set id, alert id) to
        # select once that set's alerts are published; None otherwise
        self.goto_alert = solara.reactive(None)
        # legends: published by MapLayers exactly while each layer is on the
        # map (spec §9.4)
        self.layer_legends = solara.reactive(())
        self.selected_legend = solara.reactive("")
        # The Analysis filter (``AnalysisState.alert_filter``), bound by the
        # analysis layout (``bind_alert_filter``): the navigation below, the
        # start position (``set_store.publish_set_alerts``) and Overview's Go
        # read it. Unbound (a session without Analysis, most tests), every
        # alert matches. ``_filter_set_id`` is the set the filter was made on.
        self._alert_filter = None
        self._filter_set_id = None

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

    def _install(self, p: Optional[Project], *, loading: bool) -> None:
        # The auto-save skips this publish through ``_loading``; tiles tell a
        # replayed AOI from a new one by its digest, not by a flag.
        with self._lock:
            # A project opened, created or closed shows the workflow: the
            # analysis screen belongs to the project it was opened on.
            self.screen.set("workflow")
            self.aoi_result.set(None)
            self.show_alerts.set(False)
            self.show_earlier_sets.set(False)
            self.show_unanalysable.set(False)
            # The filter belongs to the project it was made in. Cleared before
            # the alerts go, and the set it was made on forgotten: the first
            # set published after this keeps a filter set from here on (a
            # review copy's, spec §6.2).
            self.clear_alert_filter()
            self._filter_set_id = None
            self.alerts_gdf.set(None)
            self.actual_alert_id.set(-1)
            self.goto_alert.set(None)
            # The scene index is per project and per AOI: the previous
            # project's granules must not answer this one's alerts for the
            # moment it takes SceneIndexLoader to read the new index, and the
            # "+1 month" window belongs to the view that asked for it.
            self.scene_index.set(None)
            self.scene_manifest.set(None)
            self.scene_index_error.set(None)
            self.scene_window_request.set(None)
            self.save_error.set(None)  # before the set: the auto-save may report into it
            # An opened project was last saved when its manifest says (spec §4.1
            # "saved X ago"); a new one has never been saved: the auto-save stamps it.
            self.last_saved.set((p.updated_at or datetime.now()) if loading and p is not None else None)
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
        # Without a project every step but AOI (STEPS[0]) is locked: the
        # workflow must not stay parked on one ("4 / 6 ... nothing yet").
        self.active_step.set(0)

    # ---- the Analysis filter (spec §8) ------------------------------------
    def bind_alert_filter(self, reactive) -> None:
        """Make ``reactive`` (``AnalysisState.alert_filter``) the filter the
        navigation follows. Binding the same reactive again does nothing."""
        if reactive is self._alert_filter:
            return
        first = self._alert_filter is None
        self._alert_filter = reactive
        self._filter_set_id = _set_id_of(self.alerts_gdf.value)
        if first:
            # Once per AppState. Solara keeps a reactive's listeners in a set,
            # so this runs in no particular order among the other
            # ``alerts_gdf`` listeners: the readers that must not see a
            # filter left from another set (``analysis_tile.select_start_alert``
            # and ``follow_filter``) call ``forget_filter_of_another_set``
            # themselves first. It is idempotent.
            self.alerts_gdf.subscribe(self.forget_filter_of_another_set)

    @property
    def alert_filter_reactive(self):
        """The bound filter reactive, or None (unbound)."""
        return self._alert_filter

    @property
    def alert_filter(self):
        """The filter in force (an ``AlertFilter``), or None."""
        return self._alert_filter.value if self._alert_filter is not None else None

    def set_alert_filter(self, f) -> None:
        """Filter Analysis by ``f`` (None clears it). A no-op while unbound."""
        if self._alert_filter is not None:
            self._alert_filter.set(f)

    def clear_alert_filter(self) -> None:
        self.set_alert_filter(None)

    def alert_mask(self):
        """One bool per alert on hand: whether it matches the filter (all
        True without one; empty without alerts)."""
        p = self.project.value
        return af.match_mask(self.alerts_gdf.value, self.alert_filter, p.questions if p is not None else [])

    def forget_filter_of_another_set(self, gdf) -> None:
        """Spec §8.1: the filter is reset when another set's alerts arrive (a
        set switch, Overview's Go into another set, Integrate's new set). No
        alerts in between (a project install, a deleted set) keep the set it
        was made on, so the next publish is compared with that one.
        Idempotent: a second call with the same alerts changes nothing, so an
        ``alerts_gdf`` listener that reads the filter calls it first rather
        than rely on the listeners' order."""
        sid = _set_id_of(gdf)
        if sid is None:
            return
        if self._filter_set_id is not None and sid != self._filter_set_id:
            self.clear_alert_filter()
        self._filter_set_id = sid

    # ---- alert navigation (spec §8.3: over the filter's matches) -----------
    @property
    def max_alert_id(self):
        gdf = self.alerts_gdf.value
        return (len(gdf) - 1) if gdf is not None else -1

    def go_to(self, idx):
        """Select the alert at row position ``idx``, clamped. The filter is
        not consulted: the jump menu and Overview's Go check it first."""
        self.actual_alert_id.value = max(0, min(idx, self.max_alert_id))

    def go_next(self):
        """The next alert, or under a filter the next match; at the end, stay."""
        if self.alert_filter is None:
            self.go_to(self.actual_alert_id.value + 1)
            return
        target = af.next_match(self.alert_mask(), self.actual_alert_id.value)
        if target is not None:
            self.go_to(target)

    def go_prev(self):
        """The previous alert, or under a filter the previous match; at the start, stay."""
        if self.alert_filter is None:
            self.go_to(self.actual_alert_id.value - 1)
            return
        target = af.prev_match(self.alert_mask(), self.actual_alert_id.value)
        if target is not None:
            self.go_to(target)
