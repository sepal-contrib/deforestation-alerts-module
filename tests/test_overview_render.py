"""The Overview dashboard on the alert set the selector picks (spec §9.2, §9.3,
§9.5), rendered.

``OverviewTile`` is rendered with ``reacton.render`` against a real
``AppState``/``Project``, a real ``SepalMap(gee=False)`` and a real
``MapLayers``. What it shows comes off the set's **store**, not off
``app_state.alerts_gdf``: the read is a projected parquet read on a daemon
thread, so every test here starts it, joins the thread (``settle``) and then
reads the rendered numbers. ``alerts_gdf`` still drives the export card alone.

The view reads; it draws nothing on the map -- the alert layers, the earlier
sets and the click-to-review popup are the shell's, and
tests/test_alert_layers.py covers them.

No Earth Engine, no browser.
"""

import threading
from datetime import date, datetime
from types import SimpleNamespace

import geopandas as gpd
import ipyvuetify as v
import pytest
import reacton
import solara
from pysepal.i18n import set_locale
from pysepal.mapping import SepalMap
from pysepal.solara.notifications import NotificationProvider
from shapely.geometry import box

from deforestation_alerts.engine.fingerprint import (
    fingerprint, harm_key, harmonized_inputs, raw_alert_inputs, raw_key, set_inputs,
)
from deforestation_alerts.project.models import (
    AlertSet,
    AlertSetParams,
    Artifact,
    Period,
    Project,
    Question,
    QuestionOption,
    add_predefined_source,
)
from deforestation_alerts.project.models import set_id as make_set_id
from gui.i18n import t
from gui.scripts import alert_layers, charts, overview_data
from gui.scripts import questions as qs
from gui.scripts import set_store
from gui.scripts.map_layers import MapLayers
from gui.store.workflow_steps import active_set
from gui.scripts.solara_threads import spawn_in_context
from gui.store.state_manager import AppState
from gui.tile import overview_tile
from gui.tile.overview_export import ExportCard

JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
FEB = Period(id="2026-02-01_2026-02-28", start=date(2026, 2, 1), end=date(2026, 2, 28))
SET_DIR = f"periods/{JAN.id}/sets/glad-l"


def _alerts(set_id=None):
    """Four alerts, one per status, 4.5 ha in all and 1.5 ha of it confirmed.
    ``set_id``: the column the engine writes (integrate.py), naming the set the
    alerts belong to."""
    gdf = gpd.GeoDataFrame(
        {"alert_id": [0, 1, 2, 3],
         "status": ["Confirmed", "False Positive", "Need revision", "Not reviewed"],
         "area_ha": [1.5, 0.25, 2.0, 0.75]},
        geometry=[box(0, 0, 0.01, 0.01), box(1, 1, 1.01, 1.01), box(2, 2, 2.01, 2.01), box(3, 3, 3.01, 3.01)],
        crs="EPSG:4326",
    )
    if set_id is not None:
        gdf["set_id"] = set_id
    return gdf


def _february_alerts():
    """February's two alerts: 3.0 ha confirmed (twice January's) and one not
    reviewed, so a trend, a delta and a set switch are all recognisable."""
    return gpd.GeoDataFrame(
        {"alert_id": [10, 11], "status": ["Confirmed", "Not reviewed"], "area_ha": [3.0, 0.5]},
        geometry=[box(0, 0, 0.01, 0.01), box(1, 1, 1.01, 1.01)], crs="EPSG:4326",
    )


def _record_inputs(p, period, now):
    """The raw + harmonized artifacts of ``period``, with their real
    fingerprints: ``set_status`` compares those with their current inputs too.

    Raw first, and only then harmonized: a harmonized artifact's inputs name
    the raw one that is already recorded.
    """
    key = raw_key(period.id, "GLAD-L")
    p.artifacts[key] = Artifact(key=key, kind="raw_alert", rel_path="r.tif", inputs={},
                                fingerprint=fingerprint(raw_alert_inputs(p, "GLAD-L", period)),
                                content_id=f"{period.id}-r", created_at=now)
    key = harm_key(period.id, "GLAD-L")
    p.artifacts[key] = Artifact(key=key, kind="harmonized", rel_path="h.tif", inputs={},
                                fingerprint=fingerprint(harmonized_inputs(p, "GLAD-L", period)),
                                content_id=f"{period.id}-h", created_at=now)


