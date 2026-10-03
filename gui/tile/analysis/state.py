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
    before_keys: solara.Reactive = field(default_factory=lambda: solara.reactive([]))
    after_keys: solara.Reactive = field(default_factory=lambda: solara.reactive([]))
    #: Planet monthly image lists (None until a source publishes them)
    before_planet: solara.Reactive = field(default_factory=_plain)
    after_planet: solara.Reactive = field(default_factory=_plain)
    #: CNN prediction rasters by model number: {1: path, 2: path}
    prediction_files: solara.Reactive = field(default_factory=lambda: solara.reactive({}))
    #: time.time() of the last review Save
    last_save_time: solara.Reactive = field(default_factory=lambda: solara.reactive(0.0))
    #: which alert the values above belong to: bumped by every ``reset_alert``.
    #: A worker (a CNN run, an imagery fetch) captures it when it starts and
    #: drops its result once another alert is current (``apply_if_current``).
    alert_token: solara.Reactive = field(default_factory=lambda: solara.reactive(0))
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
    """``analysis.capture`` with the alert's row and the review widgets: what a
    Save, a Download or a CNN run uses, read at the click (UI thread)."""

    def _read():
        drivers = getattr(getattr(widgets, "driver_combobox", None), "v_model", None)
        return {
            "alerts": app_state.alerts_gdf.value,
            "alert_id": app_state.actual_alert_id.value,
            "grid": getattr(widgets, "actual_alert_grid", None),
            "bb": getattr(widgets, "actual_bb", None),
            "status": getattr(getattr(widgets, "confirmation_select", None), "v_model", None),
            "drivers": list(drivers) if isinstance(drivers, (list, tuple)) else drivers,
        }

    return analysis.capture(_read)
