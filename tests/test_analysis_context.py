"""Block A of the Analysis controls: the queue counter, the tally and the
progress bar. Pure helpers plus one raw-widget block driven by a real
``AppState``, as tests/test_analysis_alert_info.py drives the tile.
"""

from datetime import datetime

import geopandas as gpd
import ipyvuetify as v
import numpy as np
import pytest
from pysepal.i18n import set_locale
from shapely.geometry import box

from deforestation_alerts.project.models import AlertSet, AlertSetParams, Project
from gui.i18n import t
from gui.store.state_manager import AppState
from gui.tile.analysis import context


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    s = AppState()
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    p.alert_sets["x"] = AlertSet(
        id="x", period_id="per", sources=["GLAD-L"], digit_map={0: "GLAD-L"},
        params=AlertSetParams(), store_rel_path="periods/per/sets/glad-l/alert_db.parquet",
        combined_rel_path="periods/per/sets/glad-l/c.tif",
        footprint_rel_path="periods/per/sets/glad-l/footprint.tif",
        fingerprint="f", created_at=datetime.now())
    p.active_set = "x"
    s.new_project_state(p)
    return s


def _alerts(statuses, set_id="x"):
    n = len(statuses)
    return gpd.GeoDataFrame(
        {"status": list(statuses), "alert_id": list(range(n)), "set_id": [set_id] * n,
         "alert_sources": ["GLAD-L"] * n, "alert_date_min": [2026.010] * n,
         "alert_date_max": [2026.020] * n},
        geometry=[box(i, 0, i + 1, 1) for i in range(n)], crs="EPSG:4326")


def test_review_counts_counts_only_real_reviews():
    gdf = _alerts(["Not reviewed", "Confirmed", "False Positive", "Need revision", None, ""])
    assert context.review_counts(gdf) == (6, 3)


def test_review_counts_tolerates_a_nan_status_and_a_missing_column():
    gdf = _alerts(["Confirmed", np.nan])
    assert context.review_counts(gdf) == (2, 1)
    bare = gdf.drop(columns=["status"])
    assert context.review_counts(bare) == (2, 0)


def test_review_counts_of_nothing_is_zero_zero():
    assert context.review_counts(None) == (0, 0)
    assert context.review_counts(_alerts([])) == (0, 0)


def test_the_counter_is_one_based_and_padded_to_the_total():
    assert context.counter_text(11, 530) == "012 / 530"
    assert context.counter_text(0, 9) == "1 / 9"
    assert context.counter_text(0, 1200) == "0001 / 1,200"


def test_the_counter_and_the_tally_of_an_empty_queue_say_so():
    assert context.counter_text(-1, 0) == "No alert set is loaded"
    assert context.tally_text(0, 0) == ""


def test_the_tally_names_what_is_left():
    assert context.tally_text(530, 212) == "212 reviewed · 318 left"