def _add_set(p, period, now, *, n_alerts, n_reviewed):
    params = AlertSetParams()
    rel = f"periods/{period.id}/sets/glad-l"
    s = AlertSet(id=make_set_id(period.id, ["GLAD-L"]), period_id=period.id, sources=["GLAD-L"],
                 digit_map={0: "GLAD-L"}, params=params, store_rel_path=f"{rel}/alert_db.parquet",
                 combined_rel_path=f"{rel}/c.tif", footprint_rel_path=f"{rel}/footprint.tif",
                 n_alerts=n_alerts, n_reviewed=n_reviewed,
                 fingerprint=fingerprint(set_inputs(p, period, ["GLAD-L"], params)), created_at=now)
    p.alert_sets[s.id] = s
    return s


def _write_store(tmp_path, name, s, gdf):
    store = tmp_path / name / s.store_rel_path
    store.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(store)
    return store


def _project(tmp_path, name="d", *, current=True):
    """A project with one GLAD-L set for January, its store on disk.

    ``current``: its harmonized input is recorded, so the set's fingerprint
    matches (Status.CURRENT); otherwise it is out of date (Status.STALE).
    """
    now = datetime.now()
    p = Project(name=name, created_at=now, updated_at=now)
    add_predefined_source(p, "GLAD-L")
    p.periods[JAN.id] = JAN
    p.active_period = JAN.id
    _record_inputs(p, JAN, now)
    s = _add_set(p, JAN, now, n_alerts=4, n_reviewed=3)
    p.active_set = s.id
    if not current:
        p.artifacts[harm_key(JAN.id, "GLAD-L")].content_id = "h2"  # re-harmonized since the set was built
    _write_store(tmp_path, name, s, _alerts())
    return p


def _with_february(p, tmp_path, name="d", *, active=True):
    """Add February, its own set and store, and (by default) select it: the
    history then has two periods and the shown set has one before it."""
    now = datetime.now()
    p.periods[FEB.id] = FEB
    p.active_period = FEB.id
    _record_inputs(p, FEB, now)
    s = _add_set(p, FEB, now, n_alerts=2, n_reviewed=1)
    _write_store(tmp_path, name, s, _february_alerts())
    if active:
        p.active_set = s.id
    return s


def _state(p, gdf):
    state = AppState()
    state.new_project_state(p)
    state.alerts_gdf.set(gdf)
    return state


