"""Analysis's per-session state (spec §10): Solara reactives, no traitlets.

One instance per session (``solara.use_memo`` in the shell), handed to the
raw-widget builders. Values that hold Earth Engine images or GeoDataFrames
compare by identity: their ``==`` is either undefined or element-wise, and a
reactive must notice "a new object" without comparing contents.
"""

from dataclasses import dataclass, field

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
    #: Planet monthly image lists (None until a source publishes them)
    before_planet: solara.Reactive = field(default_factory=_plain)
    after_planet: solara.Reactive = field(default_factory=_plain)
    #: CNN prediction rasters by model number: {1: path, 2: path}
    prediction_files: solara.Reactive = field(default_factory=lambda: solara.reactive({}))
    #: time.time() of the last review Save
    last_save_time: solara.Reactive = field(default_factory=lambda: solara.reactive(0.0))

    def reset_alert(self) -> None:
        """Forget what belonged to the previous alert (called on every alert change)."""
        self.defo_layer.set(None)
        self.before_image.set(None)
        self.after_image.set(None)
        self.before_info.set(None)
        self.after_info.set(None)
        self.prediction_files.set({})
