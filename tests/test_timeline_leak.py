"""A redraw of the timeline closes the widgets it replaces.

``render`` rebuilds the lane, the axis, the blocks, the chips, the titles and
the status line on every tick. A widget dropped from a container without
``close()`` keeps its comm open and stays in ipywidgets' registry for the rest
of the session, so a reviewer moving through alerts grew the kernel by a few
hundred widgets per tick. The live-widget count is the leak's own measure.
"""

from datetime import date, timedelta

from ipywidgets.widgets.widget import _instances

from gui.tile.analysis import timeline
from gui.tile.analysis.labels import Labels
from tests.test_timeline import LS, S2, _ctx, _fake_analysis, _fake_state, _fake_widgets, _walk


def _rows(n=150):
    """``n`` S2 and Landsat rows spread over the view, clouds 0..89 %."""
    start = date(2026, 3, 5)
    return [(S2 if i % 3 else LS, (start + timedelta(days=int(i * 220 / n))).isoformat(), float((i * 37) % 90))
            for i in range(n)]


def test_repeated_redraws_leave_the_live_widget_count_unchanged():
    labels = Labels()
    analysis = _fake_analysis()
    widgets = _fake_widgets()
    ctx = dict(_ctx())
    ctx["token"] = analysis.alert_token.value
    widgets.imagery_ctx.set(ctx)
    card = timeline.build_timeline(_fake_state(rows=_rows()), analysis, widgets, labels=labels)
    slider = next(w for w in _walk(card) if isinstance(w, timeline.v.Slider))

    def chip(label):
        return next(w for w in _walk(card)
                    if getattr(w, "tag", None) == "button" and "aria-pressed" in (w.attributes or {})
                    and label in w.children)

    def ticks():
        """Every way a reviewer redraws the strip, each one undone, so the
        screen ends where it started."""
        chip(timeline.SRC_LABEL[LS]).fire_event("click", None)  # off
        chip(timeline.SRC_LABEL[LS]).fire_event("click", None)  # on again
        slider.fire_event("change", 10)
        slider.fire_event("change", 40)
        stripe = next(w for w in _walk(card) if "click" in (getattr(w, "_events", None) or [])
                      and (w.attributes or {}).get("aria-current") == "true")
        stripe.fire_event("click", None)  # the picked image again: a redraw, no change
        labels.run()  # the language hook, same locale

    ticks()  # warm-up: anything built once on first use is built now
    count = len(_instances)
    for _ in range(3):
        ticks()
    # Exact, no tolerance: the ticks put the screen back where it was, and
    # nothing in the strip caches widgets across renders.
    assert len(_instances) - count == 0
    # and what is closed is only what left the tree: nothing on screen is
    shown = [*_walk(card), *_walk(widgets.map_title_before), *_walk(widgets.map_title_after)]
    assert [w for w in shown if w.comm is None] == []