@pytest.fixture
def ov(tmp_path, monkeypatch):
    """``ov.render(state)`` -> the rendered dashboard; ``ov.settle()`` waits for
    its store read, which runs on a daemon thread as it does in the app (and
    re-renders from that thread, so a failure there is re-raised here)."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    overview_data.forget_summaries()  # a process-global memo, like app_state
    threads, errors = [], []

    def _spawn(target, args=(), **kwargs):
        def _guarded(*a):
            try:
                target(*a)
            except BaseException as exc:  # noqa: BLE001 - re-raised out of settle()
                errors.append(exc)
                raise

        thread = spawn_in_context(_guarded, args, **kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(overview_tile, "spawn_in_context", _spawn)

    def settle():
        for _ in range(100):
            alive = [th for th in threads if th.is_alive()]
            if not alive:
                break
            for th in alive:
                th.join(timeout=0.1)
        else:
            raise AssertionError("a summary read never finished")
        if errors:
            raise errors[0]

    def join_last():
        """Wait for the newest read alone -- a held one stays held."""
        threads[-1].join(timeout=10)
        assert not threads[-1].is_alive(), "the newest summary read never finished"

    def render(state, dismiss=None, wait=True):
        map_ = SepalMap(gee=False)
        layers = MapLayers(map_)

        @solara.component
        def Harness():
            NotificationProvider()  # the export worker resolves its notifier from the kernel
            overview_tile.OverviewTile(app_state=state, map_=map_, layers=layers, dismiss=dismiss)

        _, rc = reacton.render(Harness(), handle_error=False)
        if wait:
            settle()
        return map_, layers, rc

    yield SimpleNamespace(render=render, settle=settle, join_last=join_last, root=tmp_path)
    overview_data.forget_summaries()


def _strings(widget):
    out = []
    for child in getattr(widget, "children", None) or []:
        out.extend([child] if isinstance(child, str) else _strings(child))
    return out


def _texts(rc):
    return _strings(rc.container)


def _table(rc, first_header):
    """``(header, rows)`` of the table whose first column is ``first_header``;
    every cell as the text a reader sees."""
    for table in rc.find(v.SimpleTable).widgets:
        thead, tbody = table.children
        head = [" ".join(_strings(th)).strip() for th in thead.children[0].children]
        if head and head[0] == first_header:
            return head, [[" ".join(_strings(td)).strip() for td in row.children] for row in tbody.children]
    raise AssertionError(f"no table headed {first_header!r} in {_texts(rc)}")


def _numbers(rc):
    """The numbers table as ``{status: [alerts, area, share, median]}``."""
    _, rows = _table(rc, t("overview_tile.table_status"))
    return {row[0]: row[1:] for row in rows}


def _kpi(rc, label):
    """The one KPI card titled ``label``, as its strings."""
    [card] = [c for c in rc.find(v.Card).widgets if label in _strings(c)]
    return _strings(card)


def _btn(rc, label):
    return next(b for b in rc.find(v.Btn).widgets if label in _strings(b))


def _export_cards(rc):
    return [w for w in rc.find(v.Card).widgets if isinstance(w, ExportCard)]


def _figures(rc):
    """The inline SVG of every figure on the page."""
    return [w.unsafe_innerHTML for w in rc.find(solara.widgets.HTML).widgets
            if "<svg" in (w.unsafe_innerHTML or "")]


# ---------------------------------------------------------------------------
# The numbers
# ---------------------------------------------------------------------------
def test_the_numbers_table_counts_every_status_and_totals_them(ov, tmp_path):
    """The table view of the whole page (spec §9.5): both measures on every
    row, the share in the measure on screen, and Confirmed first -- the
    palette's stacking order, which keeps it away from False positive."""
    map_, layers, rc = ov.render(_state(_project(tmp_path), _alerts()))
    try:
        head, rows = _table(rc, t("overview_tile.table_status"))
        assert head == [t("overview_tile.table_status"), t("overview_tile.table_alerts"),
                        t("overview_tile.table_area"), t("overview_tile.table_share"),
                        t("overview_tile.table_median")]
        assert [row[0] for row in rows] == [
            t("legend.status.confirmed"), t("legend.status.needs_revision"),
            t("legend.status.false_positive"), t("legend.status.not_reviewed"),
            t("overview_tile.table_total"),
        ]
        assert _numbers(rc)[t("legend.status.confirmed")] == ["1", "1.5", "33%", "1.50"]
        assert _numbers(rc)[t("overview_tile.table_total")] == ["4", "4.5", "100%", "—"]
    finally:
        rc.close()


def test_the_kpi_strip_leads_with_the_confirmed_loss(ov, tmp_path):
    """Confirmed loss is the headline number, the set's own total stands beside
    it, and the largest alert is always an area -- in the Alerts measure every
    alert would be worth the same 1."""
    map_, layers, rc = ov.render(_state(_project(tmp_path), _alerts()))
    try:
        assert "1.5" in _kpi(rc, t("overview_tile.kpi_confirmed"))
        assert "4.5" in _kpi(rc, t("overview_tile.kpi_detected"))
        reviewed = _kpi(rc, t("overview_tile.kpi_reviewed"))
        assert "75%" in reviewed
        assert t("overview_tile.kpi_reviewed_of", reviewed="3", total="4") in reviewed
        largest = _kpi(rc, t("overview_tile.kpi_largest"))
        assert "2.0" in largest  # the 2.0 ha alert, whatever its status
        assert t("overview_tile.kpi_largest_at", id="2",
                 where=t("overview_tile.admin_unattributed")) in largest
    finally:
        rc.close()


