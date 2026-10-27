"""Analysis's per-session state (spec §10): Solara reactives, no traitlets.

One instance per session (``solara.use_memo`` in the shell), handed to the
raw-widget builders. Values that hold Earth Engine images or GeoDataFrames
compare by identity: their ``==`` is either undefined or element-wise, and a
reactive must notice "a new object" without comparing contents.
"""

import threading
from dataclasses import dataclass, field
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Optional

import solara

from deforestation_alerts.project import grouping as grouping_rules
from deforestation_alerts.project import review_groups
from gui.scripts.alert_filter import AlertFilter
from gui.scripts.set_store import STORE_GENERATION
from gui.store.workflow_steps import active_set


def _same(a, b) -> bool:
    return a is b


def _identity():
    return solara.reactive(None, equals=_same)


def _plain(value=None):
    return solara.reactive(value)


#: The image areas the timeline offers, as the margin in metres added around
#: the alert's box before it is snapped to the download grid. ``normal`` is
#: the margin the analysis has always used.
IMAGE_AREAS = {"normal": 100, "large": 1000, "xlarge": 2500}


@dataclass
class AnalysisState:
    #: the reviewed deforestation polygon(s) drawn for the current alert (GeoDataFrame)
    defo_layer: solara.Reactive = field(default_factory=_identity)
    #: the chosen before / after ee.Image of the current alert
    before_image: solara.Reactive = field(default_factory=_identity)
    after_image: solara.Reactive = field(default_factory=_identity)
    #: their info lists: [source, millis, cloud, value, date suffix]
    before_info: solara.Reactive = field(default_factory=_plain)
    after_info: solara.Reactive = field(default_factory=_plain)
    #: the granule keys the chosen (source, date) mosaics for *this* alert --
    #: what ``image_slider_map_callback`` rebuilds the ee.Image from. Per
    #: alert, because which granules overlap depends on the alert.
    #: These compare by list equality, so setting ``[]`` on an already-empty
    #: one notifies nobody: the matching ``*_info`` publish is the trigger,
    #: and the timeline sets the keys *before* the info for that reason.
    before_keys: solara.Reactive = field(default_factory=lambda: _plain([]))
    after_keys: solara.Reactive = field(default_factory=lambda: _plain([]))
    #: CNN prediction rasters by model number: {1: path, 2: path}
    prediction_files: solara.Reactive = field(default_factory=lambda: solara.reactive({}))
    #: the reviewer's answer for this alert: None until one of the three
    #: statuses is chosen. The store, the PMTiles paint values and Overview's
    #: counts all read these exact strings, so the verdict IS the stored value
    #: and never a label. None is refused by the Save (``confirmation_missing``).
    verdict: solara.Reactive = field(default_factory=_plain)
    #: the reviewer's answers for this alert, by question key: a list of option
    #: codes and typed entries (``gui.scripts.questions``). ``{}`` until the
    #: alert is loaded; a reviewed alert opens on what it stored, an
    #: unreviewed one on nothing (spec U13). The Questions section writes it,
    #: a Save reads it at the click (``capture_alert``).
    answers: solara.Reactive = field(default_factory=lambda: solara.reactive({}))
    #: the growth links ticked for this alert (``"<set>#<id>@<run>"``): the
    #: draft a Save writes to ``grows_from``, seeded from the row. A change
    #: goes through ``set_links``, which marks the alert unsaved.
    links: solara.Reactive = field(default_factory=lambda: solara.reactive(()))
    #: the links Integrate found for this alert (``grows_from_auto``; for a
    #: grouped alert, for any row of its group): the Earlier alerts section
    #: marks them as automatic links (``earlier.refresh_earlier``)
    auto_links: solara.Reactive = field(default_factory=lambda: solara.reactive(()))
    #: the History row's numbers for the draft links (None: no history); the
    #: Earlier alerts section fills it from a worker
    history: solara.Reactive = field(default_factory=_identity)
    #: the detection windows (start, end, as year fractions) of the linked
    #: parents, which the timeline marks; filled with ``history``
    parent_windows: solara.Reactive = field(default_factory=lambda: solara.reactive(()))
    #: whether the reviewer changed the verdict, an answer or the polygon
    #: since the last save of THIS alert. Cleared by ``reset_alert`` (an alert
    #: change forgets it) and by a save that wrote.
    dirty: solara.Reactive = field(default_factory=lambda: solara.reactive(False))
    #: whether the drawing on the maps holds changes *Apply* has not written to
    #: ``defo_layer`` yet. The review Save refuses while it is True
    #: (``review_save.save_review``): what it would store is the polygon
    #: BEFORE those changes, and Save & next would navigate the drawing away.
    #: Set by every drawing change, cleared by *Apply*, by opening or stopping
    #: the drawing session (both put the two back in step) and by
    #: ``reset_alert``.
    drawing_unapplied: solara.Reactive = field(default_factory=lambda: solara.reactive(False))
    #: how far around the alert its images reach: a key of ``IMAGE_AREAS``.
    #: Per alert -- back to ``"normal"`` on every alert change -- so a large
    #: area chosen for one alert never makes every later load and export slow.
    image_area: solara.Reactive = field(default_factory=lambda: solara.reactive("normal"))
    #: bumped once the views' area and both picks' keys follow a new
    #: ``image_area`` (``imagery.set_image_area``): the panes redraw on it.
    area_rev: solara.Reactive = field(default_factory=lambda: solara.reactive(0))
    #: what each CNN run found, by model number: ``{1: (n_patches, area_ha)}``.
    #: Derived from the prediction the worker vectorises, beside the ``.tif``
    #: paths in ``prediction_files``.
    prediction_shapes: solara.Reactive = field(default_factory=lambda: solara.reactive({}))
    #: how many changes the reviewer has made to THIS alert: bumped by every
    #: ``touch``. A save captures it with the alert at the click and clears
    #: ``dirty`` only if it is unchanged when the write lands -- a verdict or
    #: answer changed while the admin lookups blocked is NOT in what was
    #: written (``make_save_runner``). Reset by ``reset_alert``, like
    #: ``dirty`` itself.
    edit_rev: solara.Reactive = field(default_factory=lambda: solara.reactive(0))
    #: time.time() of the last review Save
    last_save_time: solara.Reactive = field(default_factory=lambda: solara.reactive(0.0))
    #: which alert the values above belong to: bumped by every ``reset_alert``.
    #: A worker (a CNN run, an imagery fetch) captures it when it starts and
    #: drops its result once another alert is current (``apply_if_current``).
    alert_token: solara.Reactive = field(default_factory=lambda: solara.reactive(0))
    #: bumped when a merge rewrote the selected alert's row (spec §7.4): the
    #: per-alert view is seeded again from the merged store. ``reset_alert``
    #: leaves it alone (it is a request, not per-alert state).
    reload_alert: solara.Reactive = field(default_factory=lambda: solara.reactive(0))
    #: the overlay of reviewed alerts from earlier periods near the current
    #: alert (spec §9). Session state: set by the panel's switch and distance
    #: field, kept across alert changes (the overlay follows the next alert),
    #: never saved to the project, so ``reset_alert`` leaves them alone.
    show_earlier_reviews: solara.Reactive = field(default_factory=lambda: solara.reactive(False))
    #: the reviewer's own last flip of that switch: a growth alert turns the
    #: switch on for itself only (spec §6.3), the next other alert opens on this
    earlier_choice: solara.Reactive = field(default_factory=lambda: solara.reactive(False))
    earlier_radius_km: solara.Reactive = field(default_factory=lambda: solara.reactive(1.0))
    #: the earlier periods the *Periods* menu hides (ids): session state, all
    #: shown by default, kept across alert changes like the switch
    earlier_hidden_periods: solara.Reactive = field(default_factory=lambda: solara.reactive(frozenset()))
    #: the Analysis filter (spec §8): an ``alert_filter.AlertFilter`` (one
    #: attribute, the values that match) or None. Session state: the analysis
    #: layout binds it into ``AppState`` (``bind_alert_filter``), whose
    #: navigation, start position and Overview Go read it; ``AppState`` clears
    #: it on a project install and when another set's alerts arrive. Kept
    #: across alert changes, so ``reset_alert`` leaves it alone.
    alert_filter: solara.Reactive = field(default_factory=lambda: solara.reactive(None))
    #: the positions ticked in the Nearby list for this alert's group save
    #: (spec 2026-10-26 group review §5.1). Per alert: ``reset_alert`` empties
    #: it. A tick goes through ``toggle_selection``, which marks the alert
    #: unsaved like a verdict change.
    selection: solara.Reactive = field(default_factory=lambda: solara.reactive(frozenset()))
    #: the last *Nearby alerts* search around this alert
    #: (``nearby.refresh_nearby``, a ``review_groups.NearbyResult``): the rows
    #: the section lists and a map click can tick. None until one lands, and
    #: while grouping is off. Per alert.
    nearby_result: solara.Reactive = field(default_factory=_identity)
    #: counts the nearby searches: a search that finishes after a newer one
    #: started is dropped; with the alert token it is the map's pick tag
    #: (``nearby.wire_pick``)
    nearby_seq: int = field(default=0, repr=False)
    #: counts the overlay refreshes (``earlier.refresh_earlier``): a
    #: load that finishes after a newer refresh started is dropped
    earlier_seq: int = field(default=0, repr=False)
    #: serializes ``reset_alert`` with a worker's check-and-apply
    _alert_lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    def reset_alert(self) -> None:
        """Forget what belonged to the previous alert (called on every alert change)."""
        with self._alert_lock:
            self.alert_token.set(self.alert_token.value + 1)
            self.defo_layer.set(None)
            self.before_image.set(None)
            self.after_image.set(None)
            self.before_info.set(None)
            self.after_info.set(None)
            self.before_keys.set([])
            self.after_keys.set([])
            self.prediction_files.set({})
            self.verdict.set(None)
            self.answers.set({})
            self.links.set(())
            self.auto_links.set(())
            self.history.set(None)
            self.parent_windows.set(())
            self.dirty.set(False)
            self.edit_rev.set(0)
            self.drawing_unapplied.set(False)
            self.prediction_shapes.set({})
            # The stamp belongs to the alert that was saved, not to the
            # session: an untouched alert must show no time at all, never the
            # previous alert's (spec §4.4). 0.0 is the declared default and
            # the empty state the footer's save-state line reads.
            self.last_save_time.set(0.0)
            self.image_area.set("normal")
            self.nearby_result.set(None)
            self.selection.set(frozenset())

    def is_current(self, token) -> bool:
        """Whether ``token`` (captured from ``alert_token``) is still the
        current alert's. ``None`` means "not tied to an alert": always current."""
        return token is None or token == self.alert_token.value

    def capture(self, read=dict) -> SimpleNamespace:
        """The current alert's token and per-alert values, read together under
        the lock ``reset_alert`` takes, plus what ``read()`` returns (the alert
        id, its grid, the questionnaire...). A handler takes it on the UI
        thread, at the click: its worker then works on one alert's values, not
        on a mix of the alert clicked for and the one shown since."""
        with self._alert_lock:
            return SimpleNamespace(
                token=self.alert_token.value,
                defo_layer=self.defo_layer.value,
                before_image=self.before_image.value,
                after_image=self.after_image.value,
                before_info=self.before_info.value,
                after_info=self.after_info.value,
                **read(),
            )

    def apply_if_current(self, token, apply) -> bool:
        """Run ``apply()`` only while ``token`` is the current alert's, under the
        lock ``reset_alert`` takes, so an alert change cannot slip in between
        the check and the write. Returns whether it ran. Keep ``apply`` short:
        an alert change waits for it."""
        with self._alert_lock:
            if not self.is_current(token):
                return False
            apply()
            return True


