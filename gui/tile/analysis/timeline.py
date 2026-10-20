"""The shared image timeline: one date axis under both map panes.

A raw-widget builder, never a Solara component. MapApp renders the Analysis
step's content with ``v-if``, which destroys and rebuilds the DOM when the
reviewer closes and reopens the step; a Solara-rendered subtree comes back
blank, while a raw ipyvuetify tree is re-viewed from its widget model.

One lane over one axis, with two handles above it: the before handle and the
after handle, which can never pass each other. A handle is dragged (the drag
runs in the browser, see ``gui/widget/handle_strip.py``) or walked with the arrow keys, and a
click on a stripe moves whichever handle ``side_for_click`` names.

The geometry is addressed in **percentages of ``TRACK_PX``**, never in the
pixels ``scene_query.x_for`` returns, so the strip keeps its dates when the
browser window is not 1120px wide. ``scene_query`` owns every piece of the
arithmetic; nothing here recomputes a month's share of the track.

The ±6-month background fill has no widget of its own: a cancelled fetch and
its replacement overlap, so anything driven by "a fill is running" blinks, and
the strip instead fills in by itself when ``scene_index`` republishes. The one
place ``scene_index_pending`` is read -- the first-load line -- is backed by the
*absence* of an index as well, so that line does not depend on the flag being
accurate.
"""

import calendar
import logging
from datetime import date, datetime, timezone
from typing import Optional

import ipyvuetify as v
import pandas as pd

from deforestation_alerts.scripts import scene_query
from deforestation_alerts.scripts.scene_index import (
    SOURCE_LANDSAT,
    SOURCE_PLANET,
    SOURCE_S2,
    SceneTables,
)
from gui.i18n import format_date, t
from gui.tile.analysis.labels import Labels
from gui.tile.analysis.state import IMAGE_AREAS, imagery_changed
from gui.tile.analysis.widgets import replace_children
from gui.widget.handle_strip import HandleStrip

logger = logging.getLogger("SEPALUI.deforestation_alerts")

TRACK_PX = 1120.0
LANE_H = 45
#: the strip above the lane the two handles and the bracket between them ride on
RAIL_H = 28
AXIS_H = 27
VIEW_MONTHS = 2

#: (top offset, height) of each collection's band inside a lane. The bands do
#: not overlap, so two collections on one date never draw over each other and
#: a stripe's row says which collection it is before its colour does.
BAND = {SOURCE_S2: (0, 17), SOURCE_LANDSAT: (19, 14), SOURCE_PLANET: (35, 10)}
#: each collection's colour. CSS variables, like every colour on the strip and
#: in the pane titles that has to follow the theme: ``THEME_CSS`` defines the
#: light values on the stage and the dark ones on its ``analysis-stage--dark``
#: marker. The fallback is the dark value.
SRC_COLOR = {SOURCE_S2: "var(--dat-s2, #7fa8e0)", SOURCE_LANDSAT: "var(--dat-landsat, #c9a87c)",
             SOURCE_PLANET: "var(--dat-planet, #9bd98a)"}
#: display labels only. ``SOURCE_S2`` -- "Sentinel 2" with a space -- is what
#: the saved store, ``mosaics_helper`` and ``before_info[0]`` switch on; the
#: hyphenated form must never reach a data structure.
SRC_LABEL = {SOURCE_S2: "Sentinel-2", SOURCE_LANDSAT: "Landsat", SOURCE_PLANET: "Planet"}
SELECTED_COLOR = "#bf8f2d"  # the theme's primary_contrast
#: the before side's selection colour. A CSS variable because it is the one
#: colour on the strip that has to follow the theme: bone on the dark stage, a
#: dark warm grey on the light one (``gui/widget/handle_strip.py`` defines
#: both). The after side keeps the gold, which reads on either.
BEFORE_COLOR = "var(--dah-before, #e9e1cf)"
MUTED = "var(--dat-muted, #a8a39b)"
#: amber, for the two things the reviewer has to notice rather than read:
#: a refresh that failed, and one image standing in for both sides.
WARN = "var(--dat-warn, #d9ab4e)"
#: the light theme's values on ``.analysis-stage`` and the dark theme's on its
#: ``analysis-stage--dark`` marker -- the switch the stage background follows.
#: On the stage, not the timeline card: the pane titles above the maps read
#: ``MUTED``, ``WARN`` and ``SRC_COLOR`` too. The light values are darker so a
#: label keeps 4.5:1 and a stripe 3:1 on the white surface.
THEME_CSS = (
    ".analysis-stage { --dat-s2: #3a6db5; --dat-landsat: #9a7240; --dat-planet: #3f8f32; "
    "--dat-muted: #6b665e; --dat-warn: #9a6400; --dat-ink: #2a2724; --dat-chip: #f4f1ec; "
    "--dat-fold-a: #ebe6de; --dat-fold-b: #f5f2ed; --dat-edge: rgba(0, 0, 0, 0.25); } "
    ".analysis-stage.analysis-stage--dark { --dat-s2: #7fa8e0; --dat-landsat: #c9a87c; "
    "--dat-planet: #9bd98a; --dat-muted: #a8a39b; --dat-warn: #d9ab4e; --dat-ink: #f0ece6; "
    "--dat-chip: #24221f; --dat-fold-a: #24221f; --dat-fold-b: #1e1c1a; --dat-edge: #44403a; } "
)
HIT_PX = 11
#: a collapsed month at least this wide carries its full label ("JUN 2026",
#: the count, "images"); a narrower one -- a long alert's, shrunk by
#: ``scene_query.FOLD_SHARE`` -- shows less and leaves its year to the axis
FULL_BLOCK_PX = 52.0
STRIPE_PX, SELECTED_STRIPE_PX = 2.5, 4.0
HIDDEN_SCALE, HIDDEN_OPACITY = 0.45, 0.3
#: the order of two collections on one date: the one a drag or an arrow lands
#: on first. The same order as the GIF's (``scene_query._RANK``).
_RANK = {SOURCE_S2: 0, SOURCE_LANDSAT: 1, SOURCE_PLANET: 2}

_LANE_STYLE = f"position: relative; height: {LANE_H}px"
_LEGEND_ID = "analysis-timeline-legend"
_LEGEND_HIDDEN = "display: none"
_LEGEND_SHOWN = f"padding: 0 8px 8px; color: {MUTED}; font-size: 12px; max-width: 70ch"
_MESSAGE_STYLE = f"padding: 14px 0; color: {MUTED}; font-size: 12.5px"
_TRACK_STYLE = "position: relative; flex: 1 1 auto; min-width: 0"


def _pct(px: float) -> float:
    """``px`` as a share of the nominal track, in percent."""
    return px / TRACK_PX * 100


def _as_date(value) -> date:
    """A selection frame's ``date`` cell as a ``datetime.date``.

    The column is a pandas Timestamp column whose unit differs between a
    freshly parsed frame and one read back from parquet, so nothing here may
    depend on the dtype -- only on it being a Timestamp or already a date.
    """
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    return value


def _month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


# ------------------------------------------------------------------ the view


def view_window(ctx, project, extra) -> Optional[tuple[date, date]]:
    """``(start, end)`` of the axis: the alert window padded by two months,
    plus whatever the side buttons have added.

    ``None`` when there is no alert on screen: the strip has nothing to be an
    axis *of*, and the builder says so rather than drawing an empty track.

    An alert whose date pixels were all nodata has no dates of its own
    (``vectorize`` writes NaN, which ``convert_julian_to_date`` renders
    "N/A"); the active period's own window stands in, so the reviewer still
    gets a timeline instead of an error.
    """
    if not ctx:
        return None
    a0, a1 = ctx.get("date_min"), ctx.get("date_max")
    if a0 is None or a1 is None:
        a0, a1 = alert_window(ctx, project)
        if a0 is None or a1 is None:
            return None
    back, fwd = extra
    return (scene_query.shift_months(a0, -(VIEW_MONTHS + back)),
            scene_query.shift_months(a1, VIEW_MONTHS + fwd))


def today() -> date:
    """The local date; a module function so a test can pin it."""
    return date.today()


