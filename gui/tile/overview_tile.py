"""The Overview dashboard (spec §9.2, §9.3), a MapApp dialog 1300 px wide.

What it shows is the alert set the set selector picks
(``gui.widget.set_selector``, shared with Analysis): a pick changes only the
project's ``active_set``, and this view then summarises that set.

Architecture layer: view. It reads; it draws nothing on the map. The alert
layers, the earlier sets and the click-to-review popup are the shell's
``_AlertLayers`` leaf, because a layer switched on stays on while the user
moves between steps and dialogs, and a view that owned a layer would have to
hide it when it closes.

It does carry that leaf's two switches, the same ones Integrate & vectorize
offers and under the same labels -- they are two booleans on ``app_state``,
which is what lets the same control sit in two places without either view
owning a map call. Turning one on closes this dialog: the map is behind it,
and the user would otherwise see nothing happen.

Three rulings shape the component:

* **The numbers come off the store, not off the session's alerts.** A summary
  is a projected parquet read (``overview_data.summary_of``), so any set can be
  summarised without loading it, and the history reads one per period. The read
  blocks, so it runs on a daemon thread through ``spawn_in_context`` from an
  effect, with a run counter -- a set switched twice quickly must not land the
  first read's numbers. ``app_state.alerts_gdf`` is read for the export card
  alone, which writes the alerts the session holds.
* **One measure for the whole page**, Area (ha) by default, which is what keeps
  every figure on a single axis.
* **The figures are drawn once per (summary, measure, theme, language,
  questions)**, in a ``use_memo``: the project is committed on every
  auto-save, which re-renders this view, and re-rendering six matplotlib
  figures plus one per question for a save nobody asked about would be felt.
  A figure that raises comes back as ``None`` and that one card lists its
  numbers instead (``_figure_or_numbers``).

Feedback is hybrid. The component reports its own read failure in place -- a
dashboard with no numbers must say why where the numbers would be, not in a
toast that scrolls away. The export card is ``gui.tile.overview_export``'s
RAW ipyvuetify ``ExportCard``, whose writes run on a daemon thread under
``notify_bridge.run_button_job`` and report through the notification bus.
"""

import logging
from contextlib import contextmanager

import reacton.ipyvuetify as rv
import solara
from pysepal.i18n import current_locale

from deforestation_alerts.engine.fingerprint import Status, set_status
from deforestation_alerts.project import paths
from deforestation_alerts.scripts.set_summary import UNATTRIBUTED
from gui.i18n import format_date, format_number, t, tn
from gui.scripts import alert_layers, charts, growth_index, overview_data, set_store
from gui.scripts import questions as questions_mod
from gui.scripts.error_text import error_text
from gui.scripts.legend_specs import status_colors
from gui.scripts.solara_threads import spawn_in_context
from gui.store.workflow_steps import active_set
from gui.tile.overview_export import build_export_card, relabel_export_card
from gui.widget.set_selector import SetSelector

logger = logging.getLogger("SEPALUI.deforestation_alerts.overview")

#: One column of the two-column layout. The figures are 420 px wide, so a
#: column that falls below that scales them down rather than clipping them,
#: and the two columns stack on a narrow screen.
_COLUMN = "flex: 1 1 420px; min-width: 320px; display: flex; flex-direction: column; gap: 10px;"
_KPI = "flex: 1 1 170px; min-width: 150px;"
_TITLE = "font-size: 0.82rem; font-weight: 600; letter-spacing: 0.02em;"
_NOTE = "font-size: 0.72rem; opacity: 0.72; line-height: 1.25;"
_HERO = "font-size: 1.5rem; font-weight: 600; line-height: 1.1;"
_VALUE = "font-size: 1.05rem; font-weight: 600; line-height: 1.1;"
#: How many administrative areas the table lists before it stops.
ADMIN_LIMIT = 8
#: The admin card's levels: ``(key, its summary rows' attribute)``. FAO GAUL
#: level 2 is recorded too (``admin3``) but left out: it lists too many areas.
ADMIN_COUNTRY, ADMIN_LEVEL_1 = "country", "level1"
_ADMIN_ROWS = {ADMIN_COUNTRY: "by_admin", ADMIN_LEVEL_1: "by_admin_level1"}


