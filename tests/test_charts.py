"""The Overview dashboard's figures (spec: Chart layer).

Every chart is a matplotlib figure inlined as an SVG string, so the tests
render each one and read the markup: there is no browser and no snapshot. What
matters is that the markup is self-contained (no global ``<style>`` that would
leak into the page), labelled for a screen reader, free of hard-coded palette
hexes, and deterministic.
"""

import re
from dataclasses import replace

import pandas as pd
import pytest

from deforestation_alerts.scripts import set_summary as ss
from gui.i18n import t
from gui.scripts import charts
from gui.scripts.legend_specs import ALERT_CLASSES, MASK_COLOR, STACK_ORDER, STATUS_CLASSES

FORBIDDEN_HEXES = [c for _, c, _ in ALERT_CLASSES + STATUS_CLASSES] + [MASK_COLOR]


def _frame(**overrides):
    """Eight alerts spread over statuses, weeks, sizes, loss drivers and sources."""
    data = {
        "alert_id": [10, 11, 12, 13, 14, 15, 16, 17],
        "status": ["Confirmed", "Confirmed", "False Positive", "Need revision",
                   "Not reviewed", "Not reviewed", "Confirmed", "False Positive"],
        "area_ha": [12.0, 2.0, 0.25, 1.5, 0.75, 30.0, 4.0, 0.4],
        "alert_date_min": [2026.045, 2026.045, 2026.052, 2026.010,
                           2026.059, 2026.066, 2026.052, 2026.038],
        "alert_sources": ["GLAD-L and RADD", "RADD", "GLAD-L", "GLAD-L",
                          "RADD", "GLAD-L and RADD", "RADD", ""],
        "description": ["logging", "mining", "", "agriculture_expansion",
                        "", "", "logging", ""],
        "admin1": ["Canindeyu", "Canindeyu", "Amambay", "Amambay", "", "", "Alto Parana", ""],
    }
    data.update(overrides)
    return pd.DataFrame(data)


@pytest.fixture
def summary():
    from datetime import date
    return ss.summarize_alerts(_frame(), set_id="feb", period_start=date(2026, 2, 9),
                               answer_columns={"loss_driver": "description"})


@pytest.fixture
def empty():
    return ss.summarize_alerts(pd.DataFrame(), set_id="empty")


@pytest.fixture
def theme():
    return charts.theme_for(False)


def _builders(summary, theme, measure=charts.MEASURE_AREA):
    """Every public chart, bound to one summary -- name -> zero-argument call."""
    return {
        "spark": lambda: charts.chart_spark((1.0, 4.0, 2.0, 9.0), theme=theme),
        "status": lambda: charts.chart_status(summary, measure=measure, theme=theme),
        "trend": lambda: charts.chart_trend(
            (("Jan", summary, False), ("Feb", summary, True)), measure=measure, theme=theme),
        "sizes": lambda: charts.chart_sizes(summary, measure=measure, theme=theme),
        "timing": lambda: charts.chart_timing(summary, measure=measure, theme=theme),
        "question": lambda: charts.chart_question(summary, "loss_driver", measure=measure, theme=theme,
                                                  label_of=str),
        "sources": lambda: charts.chart_sources(summary, measure=measure, theme=theme),
    }


ALL_CHARTS = ["spark", "status", "trend", "sizes", "timing", "question", "sources"]


# --- the module's own contracts -------------------------------------------

def test_the_module_hardcodes_no_palette_colour():
    """Status and alert colours come from legend_specs, never a literal here."""
    text = charts.__file__ and open(charts.__file__, encoding="utf-8").read().lower()
    assert [h for h in FORBIDDEN_HEXES if h in text] == []


def test_the_module_is_solara_free():
    source = open(charts.__file__, encoding="utf-8").read()
    assert "import solara" not in source and "ipyvuetify" not in source


def test_light_and_dark_are_two_chosen_sets_not_a_flip():
    light, dark = charts.theme_for(False), charts.theme_for(True)
    assert set(light) == set(dark)
    assert light["surface"] != dark["surface"]
    assert light["ink"] != dark["ink"]
    assert not any(h in light.values() or h in dark.values() for h in FORBIDDEN_HEXES)


# --- every chart, populated and empty ------------------------------------

@pytest.mark.parametrize("name", ALL_CHARTS)
def test_a_chart_is_a_self_contained_labelled_svg(name, summary, theme):
    svg = _builders(summary, theme)[name]()
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "<style" not in svg and "*{" not in svg  # no global selector escapes
    assert "<?xml" not in svg and "<!DOCTYPE" not in svg and "<metadata" not in svg
    assert 'stroke-linejoin="round"' in svg and 'stroke-linecap="butt"' in svg
    assert 'role="img"' in svg and "viewBox=" in svg
    assert re.search(r'width="\d+(\.\d+)?pt"', svg) is None  # scales to the card
    label = re.search(r'aria-label="([^"]*)"', svg)
    assert label is not None and len(label.group(1)) > 3