def test_the_measure_buttons_move_the_whole_page_to_alert_counts(ov, tmp_path):
    """One measure for the whole page (spec §9.5): the KPI, the share and every
    figure follow the same click, so no two numbers on screen can disagree."""
    map_, layers, rc = ov.render(_state(_project(tmp_path), _alerts()))
    try:
        assert "1.5" in _kpi(rc, t("overview_tile.kpi_confirmed"))
        assert _numbers(rc)[t("legend.status.confirmed")][2] == "33%"  # 1.5 of 4.5 ha
        area_figures = _figures(rc)

        _btn(rc, t("overview_tile.measure_alerts")).fire_event("click", None)

        confirmed = _kpi(rc, t("overview_tile.kpi_confirmed"))
        assert "1" in confirmed and "1.5" not in confirmed
        assert _numbers(rc)[t("legend.status.confirmed")][2] == "25%"  # 1 alert of 4
        assert _numbers(rc)[t("legend.status.confirmed")][1] == "1.5"  # the area column stays
        assert _figures(rc) != area_figures
    finally:
        rc.close()


def test_the_cards_follow_a_language_switch(ov, tmp_path):
    map_, layers, rc = ov.render(_state(_project(tmp_path), _alerts()))
    set_locale("en")
    try:
        assert t("overview_tile.card_status") in _texts(rc)
        set_locale("fr")
        assert t("overview_tile.card_status") == "Par état d'examen"
        assert t("overview_tile.card_status") in _texts(rc)
        assert _numbers(rc)[t("legend.status.confirmed")][0] == "1"  # relabelled, not recounted
    finally:
        set_locale("en")
        rc.close()


def test_the_admin_card_switches_between_country_and_level_1(ov, tmp_path):
    """Country by default; level 1 names its country, since GAUL level-1
    names repeat across countries. Reviewed alerts only, either way."""
    p = _project(tmp_path)
    gdf = _alerts()
    gdf["admin1"] = ["Paraguay", "Paraguay,", "Paraguay", "Brazil"]  # a pre-fix trailing comma
    gdf["admin2"] = ["Canindeyú", "Amambay", "Canindeyú", "Acre"]
    _write_store(tmp_path, "d", p.alert_sets[p.active_set], gdf)
    map_, layers, rc = ov.render(_state(p, gdf))
    try:
        head, rows = _table(rc, t("overview_tile.admin_level_country"))
        assert [row[0] for row in rows] == ["Paraguay"]  # Brazil's alert is not reviewed
        assert rows[0][1:] == ["3.8", "1.5", "3"]

        _btn(rc, t("overview_tile.admin_level_1")).fire_event("click", None)

        head, rows = _table(rc, t("overview_tile.admin_level_1"))
        assert {row[0]: row[1:] for row in rows} == {
            "Paraguay › Canindeyú": ["3.5", "1.5", "2"], "Paraguay › Amambay": ["0.2", "0.0", "1"]}
    finally:
        rc.close()


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
def test_one_monitoring_period_asks_for_a_second_instead_of_charting_one_column(ov, tmp_path):
    map_, layers, rc = ov.render(_state(_project(tmp_path), _alerts()))
    try:
        assert t("overview_tile.history_one_period") in _texts(rc)
        assert t("overview_tile.delta_same", period="") not in _texts(rc)
    finally:
        rc.close()


def test_a_second_period_charts_the_trend_and_compares_the_headline_with_it(ov, tmp_path):
    """February confirms 3.0 ha where January confirmed 1.5: the headline KPI
    says so in words, and the arrow only emphasises what the words carry."""
    p = _project(tmp_path)
    _with_february(p, tmp_path)
    map_, layers, rc = ov.render(_state(p, None))
    try:
        assert t("overview_tile.history_one_period") not in _texts(rc)
        assert "3.0" in _kpi(rc, t("overview_tile.kpi_confirmed"))
        assert t("overview_tile.delta_up", value="1.5",
                 period=t("steps.period.range", start="2026-01-01", end="2026-01-31")) in _texts(rc)
    finally:
        rc.close()


def test_a_period_whose_store_is_gone_is_left_out_of_the_trend(ov, tmp_path):
    """One deleted file must not cost the whole history: January drops out and
    February -- the set on screen -- is still summarised."""
    p = _project(tmp_path)
    _with_february(p, tmp_path)
    (tmp_path / "d" / p.alert_sets[make_set_id(JAN.id, ["GLAD-L"])].store_rel_path).unlink()
    map_, layers, rc = ov.render(_state(p, None))
    try:
        assert _numbers(rc)[t("overview_tile.table_total")][0] == "2"  # February's own alerts
        assert t("overview_tile.history_one_period") in _texts(rc)
        assert t("overview_tile.read_failed", error="") not in _texts(rc)
    finally:
        rc.close()