# ---------------------------------------------------------------------------
# The dashboard's own pieces. Plain functions, not components: each one emits
# its elements into whatever container the caller is inside, so a card's
# contents stay next to the numbers they come from.
# ---------------------------------------------------------------------------

def period_label(period) -> str:
    """A monitoring period as every axis and caption here names it."""
    return t("steps.period.range", start=period.start.isoformat(), end=period.end.isoformat())


def _share(value: float) -> str:
    return f"{format_number(value * 100, 0)}%"


@contextmanager
def _card(title: str, note: str = ""):
    """A dashboard card. Its contents are whatever the caller emits inside."""
    with rv.Card(outlined=True, class_="pa-3", style_="height: 100%;"):
        with solara.Column(style="gap: 6px;"):
            solara.Text(title, style=_TITLE)
            if note:
                solara.Text(note, style=_NOTE)
            yield


def _figure_or_numbers(svg, rows):
    """The figure, or -- when matplotlib raised on it -- its own numbers in
    text, so a card that cannot draw still answers the question it asks."""
    if svg is not None:
        solara.HTML(tag="div", unsafe_innerHTML=svg)
        return
    solara.Text(t("overview_tile.chart_failed"), classes=["warning--text"], style=_NOTE)
    for label, value in rows:
        solara.Text(f"{label} — {value}", style=_NOTE)


def _chips(summary, measure: str, theme: dict):
    """The status bar's legend: a colour chip, the status and its value, in
    two columns of selectable text. The figure carries no labels of its own
    (see ``charts``), so these are what name every segment."""
    with solara.Div(style="display: grid; grid-template-columns: 1fr 1fr; gap: 2px 12px;"):
        for _, colour, label, value in charts.status_rows(summary, measure=measure):
            with solara.Row(style="gap: 6px; align-items: center;"):
                rv.Html(tag="span", style_=(f"background: {colour}; width: 10px; height: 10px;"
                                            " border-radius: 2px; flex: 0 0 auto;"))
                solara.Text(label, style=f"{_NOTE} color: {theme['ink2']};")
                rv.Spacer()
                solara.Text(charts.format_value(value, measure), style=_NOTE)


def _kpi(label: str, value: str, *, support: str = "", delta=None, spark=None, hero: bool = False):
    """One KPI tile. ``delta`` is ``(text, critical)``: the text already
    carries an arrow glyph and names the period it compares with, so the ink
    only emphasises what the words already say. ``hero`` is the one headline
    number of the strip, and it stays that size whether or not there is a
    sparkline beside it -- a project that gains its second period must not
    re-size the number the user has been reading."""
    with rv.Card(outlined=True, class_="pa-3", style_=f"{_KPI} height: 100%;"):
        with solara.Column(style="gap: 2px;"):
            solara.Text(label, style=_NOTE)
            solara.Text(value, style=_HERO if hero else _VALUE)
            if support:
                solara.Text(support, style=_NOTE)
            if delta is not None:
                text, critical = delta
                ink = status_colors()[overview_data.CONFIRMED_CODE] if critical else ""
                solara.Text(text, style=f"{_NOTE}{f' color: {ink};' if ink else ''}")
            if spark is not None:
                solara.HTML(tag="div", unsafe_innerHTML=spark)


def _numbers_table(summary, measure: str):
    """The table view of the whole page: every status with its count, area,
    share and median. A status wears a colour chip; the text stays in the
    theme's ink, never in the series colour."""
    head = (t("overview_tile.table_status"), t("overview_tile.table_alerts"),
            t("overview_tile.table_area"), t("overview_tile.table_share"),
            t("overview_tile.table_median"))
    rows = []
    for code, colour, label, n, area, median, share in overview_data.table_rows(summary, measure):
        name = [solara.Text(label).key(f"name-{code}")]
        if colour is not None:
            name.insert(0, rv.Html(tag="span", style_=(
                f"background: {colour}; width: 10px; height: 10px; border-radius: 2px;"
                " display: inline-block; margin-right: 6px;")))
        cells = [rv.Html(tag="td", children=name),
                 rv.Html(tag="td", children=[format_number(n, 0)]),
                 rv.Html(tag="td", children=[format_number(area, 1)]),
                 rv.Html(tag="td", children=[_share(share)]),
                 rv.Html(tag="td", children=["—" if median is None else format_number(median, 2)])]
        rows.append(rv.Html(tag="tr", children=cells,
                            style_="font-weight: 600;" if code is None else ""))
    rv.SimpleTable(dense=True, children=[
        rv.Html(tag="thead", children=[rv.Html(tag="tr", children=[
            rv.Html(tag="th", children=[column]) for column in head])]),
        rv.Html(tag="tbody", children=rows),
    ])