@pytest.mark.parametrize("name", ALL_CHARTS)
def test_a_chart_with_nothing_to_draw_still_renders(name, empty, theme):
    svg = _builders(empty, theme)[name]()
    assert svg.startswith("<svg") and 'role="img"' in svg


@pytest.mark.parametrize("name", ALL_CHARTS)
def test_two_renders_of_one_chart_are_identical(name, summary, theme):
    build = _builders(summary, theme)[name]
    assert build() == build()  # the timestamp metadata is stripped


@pytest.mark.parametrize("name", ALL_CHARTS)
def test_dark_mode_paints_the_dark_surface(name, summary):
    dark = charts.theme_for(True)
    assert dark["surface"] in _builders(summary, dark)[name]()


@pytest.mark.parametrize("name", ["status", "trend", "sizes", "timing", "question", "sources"])
def test_the_measure_changes_what_is_drawn(name, summary, theme):
    area = _builders(summary, theme, charts.MEASURE_AREA)[name]()
    count = _builders(summary, theme, charts.MEASURE_ALERTS)[name]()
    assert area != count


# --- status ---------------------------------------------------------------

def test_status_rows_follow_the_cvd_safe_stack_order(summary):
    rows = charts.status_rows(summary, measure=charts.MEASURE_AREA)
    assert tuple(code for code, _, _, _ in rows) == STACK_ORDER


def test_status_rows_carry_the_measure_being_shown(summary):
    area = {c: v for c, _, _, v in charts.status_rows(summary, measure=charts.MEASURE_AREA)}
    count = {c: v for c, _, _, v in charts.status_rows(summary, measure=charts.MEASURE_ALERTS)}
    assert area[1] == pytest.approx(18.0) and count[1] == 3  # three Confirmed, 12+2+4 ha


def test_status_rows_label_every_status(summary):
    rows = charts.status_rows(summary, measure=charts.MEASURE_AREA)
    assert all(label and not label.startswith("legend.") for _, _, label, _ in rows)


def test_the_status_bar_paints_each_status_in_its_own_colour(summary, theme):
    svg = charts.chart_status(summary, measure=charts.MEASURE_AREA, theme=theme)
    for code, colour, _, _ in charts.status_rows(summary, measure=charts.MEASURE_AREA):
        assert colour in svg, f"status {code} missing"


def test_a_status_with_no_area_is_not_drawn(theme):
    only_confirmed = ss.summarize_alerts(_frame(status=["Confirmed"] * 8))
    svg = charts.chart_status(only_confirmed, measure=charts.MEASURE_AREA, theme=theme)
    colours = {code: c for code, c, _, _ in charts.status_rows(only_confirmed, measure=charts.MEASURE_AREA)}
    assert colours[1] in svg
    assert colours[0] not in svg and colours[2] not in svg and colours[3] not in svg


# --- trend ----------------------------------------------------------------

def test_the_trend_hatches_a_period_that_is_still_under_review(summary, theme):
    under = charts.chart_trend((("Jan", summary, False), ("Feb", summary, True)),
                               measure=charts.MEASURE_AREA, theme=theme)
    settled = charts.chart_trend((("Jan", summary, False), ("Feb", summary, False)),
                                 measure=charts.MEASURE_AREA, theme=theme)
    assert "<pattern" in under and "<pattern" not in settled


def test_the_trend_draws_a_single_period_without_complaint(summary, theme):
    svg = charts.chart_trend((("Feb", summary, True),), measure=charts.MEASURE_AREA, theme=theme)
    assert svg.startswith("<svg")


def test_the_trend_of_no_periods_is_an_empty_state(theme):
    svg = charts.chart_trend((), measure=charts.MEASURE_AREA, theme=theme)
    assert svg.startswith("<svg") and 'role="img"' in svg


# --- timing ---------------------------------------------------------------

def test_the_timing_chart_de_emphasises_weeks_before_the_period(summary, theme):
    svg = charts.chart_timing(summary, measure=charts.MEASURE_AREA, theme=theme)
    assert any(not w[3] for w in summary.by_week), "fixture should straddle the period start"
    assert theme["lookback"] in svg


def test_without_a_period_start_no_week_is_de_emphasised(theme):
    whole = ss.summarize_alerts(_frame())  # no period_start: every week counts
    svg = charts.chart_timing(whole, measure=charts.MEASURE_AREA, theme=theme)
    assert all(w[3] for w in whole.by_week)
    assert theme["lookback"] not in svg


# --- question answers and sources -----------------------------------------

def test_the_tail_of_a_long_ranking_folds_into_one_other_row():
    rows = tuple((f"d{i}", float(10 - i), 1) for i in range(9))
    folded = charts._fold_tail(rows, 5)
    assert len(folded) == 6
    assert [k for k, _, _ in folded[:5]] == ["d0", "d1", "d2", "d3", "d4"]
    assert folded[-1][0] == charts.OTHER_KEY
    assert folded[-1][1] == pytest.approx(sum(v for _, v, _ in rows[5:]))
    assert folded[-1][2] == 4