# ---------------------------------------------------------------------------
# What a read can do instead of landing numbers
# ---------------------------------------------------------------------------
def test_a_set_switched_twice_keeps_the_last_sets_numbers(ov, tmp_path, monkeypatch):
    """Review focus: a set switched while its predecessor is still being read.
    The first read is held until after the switch; only the newest read may
    publish, or the dashboard would settle on the set the user left."""
    p = _project(tmp_path)
    february = _with_february(p, tmp_path, active=False)  # January stays selected
    gate = threading.Event()
    real = overview_data.summary_of

    def _held(project, alert_set, **kwargs):
        if alert_set.period_id == JAN.id:
            assert gate.wait(5), "the first read was never released"
        return real(project, alert_set, **kwargs)

    monkeypatch.setattr(overview_data, "summary_of", _held)
    state = _state(p, None)
    map_, layers, rc = ov.render(state, wait=False)  # the first read is held
    try:
        assert t("overview_tile.loading") in _texts(rc)
        state.update_project("d", lambda x: setattr(x, "active_set", february.id))
        ov.join_last()  # February's read lands while January's is still held
        assert _numbers(rc)[t("overview_tile.table_total")][0] == "2"  # February's
        gate.set()  # January's read finishes LAST and must not publish
        ov.settle()
        assert _numbers(rc)[t("overview_tile.table_total")][0] == "2"
    finally:
        gate.set()
        rc.close()


def test_a_set_whose_store_is_missing_says_so_where_the_numbers_would_be(ov, tmp_path):
    """Review focus 4: the set's store was deleted or moved. The set label still
    counts the manifest's alerts, so without this the Overview would read
    "4 alerts" over an empty page with nothing to say why."""
    p = _project(tmp_path)
    (tmp_path / "d" / p.alert_sets[p.active_set].store_rel_path).unlink()
    map_, layers, rc = ov.render(_state(p, None))
    try:
        texts = _texts(rc)
        assert t("steps.overview.missing") in texts
        said = t("overview_tile.read_failed", error="|").split("|")[0]
        assert any(text.startswith(said) for text in texts), texts
        assert rc.find(v.SimpleTable).widgets == []  # no table over numbers nobody has
    finally:
        rc.close()


def test_an_empty_store_says_the_set_has_no_alerts(ov, tmp_path):
    p = _project(tmp_path)
    _write_store(tmp_path, "d", p.alert_sets[p.active_set], _alerts().iloc[0:0])
    map_, layers, rc = ov.render(_state(p, None))
    try:
        assert t("overview_tile.empty_set") in _texts(rc)
        assert rc.find(v.SimpleTable).widgets == []
    finally:
        rc.close()


def test_a_figure_that_cannot_be_drawn_falls_back_to_its_own_numbers(ov, tmp_path, monkeypatch):
    """Review focus: matplotlib raises on one figure. That card lists its
    numbers as text and every other card still draws."""
    def _boom(*args, **kwargs):
        raise RuntimeError("no renderer")

    monkeypatch.setattr(charts, "chart_status", _boom)
    map_, layers, rc = ov.render(_state(_project(tmp_path), _alerts()))
    try:
        texts = _texts(rc)
        assert t("overview_tile.chart_failed") in texts
        assert f"{t('legend.status.confirmed')} — 1.5" in texts
        assert len(_figures(rc)) >= 4  # sizes, timing, the Loss driver answers, sources still drawn
    finally:
        rc.close()


def test_an_out_of_date_set_shows_the_warning_banner(ov, tmp_path):
    map_, layers, rc = ov.render(_state(_project(tmp_path, current=False), _alerts()))
    try:
        assert t("steps.overview.stale") in _texts(rc)
        assert t("steps.overview.missing") not in _texts(rc)
        assert _numbers(rc)[t("overview_tile.table_total")][0] == "4"  # still summarised
    finally:
        rc.close()