def can_widen_forward(window, now: date) -> bool:
    """Whether "+1 month" on the right would add a month that has begun.

    The month after the view's last one must not lie after ``now``'s: a month
    still to come holds no image. ``True`` without a view, where there is
    nothing to judge (the width is kept for the next alert).
    """
    if window is None:
        return True
    following = scene_query.shift_months(window[1], 1)
    return (following.year, following.month) <= (now.year, now.month)


def alert_window(ctx, project) -> tuple[Optional[date], Optional[date]]:
    """The gold band's own dates, by the same fallback as ``view_window``."""
    if ctx and ctx.get("date_min") and ctx.get("date_max"):
        return (ctx["date_min"], ctx["date_max"])
    if not ctx:
        return (None, None)
    period = project.periods.get(project.active_period) if (project and project.active_period) else None
    return (period.start, period.end) if period is not None else (None, None)


def extra_to_include(ctx, project, extra, days) -> tuple[int, int]:
    """``extra`` widened by whole months until the view holds every one of
    ``days``: a saved pick outside the default view is restored, not lost
    (spec D2). Never narrower than ``extra``. Unchanged without a view."""
    back, fwd = extra
    window = view_window(ctx, project, (back, fwd))
    if window is None or not days:
        return (back, fwd)
    first, last = min(days), max(days)
    while view_window(ctx, project, (back, fwd))[0] > first and back < 240:
        back += 1
    while view_window(ctx, project, (back, fwd))[1] < last and fwd < 240:
        fwd += 1
    return (back, fwd)


# ------------------------------------------------------- the string contracts


def info_tuple(row) -> list:
    """One selection as ``before_info`` / ``after_info``.

    The five elements are a contract shared with ``paths.alert_image_name``,
    ``export.py`` (which writes elements 1 and 3 into the saved store as
    provenance) and ``edit_tools.py``:
    ``[source, millis, cloud, "<source> <YYYY-MM-DD> ", "_DDMMYY"]``.

    ``millis`` is the *acquisition* date at UTC midnight, not the processing
    timestamp the old selector stored. Nothing filters on it any more -- the
    rebuild uses the granule keys -- so this is a provenance change only, and
    the dates in a store saved from here are finally the dates the images were
    taken.
    """
    d = _as_date(row["date"])
    millis = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)
    cloud = row["cloud"]
    cloud_str = "N/A" if cloud is None or pd.isna(cloud) else f"{float(cloud):.2f}"
    return [str(row["source"]), millis, cloud_str, f"{row['source']} {d.isoformat()} ", d.strftime("_%d%m%y")]


def _row_of(sel, rid):
    if rid is None or sel is None or len(sel) == 0:
        return None
    hit = sel[sel["id"] == rid]
    return None if len(hit) == 0 else hit.iloc[0]


def readout(sel, rid) -> str:
    """The line under the track: what is selected, in words.

    An id the current window no longer holds reads as "not set", which is what
    it is for this view: a selection saved from a wider window, or one whose
    collection the reviewer has just switched off.
    """
    row = _row_of(sel, rid)
    if row is None:
        return t("analysis.timeline.unset")
    d = _as_date(row["date"])
    cloud = row["cloud"]
    tail = "" if cloud is None or pd.isna(cloud) else f" · {float(cloud):.0f}%"
    return f"{SRC_LABEL[row['source']]} {d.isoformat()}{tail}"


def days_apart(before_id, after_id) -> Optional[int]:
    """The gap the chosen pair brackets, or ``None`` until both are set."""
    if not before_id or not after_id:
        return None
    b = datetime.strptime(before_id[1:], "%Y%m%d").date()
    a = datetime.strptime(after_id[1:], "%Y%m%d").date()
    return (a - b).days


def pair_note(before_id, after_id) -> Optional[str]:
    """What the pair says about itself, or ``None`` until both sides are set.

    ``scene_query.default_pick`` never pairs an image with itself, but a pair
    saved before it learned not to (a single-day alert window with a scene on
    that day) can still reopen as one id on both sides. "0 days apart" would
    read as two images that happen to agree, so one image on both sides is
    named as that instead.
    """
    gap = days_apart(before_id, after_id)
    if gap is None:
        return None
    if before_id == after_id:
        return t("analysis.timeline.same_image")
    return t("analysis.timeline.days_apart", days=gap)


# --------------------------------------------------------------- the geometry


def _only_sources_we_can_draw(tables: SceneTables) -> SceneTables:
    """``tables`` without rows whose ``source`` this module has no band for.

    Every drawing helper indexes ``BAND``, ``SRC_COLOR`` and ``SRC_LABEL`` by
    the source string, and ``scene_query.row_id`` indexes ``SOURCE_CODE`` --
    so one row naming a fourth collection used to raise a ``KeyError`` out of
    the middle of ``render``, from where it travelled back through
    ``reactive.set`` into the loader and was reported as a failed index
    refresh (T10 Minor 7, review m1). The index is *data*: it is read from
    disk and from Earth Engine, so a release that adds a collection and a
    session that then opens the same project on an older build make this
    reachable without any bug in this file.

    A filter rather than a startup assertion over the key sets, because the
    trigger is a row and not a mismatch between the three dicts: an assertion
    would hold on a build that is about to raise anyway, and raising at import
    would cost the whole app for one unknown row. Dropping the row costs that
    collection's stripes and says so in the server log.
    """
    known = tables.scenes["source"].isin(BAND)
    if known.all():
        return tables
    logger.warning("timeline: no band for %s, dropping those rows",
                   sorted(set(tables.scenes.loc[~known, "source"])))
    return SceneTables(scenes=tables.scenes[known], tiles=tables.tiles)


def stripe_hit_style(segments, d: date, source: str) -> str:
    """The clickable box: ``HIT_PX`` wide, centred on the date, band-tall.

    The hit area is wider than the stripe because a 2.5px target is not
    clickable; at the widest view Sentinel-2's stripes still sit ~13px apart,
    so an 11px box never covers its neighbour's date.
    """
    top, height = BAND[source]
    x = scene_query.x_for(segments, d)
    return (f"position: absolute; left: calc({_pct(x):.3f}% - {HIT_PX / 2:g}px); "
            f"width: {HIT_PX}px; top: {top}px; height: {height}px")


def month_bar_style(segments, d: date) -> str:
    """A Planet row's hit area: the **whole month** it composites, not an instant.

    A NICFI basemap is a monthly mosaic, so a 2.5px stripe at one instant would
    claim a precision it does not have (spec §4.1). The bar spans its month's
    own segment, which on a collapsed month is the 66px block and on an
    expanded one its proportional share -- so it lines up with the axis either
    way, and it is the only row type whose width carries meaning.
    """
    if segments.get((d.year, d.month)) is None:
        return "display: none"
    left = _pct(scene_query.x_for(segments, date(d.year, d.month, 1)))
    right = _pct(scene_query.x_for(segments, _month_end(d), end=True))
    top, height = BAND[SOURCE_PLANET]
    return (f"position: absolute; left: {left:.3f}%; width: {max(right - left, 0.1):.3f}%; "
            f"top: {top}px; height: {height}px")


def stripe_bar_style(row, *, selected: bool, side: str = "a") -> str:
    """The visible stripe inside the hit area. A selected one takes its side's
    colour: ``BEFORE_COLOR`` for the before image, the gold for the after."""
    _, height = BAND[row["source"]]
    hidden = bool(row["hidden"]) and not selected
    h = height * HIDDEN_SCALE if hidden else height
    width = SELECTED_STRIPE_PX if selected else STRIPE_PX
    picked = BEFORE_COLOR if side == "b" else SELECTED_COLOR
    color = picked if selected else SRC_COLOR[row["source"]]
    opacity = f"; opacity: {HIDDEN_OPACITY}" if hidden else ""
    return (f"position: absolute; left: 50%; transform: translateX(-50%); bottom: 0; "
            f"width: {width:g}px; height: {h:g}px; border-radius: 1px; background: {color}{opacity}")


def stripe_cursor(row) -> str:
    """A row the cloud limit hides is not clickable: raising the limit is the
    way to reach it, and a click that silently did nothing would read as a bug."""
    return "default" if bool(row["hidden"]) else "pointer"