def apply_start_filter(analysis: AnalysisState, f: Optional[AlertFilter]) -> None:
    """Open Analysis on ``f`` (spec §6.2: a review copy opens on its
    evaluator's alerts). Call it AFTER the project is installed
    (``AppState.load_project_state`` clears the filter): the first set
    published after an install keeps it, and its start alert comes from
    ``alert_filter.start_position``. Alerts already on screen move to the
    filter's start at once (``analysis_tile.follow_filter``, subscribed by
    ``wire_alert_navigation``, which also resolves a filter set before it
    ran). ``AppState`` resets the filter inside ``_install`` only, never on
    ``project_loaded_signal``, so nothing after the install clears it."""
    analysis.alert_filter.set(f)


def area_crs(project) -> Optional[str]:
    """The CRS every area of the review is measured in: the project's grid
    CRS, the one Integrate measured the alerts' ``area_ha`` in. ``None``
    before there is a grid (``calculate_total_area`` then takes the local UTM
    zone)."""
    return project.grid.epsg if project is not None and project.grid is not None else None


def min_patch_ha(project) -> float:
    """The smallest CNN patch worth offering: the active set's minimum size,
    the one Integrate dropped its smaller clusters with. ``0.0`` (keep every
    patch) without an active set."""
    s = active_set(project)
    return float(s.params.min_size_ha) if s is not None else 0.0