# ---------------------------------------------------------------------------
# One card per question (spec D9)
# ---------------------------------------------------------------------------
ROAD = Question(key="road", label="Road access",
                options=[QuestionOption(code="yes", label="Yes"), QuestionOption(code="no", label="No")])


def _answer_notes(rc):
    """How many answer cards the page holds: each carries the reviewed note."""
    note = t("overview_tile.answers_note", reviewed="3", total="4")
    return _texts(rc).count(note)


def _answered_alerts():
    """``_alerts`` with a Loss driver answer and a Road access answer per alert."""
    gdf = _alerts()
    gdf["description"] = ["logging", "", "mining", ""]
    gdf["q_road"] = ["yes", "", "no", "yes"]
    return gdf


def test_each_question_gets_its_own_card_in_questionnaire_order(ov, tmp_path):
    p = _project(tmp_path)
    p.questions.append(ROAD)
    _write_store(tmp_path, "d", p.alert_sets[p.active_set], _answered_alerts())
    map_, layers, rc = ov.render(_state(p, None))
    try:
        texts = _texts(rc)
        loss, road = qs.question_label(p.questions[0]), "Road access"
        assert loss in texts and road in texts
        assert texts.index(loss) < texts.index(road)
        assert _answer_notes(rc) == 2
        assert any("Yes: " in f and "No: " in f for f in _figures(rc))  # the road chart, labelled
        assert any(t("analysis_tile.questionarie.loss_driver3") in f for f in _figures(rc))  # "Logging"
    finally:
        rc.close()


def test_a_question_chart_that_cannot_be_drawn_lists_its_answers(ov, tmp_path, monkeypatch):
    """The card says its numbers follow, so it must show them: one row per
    answer, labelled through the question and formatted for the measure."""
    def _boom(*args, **kwargs):
        raise RuntimeError("no renderer")

    monkeypatch.setattr(charts, "chart_question", _boom)
    p = _project(tmp_path)
    p.questions.append(ROAD)
    gdf = _answered_alerts()
    _write_store(tmp_path, "d", p.alert_sets[p.active_set], gdf)
    map_, layers, rc = ov.render(_state(p, None))
    try:
        texts = _texts(rc)
        assert texts.count(t("overview_tile.chart_failed")) == 2  # both question cards
        rows = [x for x in texts if " — " in x]
        assert rows == ["Mining — 2.0", "Logging — 1.5", "No answer — 0.2",  # Loss driver, by area (ha)
                        "No — 2.0", "Yes — 1.5", "No answer — 0.2"]  # Road access
    finally:
        rc.close()


def test_a_project_without_questions_shows_no_answer_card(ov, tmp_path):
    """Spec D9: no questions means no cards -- and the rest of the page stands."""
    p = _project(tmp_path)
    p.questions.clear()
    map_, layers, rc = ov.render(_state(p, None))
    try:
        assert _answer_notes(rc) == 0
        assert t("overview_tile.answers_empty") not in _texts(rc)
        assert _numbers(rc)[t("overview_tile.table_total")][0] == "4"
    finally:
        rc.close()


def test_a_question_nobody_has_answered_yet_says_so_in_its_card(ov, tmp_path):
    p = _project(tmp_path)
    p.alert_sets[p.active_set].n_reviewed = 0
    unreviewed = _alerts()
    unreviewed["status"] = "Not reviewed"
    _write_store(tmp_path, "d", p.alert_sets[p.active_set], unreviewed)
    map_, layers, rc = ov.render(_state(p, None))
    try:
        assert t("overview_tile.answers_empty") in _texts(rc)
        assert t("overview_tile.answers_empty") == "No reviewed alert answered this yet."
    finally:
        rc.close()


def test_a_question_added_to_an_old_store_shows_its_card_unanswered(ov, tmp_path):
    """Review focus 5: the store has no ``q_road`` column yet. The card reads
    the reviewed alerts as unanswered instead of failing the page."""
    p = _project(tmp_path)  # _alerts(): neither description nor q_road
    state = _state(p, None)
    map_, layers, rc = ov.render(state)
    try:
        assert _answer_notes(rc) == 1
        state.update_project("d", lambda x: x.questions.append(ROAD))
        ov.settle()
        assert "Road access" in _texts(rc)
        assert _answer_notes(rc) == 2
        assert t("overview_tile.read_failed", error="|").split("|")[0] not in " ".join(_texts(rc))
        assert sum(t("overview_tile.answer_unknown") in f for f in _figures(rc)) == 2
    finally:
        rc.close()