def test_a_short_ranking_is_left_alone():
    rows = (("a", 2.0, 1), ("b", 1.0, 1))
    assert charts._fold_tail(rows, 5) == rows


def test_a_question_chart_asks_the_caller_for_every_label_but_other(summary, theme):
    seen = []
    charts.chart_question(summary, "loss_driver", measure=charts.MEASURE_AREA, theme=theme,
                          label_of=lambda code: seen.append(code) or f"D-{code}")
    assert seen and charts.OTHER_KEY not in seen


def test_a_question_chart_draws_its_own_breakdown_only(theme):
    frame = _frame(q_road=["yes", "no", "", "yes", "", "", "no", ""])
    summary = ss.summarize_alerts(frame, answer_columns={"loss_driver": "description", "road": "q_road"})
    aria = _aria(charts.chart_question(summary, "road", measure=charts.MEASURE_ALERTS, theme=theme,
                                       label_of=lambda code: f"R-{code}"))
    assert "R-yes" in aria and "R-no" in aria and "R-logging" not in aria


def test_a_question_that_was_not_summarised_draws_the_empty_chart(summary, theme):
    svg = charts.chart_question(summary, "road", measure=charts.MEASURE_AREA, theme=theme, label_of=str)
    assert svg.startswith("<svg") and t("overview_tile.chart_no_data") in svg


def _aria(svg) -> str:
    return re.search(r'aria-label="([^"]*)"', svg).group(1)


def test_a_category_whose_own_name_is_other_absorbs_the_folded_tail(empty, theme):
    """Two bars both reading "Other" -- the driver code of that name and the
    folded tail -- would misrank the chart and read as a duplicate."""
    rows = tuple((f"d{i}", float(9 - i), 1) for i in range(8))
    summary = replace(empty, by_question=(("loss_driver", rows),))
    svg = charts.chart_question(summary, "loss_driver", measure=charts.MEASURE_AREA, theme=theme,
                                label_of=lambda code: "Other" if code == "d0" else code)
    aria = _aria(svg)
    assert aria.count("Other") == 1
    assert "Other: 18" in aria  # its own 9 ha plus the 9 ha folded in


def test_two_names_clipped_to_the_same_text_become_one_bar():
    pairs = [("Agriculture\u2026", 4.0, False), ("Agriculture\u2026", 3.0, False), ("Fire", 6.0, False)]
    assert charts._merge_by_label(pairs) == (("Agriculture\u2026", 7.0), ("Fire", 6.0))


def test_the_folded_row_always_sits_last_however_big_it_is():
    pairs = [("Logging", 2.0, False), ("Other", 99.0, True)]
    assert charts._merge_by_label(pairs) == (("Logging", 2.0), ("Other", 99.0))


def test_the_unanswered_sentinel_is_named_not_passed_to_the_caller(summary, theme):
    assert any(code == ss.UNKNOWN_DRIVER for code, _, _ in summary.breakdown("loss_driver"))
    svg = charts.chart_question(summary, "loss_driver", measure=charts.MEASURE_AREA, theme=theme,
                                label_of=lambda code: f"D-{code}")
    aria = _aria(svg)
    assert f"D-{ss.UNKNOWN_DRIVER}" not in aria
    assert t("overview_tile.answer_unknown") == "No answer"
    assert t("overview_tile.answer_unknown") in aria


def test_the_status_bar_writes_no_text_on_a_coloured_fill(summary, theme):
    """Neither ink clears 4.5:1 on every status step, so the chips carry the
    numbers instead -- see the module docstring."""
    svg = charts.chart_status(summary, measure=charts.MEASURE_AREA, theme=theme)
    assert re.search(r'id="text_\d+"', svg) is None  # how the SVG marks a text artist
    assert re.search(r'id="text_\d+"', charts.chart_sizes(summary, measure=charts.MEASURE_AREA,
                                                          theme=theme)) is not None


def test_sources_draws_a_row_for_alerts_with_no_recorded_source(summary, theme):
    assert any(key == ss.UNKNOWN_SOURCE for key, _, _ in summary.by_source)
    assert charts.chart_sources(summary, measure=charts.MEASURE_AREA, theme=theme).startswith("<svg")


# --- the sparkline --------------------------------------------------------

def test_the_sparkline_is_small_and_needs_no_axes(theme):
    svg = charts.chart_spark((1.0, 4.0, 2.0, 9.0), theme=theme)
    assert 'viewBox="0 0 120 30"' in svg


def test_a_sparkline_of_one_point_renders(theme):
    assert charts.chart_spark((3.0,), theme=theme).startswith("<svg")


def test_a_sparkline_of_nothing_renders(theme):
    assert charts.chart_spark((), theme=theme).startswith("<svg")