# ------------------------------------------------------------ the handles


def handle_x(segments, rid) -> Optional[float]:
    """The track pixel a handle on ``rid`` points at, or ``None``.

    A stripe's own date; for a Planet month, the middle of its bar, which is
    what the eye takes a month-wide bar's position to be. ``None`` for no row
    and for a row whose month is not laid out. Read from the id, not from a
    frame row, so a selection whose collection is switched off still has a
    place on the track for the other handle to stop at.
    """
    if not rid:
        return None
    source, d = scene_query.parse_row_id(rid)
    if segments.get((d.year, d.month)) is None:
        return None
    if source == SOURCE_PLANET:
        left = scene_query.x_for(segments, date(d.year, d.month, 1))
        right = scene_query.x_for(segments, _month_end(d), end=True)
        return (left + right) / 2
    return scene_query.x_for(segments, d)


def _navigable(sel, segments) -> list:
    """The row ids a handle can land on, in track order.

    Hidden rows and rows inside a collapsed month are not drawn, so a handle
    on one would point at nothing. A row whose month is not in ``segments`` at
    all is not drawn either. On one date Sentinel-2 comes first: the order is
    the same-day preference a drag resolves its ties by.

    Sorted here rather than trusted to arrive sorted: ``scenes_for`` does sort
    today, but does not promise to, and by ``source`` as a string, which puts
    Landsat first.
    """
    if sel is None or len(sel) == 0:
        return []
    ids = []
    for _, row in sel.iterrows():
        if bool(row["hidden"]):
            continue
        d = _as_date(row["date"])
        seg = segments.get((d.year, d.month))
        if seg is None or seg.collapsed:
            continue
        ids.append((d, _RANK.get(row["source"], len(_RANK)), str(row["id"])))
    return [rid for _, _, rid in sorted(ids)]


def reachable(sel, segments, side: str, *, before_id, after_id) -> list:
    """The rows ``side``'s handle may land on: ``_navigable`` less every row
    on or past the other handle, so the two can never cross or meet.

    Compared by track position, not by date, because that is what the
    reviewer sees: a Planet month's handle sits mid-month, and an image on
    the 3rd is left of it however the dates compare.
    """
    other = handle_x(segments, after_id if side == "b" else before_id)
    ids = _navigable(sel, segments)
    if other is None:
        return ids
    if side == "b":
        return [rid for rid in ids if handle_x(segments, rid) < other]
    return [rid for rid in ids if handle_x(segments, rid) > other]


def _same_day(rid, other) -> bool:
    """Two scenes of one date. A Planet month has no date of its own."""
    if not rid or not other:
        return False
    (src, d), (other_src, other_d) = scene_query.parse_row_id(rid), scene_query.parse_row_id(other)
    return SOURCE_PLANET not in (src, other_src) and d == other_d


def side_for_click(segments, rid, before_id, after_id, a0) -> Optional[str]:
    """Which handle a click on ``rid``'s stripe moves: ``"b"``, ``"a"`` or ``None``.

    - the stripe already selected on a side: nothing;
    - the other collection of a selected day: that side, switching source;
    - left of the before handle: before; right of the after handle: after;
    - between the two: the alert start decides -- a scene from before it is a
      "before" look, one on or after it an "after" look. Without an alert
      start the nearer handle moves.

    A side that is not set does not bound anything, so its handle is reached
    through the alert-start rule.
    """
    if rid in (before_id, after_id):
        return None
    if _same_day(rid, before_id):
        return "b"
    if _same_day(rid, after_id):
        return "a"
    x = handle_x(segments, rid)
    bx, ax = handle_x(segments, before_id), handle_x(segments, after_id)
    if x is not None and bx is not None and x < bx:
        return "b"
    if x is not None and ax is not None and x > ax:
        return "a"
    if a0 is not None:
        return "b" if scene_query.parse_row_id(rid)[1] < a0 else "a"
    if bx is None or ax is None or x is None:
        return "b" if ax is not None else "a"
    return "b" if abs(x - bx) <= abs(x - ax) else "a"


def same_day_alternative(sel, rid) -> Optional[str]:
    """The other collection's image of ``rid``'s day, or ``None``: what the
    map title offers to switch to. Never a hidden row (the cloud limit hides
    it for a reason) and never a Planet month (it has no day)."""
    if not rid or sel is None or len(sel) == 0:
        return None
    for _, row in sel.sort_values("source", key=lambda s: s.map(_RANK)).iterrows():
        other = str(row["id"])
        if other != rid and not bool(row["hidden"]) and _same_day(rid, other):
            return other
    return None


# ------------------------------------------------------------ widget helpers


def _walk(widget):
    """Every widget in ``widget``'s subtree, itself first. Strings are skipped."""
    yield widget
    for child in (getattr(widget, "children", None) or ()):
        if not isinstance(child, str):
            yield from _walk(child)


def _button(style, attrs, handler, child_style, cursor="pointer"):
    """One absolutely-positioned button wrapping one coloured span.

    A button with no handler is marked ``disabled``, not merely
    ``aria-disabled``: the native attribute is what takes it out of the tab
    order, so a row the cloud limit hides or a month that is folded away is
    skipped by keyboard navigation without a tabindex of its own.
    """
    attributes = {"type": "button"}
    attributes.update(attrs)
    if handler is None:
        attributes["disabled"] = "disabled"
        attributes["aria-disabled"] = "true"
    btn = v.Html(tag="button", attributes=attributes,
                 style_=f"{style}; background: none; border: 0; padding: 0; cursor: {cursor}",
                 children=[v.Html(tag="span", style_=child_style)])
    if handler is not None:
        btn.on_event("click", handler)
    return btn


# ---------------------------------------------------------------- the builder