def test_the_queue_line_follows_the_alerts_and_the_position(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    card = context.build_context(s, v.TextField(v_model=None))
    counter, tally, bar = context.queue_widgets(card)
    assert counter.children == ["No alert set is loaded"]

    s.alerts_gdf.set(_alerts(["Confirmed"] + ["Not reviewed"] * 9))
    s.actual_alert_id.value = 0
    assert counter.children == ["01 / 10"]
    assert tally.children == ["1 reviewed · 9 left"]
    assert bar.value == 10.0

    s.go_to(4)
    assert counter.children == ["05 / 10"]


def test_the_tally_is_formatted_in_the_language_in_force_when_it_is_written(tmp_path, monkeypatch):
    """The block is built once per session and a language switch never rebuilds
    it, so the strings must be resolved at write time, not at build time."""
    s = _state(tmp_path, monkeypatch)
    card = context.build_context(s, v.TextField(v_model=None))
    _, tally, _ = context.queue_widgets(card)
    set_locale("es-ES")
    s.alerts_gdf.set(_alerts(["Confirmed", "Not reviewed"]))
    s.actual_alert_id.value = 0
    assert tally.children == ["1 revisadas · 1 pendientes"]


def test_the_nav_buttons_move_the_selection(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    card = context.build_context(s, v.TextField(v_model=None))
    prev, nxt, jump, field_box, go = context.nav_widgets(card)
    s.alerts_gdf.set(_alerts(["Not reviewed"] * 4))
    s.actual_alert_id.value = 0  # a real position, as the queue line leaves it

    nxt.fire_event("click", None)
    assert s.actual_alert_id.value == 1
    prev.fire_event("click", None)
    assert s.actual_alert_id.value == 0


def test_the_nav_buttons_are_safe_with_no_alerts(tmp_path, monkeypatch):
    """``go_to`` is ``max(0, min(idx, max_alert_id))`` and ``max_alert_id`` is
    -1 with no alerts loaded, so every press clamps to 0 by construction —
    not a no-op, but it never raises and never goes negative."""
    s = _state(tmp_path, monkeypatch)
    card = context.build_context(s, v.TextField(v_model=None))
    prev, nxt, _, _, _ = context.nav_widgets(card)
    nxt.fire_event("click", None)
    prev.fire_event("click", None)
    assert s.actual_alert_id.value == 0


def test_the_jump_field_is_one_based_and_hidden_until_asked(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    field = v.TextField(v_model=None)
    card = context.build_context(s, field)
    _, _, jump, field_box, go = context.nav_widgets(card)
    s.alerts_gdf.set(_alerts(["Not reviewed"] * 9))

    assert field in field_box.children  # the field itself ships inside the box
    assert "display: none" in field_box.style_
    jump.fire_event("click", None)
    assert "display: none" not in field_box.style_

    field.v_model = 7          # the reviewer types the number the counter shows
    go.fire_event("click", None)
    assert s.actual_alert_id.value == 6

    field.v_model = "not a number"
    go.fire_event("click", None)
    assert s.actual_alert_id.value == 6   # unchanged, no exception

    field.v_model = 99         # out of range: go_to clamps
    go.fire_event("click", None)
    assert s.actual_alert_id.value == 8


def test_facts_of_reads_the_row(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    gdf = _alerts(["Confirmed", "Not reviewed"])
    facts = context.facts_of(gdf, 0, s.project.value)
    assert facts["source"] == "GLAD-L"
    assert facts["status"] == "Confirmed"
    assert facts["first"] == "2026-01-10" and facts["last"] == "2026-01-20"


def test_facts_of_without_a_status_reads_not_reviewed(tmp_path, monkeypatch):
    """A missing (NaN) status is not a review, as the tally counts it."""
    s = _state(tmp_path, monkeypatch)
    gdf = _alerts([None])
    assert context.facts_of(gdf, 0, s.project.value)["status"] == "Not reviewed"


@pytest.mark.parametrize("gdf_id", [(None, 0), ("ok", None), ("ok", -1), ("ok", 9)])
def test_facts_of_guards_nothing_to_show(tmp_path, monkeypatch, gdf_id):
    s = _state(tmp_path, monkeypatch)
    which, alert_id = gdf_id
    gdf = None if which is None else _alerts(["Confirmed"])
    assert context.facts_of(gdf, alert_id, s.project.value) is None


def test_facts_of_refuses_another_sets_alerts(tmp_path, monkeypatch):
    """Integrate records a new set before it publishes that set's alerts."""
    s = _state(tmp_path, monkeypatch)
    assert context.facts_of(_alerts(["Confirmed"], set_id="old"), 0, s.project.value) is None


@pytest.mark.parametrize("status,key", [
    ("Not reviewed", "legend.status.not_reviewed"),
    ("Confirmed", "legend.status.confirmed"),
    ("False Positive", "legend.status.false_positive"),
    ("Need revision", "legend.status.needs_revision"),
    (None, "legend.status.not_reviewed"),
    ("Who knows", "legend.status.not_reviewed"),
])
def test_the_status_chip_names_and_paints_a_status(status, key):
    from gui.scripts import legend_specs

    assert context.status_label_key(status) == key
    expected = legend_specs.status_colors()[
        {"Confirmed": 1, "False Positive": 2, "Need revision": 3}.get(status, 0)]
    assert context.status_color(status) == expected
    assert t(key) != f"⟦{key}⟧"


def test_the_status_chip_wears_its_colour_as_a_swatch(tmp_path, monkeypatch):
    """Overview's convention for this palette, stated in
    ``overview_tile._numbers_table``: "A status wears a colour chip; the text
    stays in the theme's ink, never in the series colour." Coloured label text
    fails it: the grey "Not reviewed" wears is roughly 2.8:1 on the light
    surface — below WCAG AA — and the dark theme was never verified. So the chip holds a
    10px swatch in the status colour beside text in the theme's ink, exactly as
    the numbers table draws it, and the chip itself is never painted.
    """
    s = _state(tmp_path, monkeypatch)
    card = context.build_context(s, v.TextField(v_model=None))
    _, _, chip = context.facts_widgets(card)
    s.alerts_gdf.set(_alerts(["Confirmed"]))
    s.actual_alert_id.value = 0

    swatch, label = chip.children
    assert context.status_color("Confirmed") in swatch.style_
    assert label == t("legend.status.confirmed")
    assert not chip.color  # the words stay in the theme's ink


def test_the_facts_grid_follows_a_review_save(tmp_path, monkeypatch):
    """A Save writes the status into the row and rebinds ``alerts_gdf`` to a
    copy with the same alert still selected; the chip and the tally must both
    move (the old card read "Not reviewed" until the next alert change)."""
    s = _state(tmp_path, monkeypatch)
    card = context.build_context(s, v.TextField(v_model=None))
    source, detected, chip = context.facts_widgets(card)
    _, tally, _ = context.queue_widgets(card)

    gdf = _alerts(["Not reviewed"] * 3)
    s.alerts_gdf.set(gdf)
    s.actual_alert_id.value = 0  # wire_alert_navigation selects; the block only watches
    assert source.children == ["GLAD-L"]
    assert detected.children == ["2026-01-10 → 2026-01-20"]
    assert chip.children[-1] == t("legend.status.not_reviewed")

    gdf.at[0, "status"] = "Confirmed"
    s.alerts_gdf.value = gdf.copy()
    assert chip.children[-1] == t("legend.status.confirmed")
    assert context.status_color("Confirmed") in chip.children[0].style_
    assert tally.children == ["1 reviewed · 2 left"]


def test_another_sets_alerts_leave_the_facts_alone(tmp_path, monkeypatch):
    """The totals still follow the table on hand (they are its real totals)
    while the facts of the alert on screen stay as they were."""
    s = _state(tmp_path, monkeypatch)
    card = context.build_context(s, v.TextField(v_model=None))
    source, _, _ = context.facts_widgets(card)
    _, tally, _ = context.queue_widgets(card)
    s.alerts_gdf.set(_alerts(["Confirmed"]))
    s.actual_alert_id.value = 0
    before = list(source.children)

    s.alerts_gdf.value = _alerts(["Confirmed", "Not reviewed"], set_id="old")
    assert source.children == before
    assert tally.children == ["1 reviewed · 1 left"]