def _admin_label(level: str) -> str:
    return t("overview_tile.admin_level_country" if level == ADMIN_COUNTRY else "overview_tile.admin_level_1")


def _admin_level_picker(level: str, on_level):
    """Country or FAO GAUL level 1, the same segmented buttons as the measure."""
    with solara.Row(style="gap: 4px;"):
        for key in (ADMIN_COUNTRY, ADMIN_LEVEL_1):
            solara.Button(_admin_label(key), x_small=True, color="primary", outlined=level != key,
                          on_click=lambda key=key: on_level(key))


def _admin_table(summary, level: str):
    """Where the loss is, by the admin names the reviewer recorded. Reviewed
    alerts only -- nothing writes them for an unreviewed alert -- so the card
    says so and shows an empty state until there are reviews."""
    confirmed = t("legend.status.confirmed")
    head = (_admin_label(level), t("overview_tile.table_area"), confirmed,
            t("overview_tile.table_alerts"))
    rows = []
    for name, area, confirmed_ha, n in getattr(summary, _ADMIN_ROWS[level])[:ADMIN_LIMIT]:
        label = t("overview_tile.admin_unattributed") if name == UNATTRIBUTED else name
        rows.append(rv.Html(tag="tr", children=[
            rv.Html(tag="td", children=[label]),
            rv.Html(tag="td", children=[format_number(area, 1)]),
            rv.Html(tag="td", children=[format_number(confirmed_ha, 1)]),
            rv.Html(tag="td", children=[format_number(n, 0)]),
        ]))
    rv.SimpleTable(dense=True, children=[
        rv.Html(tag="thead", children=[rv.Html(tag="tr", children=[
            rv.Html(tag="th", children=[column]) for column in head])]),
        rv.Html(tag="tbody", children=rows),
    ])


def _reviewed_note(key: str, summary) -> str:
    return t(key, reviewed=format_number(summary.n_reviewed, 0),
             total=format_number(summary.n_alerts, 0))


def _delta_of(history, summary, measure: str):
    """``(text, critical)`` comparing the shown set's confirmed loss with the
    period before it, or None when there is no earlier period to compare
    with. More loss is worse, so a rise is the critical one."""
    entry = next((e for e in history if e[2].set_id == summary.set_id), None)
    if entry is None:
        return None
    before = overview_data.previous_in(history, entry[0].id)
    if before is None:
        return None
    now = overview_data.status_value(summary, overview_data.CONFIRMED_CODE, measure)
    was = overview_data.status_value(before[2], overview_data.CONFIRMED_CODE, measure)
    label = period_label(before[0])
    if now == was:
        return t("overview_tile.delta_same", period=label), False
    key = "overview_tile.delta_up" if now > was else "overview_tile.delta_down"
    return t(key, value=charts.format_value(abs(now - was), measure), period=label), now > was