# ---------------------------------------------------------------------------
# Review, the set label and the export card
# ---------------------------------------------------------------------------
def test_review_alerts_closes_the_dialog_before_it_opens_analysis(ov, tmp_path):
    """Analysis is behind this dialog: the user would otherwise see the
    dashboard over the step they asked for."""
    state = _state(_project(tmp_path), _alerts())
    seen = []
    before = state.current_page_view.value
    map_, layers, rc = ov.render(state, dismiss=lambda: seen.append(state.current_page_view.value))
    try:
        _btn(rc, t("overview_tile.review_button")).fire_event("click", None)
        assert seen == [before]  # dismissed first, while Analysis was not open yet
        assert state.current_page_view.value == "analysis_tile"
    finally:
        rc.close()


def test_the_export_card_follows_a_language_switch(ov, tmp_path):
    """The export card is a raw widget built once per set (a write in progress
    must survive a re-render), so a ``set_locale`` relabels
    the same card instead of rebuilding it. The slice-6 walk found its title
    still English in the French Overview."""
    state = _state(_project(tmp_path), _alerts())
    set_locale("en")
    map_, layers, rc = ov.render(state)
    try:
        [card] = _export_cards(rc)
        set_locale("fr")
        cards = [w for w in rc.find(v.Card).widgets if w is card]
        assert cards == [card], "the card was rebuilt"
        assert t("overview_tile.export_label") == "Exporter les alertes"
        assert _export_cards(rc) == [card]
        assert t("overview_tile.export_scope_all") in _strings(card), _strings(card)
        assert t("overview_tile.export_csv") in _strings(card), _strings(card)
    finally:
        set_locale("en")
        rc.close()


def test_the_set_label_and_the_export_card_only_with_alerts(ov, tmp_path):
    p = _project(tmp_path)
    state = _state(p, _alerts())
    map_, layers, rc = ov.render(state)
    try:
        [select] = rc.find(v.Select).widgets
        assert select.v_model == p.active_set
        assert select.items == [{"text": "2026-01-01 → 2026-01-31 · GLAD-L · 4 alerts · 3 reviewed",
                                 "value": p.active_set}]
        assert len(_export_cards(rc)) == 1
        assert t("steps.overview.stale") not in _texts(rc)  # a current set: no banner

        state.alerts_gdf.set(_alerts().iloc[0:0])  # an empty session: nothing to export
        assert _export_cards(rc) == []
        assert _numbers(rc)[t("overview_tile.table_total")][0] == "4"  # the store still counts
        state.alerts_gdf.set(None)
        assert _export_cards(rc) == []

        state.close_project_state()  # no project, no set
        assert _export_cards(rc) == []
        assert rc.find(v.Select).widgets == []
        assert t("overview_tile.no_set") in _texts(rc)
    finally:
        rc.close()


def test_the_export_card_writes_into_the_set_folder_named_by_sources_and_dates(ov, tmp_path, monkeypatch):
    calls = []
    real = overview_tile.build_export_card  # a real card: the tile relabels it on a language switch
    monkeypatch.setattr(overview_tile, "build_export_card",
                        lambda *a, **k: calls.append((a, k)) or real(*a, **k))
    alerts = _alerts()
    state = _state(_project(tmp_path), alerts)
    map_, layers, rc = ov.render(state)
    try:
        p = state.project.value
        store = str(set_store.store_path(p, active_set(p)))
        assert store.startswith(str(tmp_path / "d" / SET_DIR))
        assert calls == [((alerts, str(tmp_path / "d" / SET_DIR), ["GLAD-L"], "2026-01-01", "2026-01-31", "d",
                           tuple(p.questions)), {"store": store, "scope": None})]
        # a re-render with the same inputs keeps the card
        state.update_project("d", lambda p: setattr(p, "look_back_days", 30))
        assert len(calls) == 1
    finally:
        rc.close()


