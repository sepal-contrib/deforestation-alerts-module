"""Analysis's per-session state (spec §10): Solara reactives, no traitlets.

One instance per session (``solara.use_memo`` in the shell), handed to the
raw-widget builders. Values that hold Earth Engine images or GeoDataFrames
compare by identity: their ``==`` is either undefined or element-wise, and a
reactive must notice "a new object" without comparing contents.
"""

import threading
from dataclasses import dataclass, field
from types import SimpleNamespace

import solara


def _same(a, b) -> bool:
    return a is b


def _identity():
    return solara.reactive(None, equals=_same)


def _plain(value=None):
    return solara.reactive(value)


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
    #: whether the reviewer changed the verdict, an answer or the polygon
    #: since the last save of THIS alert. Cleared by ``reset_alert`` (an alert
    #: change forgets it) and by a save that wrote.
    dirty: solara.Reactive = field(default_factory=lambda: solara.reactive(False))
    #: whether the drawing on the maps holds changes *Apply* has not written to
    #: ``defo_layer`` yet. The review Save refuses while it is True
    #: (``export._save_attributes``): what it would store is the polygon
    #: BEFORE those changes, and Save & next would navigate the drawing away.
    #: Set by every drawing change, cleared by *Apply*, by opening or stopping
    #: the drawing session (both put the two back in step) and by
    #: ``reset_alert``.
    drawing_unapplied: solara.Reactive = field(default_factory=lambda: solara.reactive(False))
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
    #: the overlay of reviewed alerts from earlier periods near the current
    #: alert (spec §9). Session state: set by the panel's switch and distance
    #: field, kept across alert changes (the overlay follows the next alert),
    #: never saved to the project, so ``reset_alert`` leaves them alone.
    show_earlier_reviews: solara.Reactive = field(default_factory=lambda: solara.reactive(False))
    earlier_radius_km: solara.Reactive = field(default_factory=lambda: solara.reactive(1.0))
    #: counts the overlay refreshes (``earlier.refresh_earlier_reviews``): a
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
            self.dirty.set(False)
            self.edit_rev.set(0)
            self.drawing_unapplied.set(False)
            self.prediction_shapes.set({})
            # The stamp belongs to the alert that was saved, not to the
            # session: an untouched alert must show no time at all, never the
            # previous alert's (spec §4.4). 0.0 is the declared default and
            # the empty state the footer's save-state line reads.
            self.last_save_time.set(0.0)

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


def capture_alert(analysis: AnalysisState, widgets, app_state) -> SimpleNamespace:
    """``analysis.capture`` with the alert's row, the review widgets, the
    answers and the questions they answer: what a Save, a Download or a CNN
    run uses, read at the click (UI thread). The answers are copied, so an
    edit after the click is not in what the Save writes."""

    def _read():
        p = app_state.project.value
        return {
            "alerts": app_state.alerts_gdf.value,
            "alert_id": app_state.actual_alert_id.value,
            "grid": getattr(widgets, "actual_alert_grid", None),
            "bb": getattr(widgets, "actual_bb", None),
            "status": analysis.verdict.value,
            "answers": {k: list(v) for k, v in (analysis.answers.value or {}).items()},
            "questions": list(p.questions) if p is not None else [],
            "drawing_unapplied": analysis.drawing_unapplied.value,
            # What a save compares against when it lands: see ``touch``.
            "edit_rev": analysis.edit_rev.value,
        }

    return analysis.capture(_read)


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
