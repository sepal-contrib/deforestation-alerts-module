"""The shared image timeline: one date axis under both map panes.

A raw-widget builder, never a Solara component. MapApp renders the Analysis
step's content with ``v-if``, which destroys and rebuilds the DOM when the
reviewer closes and reopens the step; a Solara-rendered subtree comes back
blank, while a raw ipyvuetify tree is re-viewed from its widget model.

Two lanes over one axis: clicking a stripe in the upper lane sets the before
image, in the lower lane the after image. One click, no mode to be in.

The geometry is addressed in **percentages of ``TRACK_PX``**, never in the
pixels ``scene_query.x_for`` returns, so the strip keeps its dates when the
browser window is not 1120px wide. ``scene_query`` owns every piece of the
arithmetic; nothing here recomputes a month's share of the track.

``app_state.scene_widening`` is deliberately not rendered. A cancelled fetch's
``finally`` can clear it while the replacement fetch is still running
(``gui/scripts/scene_loader.py``), so a widget driven by it blinks; the strip
instead fills in by itself when ``scene_index`` republishes. The one place the
pending flag is read -- the first-load line -- is backed by the *absence* of an
index as well, so that line does not depend on the flag being accurate.
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
)
from gui.i18n import format_date, t
from gui.scripts.scene_loader import shift_months

logger = logging.getLogger("SEPALUI.deforestation_alerts")

TRACK_PX = 1120.0
LANE_H = 45
AXIS_H = 27
VIEW_MONTHS = 2

#: (top offset, height) of each collection's band inside a lane. The bands do
#: not overlap, so two collections on one date never draw over each other and
#: a stripe's row says which collection it is before its colour does.
BAND = {SOURCE_S2: (0, 17), SOURCE_LANDSAT: (19, 14), SOURCE_PLANET: (35, 10)}
SRC_COLOR = {SOURCE_S2: "#7fa8e0", SOURCE_LANDSAT: "#c9a87c", SOURCE_PLANET: "#9bd98a"}
#: display labels only. ``SOURCE_S2`` -- "Sentinel 2" with a space -- is what
#: the saved store, ``mosaics_helper`` and ``before_info[0]`` switch on; the
#: hyphenated form must never reach a data structure.
SRC_LABEL = {SOURCE_S2: "Sentinel-2", SOURCE_LANDSAT: "Landsat", SOURCE_PLANET: "Planet"}
SELECTED_COLOR = "#bf8f2d"  # the theme's primary_contrast
MUTED = "#a8a39b"
HIT_PX = 11
STRIPE_PX, SELECTED_STRIPE_PX = 2.5, 4.0
HIDDEN_SCALE, HIDDEN_OPACITY = 0.45, 0.3

_LANE_STYLE = f"position: relative; height: {LANE_H}px"
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
    return (shift_months(a0, -(VIEW_MONTHS + back)), shift_months(a1, VIEW_MONTHS + fwd))


def alert_window(ctx, project) -> tuple[Optional[date], Optional[date]]:
    """The gold band's own dates, by the same fallback as ``view_window``."""
    if ctx and ctx.get("date_min") and ctx.get("date_max"):
        return (ctx["date_min"], ctx["date_max"])
    if not ctx:
        return (None, None)
    period = project.periods.get(project.active_period) if (project and project.active_period) else None
    return (period.start, period.end) if period is not None else (None, None)


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

    ``scene_query.default_pick`` returns the *same* id on both sides when the
    alert window is a single day and a scene lands exactly on it -- pinned
    behaviour, because both bounds are inclusive. "0 days apart" would read as
    two images that happen to agree, so one image on both sides is named as
    that instead.
    """
    gap = days_apart(before_id, after_id)
    if gap is None:
        return None
    if before_id == after_id:
        return t("analysis.timeline.same_image")
    return t("analysis.timeline.days_apart", days=gap)


# --------------------------------------------------------------- the geometry


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


def stripe_bar_style(row, *, selected: bool) -> str:
    """The visible stripe inside the hit area."""
    _, height = BAND[row["source"]]
    hidden = bool(row["hidden"]) and not selected
    h = height * HIDDEN_SCALE if hidden else height
    width = SELECTED_STRIPE_PX if selected else STRIPE_PX
    color = SELECTED_COLOR if selected else SRC_COLOR[row["source"]]
    opacity = f"; opacity: {HIDDEN_OPACITY}" if hidden else ""
    return (f"position: absolute; left: 50%; transform: translateX(-50%); bottom: 0; "
            f"width: {width:g}px; height: {h:g}px; border-radius: 1px; background: {color}{opacity}")


def stripe_cursor(row) -> str:
    """A row the cloud limit hides is not clickable: raising the limit is the
    way to reach it, and a click that silently did nothing would read as a bug."""
    return "default" if bool(row["hidden"]) else "pointer"


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


def build_timeline(app_state, analysis, widgets):
    """The strip, built once per session and re-rendered from the reactives.

    Returns a ``v.Card`` holding the toolbar, the track and the readout. Every
    piece of state it reads is a reactive it subscribes to, so the only thing
    an alert change has to do is publish ``widgets.imagery_ctx``.
    """
    import solara

    max_cloud = solara.reactive(40.0)
    sources = solara.reactive(frozenset(SRC_COLOR))
    collapsed = solara.reactive(frozenset())
    extra = solara.reactive((0, 0))
    before_id = solara.reactive(None)
    after_id = solara.reactive(None)
    #: the alert the collapse set and the selection belong to, so a new alert
    #: gets its own auto-collapse instead of inheriting the last one's
    seen_alert = solara.reactive(None)

    lane_before = v.Html(tag="div", style_=f"{_LANE_STYLE}; margin-top: 11px")
    lane_axis = v.Html(tag="div", style_=f"position: relative; height: {AXIS_H}px")
    lane_after = v.Html(tag="div", style_=_LANE_STYLE)
    blocks = v.Html(tag="div", style_="position: absolute; left: 0; right: 0; top: 11px; bottom: 0; "
                                      "pointer-events: none")
    window_band = v.Html(tag="div", style_="display: none")
    track = v.Html(tag="div", style_=_TRACK_STYLE,
                   children=[window_band, lane_before, lane_axis, lane_after, blocks])
    message = v.Html(tag="div", style_=_MESSAGE_STYLE, children=[])
    readout_row = v.Html(tag="div", style_="display: flex; align-items: center; gap: 13px; font-size: 12.5px",
                         children=[])
    chips = v.Html(tag="div", style_="display: flex; align-items: center; gap: 8px", children=[])
    status = v.Html(tag="div", style_=f"font-size: 11.5px; color: {MUTED}", children=[])

    def _selection():
        """``(rows, window)`` for what is on screen, or ``(None, None)``."""
        tables = app_state.scene_index.value
        ctx = widgets.imagery_ctx.value
        window = view_window(ctx, app_state.project.value, extra.value)
        if tables is None or ctx is None or window is None or ctx.get("bbox") is None:
            return (None, None)
        sel = scene_query.scenes_for(tables, ctx["bbox"], window[0], window[1], max_cloud=max_cloud.value)
        return (sel[sel["source"].isin(sources.value)].reset_index(drop=True), window)

    def _keys_for(row, sel):
        """Every granule key the chosen (source, date) mosaics for this alert."""
        tables = app_state.scene_index.value
        ctx = widgets.imagery_ctx.value
        if tables is None or ctx is None or ctx.get("bbox") is None:
            return []
        tiles = scene_query.overlapping_tiles(tables.tiles, ctx["bbox"])
        rows = tables.scenes
        same = rows[(rows["source"] == row["source"]) & (rows["date"] == row["date"])]
        return list(same[same["tile"].isin(tiles) | (same["tile"] == "")]["key"])

    def _assign(lane, rid, sel):
        row = _row_of(sel, rid)
        if row is None or bool(row["hidden"]):
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

    def _on_pick(lane, rid):
        sel, _ = _selection()
        _assign(lane, rid, sel)
        render()

    def _render_lane(host, lane, sel, segs):
        chosen = (before_id if lane == "b" else after_id).value
        set_label = t("analysis.timeline.set_before" if lane == "b"
                      else "analysis.timeline.set_after")
        kids = []
        for _, row in sel.iterrows():
            d = _as_date(row["date"])
            seg = segs.get((d.year, d.month))
            if seg is None or seg.collapsed:
                continue  # its count is on the block instead
            rid = row["id"]
            hidden = bool(row["hidden"])
            head = f"{SRC_LABEL[row['source']]}, {d.isoformat()}"
            # A hidden row is not clickable, so its label must not offer the
            # action: the cloud limit is what stands between it and a pick.
            # ``_button`` marks a handler-less stripe disabled/aria-disabled,
            # so only the label and the selected state are decided here.
            attrs = {"aria-label": head if hidden else f"{head}, {set_label}"}
            if not hidden and rid == chosen:
                attrs["aria-current"] = "true"
            hit = (month_bar_style(segs, d) if row["source"] == SOURCE_PLANET
                   else stripe_hit_style(segs, d, row["source"]))
            kids.append(_button(
                hit, attrs,
                None if hidden else (lambda _w, _e, _d, lane=lane, rid=rid: _on_pick(lane, rid)),
                stripe_bar_style(row, selected=rid == chosen), stripe_cursor(row)))
        host.children = kids

    def _expand(key):
        collapsed.set(collapsed.value - {key})
        render()

    def _collapse(key):
        collapsed.set(collapsed.value | {key})
        render()

    def _render_blocks(segs, sel):
        counts = scene_query.month_counts(sel)
        kids = []
        for key, seg in sorted(segs.items()):
            if not seg.collapsed:
                continue
            # the locale's own month name: a CLDR pattern, not strftime, which
            # would put "JUN 2026" inside a French aria-label. The visible
            # label is uppercased by CSS, so the text a screen reader reads
            # and the text interpolated into ``collapsed_month`` stay natural.
            label = format_date(date(seg.year, seg.month, 1), "MMM y")
            n = counts.get(key, 0)
            btn = v.Html(
                tag="button",
                attributes={"type": "button",
                            "aria-label": t("analysis.timeline.collapsed_month", month=label, count=n)},
                style_=(f"pointer-events: auto; position: absolute; left: {_pct(seg.x):.3f}%; "
                        f"width: {_pct(seg.w):.3f}%; top: 0; bottom: 0; cursor: pointer; "
                        "background: repeating-linear-gradient(135deg, #24221f, #24221f 4px, "
                        "#1e1c1a 4px, #1e1c1a 9px); border-left: 1px dashed #44403a; "
                        "border-right: 1px dashed #44403a; display: flex; flex-direction: column; "
                        f"align-items: center; justify-content: center; gap: 3px; color: {MUTED}"),
                children=[
                    v.Html(tag="span", style_="font-size: 9px; letter-spacing: 0.1em; "
                                              "text-transform: uppercase", children=[label]),
                    v.Html(tag="span", style_="font-size: 16px; font-weight: 600; color: #f0ece6; "
                                              "line-height: 1", children=[str(n)]),
                    v.Html(tag="span", style_="font-size: 8px", children=[t("analysis.timeline.images")]),
                ])
            btn.on_event("click", lambda w, e, d, key=key: _expand(key))
            kids.append(btn)
        blocks.children = [v.Html(tag="div", style_="position: relative; height: 100%; "
                                                    "pointer-events: none", children=kids)]

    def _render_band(segs, a0, a1):
        if a0 is None or a1 is None:
            window_band.style_ = "display: none"
            return
        x0 = scene_query.x_for(segs, a0)
        x1 = scene_query.x_for(segs, a1, end=True)
        window_band.style_ = (f"position: absolute; left: {_pct(x0):.3f}%; "
                              f"width: {_pct(x1 - x0):.3f}%; "
                              f"top: 11px; bottom: 0; background: {SELECTED_COLOR}0d; "
                              f"border-left: 1px dashed {SELECTED_COLOR}aa; "
                              f"border-right: 1px dashed {SELECTED_COLOR}aa; pointer-events: none")

    def _render_axis(segs):
        kids = [v.Html(tag="span", style_="position: absolute; left: 0; right: 0; top: 0; height: 1px; "
                                          "background: #44403a")]
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
        lane_axis.children = kids

    def _toggle(name):
        sources.set(sources.value ^ {name})
        render()

    def _render_chips(sel):
        counts = scene_query.source_counts(sel) if sel is not None else {}
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
            chip = v.Html(tag="button", attributes=attrs,
                          style_=(f"height: 32px; padding: 0 10px; border-radius: 4px; "
                                  f"cursor: {'not-allowed' if blocked else 'pointer'}; "
                                  f"background: #24221f; color: #f0ece6; font-size: 11.5px; "
                                  f"border: 1px solid "
                                  f"{SRC_COLOR[name] if (on and not blocked) else '#44403a'}; "
                                  f"opacity: {'0.4' if blocked else ('1' if on else '0.5')}"),
                          children=[label])
            if not blocked:
                chip.on_event("click", lambda w, e, d, name=name: _toggle(name))
            kids.append(chip)
        chips.children = kids

    def _render_readout(sel):
        kids = []
        for lane, rid in (("b", before_id.value), ("a", after_id.value)):
            tag = t("analysis.timeline.before" if lane == "b" else "analysis.timeline.after")
            kids.append(v.Html(tag="span", children=[f"{tag}: {readout(sel, rid)}"]))
        # Only when both sides still resolve in this window: a note about a
        # pair whose readout says "not set" would contradict it.
        both = _row_of(sel, before_id.value) is not None and _row_of(sel, after_id.value) is not None
        note = pair_note(before_id.value, after_id.value) if both else None
        if note:
            kids.append(v.Html(tag="span", style_=f"margin-left: auto; color: {MUTED}", children=[note]))
        readout_row.children = kids

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
            retry = v.Btn(x_small=True, text=True, children=[t("analysis.timeline.retry")])
            retry.on_event("click", lambda w, e, d: app_state.scene_index_retry.set(
                app_state.scene_index_retry.value + 1))
            kids.append(v.Html(tag="span", style_="color: #d9ab4e; margin-left: 8px", children=[
                t("analysis.timeline.refresh_failed", error=error)]))
            kids.append(retry)
        elif app_state.scene_index_pending.value or (manifest is None
                                                     and app_state.scene_index.value is None):
            # Backed by the absence of an index as well as by the flag: the
            # flag alone can read ``False`` while a fetch is still running.
            kids.append(v.Html(tag="span", style_="margin-left: 8px",
                               children=[t("analysis.timeline.loading")]))
        status.children = kids

    def _widen(side):
        """One more month on ``side``, and a request for it if the cache is short.

        Widening the local view is not enough: outside the cached coverage the
        extra month would simply draw empty. ``scene_window_request`` is what
        ``SceneIndexLoader`` unions with its own ±2 months, so the month is
        fetched in the foreground and the strip fills in when it lands
        (``scene_index`` is one of the reactives ``render`` subscribes to).
        The request only ever grows, so a reviewer who widens and then moves
        to another alert keeps the context they asked for.
        """
        back, fwd = extra.value
        extra.set((back + 1, fwd) if side == "left" else (back, fwd + 1))
        window = view_window(widgets.imagery_ctx.value, app_state.project.value, extra.value)
        if window is not None:
            was = app_state.scene_window_request.value
            app_state.scene_window_request.set(
                window if was is None else (min(was[0], window[0]), max(was[1], window[1]))
            )
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
        render()

    def _clear_track():
        for host in (lane_before, lane_after, lane_axis):
            host.children = []
        blocks.children = []
        window_band.style_ = "display: none"

    def render(*_):
        sel, window = _selection()
        if sel is None or window is None or len(sel) == 0:
            track.style_ = "display: none"
            message.children = [t("analysis.timeline.no_images")]
            message.style_ = _MESSAGE_STYLE
            _clear_track()
            _render_chips(sel)
            _render_readout(sel)
            _render_status()
            return
        track.style_ = _TRACK_STYLE
        message.style_ = "display: none"
        # cleared, not only hidden: a leftover "no images" string in the tree
        # is still read by a screen reader walking the subtree.
        message.children = []

        ctx = widgets.imagery_ctx.value
        a0, a1 = alert_window(ctx, app_state.project.value)
        # ``alert_id`` is ``actual_alert_id``, an *index* into the active set's
        # GeoDataFrame, so it repeats across sets and across a reload of the
        # same set. ``token`` is ``AnalysisState.alert_token``, which
        # ``reset_alert`` bumps on every alert change, so the pair is the
        # identity: without it a second alert landing on index 7 inherits the
        # first one's collapse set and selection while ``reset_alert`` has
        # already cleared ``before_info`` -- a readout contradicting the
        # published pair, and no auto-pick at all.
        identity = ((ctx or {}).get("alert_id"), (ctx or {}).get("token"))
        if seen_alert.value != identity:
            seen_alert.set(identity)
            if a0 is not None and a1 is not None:
                collapsed.set(frozenset(scene_query.auto_collapsed(window[0], window[1], a0, a1)))
                b, a = scene_query.default_pick(sel, a0, a1)
                before_id.set(b)
                after_id.set(a)
                for lane, rid in (("b", b), ("a", a)):
                    if rid:
                        _assign(lane, rid, sel)

        segs = scene_query.month_segments(window[0], window[1], collapsed.value, TRACK_PX)
        _render_band(segs, a0, a1)
        _render_lane(lane_before, "b", sel, segs)
        _render_lane(lane_after, "a", sel, segs)
        _render_axis(segs)
        _render_blocks(segs, sel)
        _render_chips(sel)
        _render_readout(sel)
        _render_status()

    def _on_cloud(widget, event, value):
        # @change, not @input: dragging would otherwise rebuild ~170 widgets
        # per pixel. Vuetify sends the new value; a null slip is ignored
        # rather than crashing the handler on ``float(None)``.
        if value is None:
            return
        max_cloud.set(float(value))
        render()

    slider = v.Slider(v_model=max_cloud.value, min=0, max=100, step=5, hide_details=True,
                      thumb_label=True, style_="max-width: 160px",
                      label=t("analysis.timeline.max_cloud"))
    slider.on_event("change", _on_cloud)

    def _side(side, arrow):
        btn = v.Btn(small=True, text=True, children=[arrow + " " + t("analysis.timeline.one_more_month")])
        btn.on_event("click", lambda w, e, d, side=side: _widen(side))
        return btn

    fold = v.Btn(small=True, text=True, children=[t("analysis.timeline.collapse_all")])
    fold.on_event("click", lambda w, e, d: _collapse_window())
    unfold = v.Btn(small=True, text=True, children=[t("analysis.timeline.expand_all")])
    unfold.on_event("click", lambda w, e, d: (collapsed.set(frozenset()), render()))

    legend = t("analysis.timeline.legend")
    help_badge = v.Html(tag="span", attributes={"title": legend, "aria-label": legend, "role": "note"},
                        style_=(f"display: inline-flex; align-items: center; justify-content: center; "
                                f"width: 18px; height: 18px; border-radius: 9px; cursor: help; "
                                f"border: 1px solid #44403a; color: {MUTED}; font-size: 11px"),
                        children=["?"])

    card = v.Card(flat=True, class_="analysis-timeline", children=[
        v.Html(tag="div", style_="display: flex; align-items: center; gap: 10px; padding: 0 8px 6px",
               children=[chips, help_badge, v.Spacer(), fold, unfold, slider, status]),
        v.Html(tag="div", style_="display: flex; gap: 10px; align-items: stretch; padding: 0 8px",
               children=[_side("left", "‹"), track, message, _side("right", "›")]),
        v.Html(tag="div", style_="padding: 8px", children=[readout_row]),
    ])

    for reactive in (app_state.scene_index, app_state.scene_manifest, app_state.scene_index_error,
                     app_state.scene_index_pending, widgets.imagery_ctx):
        reactive.subscribe(render)
    render()
    return card