def test_a_rebuilt_export_card_keeps_the_scope_the_user_picked(ov, tmp_path):
    """A review save hands the session a new copy of the alerts, which
    rebuilds the card; the scope the user picked must survive it."""
    alerts = _alerts()
    state = _state(_project(tmp_path), alerts)
    map_, layers, rc = ov.render(state)
    try:
        [card] = _export_cards(rc)
        assert card.scope == "confirmed"
        card.scope_group.v_model = "all"
        state.alerts_gdf.set(alerts.copy())  # what a review save does
        [rebuilt] = _export_cards(rc)
        assert rebuilt is not card and rebuilt.scope == "all"
    finally:
        rc.close()


# ---------------------------------------------------------------------------
# The map switches (the same two Integrate & vectorize offers)
# ---------------------------------------------------------------------------
def _switch(rc, label):
    return next(s for s in rc.find(v.Switch).widgets if s.label == label)


def test_the_map_switches_drive_the_shells_two_booleans(ov, tmp_path):
    """Neither switch calls the map: ``_AlertLayers`` owns every MapLayers call,
    which is what lets the same control sit here and in Integrate."""
    p = _project(tmp_path)
    state = _state(p, _alerts(set_id=p.active_set))
    map_, layers, rc = ov.render(state)
    try:
        assert not state.show_alerts.value
        _switch(rc, t("steps.integrate.show_alerts")).v_model = True
        assert state.show_alerts.value is True
        assert layers.on.value == frozenset()  # no layer registered from here
        _switch(rc, t("steps.integrate.show_alerts")).v_model = False
        assert state.show_alerts.value is False
    finally:
        rc.close()


def test_switching_a_layer_on_closes_the_dialog_and_off_leaves_it_open(ov, tmp_path):
    """The map is behind this dialog: a switch that stays open would look like
    it did nothing. Switching off has nothing to go and look at."""
    p = _project(tmp_path)
    closed = []
    state = _state(p, _alerts(set_id=p.active_set))
    map_, layers, rc = ov.render(state, dismiss=lambda: closed.append(state.show_alerts.value))
    try:
        _switch(rc, t("steps.integrate.show_alerts")).v_model = True
        assert closed == [True]  # dismissed with the layer already asked for
        _switch(rc, t("steps.integrate.show_alerts")).v_model = False
        assert closed == [True]
    finally:
        rc.close()


def test_the_earlier_sets_switch_only_with_an_earlier_set(ov, tmp_path):
    p = _project(tmp_path)
    state = _state(p, _alerts(set_id=p.active_set))
    map_, layers, rc = ov.render(state)
    try:
        assert [s.label for s in rc.find(v.Switch).widgets] == [t("steps.integrate.show_alerts")]
    finally:
        rc.close()

    p = _project(tmp_path, "e")
    february = _with_february(p, tmp_path, "e")
    state = _state(p, _alerts(set_id=february.id))
    map_, layers, rc = ov.render(state)
    try:
        assert [s.label for s in rc.find(v.Switch).widgets] == [
            t("steps.integrate.show_alerts"), t("steps.integrate.show_earlier")]
        _switch(rc, t("steps.integrate.show_earlier")).v_model = True
        assert state.show_earlier_sets.value is True
    finally:
        rc.close()


def test_a_switch_is_held_while_its_own_layer_is_being_built(ov, tmp_path):
    """A flip here must not race a build Integrate started: the shell's
    registry publishes the keys in flight and the switch follows it."""
    p = _project(tmp_path)
    state = _state(p, _alerts(set_id=p.active_set))
    map_, layers, rc = ov.render(state)
    try:
        assert _switch(rc, t("steps.integrate.show_alerts")).disabled is False
        layers.inflight.set(frozenset({alert_layers.SET_LAYER_KEY}))
        assert _switch(rc, t("steps.integrate.show_alerts")).disabled is True
    finally:
        rc.close()


def test_no_map_switch_without_the_alerts_on_hand(ov, tmp_path):
    """``_AlertLayers`` draws nothing without them, so a switch offered here
    would be a lie -- and the alerts on hand are another set's until the
    shell's loader has read the one the selector picked."""
    map_, layers, rc = ov.render(_state(_project(tmp_path), None))
    try:
        assert rc.find(v.Switch).widgets == []
    finally:
        rc.close()
