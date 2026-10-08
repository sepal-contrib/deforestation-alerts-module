"""The image timeline's two handles: a before and an after, on one rail above
the lane of image stripes (``gui/tile/analysis/timeline.py``).

Kept out of ``gui/tile/analysis`` because a template widget needs traitlets
for its synced traits, and that package holds its state in reactives only.
"""

from pathlib import Path

import ipyvuetify as v
import traitlets


#: the strip's own rules. Global, so every class is ``dah-`` prefixed. The
#: theme variables hang off ``.analysis-timeline`` (the card) and its dark
#: variant off the stage's ``analysis-stage--dark`` marker, the same switch the
#: stage's own background follows.
_HANDLES_CSS = """
.analysis-timeline { --dah-before: #5f5a52; --dah-before-ink: #ffffff; --dah-line: rgba(0, 0, 0, 0.25); }
.analysis-stage--dark .analysis-timeline { --dah-before: #e9e1cf; --dah-before-ink: #1b1a18; --dah-line: #44403a; }
.dah-root { position: relative; user-select: none; -webkit-user-select: none; }
.dah-base { position: absolute; left: 0; right: 0; bottom: 0; height: 1px; background: var(--dah-line); }
.dah-tick { position: absolute; bottom: 0; width: 1px; height: 6px; background: var(--dah-line); }
.dah-bracket { position: absolute; border: 1px solid rgba(191, 143, 45, 0.6); border-bottom: 0;
  pointer-events: none; }
.dah-gap { position: absolute; top: 0; transform: translateX(-50%); font-size: 10px; line-height: 12px;
  color: #bf8f2d; white-space: nowrap; font-variant-numeric: tabular-nums; pointer-events: none; }
.dah-gap.dah-warn { color: #d9ab4e; }
.dah-wash { position: absolute; background: rgba(191, 143, 45, 0.06); pointer-events: none; z-index: 1; }
.dah-glow { position: absolute; pointer-events: none; z-index: 3; border-radius: 2px;
  box-shadow: 0 0 0 2px rgba(227, 181, 83, 0.45), 0 0 10px 2px rgba(227, 181, 83, 0.35); }
.dah-h { position: absolute; bottom: 0; z-index: 4; cursor: grab; touch-action: none; outline: none;
  display: flex; flex-direction: column; }
.dah-h.dah-moving { cursor: grabbing; }
.dah-h.dah-unset { opacity: 0.45; }
.dah-before { transform: translateX(-100%); padding-left: 9px; align-items: flex-end; }
.dah-after { padding-right: 9px; align-items: flex-start; }
.dah-h svg { display: block; overflow: visible; }
.dah-before polygon { fill: var(--dah-before); }
.dah-after polygon { fill: #bf8f2d; }
.dah-h:focus-visible polygon { stroke: #d9ab4e; stroke-width: 1.5px; }
.dah-flag { position: absolute; bottom: calc(100% + 2px); padding: 3px 6px; border-radius: 3px;
  font-size: 10.5px; line-height: 1; white-space: nowrap; font-weight: 500; pointer-events: none; }
.dah-before .dah-flag { right: 0; background: var(--dah-before); color: var(--dah-before-ink);
  border-bottom-right-radius: 0; }
.dah-after .dah-flag { left: 0; background: #bf8f2d; color: #1b1a18; border-bottom-left-radius: 0; }
"""


class HandleStrip(v.VuetifyTemplate):
    """The two handles above the lane, the bracket between them and the glow
    on the row a moving handle would land on.

    A template rather than ``v.Html`` because the drag has to run in the
    browser: a kernel round trip per pointer move would make the handle lag
    the pointer. The browser holds only the gesture (where the handle is while
    it moves, and which row it would land on); everything it shows at rest is
    a trait this module sets from the reactives, and the one thing it sends
    back is a ``pick`` event naming a row id, which the caller checks
    (the timeline, against ``reachable``) before it assigns anything. So the traits are a
    rendering of the state, never state of their own.

    The arrow keys walk a pending row in the browser too and Enter sends it:
    each assignment costs an Earth Engine redraw of the pane.

    Every position is a percentage of the track, like the rest of the strip.
    """

    template = traitlets.Unicode(
        (Path(__file__).with_name("handle_strip.vue")).read_text(encoding="utf-8")).tag(sync=True)
    css = traitlets.Unicode(_HANDLES_CSS).tag(sync=True)
    #: every row a handle may land on, in track order:
    #: ``{id, x, top, h, flag}``, plus ``x0``/``w`` for a Planet month's bar
    candidates = traitlets.List([]).tag(sync=True)
    #: each side's selection, ``{id, x, flag}``, or ``None``
    before = traitlets.Dict(None, allow_none=True).tag(sync=True)
    after = traitlets.Dict(None, allow_none=True).tag(sync=True)
    #: the bracket's label, and whether it is the one-image warning
    gap = traitlets.Unicode("").tag(sync=True)
    gap_warn = traitlets.Bool(False).tag(sync=True)
    #: month boundaries, in %, as ticks on the rail
    ticks = traitlets.List([]).tag(sync=True)
    #: the handles' accessible names and the value text of an unset side
    labels = traitlets.Dict({}).tag(sync=True)
    #: the rail's height and the lane's below it, in px
    rail_h = traitlets.Int(28).tag(sync=True)
    lane_h = traitlets.Int(45).tag(sync=True)

    def __init__(self, on_pick, **kwargs):
        super().__init__(**kwargs)
        self._on_pick = on_pick

    def vue_pick(self, data):
        """A handle was let go of (or Enter pressed) on a row."""
        if not isinstance(data, dict):
            return
        side, rid = data.get("side"), data.get("id")
        if side in ("b", "a") and isinstance(rid, str):
            self._on_pick(side, rid)