def _kpi_strip(summary, history, figures, measure: str):
    """Confirmed loss (the headline, with its trend), the set's total, how
    much of it has been reviewed, and the single largest alert."""
    confirmed = overview_data.status_value(summary, overview_data.CONFIRMED_CODE, measure)
    with solara.Row(style="gap: 10px; flex-wrap: wrap; align-items: stretch;"):
        _kpi(t("overview_tile.kpi_confirmed"), charts.format_value(confirmed, measure), hero=True,
             delta=_delta_of(history, summary, measure),
             spark=figures.get("spark") if len(history) > 1 else None)
        _kpi(t("overview_tile.kpi_detected"),
             charts.format_value(overview_data.total_of(summary, measure), measure))
        _kpi(t("overview_tile.kpi_reviewed"),
             _share(summary.n_reviewed / summary.n_alerts if summary.n_alerts else 0.0),
             support=_reviewed_note("overview_tile.kpi_reviewed_of", summary))
        # Always an area: "the largest alert" means the largest polygon, and
        # in the Alerts measure every alert would be worth the same 1.
        largest = summary.largest
        value, support = "—", ""
        if largest is not None:
            alert_id, area, admin, when = largest
            where = admin or t("overview_tile.admin_unattributed")
            value = charts.format_value(area, charts.MEASURE_AREA)
            support = t("overview_tile.kpi_largest_at", id=format_number(alert_id, 0),
                        where=where if when is None else f"{where} · {format_date(when)}")
        _kpi(t("overview_tile.kpi_largest"), value, support=support)


def _left_column(summary, history, figures, measure: str, theme: dict, admin_level: str, on_admin_level):
    with solara.Div(style=_COLUMN):
        with _card(t("overview_tile.card_status")):
            _figure_or_numbers(figures.get("status"),
                               [(label, charts.format_value(value, measure))
                                for _, _, label, value in charts.status_rows(summary, measure=measure)])
            _chips(summary, measure, theme)
        with _card(t("overview_tile.card_history")):
            if len(history) < 2:
                solara.Text(t("overview_tile.history_one_period"), style=_NOTE)
            else:
                _figure_or_numbers(figures.get("history"),
                                   [(period_label(period),
                                     charts.format_value(overview_data.total_of(item, measure), measure))
                                    for period, _, item in history])
        with _card(t("overview_tile.card_sizes")):
            _figure_or_numbers(figures.get("sizes"), [])
        with _card(t("overview_tile.card_admin"),
                   _reviewed_note("overview_tile.admin_note", summary)):
            if not summary.by_admin:
                solara.Text(t("overview_tile.admin_empty"), style=_NOTE)
            else:
                _admin_level_picker(admin_level, on_admin_level)
                _admin_table(summary, admin_level)


def _period_id_label(periods, period_id: str) -> str:
    """A period by its id; a review copy may hold earlier periods the project
    does not, whose ids are ``<start>_<end>``."""
    if period_id in periods:
        return period_label(periods[period_id])
    start, _, end = period_id.partition("_")
    return t("steps.period.range", start=start, end=end) if end else period_id


def _growth_range(row, periods) -> str:
    first, last = (_period_id_label(periods, pid) for pid in (row.first_period, row.last_period))
    return first if row.first_period == row.last_period else t("overview_tile.growth_range", first=first, last=last)


def _growth_card(growth, measure: str, periods, on_review, loaded: bool):
    """The growing clearings of the set (spec 2026-10-20 §7): how many growth
    alerts it holds, and the histories they belong to, largest first, each
    with the button that opens its alert in Analysis. The headline is the
    measure's; the split under it is always counts (the store holds a growth
    alert's area, not the area of each kind)."""
    n = growth.n_growth + growth.n_growth_small
    headline = (tn("overview_tile.growth_headline_count", n)
                if measure == charts.MEASURE_ALERTS
                else tn("overview_tile.growth_headline", n, ha=format_number(growth.area_ha, 1)))
    with _card(t("overview_tile.card_growth"), t("overview_tile.growth_note")):
        solara.Text(headline, style=_VALUE)
        split = t("overview_tile.growth_split", growth=format_number(growth.n_growth, 0),
                  small=format_number(growth.n_growth_small, 0))
        reviewed = tn("overview_tile.growth_reviewed", growth.n_reviewed)
        solara.Text(f"{split} · {reviewed}", style=_NOTE)
        if not growth.rows:
            return
        head = (t("overview_tile.growth_period"), t("overview_tile.table_alerts"),
                t("overview_tile.growth_confirmed"), t("overview_tile.growth_added"), "")
        rows = [rv.Html(tag="tr", children=[
            rv.Html(tag="td", children=[_growth_range(row, periods)]),
            rv.Html(tag="td", children=[format_number(row.n, 0)]),
            rv.Html(tag="td", children=[format_number(row.confirmed_ha, 1)]),
            rv.Html(tag="td", children=[format_number(row.added_ha, 1)]),
            rv.Html(tag="td", children=[solara.Button(
                t("overview_tile.growth_review"), x_small=True, color="primary", outlined=True,
                disabled=not loaded, on_click=lambda alert_id=row.alert_id: on_review(alert_id))]),
        ]).key(f"growth-{row.alert_id}") for row in growth.rows]
        rv.SimpleTable(dense=True, children=[
            rv.Html(tag="thead", children=[rv.Html(tag="tr", children=[
                rv.Html(tag="th", children=[column]) for column in head])]),
            rv.Html(tag="tbody", children=rows),
        ])