def _group_plan(alerts, alert_id, grouping, selection, remove):
    """The plan a Save (or *Remove from group*) of ``alert_id`` writes, made
    from the table on hand (spec §5.1): None while grouping is off or there
    is no row to save."""
    if grouping is None or alerts is None or alert_id is None or not 0 <= alert_id < len(alerts):
        return None
    if remove:
        return review_groups.plan_remove(alerts, alert_id)
    return review_groups.plan_group(alerts, alert_id, selection)


def capture_alert(analysis: AnalysisState, widgets, app_state, *, remove=False) -> SimpleNamespace:
    """``analysis.capture`` with the alert's row, the review widgets, the
    answers and the questions they answer: what a Save, a Download or a CNN
    run uses, read at the click (UI thread). The answers are copied, so an
    edit after the click is not in what the Save writes. ``area_crs`` and
    ``min_patch_ha`` are the project's, for the areas the Save stores and the
    patches a CNN run offers.

    Group review (spec 2026-10-26 §5.1): ``grouping`` is the set's effective
    grouping (None: off), ``selection`` the ticked rows, ``plan`` the
    ``review_groups.GroupPlan`` the save writes (None while grouping is off:
    today's single save; a REMOVE plan with ``remove``, which raises
    ``ValueError`` for a row that is not a group's member), ``evaluator`` the
    review copy's evaluator (None outside a copy). ``set_id`` and
    ``generation`` (the alerts' store generation) tell a later step whether
    the save was made for another set or store."""

    def _read():
        p = app_state.project.value
        s = active_set(p) if p is not None else None
        alerts = app_state.alerts_gdf.value
        alert_id = app_state.actual_alert_id.value
        grouping = grouping_rules.effective(p, s)
        selection = frozenset(analysis.selection.value or ())
        rc = getattr(p, "review_copy", None) if p is not None else None
        return {
            "area_crs": area_crs(p),
            "min_patch_ha": min_patch_ha(p),
            "alerts": alerts,
            "alert_id": alert_id,
            "grid": getattr(widgets, "actual_alert_grid", None),
            "bb": getattr(widgets, "actual_bb", None),
            "status": analysis.verdict.value,
            "answers": {k: list(v) for k, v in (analysis.answers.value or {}).items()},
            "questions": list(p.questions) if p is not None else [],
            "links": tuple(analysis.links.value),
            "drawing_unapplied": analysis.drawing_unapplied.value,
            # What a save compares against when it lands: see ``touch``.
            "edit_rev": analysis.edit_rev.value,
            "grouping": grouping,
            "selection": selection,
            "plan": _group_plan(alerts, alert_id, grouping, selection, remove),
            "evaluator": rc.evaluator if rc is not None else None,
            "set_id": s.id if s is not None else None,
            "generation": (getattr(alerts, "attrs", None) or {}).get(STORE_GENERATION),
        }

    return analysis.capture(_read)


