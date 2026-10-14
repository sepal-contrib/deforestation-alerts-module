"""The Overview dashboard's figures, drawn server-side as inline SVG (spec §12).

The app is served by Voila, so a chart may not depend on a CDN or a widget
bundle: every figure here is a matplotlib ``Figure`` saved to an SVG string
that a tile drops into the page with ``solara.HTML(unsafe_innerHTML=...)``.
``to_inline_svg`` makes that markup safe to inline -- it strips matplotlib's
global ``*{...}`` stylesheet (which would otherwise restyle the whole app),
the XML prolog and the timestamped metadata, and labels the root ``<svg>``
for a screen reader.

Three rules hold across every figure:

* **No colour is written here.** Status fills come from
  ``legend_specs.status_colors()`` and are stacked in ``STACK_ORDER``, which
  keeps Confirmed and False positive non-adjacent; chrome comes from
  ``THEMES``. The only hues a chart invents are its own surface, ink and grid.
* **One measure at a time.** ``measure`` is ``MEASURE_AREA`` or
  ``MEASURE_ALERTS`` and picks both the values and the axis label. No figure
  ever carries two scales.
* **Text wears ink, never the series colour.** No figure writes a number on
  top of a coloured fill: at these sizes neither a white nor a dark label
  clears 4.5:1 on every status step, and labelling only the segments that
  happen to pass reads as a bug. The card's HTML chips carry the labels,
  values and shares as selectable text on the surface.

Nothing in this module imports solara: a chart is a string, and the tests
render every one of them without a browser.
"""

import io
import logging
import re
from xml.sax.saxutils import escape

from matplotlib import rc_context
from matplotlib.backends.backend_svg import FigureCanvasSVG
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MaxNLocator

from deforestation_alerts.project.review_status import NOT_REVIEWED, STATUS_CODES
from deforestation_alerts.scripts.set_summary import UNKNOWN_DRIVER
from gui.i18n import format_date, format_number, t
from gui.scripts.legend_specs import STACK_ORDER, STATUS_CLASSES, status_colors

#: The two things a chart can measure. Never both at once.
MEASURE_AREA = "area"
MEASURE_ALERTS = "alerts"
MEASURES = (MEASURE_AREA, MEASURE_ALERTS)

#: The ranking row that stands for everything past the cut (``_fold_tail``).
OTHER_KEY = "__other__"
#: How many named rows a ranking shows before folding the rest into one.
RANK_LIMIT = 5

#: Chart chrome, per mode. Dark is a chosen second set validated against its
#: own surface, not an inversion of light. ``ink_dark``/``ink_light`` are the
#: two inks a label inside a coloured fill may take (see ``_ink_on``); they do
#: not change with the mode, because the fill they sit on does not either.
THEMES = {
    "light": {
        "surface": "#ffffff",
        "ink": "#202124",
        "ink2": "#5f6368",
        "grid": "#e8eaed",
        "accent": "#1565c0",
        "lookback": "#c4c7c5",
        "rule": "#9aa0a6",
        "ink_dark": "#1a1a1a",
        "ink_light": "#ffffff",
    },
    "dark": {
        "surface": "#1e1e1e",
        "ink": "#e8eaed",
        "ink2": "#a2a9b0",
        "grid": "#3c4043",
        "accent": "#6aa9e9",
        "lookback": "#55595e",
        "rule": "#8a9096",
        "ink_dark": "#1a1a1a",
        "ink_light": "#ffffff",
    },
}

# ``svg.hashsalt`` fixes the clip-path and hatch ids, so the same data renders
# to the same bytes twice -- a tile may re-render for reasons that have nothing
# to do with the figure. Paths, not text, so the browser's fonts cannot shift a
# label off its mark.
_RC = {
    "svg.fonttype": "path",
    "svg.hashsalt": "deforestation-alerts",
    "hatch.linewidth": 0.6,
    "font.family": ["Roboto", "Helvetica Neue", "Arial", "DejaVu Sans", "sans-serif"],
    "font.size": 8.0,
    "path.simplify": True,
}
# Roboto is the app's face but rarely a system font; the fallback is silent.
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