def _right_column(summary, figures, measure: str, questions, growth, periods, on_review, loaded: bool):
    with solara.Div(style=_COLUMN):
        with _card(t("overview_tile.card_numbers")):
            _numbers_table(summary, measure)
        if growth is not None and growth.n_growth + growth.n_growth_small:
            _growth_card(growth, measure, periods, on_review, loaded)
        with _card(t("overview_tile.card_timing"), t("overview_tile.timing_note")):
            _figure_or_numbers(figures.get("timing"), [])
        # One card per question, in questionnaire order (spec D9); none
        # without questions. Reviewed alerts only, like the admin table.
        for q in questions:
            with _card(questions_mod.question_label(q), _reviewed_note("overview_tile.answers_note", summary)):
                if not summary.breakdown(q.key):
                    solara.Text(t("overview_tile.answers_empty"), style=_NOTE)
                else:
                    _figure_or_numbers(
                        figures.get(f"q:{q.key}"),
                        charts.question_rows(summary, q.key, measure=measure,
                                             label_of=lambda code, q=q: questions_mod.label(q, code)))
        with _card(t("overview_tile.card_sources")):
            _figure_or_numbers(figures.get("sources"), [])


def _measure_picker(measure: str, on_measure):
    """One measure for the whole page (spec §9.5): two buttons, the current
    one filled. A segmented control rather than a dropdown, because the
    choice is binary and changes every figure on screen."""
    with solara.Row(style="gap: 4px;"):
        for key, label in ((charts.MEASURE_AREA, t("overview_tile.measure_area")),
                           (charts.MEASURE_ALERTS, t("overview_tile.measure_alerts"))):
            solara.Button(label, small=True, color="primary", outlined=measure != key,
                          on_click=lambda key=key: on_measure(key))


class _Runs:
    """The read counter: only the newest read may publish its numbers."""

    __slots__ = ("current",)

    def __init__(self):
        self.current = 0


#: ``(summary key, summary, history, failure, growth)`` before the first read
#: lands; ``growth`` is None when the History card could not be read.
_NOTHING = (None, None, (), None, None)