def toggle_selection(analysis: AnalysisState, position: int) -> None:
    """Tick or untick one Nearby row and mark the alert unsaved: a tick made
    after a Save's click is not in what that save writes (``edit_rev``)."""
    current = frozenset(analysis.selection.value or ())
    position = int(position)
    analysis.selection.set(current - {position} if position in current else current | {position})
    touch(analysis)


def touch(analysis: AnalysisState) -> None:
    """Mark the current alert as changed since its last save (the save-state
    line in the panel footer reads it).

    ``edit_rev`` counts the changes as well as flagging them, because a save
    in flight has to tell "still the review I captured" from "changed since":
    ``dirty`` alone is cleared by that save either way, and the reviewer's
    later change would be reported as stored.
    """
    analysis.dirty.set(True)
    analysis.edit_rev.set(analysis.edit_rev.value + 1)


def set_links(analysis: AnalysisState, links: Sequence[str]) -> None:
    """Replace the draft growth links (duplicates dropped, order kept) and mark
    the alert unsaved, only when they changed."""
    new = tuple(dict.fromkeys(links))
    if new != tuple(analysis.links.value):
        analysis.links.set(new)
        touch(analysis)


def imagery_changed(analysis: AnalysisState) -> None:
    """The reviewer picked other imagery for this alert (the timeline's lanes).

    Two consequences, both per alert. The review is unsaved: the store keeps
    the pair a save was made with (``before_img`` / ``after_img`` and their
    info), so another pick is a change to save. And whatever a CNN run found
    is invalidated: a prediction is a function of the composite it ran on, so
    the "Found ..." line and its *Add to map* would otherwise offer a raster
    of imagery the reviewer is no longer looking at (``edit_tools`` watches
    ``prediction_files`` and puts the rows back to "nothing run").

    What a prediction already put in the *drawing* is left alone: by then it is
    the reviewer's own work, and *Remove from map* still takes it off.

    Not called by the auto-pick in ``timeline.render``: that one is the alert's
    default, and an alert nobody touched must not open claiming unsaved work.
    """
    analysis.prediction_files.set({})
    analysis.prediction_shapes.set({})
    touch(analysis)


def drawing_changed(analysis: AnalysisState) -> None:
    """Mark the drawing changed since the last *Apply*, and the alert unsaved.

    The two go together for every change the reviewer makes on the maps: the
    alert has unsaved work AND the review Save must refuse until *Apply*
    writes it to ``defo_layer``, or the save stores the polygon from before the
    change and Save & next moves off it."""
    analysis.drawing_unapplied.set(True)
    touch(analysis)