_STATUS_LABEL_KEYS = {code: key for code, _, key in STATUS_CLASSES}
_NOT_REVIEWED = STATUS_CODES[NOT_REVIEWED]
_STYLE = re.compile(r"<style[^>]*>.*?</style>", re.S)
_METADATA = re.compile(r"<metadata>.*?</metadata>", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_PT_SIZE = re.compile(r'\s(?:width|height)="[\d.]+pt"')
_TICK = 7.5
_LABEL = 8.0


def theme_for(dark: bool) -> dict:
    """The chrome for the mode the app is in (``app_state.dark``)."""
    return dict(THEMES["dark" if dark else "light"])


# --- numbers and text -----------------------------------------------------

def _num(value) -> str:
    """A bin edge: ``0.5``, ``25`` -- no trailing zero on a whole number."""
    value = float(value)
    return format_number(value, 0 if value.is_integer() else 1)


def format_value(value, measure: str) -> str:
    """A measured quantity, in the reader's locale.

    Public: the dashboard's KPI numbers, legend chips and numbers table read
    it too, so a figure and the text beside it can never round differently.
    """
    if measure == MEASURE_ALERTS:
        return format_number(round(float(value)), 0)
    value = float(value)
    return format_number(value, 1 if value < 100 else 0)


def _tick_text(value, measure: str) -> str:
    """An axis tick. ``MaxNLocator`` picks round numbers, so a hectare tick
    shows no decimal unless the locator needed one."""
    if measure == MEASURE_ALERTS:
        return format_number(round(float(value)), 0)
    return _num(value)


def _axis_label(measure: str) -> str:
    return t("overview_tile.axis_alerts" if measure == MEASURE_ALERTS else "overview_tile.axis_area")


def _clip(text: str, width: int = 22) -> str:
    text = str(text)
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def _size_label(lo, hi) -> str:
    if not lo:
        return t("overview_tile.size_under", size=_num(hi))
    if hi == float("inf"):
        return t("overview_tile.size_over", size=_num(lo))
    return t("overview_tile.size_range", lo=_num(lo), hi=_num(hi))


def _listing(pairs) -> str:
    """An aria-label: the figure's own numbers, read out in order."""
    return "; ".join(f"{label}: {value}" for label, value in pairs) or t("overview_tile.chart_no_data")


# --- figures --------------------------------------------------------------

def _figure(width: float, height: float, theme: dict, rect=(0.0, 0.0, 1.0, 1.0)):
    """A bare figure of exactly ``width`` x ``height`` CSS pixels (72 dpi, so
    one point is one pixel) with its spines and tick marks already gone."""
    fig = Figure(figsize=(width / 72.0, height / 72.0), dpi=72)
    FigureCanvasSVG(fig)  # attaches itself; no pyplot, no global figure registry
    fig.patch.set_facecolor(theme["surface"])
    ax = fig.add_axes(rect)
    ax.set_facecolor(theme["surface"])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0, colors=theme["ink2"], labelsize=_TICK)
    return fig, ax


def to_inline_svg(fig, *, label: str) -> str:
    """``fig`` as markup safe to drop into the page: no prolog, no timestamp,
    no global stylesheet, labelled, and sized by its container."""
    buffer = io.StringIO()
    with rc_context(_RC):
        fig.savefig(buffer, format="svg", facecolor=fig.get_facecolor(), edgecolor="none")
    svg = buffer.getvalue()
    svg = svg[svg.index("<svg"):]
    for pattern in (_METADATA, _STYLE, _COMMENT):
        svg = pattern.sub("", svg)
    cut = svg.index(">")
    head = _PT_SIZE.sub("", svg[:cut]).rstrip()
    attributes = (
        f' role="img" aria-label="{escape(label, {chr(34): "&quot;"})}"'
        ' stroke-linejoin="round" stroke-linecap="butt"'
        ' style="width:100%;height:auto;display:block"'
    )
    return head + attributes + svg[cut:]


def _empty(width: float, height: float, theme: dict) -> str:
    """The figure a card shows when there is nothing in it yet."""
    text = t("overview_tile.chart_no_data")
    fig, ax = _figure(width, height, theme)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.text(0.5, 0.5, text, ha="center", va="center", fontsize=_LABEL, color=theme["ink2"])
    return to_inline_svg(fig, label=text)


# --- status ---------------------------------------------------------------

def status_rows(summary, *, measure: str, order=STACK_ORDER) -> tuple:
    """``(code, colour, label, value)`` per review status, in ``order``.

    The dashboard's legend chips, its stacked bars and its numbers table all
    read this, so they can never disagree about a colour or a total.
    """
    colours = status_colors()
    found = {breakdown.code: breakdown for breakdown in summary.by_status}
    rows = []
    for code in order:
        breakdown = found.get(code)
        value = 0.0
        if breakdown is not None:
            value = float(breakdown.n) if measure == MEASURE_ALERTS else breakdown.area_ha
        rows.append((code, colours[code], t(_STATUS_LABEL_KEYS[code]), value))
    return tuple(rows)