def build_timeline(app_state, analysis, widgets, *, labels=None):
    """The strip, built once per session and re-rendered from the reactives.

    Returns a ``v.Card`` holding the toolbar and the track. What is selected
    is named in the two map titles (``widgets.map_title_before`` /
    ``map_title_after``, when the widgets have them), next to the image it
    describes. Every piece of state it reads is a reactive it subscribes to,
    so the only thing an alert change has to do is publish
    ``widgets.imagery_ctx``.

    ``labels`` is the screen's ``labels.Labels`` (a fresh one when none is
    given): the toolbar's text and ``render`` are registered with it, so a
    language switch redraws the strip, the axis months and both view titles.
    """
    import solara

    labels = labels if labels is not None else Labels()
    max_cloud = solara.reactive(40.0)
    sources = solara.reactive(frozenset(SRC_COLOR))
    collapsed = solara.reactive(frozenset())
    extra = solara.reactive((0, 0))
    before_id = solara.reactive(None)
    after_id = solara.reactive(None)
    #: the half-years (``scene_query.half_of``) the reviewer has unfolded into
    #: months; reset with the collapse set
    split = solara.reactive(frozenset())
    #: the alert the collapse set and the selection belong to, so a new alert
    #: gets its own auto-collapse instead of inheriting the last one's
    seen_alert = solara.reactive(None)
    #: the alert ``scene_window_request`` was last published for. Separate
    #: from ``seen_alert`` on purpose: the auto-pick waits for rows to pick
    #: from, while the window request is what *gets* the rows, so it has to be
    #: made for an alert whose months are not in the index yet.
    asked_alert = solara.reactive(None)
    #: each side's saved pick still waiting for its row: ``{"b": id, "a": id}``
    #: (spec D4). A side leaves it when restored, when the index has answered
    #: without it, or when the reviewer picks that side by hand.
    restoring = solara.reactive({})
    #: the sides whose saved pick was not found, so the automatic pick stood in
    fell_back = solara.reactive(frozenset())

    lane = v.Html(tag="div", style_=_LANE_STYLE)
    lane_axis = v.Html(tag="div", style_=f"position: relative; height: {AXIS_H}px")
    blocks = v.Html(tag="div", style_=f"position: absolute; left: 0; right: 0; top: {RAIL_H}px; bottom: 0; "
                                      "pointer-events: none")
    window_band = v.Html(tag="div", style_="display: none")
    strip = HandleStrip(on_pick=lambda side, rid: _on_drop(side, rid), rail_h=RAIL_H, lane_h=LANE_H)
    track = v.Html(tag="div", style_=_TRACK_STYLE,
                   children=[strip, window_band, lane, lane_axis, blocks])
    message = v.Html(tag="div", style_=_MESSAGE_STYLE, children=[])
    chips = v.Html(tag="div", style_="display: flex; flex: 0 0 auto; align-items: center; gap: 8px", children=[])
    status = v.Html(tag="div", style_=f"font-size: 11.5px; color: {MUTED}", children=[])

    def _in_window():
        """``(rows, window)`` for everything the view window holds, or
        ``(None, None)``.

        Every collection, including the ones the reviewer has switched off.
        This is what the chips count: spec §4.6 wants the in-view count, which
        is the number that tells the reviewer whether re-enabling a collection
        is worth it -- a chip reading 0 because its own toggle is off says
        nothing. The cloud limit is applied here, as the ``hidden`` flag
        ``source_counts`` splits on, so "+2 over the cloud limit" survives the
        split too.
        """
        tables = app_state.scene_index.value
        ctx = widgets.imagery_ctx.value
        window = view_window(ctx, app_state.project.value, extra.value)
        if tables is None or ctx is None or window is None or ctx.get("bbox") is None:
            return (None, None)
        tables = _only_sources_we_can_draw(tables)
        return (scene_query.scenes_for(tables, ctx["bbox"], window[0], window[1],
                                       max_cloud=max_cloud.value), window)

    def _enabled(rows):
        """``rows`` less the collections that are toggled off: what is drawn.

        The lanes, the month counts, the blocks, the readout and the auto-pick
        all take this frame -- a hidden collection must not reappear as a
        stripe or inflate a collapsed month's count.
        """
        return rows[rows["source"].isin(sources.value)].reset_index(drop=True)

    def _selection():
        """``(rows, window)`` for what is on screen, or ``(None, None)``."""
        rows, window = _in_window()
        if rows is None or window is None:
            return (None, None)
        return (_enabled(rows), window)

    def _keys_for(row, sel):
        """Every granule key the chosen (source, date) mosaics for this alert."""
        tables = app_state.scene_index.value
        ctx = widgets.imagery_ctx.value
        if tables is None or ctx is None or ctx.get("bbox") is None:
            return []
        # The image area (``area_bbox``, the alert box plus the chosen margin)
        # when there is one: the mosaic must fill it, not only the alert box.
        return scene_query.granule_keys(tables, ctx.get("area_bbox") or ctx["bbox"], row["source"], row["date"])

    def _rekey():
        """Recompute both picks' granule keys for the current image area
        (``imagery.set_image_area`` calls it through ``widgets``)."""
        sel, _window = _selection()
        if sel is None:
            return
        for lane, rid in (("b", before_id.value), ("a", after_id.value)):
            row = _row_of(sel, rid) if rid else None
            if row is not None:
                (analysis.before_keys if lane == "b" else analysis.after_keys).set(sorted(_keys_for(row, sel)))

    widgets.timeline_rekey = _rekey

    def _assign(lane, rid, sel, *, restore=False):
        """Publish ``rid`` as ``lane``'s image. A hidden row is refused unless
        it is a saved pick being restored: the reviewer chose it when the
        cloud limit did not hide it (spec D2)."""
        row = _row_of(sel, rid)
        if row is None or (bool(row["hidden"]) and not restore):
            # Reachable: the index can republish between the render that drew
            # the stripe and the click on it. Nothing is said to the reviewer
            # because the strip they are looking at has already been redrawn
            # without that row -- there is no stale selection to explain.
            logger.info("timeline: %s is no longer selectable, ignoring the pick", rid)
            return
        (before_id if lane == "b" else after_id).set(rid)
        # The granule keys go first: ``image_slider_map_callback`` reads them
        # when the info publish wakes it, so a half-set pair is never rebuilt.
        (analysis.before_keys if lane == "b" else analysis.after_keys).set(sorted(_keys_for(row, sel)))
        (analysis.before_info if lane == "b" else analysis.after_info).set(info_tuple(row))

    def _picked(lane, rid, sel):
        """Assign a pick the REVIEWER made, and record what it costs the review.

        The auto-pick in ``render`` calls ``_assign`` directly: it is the
        alert's default pair, not a change, and must not open the alert with
        "Unsaved changes" or drop a prediction nobody replaced. Keyed on the
        published id actually changing, so a click on the stripe that is
        already selected — or one ``_assign`` refuses because the row is gone
        — costs nothing.
        """
        # A hand pick outranks a saved one still waiting, and answers the
        # "saved image not available" note for that side (Review Focus #1).
        restoring.set({k: rid_ for k, rid_ in restoring.value.items() if k != lane})
        fell_back.set(fell_back.value - {lane})
        chosen = before_id if lane == "b" else after_id
        was = chosen.value
        _assign(lane, rid, sel)
        if chosen.value != was:
            imagery_changed(analysis)

    def _segments_now():
        """``(sel, segments)`` as the lane is currently drawn, or ``(None, None)``."""
        sel, window = _selection()
        if sel is None or window is None:
            return (None, None)
        return (sel, scene_query.month_segments(window[0], window[1], collapsed.value, TRACK_PX))

    def _pick_if_reachable(lane_, rid):
        """Assign ``rid`` to ``lane_`` if its handle may land there.

        The one gate for every hand pick -- drop, click and the title's swap
        -- so the browser's idea of the other handle's position, which can be
        a round trip stale, is never what keeps the two from crossing."""
        sel, segs = _segments_now()
        if sel is None:
            return
        if rid not in reachable(sel, segs, lane_, before_id=before_id.value, after_id=after_id.value):
            logger.info("timeline: %s is not reachable for the %s handle, ignoring the pick", rid, lane_)
            render()  # put the browser's handle back where the state says
            return
        _picked(lane_, rid, sel)
        render()

    def _on_drop(lane_, rid):
        _pick_if_reachable(lane_, rid)

    def _on_stripe(rid):
        a0, _ = alert_window(widgets.imagery_ctx.value, app_state.project.value)
        sel, segs = _segments_now()
        if sel is None:
            return
        side = side_for_click(segs, rid, before_id.value, after_id.value, a0)
        if side is not None:
            _pick_if_reachable(side, rid)

    def _render_lane(sel, segs):
        before, after = before_id.value, after_id.value
        a0, _ = alert_window(widgets.imagery_ctx.value, app_state.project.value)
        kids = []
        for _, row in sel.iterrows():
            d = _as_date(row["date"])
            seg = segs.get((d.year, d.month))
            if seg is None or seg.collapsed:
                continue  # its count is on the block instead
            rid = row["id"]
            hidden = bool(row["hidden"])
            head = f"{SRC_LABEL[row['source']]}, {d.isoformat()}"
            # ``selected`` and not ``not hidden and selected``: a selection the
            # reviewer has just pushed over the cloud limit keeps its
            # full-height stripe, so lowering the limit never makes their own
            # pick disappear into the faded rows.
            side = "b" if rid == before else ("a" if rid == after else None)
            if side is not None:
                label = f"{head}, {t('analysis.timeline.before' if side == 'b' else 'analysis.timeline.after')}"
            else:
                # A hidden row is not clickable, so its label must not offer
                # the action: the cloud limit is what stands between it and a
                # pick. A clickable one says which image it would set.
                goes = None if hidden else side_for_click(segs, rid, before, after, a0)
                verb = t("analysis.timeline.set_before") if goes == "b" else t("analysis.timeline.set_after")
                label = head if goes is None else f"{head}, {verb}"
            # ``tabindex="-1"``: the two handles are the keyboard's way along
            # the track, so Tab is two presses, not one per image.
            attrs = {"aria-label": label, "tabindex": "-1"}
            if side is not None:
                attrs["aria-current"] = "true"
            hit = (month_bar_style(segs, d) if row["source"] == SOURCE_PLANET
                   else stripe_hit_style(segs, d, row["source"]))
            kids.append(_button(
                hit, attrs,
                None if hidden else (lambda _w, _e, _d, rid=rid: _on_stripe(rid)),
                stripe_bar_style(row, selected=side is not None, side=side or "a"), stripe_cursor(row)))
        replace_children(lane, kids)

    def _flag(rid) -> str:
        """What a handle's flag says about ``rid``: the day and the collection."""
        source, d = scene_query.parse_row_id(rid)
        when = format_date(d, "MMM y") if source == SOURCE_PLANET else format_date(d, "d MMM")
        return f"{when} · {SRC_LABEL[source]}"

    def _render_strip(sel, segs):
        cands = []
        for rid in _navigable(sel, segs):
            source, d = scene_query.parse_row_id(rid)
            top, height = BAND[source]
            cand = {"id": rid, "x": round(_pct(handle_x(segs, rid)), 3), "top": top, "h": height,
                    "flag": _flag(rid)}
            if source == SOURCE_PLANET:
                x0 = scene_query.x_for(segs, date(d.year, d.month, 1))
                x1 = scene_query.x_for(segs, _month_end(d), end=True)
                cand.update(x0=round(_pct(x0), 3), w=round(_pct(x1 - x0), 3))
            cands.append(cand)

        def _side(rid):
            x = handle_x(segs, rid)
            return None if x is None else {"id": rid, "x": round(_pct(x), 3), "flag": _flag(rid)}

        strip.candidates = cands
        strip.before = _side(before_id.value)
        strip.after = _side(after_id.value)
        # Only when both sides still resolve in this window: a gap between
        # images one of which the strip cannot show would contradict it.
        both = _row_of(sel, before_id.value) is not None and _row_of(sel, after_id.value) is not None
        strip.gap = (pair_note(before_id.value, after_id.value) or "") if both else ""
        strip.gap_warn = bool(before_id.value) and before_id.value == after_id.value
        strip.ticks = [round(_pct(seg.x), 3) for _, seg in sorted(segs.items())][1:]
        strip.labels = {"b": t("analysis.timeline.lane_before"), "a": t("analysis.timeline.lane_after"),
                        "unset": t("analysis.timeline.unset")}

    def _expand(key):
        collapsed.set(collapsed.value - {key})
        render()

    def _collapse(key):
        collapsed.set(collapsed.value | {key})
        render()

    def _split(half):
        split.set(split.value | {half})
        render()

    def _block(x, w, aria, children, bottom):
        return v.Html(
            tag="button",
            attributes={"type": "button", "aria-label": aria},
            style_=(f"pointer-events: auto; position: absolute; left: {_pct(x):.3f}%; "
                    f"width: {_pct(w):.3f}%; top: 0; bottom: {bottom}; cursor: pointer; overflow: hidden; "
                    "background: repeating-linear-gradient(135deg, var(--dat-fold-a), var(--dat-fold-a) 4px, "
                    "var(--dat-fold-b) 4px, var(--dat-fold-b) 9px); "
                    "border-left: 1px dashed var(--dat-edge); "
                    "border-right: 1px dashed var(--dat-edge); display: flex; flex-direction: column; "
                    f"align-items: center; justify-content: center; gap: 3px; color: {MUTED}"),
            children=children)

    def _block_text(label, n, *, images=True):
        kids = [v.Html(tag="span", style_="font-size: 9px; letter-spacing: 0.1em; white-space: nowrap; "
                                          "text-transform: uppercase", children=[label]),
                v.Html(tag="span", style_="font-size: 16px; font-weight: 600; color: var(--dat-ink); "
                                          "line-height: 1", children=[str(n)])]
        if images:
            kids.append(v.Html(tag="span", style_="font-size: 8px", children=[t("analysis.timeline.images")]))
        return kids

    def _render_blocks(segs, sel):
        counts = scene_query.month_counts(sel)
        folded = [key for key, seg in sorted(segs.items()) if seg.collapsed]
        # A long alert's months are narrower than a full block (``FOLD_SHARE``):
        # they then sit on the lane only and leave the axis below them to the
        # years, which their own labels no longer carry.
        narrow = bool(folded) and segs[folded[0]].w < FULL_BLOCK_PX
        bottom = f"{AXIS_H}px" if narrow else "0"
        kids = []
        for group in scene_query.fold_groups(segs, split.value):
            first, last = segs[group[0]], segs[group[-1]]
            n = sum(counts.get(key, 0) for key in group)
            if len(group) > 1:
                start, end = date(first.year, first.month, 1), date(last.year, last.month, 1)
                label = f"{format_date(start, 'MMM')} – {format_date(end, 'MMM y')}"
                btn = _block(first.x, last.x + last.w - first.x,
                             t("analysis.timeline.collapsed_group", months=label, count=n),
                             _block_text(label, n), bottom)
                btn.on_event("click", lambda w, e, d, half=scene_query.half_of(group[0]): _split(half))
                kids.append(btn)
                continue
            # the locale's own month name: a CLDR pattern, not strftime, which
            # would put "JUN 2026" inside a French aria-label. The visible
            # label is uppercased by CSS, so the text a screen reader reads
            # and the text interpolated into ``collapsed_month`` stay natural.
            key, month = group[0], date(first.year, first.month, 1)
            label = format_date(month, "MMM y")
            if first.w >= FULL_BLOCK_PX:
                text = _block_text(label, n)
            elif first.w >= scene_query.FOLD_GROUP_BELOW_PX:
                text = _block_text(format_date(month, "MMM"), n, images=False)
            else:
                text = _block_text(format_date(month, "MMMMM"), n, images=False)  # the narrow form: "J"
            btn = _block(first.x, first.w, t("analysis.timeline.collapsed_month", month=label, count=n),
                         text, bottom)
            btn.on_event("click", lambda w, e, d, key=key: _expand(key))
            kids.append(btn)
        if narrow:
            for key in folded:
                seg = segs[key]
                prev = segs.get((key[0], key[1] - 1))  # None for a January: a new year
                if prev is not None and prev.collapsed:
                    continue  # not the start of this year's folded stretch
                kids.append(v.Html(
                    tag="span", class_="defo-tl-year",
                    style_=(f"position: absolute; left: {_pct(seg.x):.3f}%; bottom: 0; height: {AXIS_H}px; "
                            "padding: 7px 0 0 4px; box-sizing: border-box; "
                            "border-left: 1px solid var(--dat-edge); font-size: 10.5px; font-weight: 600; "
                            "color: var(--dat-ink); white-space: nowrap"),
                    children=[str(seg.year)]))
        replace_children(blocks, [v.Html(tag="div", style_="position: relative; height: 100%; "
                                                           "pointer-events: none", children=kids)])

    def _render_band(segs, a0, a1):
        if a0 is None or a1 is None:
            window_band.style_ = "display: none"
            return
        x0 = scene_query.x_for(segs, a0)
        x1 = scene_query.x_for(segs, a1, end=True)
        window_band.style_ = (f"position: absolute; left: {_pct(x0):.3f}%; "
                              f"width: {_pct(x1 - x0):.3f}%; "
                              f"top: {RAIL_H}px; bottom: 0; background: {SELECTED_COLOR}0d; "
                              f"border-left: 1px dashed {SELECTED_COLOR}aa; "
                              f"border-right: 1px dashed {SELECTED_COLOR}aa; pointer-events: none")

    def _render_axis(segs):
        kids = [v.Html(tag="span", style_="position: absolute; left: 0; right: 0; top: 0; height: 1px; "
                                          "background: var(--dat-edge)")]
        for key, seg in sorted(segs.items()):
            if seg.collapsed:
                continue
            label = format_date(date(seg.year, seg.month, 1), "MMM")
            btn = v.Html(
                tag="button",
                attributes={"type": "button",
                            "aria-label": t("analysis.timeline.collapse_month", month=label)},
                style_=(f"position: absolute; left: calc({_pct(seg.x + seg.w / 2):.3f}% - 26px); "
                        "top: 7px; width: 52px; height: 18px; background: none; border: 0; "
                        f"color: {MUTED}; font-size: 9.5px; letter-spacing: 0.1em; "
                        "text-transform: uppercase; cursor: pointer"),
                children=[label])
            btn.on_event("click", lambda w, e, d, key=key: _collapse(key))
            kids.append(btn)
        replace_children(lane_axis, kids)

    def _publish_filter():
        target = getattr(widgets, "timeline_filter", None)
        if target is not None:
            target.set((frozenset(sources.value), float(max_cloud.value)))

    def _toggle(name):
        sources.set(sources.value ^ {name})
        _publish_filter()
        render()

    def _render_chips(rows):
        # ``rows`` is the whole view window, not ``_enabled(rows)``: a chip
        # counts what its collection holds here whether or not it is switched
        # on, so a disabled chip tells the reviewer what they are hiding.
        counts = scene_query.source_counts(rows) if rows is not None else {}
        planet = getattr(app_state.scene_manifest.value, "planet", None)
        kids = []
        for name in (SOURCE_PLANET, SOURCE_S2, SOURCE_LANDSAT):
            on = name in sources.value
            shown, hidden = counts.get(name, (0, 0))
            label = f"{SRC_LABEL[name]} {shown}" + (f" +{hidden}" if hidden else "")
            # NICFI is licence-gated. A toggle that does nothing because the
            # account has no access must say so rather than read as a
            # collection with no images this month (spec §5.4), which is why
            # the loader records ``planet.access`` instead of only logging it.
            blocked = name == SOURCE_PLANET and planet is not None and not planet.access
            if name != SOURCE_PLANET:
                title = ""
            elif blocked:
                title = t("steps.layers.err_nicfi", region=(planet.region or ""))
            else:
                # Planet has no cloud property, so the slider never hides it.
                title = t("analysis.timeline.no_cloud_filter")
            attrs = {"type": "button", "aria-pressed": "true" if (on and not blocked) else "false"}
            if title:
                attrs["title"] = title
                attrs["aria-label"] = f"{label}. {title}"
            if blocked:
                attrs["disabled"] = "disabled"
                attrs["aria-disabled"] = "true"
            # A toggle chip in the screen's type scale: the collection's
            # colour as a dot (the colour of its stripes), its name, and the
            # count dimmed. On, it is filled; off, an empty outline.
            active = on and not blocked
            dot = v.Html(tag="span", style_=(f"width: 8px; height: 8px; border-radius: 4px; flex: 0 0 8px; "
                                             f"background: {SRC_COLOR[name] if active else 'transparent'}; "
                                             f"border: 1.5px solid {SRC_COLOR[name]};"))
            count = f"{shown}" + (f" +{hidden}" if hidden else "")
            chip = v.Html(tag="button", attributes=attrs,
                          # one line, at its own width: a full toolbar squeezed
                          # the chips until "Sentinel-2" and "17 +7" broke in two
                          style_=(f"height: 28px; padding: 0 10px; border-radius: 14px; display: inline-flex; "
                                  f"flex: 0 0 auto; white-space: nowrap; "
                                  f"align-items: center; gap: 6px; font-size: 12px; line-height: 16px; "
                                  f"cursor: {'not-allowed' if blocked else 'pointer'}; color: var(--dat-ink); "
                                  f"background: {'var(--dat-chip)' if active else 'transparent'}; "
                                  f"border: 1px solid var(--dat-edge); "
                                  f"opacity: {'0.4' if blocked else ('1' if on else '0.6')}"),
                          children=[dot, SRC_LABEL[name],
                                    v.Html(tag="span", style_="opacity: 0.7; font-variant-numeric: tabular-nums;",
                                           children=[count])])
            if not blocked:
                chip.on_event("click", lambda w, e, d, name=name: _toggle(name))
            kids.append(chip)
        replace_children(chips, kids)

    def _render_titles(sel):
        """Name each side's image in its own map's title.

        The title also carries the two things about that image the reviewer
        acts on: the other collection's image of the same day, as a button
        that switches to it, and the note that a saved image was not found
        and the automatic pick stood in (amber, like every warning here).
        The bracket on the strip says how far apart the two are.
        """
        for lane_, head, rid in (("b", t("analysis_tile.img_selection.prev_image_title"), before_id.value),
                                 ("a", t("analysis_tile.img_selection.post_image_title"), after_id.value)):
            title = getattr(widgets, "map_title_before" if lane_ == "b" else "map_title_after", None)
            if title is None:
                continue
            kids = [v.Html(tag="span", children=[head]),
                    v.Html(tag="span", style_=f"font-size: 12.5px; font-weight: 400; color: {MUTED}",
                           children=[readout(sel, rid)])]
            alt = same_day_alternative(sel, rid) if _row_of(sel, rid) is not None else None
            if alt is not None:
                src = SRC_LABEL[scene_query.parse_row_id(alt)[0]]
                swap = v.Html(tag="button",
                              attributes={"type": "button",
                                          "title": t("analysis.timeline.swap_source", source=src)},
                              style_=("font-size: 11px; font-weight: 400; line-height: 18px; padding: 0 6px; "
                                      f"border-radius: 3px; border: 1px solid var(--dat-edge); cursor: pointer; "
                                      f"color: {SRC_COLOR[scene_query.parse_row_id(alt)[0]]}"),
                              children=[f"\u21c4 {src}"])
                swap.on_event("click", lambda *_a, lane_=lane_, alt=alt: _pick_if_reachable(lane_, alt))
                kids.append(swap)
            if lane_ in fell_back.value:
                kids.append(v.Html(tag="span", style_=f"font-size: 12px; font-weight: 400; color: {WARN}",
                                   children=[t("analysis.timeline.saved_missing")]))
            # the title is a wrapping flex row: the gap is what keeps the pane
            # name and the image name two words
            title.style_ = "column-gap: 10px; row-gap: 2px"
            replace_children(title, kids)

    def _render_status():
        """The index's age and the refresh's outcome, which are two facts.

        Review Focus #2 is both halves at once: a reviewer whose stale index
        failed to refresh must see *how old what they are looking at is* and
        *that the refresh failed*. Showing only the error would leave them
        with no idea whether the timeline is a day or a month behind, so the
        age line is rendered first and the failure after it, not instead.
        """
        error = app_state.scene_index_error.value
        manifest = app_state.scene_manifest.value
        kids = []
        if manifest is not None:
            age = (datetime.now(timezone.utc) - manifest.refreshed).total_seconds() / 3600
            if age > scene_query.TTL_HOURS or error:
                kids.append(v.Html(tag="span", children=[
                    t("analysis.timeline.stale", when=manifest.refreshed.date().isoformat())]))
        if error:
            # The index already on screen keeps answering: this says the
            # refresh failed, not that there is nothing to look at.
            retry = v.Btn(x_small=True, text=True, class_="defo-tl-btn", children=[t("analysis.timeline.retry")])
            retry.on_event("click", lambda w, e, d: app_state.scene_index_retry.set(
                app_state.scene_index_retry.value + 1))
            kids.append(v.Html(tag="span", style_=f"color: {WARN}; margin-left: 8px", children=[
                t("analysis.timeline.refresh_failed", error=error)]))
            kids.append(retry)
        elif app_state.scene_index_pending.value or (manifest is None
                                                     and app_state.scene_index.value is None):
            # Backed by the absence of an index as well as by the flag: the
            # flag alone can read ``False`` while a fetch is still running.
            kids.append(v.Html(tag="span", style_="margin-left: 8px",
                               children=[t("analysis.timeline.loading")]))
        replace_children(status, kids)

    def _ask_for_the_view():
        """Ask the loader for the months *this* alert's view needs, and no others.

        Widening the local view is not enough: outside the cached coverage the
        extra month would simply draw empty. ``scene_window_request`` is what
        ``SceneIndexLoader`` unions with its own ±2 months, so the month is
        fetched in the foreground and the strip fills in when it lands
        (``scene_index`` is one of the reactives ``render`` subscribes to).

        The request is the current alert's view window, absolute but *replaced*
        rather than unioned. It used to only ever grow, and because it was
        reset only on a project switch, "+1 month" on a Jan-2024 alert still
        widened the foreground fetch of a Dec-2026 one opened afterwards --
        roughly Nov 2023 to Feb 2027 in one ``getInfo``, where spec §5.5
        promises one foreground single-month fetch. Cumulative widening on the
        same alert is unaffected: ``extra`` grows and the window is recomputed
        from it, so a second press asks for two months, not one.

        ``extra == (0, 0)`` asks for nothing: the alert's own ±2 months lie
        inside ``wanted_window``'s padding of the active period, which the
        loader already fetches, and a request equal to it would be noise. A
        widen that is already cached is not refetched either -- the loader
        asks ``needs_refresh``, which answers "" for a covered window.
        """
        window = view_window(widgets.imagery_ctx.value, app_state.project.value, extra.value)
        app_state.scene_window_request.set(
            None if window is None or extra.value == (0, 0) else window)

    def _later_allowed() -> bool:
        return can_widen_forward(view_window(widgets.imagery_ctx.value, app_state.project.value, extra.value),
                                 today())

    def _widen(side):
        """One more month on ``side``, and a request for it if the cache is
        short. Never a month that has not begun (``can_widen_forward``): a
        click sent before the button was disabled is dropped here."""
        if side == "right" and not _later_allowed():
            return
        back, fwd = extra.value
        extra.set((back + 1, fwd) if side == "left" else (back, fwd + 1))
        _ask_for_the_view()
        render()

    def _collapse_window():
        """Put the alert window's inner months back into blocks."""
        ctx = widgets.imagery_ctx.value
        project = app_state.project.value
        window = view_window(ctx, project, extra.value)
        a0, a1 = alert_window(ctx, project)
        if window is None or a0 is None or a1 is None:
            return
        collapsed.set(frozenset(scene_query.auto_collapsed(window[0], window[1], a0, a1)))
        split.set(frozenset())
        render()

    def _clear_track():
        for host in (lane, lane_axis, blocks):
            replace_children(host, [])
        strip.candidates = []
        strip.before = strip.after = None
        strip.gap = ""
        window_band.style_ = "display: none"

    def _make_room_for(ids):
        """Switch on the collections and widen the view the saved picks need
        (spec D2), before the rows are read."""
        parsed = [scene_query.parse_row_id(rid) for rid in ids]
        wanted = {source for source, _ in parsed}
        if not wanted <= sources.value:
            sources.set(sources.value | wanted)
            _publish_filter()
        extra.set(extra_to_include(widgets.imagery_ctx.value, app_state.project.value,
                                   extra.value, [day for _, day in parsed]))

    def _settled(rid) -> bool:
        """Whether the index has answered for ``rid``'s date, so a row it does
        not hold is gone rather than not fetched yet (spec D4)."""
        if app_state.scene_index_error.value:
            return True
        manifest = app_state.scene_manifest.value
        if manifest is None or app_state.scene_index.value is None or app_state.scene_index_pending.value:
            return False
        _, day = scene_query.parse_row_id(rid)
        return manifest.covered.start <= day <= manifest.covered.end

    def _resolve_restores(sel, a0, a1):
        """Restore each waiting saved pick whose row is here, and fall back to
        the automatic pick for one the index has answered without (spec D4).
        Neither is an edit (spec D3): both go through ``_assign``.

        ``render`` also runs on the scene loader's thread, so a hand pick
        (``_picked``) can take a side out of ``restoring`` while this loop
        runs: each side is re-checked right before it is written, and leaves
        ``restoring`` on its own -- never by writing back a copy taken before
        the loop, which would put the hand-picked side back on the wait."""
        if not restoring.value:
            return
        auto = None

        def _still_waiting(lane, rid):
            return restoring.value.get(lane) == rid

        def _done(lane):
            restoring.set({k: rid_ for k, rid_ in restoring.value.items() if k != lane})

        for lane, rid in list(restoring.value.items()):
            if _row_of(sel, rid) is not None:
                if not _still_waiting(lane, rid):
                    continue
                _done(lane)
                _assign(lane, rid, sel, restore=True)
            elif _settled(rid):
                if not _still_waiting(lane, rid):
                    continue
                _done(lane)
                if a0 is None or a1 is None:
                    continue
                if auto is None:
                    auto = dict(zip(("b", "a"), scene_query.default_pick(sel, a0, a1)))
                # The note says the automatic pick stood in, so only when one
                # did: a side with no candidate is "not set", and says so.
                if auto[lane]:
                    fell_back.set(fell_back.value | {lane})
                    _assign(lane, auto[lane], sel)

    def render(*_):
        ctx = widgets.imagery_ctx.value
        # ``alert_id`` is ``actual_alert_id``, an *index* into the active set's
        # GeoDataFrame, so it repeats across sets and across a reload of the
        # same set. ``token`` is ``AnalysisState.alert_token``, which
        # ``reset_alert`` bumps on every alert change, so the pair is the
        # identity: without it a second alert landing on index 7 inherits the
        # first one's collapse set and selection while ``reset_alert`` has
        # already cleared ``before_info`` -- a readout contradicting the
        # published pair, and no auto-pick at all.
        identity = ((ctx or {}).get("alert_id"), (ctx or {}).get("token"))
        if asked_alert.value != identity:
            # Before the empty-state return below, not after it: the window
            # width is an analyst preference that persists across alerts
            # (spec §4.7), and the alert that most needs its months fetched is
            # exactly the one the index has nothing for yet.
            asked_alert.set(identity)
            saved = {lane: scene_query.saved_pick_id((ctx or {}).get(key))
                     for lane, key in (("b", "saved_before"), ("a", "saved_after"))}
            saved = {lane: rid for lane, rid in saved.items() if rid}
            restoring.set(saved)
            fell_back.set(frozenset())
            if saved:
                _make_room_for(saved.values())
            _ask_for_the_view()
        # every render: a press, another alert and the date all move it
        right_btn.disabled = not _later_allowed()
        rows, window = _in_window()
        sel = None if rows is None or window is None else _enabled(rows)
        if sel is None or len(sel) == 0:
            track.style_ = "display: none"
            # Two different facts, and they were one message until the final
            # review: "nothing was found here" is the index's answer, while
            # "everything found here is in a collection you switched off" is
            # the reviewer's own doing and is undone by one click on a chip.
            # Saying the first when the second is true made the strip claim
            # the index was empty while it held rows.
            empty = rows is None or len(rows) == 0
            message.children = [t("analysis.timeline.no_images" if empty
                                  else "analysis.timeline.sources_off")]
            message.style_ = _MESSAGE_STYLE
            _clear_track()
            _render_chips(rows)
            _render_titles(sel)
            _render_status()
            return
        track.style_ = _TRACK_STYLE
        message.style_ = "display: none"
        # cleared, not only hidden: a leftover "no images" string in the tree
        # is still read by a screen reader walking the subtree.
        message.children = []

        a0, a1 = alert_window(ctx, app_state.project.value)
        if seen_alert.value != identity:
            seen_alert.set(identity)
            if a0 is not None and a1 is not None:
                collapsed.set(frozenset(scene_query.auto_collapsed(window[0], window[1], a0, a1)))
                split.set(frozenset())
                auto =dict(zip(("b", "a"), scene_query.default_pick(sel, a0, a1)))
                for lane in ("b", "a"):
                    chosen = before_id if lane == "b" else after_id
                    if lane in restoring.value:
                        chosen.set(None)  # a saved pick is on its way (D1)
                        continue
                    chosen.set(auto[lane])
                    if auto[lane]:
                        _assign(lane, auto[lane], sel)
        _resolve_restores(sel, a0, a1)

        segs = scene_query.month_segments(window[0], window[1], collapsed.value, TRACK_PX)
        _render_band(segs, a0, a1)
        _render_lane(sel, segs)
        _render_strip(sel, segs)
        _render_axis(segs)
        _render_blocks(segs, sel)
        _render_chips(rows)
        _render_titles(sel)
        _render_status()

    def _on_cloud(widget, event, value):
        # @change, not @input: dragging would otherwise rebuild ~170 widgets
        # per pixel. Vuetify sends the new value; a null slip is ignored
        # rather than crashing the handler on ``float(None)``.
        if value is None:
            return
        max_cloud.set(float(value))
        slider.label = _cloud_label()
        _publish_filter()
        render()

    def _cloud_label():
        # The limit in words, always: Vuetify's thumb label shows only while
        # the thumb is held, which left the reviewer guessing what the faded
        # rows were faded by. In the label rather than beside the slider: the
        # toolbar is full at the drawer-open width, and an extra element there
        # pushed the value off the card.
        return f"{t('analysis.timeline.max_cloud')} {max_cloud.value:.0f}%"

    slider = v.Slider(v_model=max_cloud.value, min=0, max=100, step=5, hide_details=True, dense=True,
                      # gives way first on a narrow card, down to a track
                      # still wide enough to drag beside its label
                      class_="defo-cloud-slider", style_="flex: 0 1 230px; min-width: 170px; max-width: 230px",
                      label=_cloud_label())
    slider.on_event("change", _on_cloud)

    def _on_cloud_drag(widget, event, value):
        # The label follows the thumb while it is dragged, in place of
        # Vuetify's thumb label: that one floats above the slider, past the
        # strip's top edge, where the panes cover it. Only the label: the
        # filter waits for @change (see ``_on_cloud``).
        if value is not None:
            slider.label = f"{t('analysis.timeline.max_cloud')} {float(value):.0f}%"

    slider.on_event("input", _on_cloud_drag)

    def _side(side, arrow):
        # its text comes from ``_label_side`` (the toolbar's label hook)
        btn = v.Btn(small=True, text=True, class_="defo-tl-btn", attributes={}, children=[""])
        btn.on_event("click", lambda w, e, d, side=side: _widen(side))
        return btn

    def _label_side(btn, side, arrow):
        hint = t("analysis.timeline.month_before" if side == "left" else "analysis.timeline.month_after")
        btn.attributes = {**btn.attributes, "title": hint, "aria-label": hint}
        btn.children = [arrow + " " + t("analysis.timeline.one_more_month")]

    left_btn = _side("left", "‹")
    right_btn = _side("right", "›")

    # sentence case, not Vuetify's spaced capitals: the toolbar is full at
    # the drawer-open width, and the slider is what gave way. On a card too
    # narrow for the toolbar the labels go too (``defo-tl-label``, hidden by
    # the screen's container query), so each button carries its name in
    # ``title`` and ``aria-label`` as well.
    _compact = "text-transform: none; letter-spacing: normal"

    def _fold_btn(icon, key):
        text = t(key)
        return v.Btn(small=True, text=True, class_="defo-tl-btn defo-tl-fold", style_=_compact,
                     attributes={"title": text, "aria-label": text},
                     children=[v.Icon(small=True, left=True, children=[icon]),
                               v.Html(tag="span", class_="defo-tl-label", children=[text])])

    def _label_fold(btn, key):
        text = t(key)
        btn.attributes = {**btn.attributes, "title": text, "aria-label": text}
        btn.children[1].children = [text]

    fold = _fold_btn("mdi-arrow-collapse-horizontal", "analysis.timeline.collapse_all")
    fold.on_event("click", lambda w, e, d: _collapse_window())
    unfold = _fold_btn("mdi-arrow-expand-horizontal", "analysis.timeline.expand_all")
    unfold.on_event("click", lambda w, e, d: (collapsed.set(frozenset()), render()))

    # The legend is the only description of what the lanes, bands, fade and
    # gold band mean, and it used to hang off ``aria-label`` on a
    # ``role="note"`` span: not focusable, and not reliably announced. It is a
    # disclosure button now, so a keyboard reviewer can reach the explanation
    # and a screen reader reads it as text rather than as a label.
    legend = t("analysis.timeline.legend")
    legend_text = v.Html(tag="div", attributes={"id": _LEGEND_ID},
                         style_=_LEGEND_HIDDEN, children=[])
    help_badge = v.Html(tag="button",
                        attributes={"type": "button", "title": legend,
                                    "aria-label": t("analysis.timeline.legend_label"),
                                    "aria-expanded": "false", "aria-controls": _LEGEND_ID},
                        style_=(f"display: inline-flex; align-items: center; justify-content: center; "
                                f"width: 18px; height: 18px; border-radius: 9px; cursor: pointer; "
                                f"background: none; padding: 0; "
                                f"border: 1px solid var(--dat-edge); color: {MUTED}; font-size: 11px"),
                        children=["?"])

    def _toggle_legend(*_):
        shown = help_badge.attributes.get("aria-expanded") == "true"
        help_badge.attributes = {**help_badge.attributes,
                                 "aria-expanded": "false" if shown else "true"}
        # cleared, not only hidden, for the same reason as ``message``: a
        # string left in the tree is still read by a screen reader walking it
        legend_text.children = [] if shown else [t("analysis.timeline.legend")]
        legend_text.style_ = _LEGEND_HIDDEN if shown else _LEGEND_SHOWN

    help_badge.on_event("click", _toggle_legend)

    # The image area around the alert (``imagery.IMAGE_AREAS``): per alert,
    # back to Normal on the next one (``AnalysisState.reset_alert``).
    area = v.Select(items=[{"text": t(f"analysis.timeline.area_{k}"), "value": k} for k in IMAGE_AREAS],
                    v_model=analysis.image_area.value, dense=True, outlined=True, hide_details=True,
                    label=t("analysis.timeline.image_area"), class_="defo-area-select",
                    attributes={"title": t("analysis.timeline.area_hint")},
                    style_="flex: 0 1 150px; min-width: 120px; max-width: 150px;")
    area.observe(lambda change: analysis.image_area.set(change["new"] or "normal"), "v_model")
    analysis.image_area.subscribe(lambda value: setattr(area, "v_model", value))

    def _label_toolbar():
        slider.label = _cloud_label()
        _label_fold(fold, "analysis.timeline.collapse_all")
        _label_fold(unfold, "analysis.timeline.expand_all")
        legend = t("analysis.timeline.legend")
        help_badge.attributes = {**help_badge.attributes, "title": legend,
                                 "aria-label": t("analysis.timeline.legend_label")}
        if help_badge.attributes.get("aria-expanded") == "true":
            legend_text.children = [legend]
        area.items = [{"text": t(f"analysis.timeline.area_{k}"), "value": k} for k in IMAGE_AREAS]
        area.label = t("analysis.timeline.image_area")
        area.attributes = {**area.attributes, "title": t("analysis.timeline.area_hint")}
        _label_side(left_btn, "left", "‹")
        _label_side(right_btn, "right", "›")

    labels.add(_label_toolbar)

    def _divider():
        # hidden where the toolbar may wrap (the screen's container query): a
        # divider left at the end of a line separates nothing
        return v.Html(tag="span", class_="defo-tl-divider",
                      style_="width: 1px; height: 24px; background: var(--dat-edge); flex: 0 0 1px;")

    card = v.Card(flat=True, class_="analysis-timeline", children=[
        # wraps rather than running off the card when even the icon-only
        # toolbar does not fit
        v.Html(tag="div", style_="display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px;"
                                 " padding: 8px 8px 6px",
               children=[chips, help_badge, v.Spacer(), fold, unfold, _divider(), slider, _divider(),
                         area, status]),
        legend_text,
        # tall enough, even when it holds only a one-line message, for
        # pysepal's log button to sit under "+1 month" (the shell's
        # ``.pill-wrapper`` rule) without covering it
        v.Html(tag="div", style_="display: flex; gap: 10px; align-items: stretch; padding: 0 8px 8px;"
                                 " min-height: 84px",
               children=[left_btn, track, message, right_btn]),
    ])

    for reactive in (app_state.scene_index, app_state.scene_manifest, app_state.scene_index_error,
                     app_state.scene_index_pending, widgets.imagery_ctx):
        reactive.subscribe(render)
    _publish_filter()
    render()
    # a language switch redraws the strip: chips, lane, blocks, axis months,
    # status and both view titles. It re-picks only on a new alert identity,
    # and ``_resolve_restores`` leaves a pending restore pending while its row
    # is missing, so a switch is the same as any reactive tick.
    labels.add(render, now=False)
    return card