@solara.component
def OverviewTile(app_state, map_, layers, dismiss=None):
    """The dashboard of the alert set the selector picks (spec §9.3, §9.5).

    Parameters
    ----------
    app_state : AppState
        The session state: the project (its active set), ``alerts_gdf`` (the
        active set's alerts, loaded by the shell -- the export card and the
        map switches are all that read them) and the navigation reactives.
    map_ : SepalMap
        The single shared map. Unused: this view draws nothing on it (the
        alert layers are the shell's ``_AlertLayers``), and it is passed only
        so the signature matches the other views the shell mounts.
    layers : MapLayers
        The session's map-layer registry. Read, never called: the switches are
        held while their own layer is being built (``layers.inflight``), so a
        flip here cannot race the build another view started.
    dismiss : callable, optional
        Closes this dialog, as its own close button does
        (``navigation.dismiss_dialog``). "Review alerts" closes it before it
        sends the user to Analysis, and a map switch closes it on its way on.
    """
    measure, set_measure = solara.use_state(charts.MEASURE_AREA)
    admin_level, set_admin_level = solara.use_state(ADMIN_COUNTRY)
    read, set_read = solara.use_state(_NOTHING)
    runs = solara.use_memo(_Runs, [])

    p = app_state.project.value
    s = active_set(p)
    # Read in the body, so a theme toggle or a language switch re-renders the
    # view AND re-draws its figures (both are memo dependencies below).
    theme = charts.theme_for(app_state.dark.value)
    locale = current_locale()

    key = overview_data.summary_key(p, s)
    keys = overview_data.history_keys(p)
    # Each answer split by its own question's codes (legacy labels read back
    # to codes). The summary key carries the questions, so a question added or
    # removed starts a new read with a new split.
    qmap = {q.key: q for q in p.questions} if p is not None else {}

    def _split(key, value):
        q = qmap.get(key)
        return questions_mod.codes(q, value) if q is not None else []

    def _start_read():
        if p is None or s is None:
            return
        runs.current += 1
        run, project, alert_set = runs.current, p, s

        def _work():
            # On a worker (parquet): the summary of the set on screen, then one
            # per period for the trend. Only the newest read publishes, so a
            # set switched twice in a row cannot land the first read's numbers.
            try:
                summary = overview_data.summary_of(project, alert_set, split_of=_split)
                history = overview_data.history_of(project, split_of=_split)
            except Exception as exc:  # noqa: BLE001 - said in place, where the numbers would be
                logger.exception("could not read the alerts of set %s", alert_set.id)
                if runs.current == run:
                    set_read((key, None, (), error_text(exc), None))
                return
            # The History card reads the other sets' stores too; without it
            # the dashboard is still whole, so a failure here is only logged.
            growth = None
            try:
                alerts = set_store.load_set_alerts(project, alert_set)
                if alerts is not None:
                    growth = growth_index.set_histories(project, alert_set.id, alerts)
            except Exception:  # noqa: BLE001 - one card, not the dashboard
                logger.exception("could not read the history of set %s", alert_set.id)
            if runs.current == run:
                set_read((key, summary, history, None, growth))

        spawn_in_context(_work)

    solara.use_effect(_start_read, [key, keys])

    read_key, summary, history, failure, growth = read
    fresh = summary is not None and read_key == key
    series = tuple((period_label(period), item, item.n_reviewed < item.n_alerts)
                   for period, _, item in history) if fresh else ()
    # ``use_memo`` compares its dependencies by value, so the tuple of
    # questions (pydantic models) is fine, and an edit to a question redraws
    # its chart (a renamed option) and rebuilds the export card.
    questions = tuple(p.questions) if p is not None else ()
    # Six matplotlib figures plus one per question: drawn only when their
    # inputs change, never on the project commit an auto-save makes.
    figures = solara.use_memo(
        lambda: (overview_data.draw_figures(summary, series, measure=measure, theme=theme,
                                            label_of=lambda q, code: questions_mod.label(q, code),
                                            questions=questions) if fresh else {}),
        dependencies=[summary, series, measure, theme["surface"], locale, questions],
    )

    # The export card is built only when its inputs change, so a re-render (a
    # measure click) keeps a write in progress. A review save changes the
    # alerts, which rebuilds it and re-reads which files are out of date.
    gdf = set_store.active_set_alerts(p, app_state.alerts_gdf.value)
    has_alerts = gdf is not None and len(gdf) > 0
    period = p.periods.get(s.period_id) if s is not None else None
    folder = str(set_store.set_folder(p, s)) if s is not None else None
    sources = tuple(s.sources) if s is not None else ()
    start = period.start.isoformat() if period is not None else ""
    end = period.end.isoformat() if period is not None else ""
    store = str(set_store.store_path(p, s)) if s is not None else None
    # The card a rebuild replaces, so the new one starts on the scope the
    # user had picked (a review save rebuilds it with the alerts' new copy).
    last_card = solara.use_ref(None)
    export_card = solara.use_memo(
        lambda: build_export_card(gdf, folder, list(sources), start, end, p.name, questions, store=store,
                                  scope=last_card.current.scope if last_card.current is not None else None,
                                  growth=lambda g: growth_index.with_growth_columns(p, s.id, g))
        if s is not None and has_alerts else None,
        [gdf, folder, sources, start, end, questions, store],
    )
    if export_card is not None:
        last_card.current = export_card

    def _relabel_export():
        if export_card is not None:
            relabel_export_card(export_card)

    # Its labels follow a language switch on the same card (``locale`` read above).
    solara.use_effect(_relabel_export, [export_card, locale])

    def _review_alerts():
        # Analysis is behind this dialog: close it first, or the user would see
        # the dashboard over the step they asked for.
        if dismiss is not None:
            dismiss()
        app_state.current_page_view.value = "analysis_tile"

    def _review_growth(alert_id):
        # Analysis opens on this set's alert of the history; the position is
        # its row in the alerts the session holds.
        position = set_store.position_of(gdf, alert_id)
        if position is None:
            return
        if dismiss is not None:
            dismiss()
        set_store.go_to_alert(app_state, p.name, s.id, alert_id, position)

    def _switch(reactive):
        """Flip a map-layer switch. Turning one ON closes this dialog, because
        the map it just changed is behind it and the user would otherwise see
        nothing happen; turning one off leaves the dialog open, since there is
        nothing to go and look at."""

        def _on(value):
            reactive.set(value)
            if value and dismiss is not None:
                dismiss()

        return _on

    with solara.Column(style="padding: 0 8px 12px; gap: 10px;"):
        with solara.Row(style="gap: 12px; align-items: center; flex-wrap: wrap;"):
            if s is not None:
                with solara.Div(style="flex: 1 1 300px; min-width: 240px;"):
                    SetSelector(app_state=app_state)
                _measure_picker(measure, set_measure)
            rv.Spacer()
            solara.Button(t("overview_tile.review_button"), color="primary", small=True,
                          disabled=s is None or not s.n_alerts, on_click=_review_alerts)
        if s is None:
            solara.Text(t("overview_tile.no_set"), style=_NOTE)
            return
        if has_alerts:
            # The same two booleans Integrate & vectorize switches, under the
            # same labels: one switch in two places must read the same, and the
            # shell's _AlertLayers leaf owns every MapLayers call either way.
            # Only offered with the alerts on hand, because that leaf draws
            # nothing without them and a dead switch would be a lie.
            inflight = layers.inflight.value
            with solara.Row(style="gap: 20px; align-items: center; flex-wrap: wrap;"):
                rv.Switch(label=t("steps.integrate.show_alerts"), v_model=app_state.show_alerts.value,
                          on_v_model=_switch(app_state.show_alerts), dense=True, hide_details=True,
                          disabled=alert_layers.SET_LAYER_KEY in inflight)
                if alert_layers.earlier_of(p, s):
                    rv.Switch(label=t("steps.integrate.show_earlier"),
                              v_model=app_state.show_earlier_sets.value,
                              on_v_model=_switch(app_state.show_earlier_sets), dense=True, hide_details=True,
                              disabled=any(k.startswith(alert_layers.EARLIER_PREFIX) for k in inflight))
        status = set_status(p, paths.project_dir(p.name), s)
        if status is Status.STALE:
            solara.Text(t("steps.overview.stale"), classes=["warning--text"], style=_NOTE)
        elif status is Status.MISSING:
            solara.Text(t("steps.overview.missing"), classes=["warning--text"], style=_NOTE)
        if failure is not None and read_key == key:
            solara.Text(t("overview_tile.read_failed", error=failure),
                        classes=["warning--text"], style=_NOTE)
        elif not fresh:
            solara.Text(t("overview_tile.loading"), style=_NOTE)
        elif not summary.n_alerts:
            solara.Text(t("overview_tile.empty_set"), style=_NOTE)
        else:
            _kpi_strip(summary, history, figures, measure)
            with solara.Row(style="gap: 10px; align-items: stretch; flex-wrap: wrap;"):
                _left_column(summary, history, figures, measure, theme, admin_level, set_admin_level)
                _right_column(summary, figures, measure, questions, growth, p.periods, _review_growth,
                              has_alerts)
        if export_card is not None:
            # A child of a reacton element, so the raw card is part of the
            # rendered tree itself.
            rv.Html(tag="div", children=[export_card])