def chart_status(summary, *, measure: str, theme: dict) -> str:
    """One stacked bar: how the set's area (or count) splits across statuses.

    A proportion bar, deliberately wordless -- the card draws the matching
    chips in HTML, so every segment is named and measured in selectable text
    that contrasts with the surface rather than with its own fill.
    """
    rows = status_rows(summary, measure=measure)
    total = sum(value for *_, value in rows)
    if total <= 0:
        return _empty(420, 56, theme)
    fig, ax = _figure(420, 56, theme, rect=(0.0, 0.14, 1.0, 0.72))
    offset = 0.0
    for _, colour, _, value in rows:
        if value <= 0:
            continue
        ax.barh([0], [value], left=[offset], height=1.0, color=colour,
                edgecolor=theme["surface"], linewidth=2.0, zorder=2)
        offset += value
    ax.set_xlim(0, total)
    ax.set_ylim(-0.5, 0.5)
    ax.set_xticks([])
    ax.set_yticks([])
    return to_inline_svg(fig, label=_listing(
        (label, format_value(value, measure)) for _, _, label, value in rows if value > 0))


# --- the trend across periods --------------------------------------------

def chart_trend(series, *, measure: str, theme: dict) -> str:
    """Stacked columns, one per monitoring period, oldest first.

    ``series`` is ``(label, SetSummary, under_review)``. A period still being
    reviewed gets its Not-reviewed segment hatched, so a tall column that is
    merely unfinished never reads as a bad month.
    """
    series = tuple(series)
    if not series:
        return _empty(420, 196, theme)
    fig, ax = _figure(420, 196, theme, rect=(0.125, 0.16, 0.855, 0.74))
    spots = range(len(series))
    bottoms = [0.0] * len(series)
    for code in STACK_ORDER:
        values = [dict((c, v) for c, _, _, v in status_rows(s, measure=measure))[code]
                  for _, s, _ in series]
        colour = status_colors()[code]
        ax.bar(spots, values, bottom=bottoms, width=0.62, color=colour,
               edgecolor=theme["surface"], linewidth=2.0, zorder=2)
        if code == _NOT_REVIEWED:
            for spot, (_, _, under_review) in enumerate(series):
                if under_review and values[spot] > 0:
                    ax.bar([spot], [values[spot]], bottom=[bottoms[spot]], width=0.62,
                           color="none", edgecolor=theme["surface"], hatch="///",
                           linewidth=0.0, zorder=3)
        bottoms = [carried + value for carried, value in zip(bottoms, values)]
    top = max(bottoms) or 1.0
    for spot, total in enumerate(bottoms):
        if total > 0:
            ax.text(spot, total + top * 0.035, format_value(total, measure), ha="center", va="bottom",
                    fontsize=_LABEL, color=theme["ink"], zorder=4)
    ax.set_ylim(0, top * 1.2)
    ax.set_xlim(-0.6, len(series) - 0.4)
    ax.set_xticks(list(spots))
    rotated = len(series) > 5
    ax.set_xticklabels([_clip(label, 12) for label, _, _ in series], fontsize=_TICK,
                       color=theme["ink2"], rotation=30 if rotated else 0,
                       ha="right" if rotated else "center")
    ax.yaxis.set_major_locator(MaxNLocator(3, prune=None))
    # A formatter, not set_yticklabels: the locator is free to re-pick the
    # ticks while the figure is being drawn, and fixed labels would then sit
    # on the wrong lines.
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: _tick_text(value, measure)))
    ax.set_ylabel(_axis_label(measure), fontsize=_TICK, color=theme["ink2"], labelpad=3)
    ax.grid(axis="y", color=theme["grid"], linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    return to_inline_svg(fig, label=_listing(
        (label, format_value(total, measure)) for (label, _, _), total in zip(series, bottoms)))


# --- how big the alerts are ----------------------------------------------

def chart_sizes(summary, *, measure: str, theme: dict) -> str:
    """One hue, one series: the distribution of alert sizes across the bins.

    Every bar carries its own number instead of a y axis -- seven bars is few
    enough to label, and the reader never has to trace a gridline.
    """
    bins = tuple(summary.size_bins)
    values = [float(n) if measure == MEASURE_ALERTS else area for _, _, n, area in bins]
    if not bins or sum(values) <= 0:
        return _empty(420, 150, theme)
    labels = [_size_label(lo, hi) for lo, hi, _, _ in bins]
    fig, ax = _figure(420, 150, theme, rect=(0.03, 0.26, 0.94, 0.62))
    spots = range(len(bins))
    ax.bar(spots, values, width=0.64, color=theme["accent"], zorder=2)
    top = max(values) or 1.0
    for spot, value in enumerate(values):
        if value > 0:
            ax.text(spot, value + top * 0.05, format_value(value, measure), ha="center", va="bottom",
                    fontsize=_TICK, color=theme["ink"])
    ax.set_ylim(0, top * 1.22)
    ax.set_yticks([])
    ax.set_xticks(list(spots))
    ax.set_xticklabels(labels, fontsize=7.0, color=theme["ink2"])
    ax.set_xlabel(t("overview_tile.axis_size"), fontsize=_TICK, color=theme["ink2"], labelpad=3)
    return to_inline_svg(fig, label=_listing(zip(labels, (format_value(v, measure) for v in values))))


# --- when the alerts were detected ---------------------------------------

def chart_timing(summary, *, measure: str, theme: dict) -> str:
    """Weekly columns. Weeks before the period's own start are drawn in the
    lookback grey and separated by a rule, because those alerts were detected
    earlier and carried in -- counting them as this period's would mislead.
    """
    weeks = tuple(summary.by_week)
    values = [float(n) if measure == MEASURE_ALERTS else area for _, area, n, _ in weeks]
    if not weeks or sum(values) <= 0:
        return _empty(420, 162, theme)
    fig, ax = _figure(420, 162, theme, rect=(0.115, 0.20, 0.865, 0.68))
    spots = range(len(weeks))
    colours = [theme["accent"] if in_period else theme["lookback"] for *_, in_period in weeks]
    ax.bar(spots, values, width=0.78, color=colours, zorder=2)
    top = max(values) or 1.0
    starts = [spot for spot, week in enumerate(weeks) if week[3]]
    if starts and starts[0] > 0:
        ax.axvline(starts[0] - 0.5, color=theme["rule"], linewidth=1.0,
                   linestyle=(0, (3, 2)), zorder=3)
        ax.text(starts[0] - 0.35, top * 1.1, t("overview_tile.period_starts"), ha="left",
                va="top", fontsize=7.0, color=theme["ink2"], zorder=4)
    marks = sorted({0, len(weeks) - 1} | ({starts[0]} if starts else set()))
    ax.set_ylim(0, top * 1.18)
    ax.set_xlim(-0.7, len(weeks) - 0.3)
    ax.set_xticks(marks)
    ax.set_xticklabels([format_date(weeks[mark][0], "d MMM") for mark in marks],
                       fontsize=7.0, color=theme["ink2"])
    ax.yaxis.set_major_locator(MaxNLocator(3, prune=None))
    # A formatter, not set_yticklabels: the locator is free to re-pick the
    # ticks while the figure is being drawn, and fixed labels would then sit
    # on the wrong lines.
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: _tick_text(value, measure)))
    ax.set_ylabel(_axis_label(measure), fontsize=_TICK, color=theme["ink2"], labelpad=3)
    ax.grid(axis="y", color=theme["grid"], linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    return to_inline_svg(fig, label=_listing(
        (format_date(week[0], "d MMM"), format_value(value, measure)) for week, value in zip(weeks, values)))


# --- rankings: question answers and sources -----------------------------

def _fold_tail(rows, limit: int = RANK_LIMIT) -> tuple:
    """``rows`` cut to ``limit``, with everything past it summed into one
    ``OTHER_KEY`` row -- a ranking never grows a hue per category."""
    rows = tuple(rows)
    if len(rows) <= limit:
        return rows
    tail = rows[limit:]
    return rows[:limit] + ((OTHER_KEY,
                            sum(area for _, area, _ in tail),
                            sum(n for _, _, n in tail)),)


def _ranked_for(rows, measure: str) -> tuple:
    """``rows`` re-sorted by the measure on screen: a ranking by area is not a
    ranking by count, and the bars must match their own axis."""
    index = 2 if measure == MEASURE_ALERTS else 1
    return tuple(sorted(rows, key=lambda row: (-row[index], str(row[0]))))


def _merge_by_label(pairs) -> tuple:
    """Rows that read the same become one bar, and the folded row sinks to the
    bottom. Two bars both saying "Other" -- an answer code of that name plus
    the folded tail -- is the case that forces this, but two long names
    clipped to the same text would read just as wrongly.
    """
    order, totals, folded = [], {}, set()
    for label, value, is_folded in pairs:
        if label not in totals:
            order.append(label)
            totals[label] = 0.0
        totals[label] += value
        if is_folded:
            folded.add(label)
    merged = sorted(((label, totals[label]) for label in order), key=lambda row: -row[1])
    merged.sort(key=lambda row: row[0] in folded)  # stable: keeps the ranking
    return tuple(merged)


def _ranking(rows, *, measure, theme, label_of, width, height, left) -> str:
    """Horizontal bars, largest on top, one hue, each labelled with its value."""
    ranked = _fold_tail(_ranked_for(rows, measure), RANK_LIMIT)
    index = 2 if measure == MEASURE_ALERTS else 1
    merged = _merge_by_label(
        (t("overview_tile.driver_other") if row[0] == OTHER_KEY else _clip(label_of(row[0])),
         float(row[index]), row[0] == OTHER_KEY)
        for row in ranked)
    labels = [label for label, _ in merged]
    values = [value for _, value in merged]
    if not merged or sum(values) <= 0:
        return _empty(width, height, theme)
    fig, ax = _figure(width, height, theme, rect=(left, 0.10, 0.97 - left, 0.86))
    spots = range(len(merged))
    ax.barh(spots, values, height=0.62, color=theme["accent"], zorder=2)
    span = max(values) or 1.0
    for spot, value in enumerate(values):
        ax.text(value + span * 0.02, spot, format_value(value, measure), ha="left", va="center",
                fontsize=_TICK, color=theme["ink"])
    ax.set_xlim(0, span * 1.22)
    ax.set_ylim(len(merged) - 0.5, -0.5)  # largest on top
    ax.set_xticks([])
    ax.set_yticks(list(spots))
    ax.set_yticklabels(labels, fontsize=_TICK, color=theme["ink2"])
    return to_inline_svg(fig, label=_listing(zip(labels, (format_value(v, measure) for v in values))))


def _answer_label(label_of):
    """``label_of`` for one answer code, naming the unanswered sentinel too."""
    def label(code):
        return t("overview_tile.answer_unknown") if code == UNKNOWN_DRIVER else label_of(code)

    return label


def question_rows(summary, key: str, *, measure: str, label_of) -> tuple:
    """``(label, value)`` per answer of question ``key``, largest first and
    value formatted for ``measure``: the numbers behind ``chart_question``,
    for a card whose figure could not be drawn."""
    label = _answer_label(label_of)
    index = 2 if measure == MEASURE_ALERTS else 1
    return tuple((label(row[0]), format_value(float(row[index]), measure))
                 for row in _ranked_for(summary.breakdown(key), measure))


def chart_question(summary, key: str, *, measure: str, theme: dict, label_of) -> str:
    """What the reviewers answered to question ``key`` (spec D9). Reviewed
    alerts only, so the bars may sum to less than the set's total -- the card
    says so -- and an alert with two answers counts under each.

    ``label_of`` translates one answer code of that question; this module
    never imports the questionnaire. The one key it does not pass on is the
    unanswered sentinel, which is not an answer and has no option there.
    """
    return _ranking(summary.breakdown(key), measure=measure, theme=theme, label_of=_answer_label(label_of),
                    width=420, height=160, left=0.40)


def chart_sources(summary, *, measure: str, theme: dict) -> str:
    """Which alert system (or combination of them) flagged the loss."""
    return _ranking(summary.by_source, measure=measure, theme=theme,
                    label_of=lambda key: key or t("overview_tile.source_unknown"),
                    width=420, height=110, left=0.32)


# --- the sparkline beside a headline number ------------------------------

def chart_spark(values, *, theme: dict) -> str:
    """A 120x30 line with no axes: the shape of a run of periods, for a KPI
    tile. Support for a number, not a chart to read values off.
    """
    values = [float(value) for value in values]
    if len(values) < 2:
        return _empty(120, 30, theme)
    fig, ax = _figure(120, 30, theme, rect=(0.03, 0.12, 0.94, 0.76))
    ax.plot(range(len(values)), values, color=theme["accent"], linewidth=2.0,
            solid_capstyle="round", zorder=2)
    ax.plot([len(values) - 1], [values[-1]], marker="o", markersize=4.0,
            color=theme["accent"], zorder=3)
    low, high = min(values), max(values)
    pad = (high - low) * 0.18 or (abs(high) * 0.1 or 1.0)
    ax.set_ylim(low - pad, high + pad)
    ax.set_xlim(-0.3, len(values) - 0.7 + 0.4)
    ax.set_xticks([])
    ax.set_yticks([])
    return to_inline_svg(fig, label="; ".join(format_number(value, 1) for value in values))
