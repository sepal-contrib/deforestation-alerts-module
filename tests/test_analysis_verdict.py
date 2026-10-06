"""Block B: the three-button verdict, the driver that disappears for a false
positive, Save & next, and the save-state line.
"""

from datetime import datetime
from types import SimpleNamespace

import geopandas as gpd
import pytest
from pysepal.i18n import set_locale
from shapely.geometry import box

from deforestation_alerts.project.models import Project
from gui.i18n import t
from gui.scripts.error_text import error_text
from gui.scripts.set_context import SetContext
from gui.store.state_manager import AppState
from gui.tile.analysis import review_panel
from gui.tile.analysis.state import AnalysisState


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


def _panel(monkeypatch, tmp_path, saves=None):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    app_state = AppState()
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    app_state.new_project_state(p)
    analysis = AnalysisState()
    widgets = SimpleNamespace()
    if saves is not None:
        monkeypatch.setattr(review_panel, "make_save_runner",
                            lambda *a, **k: lambda button=None, then=None: saves(button, then))
    card = review_panel.build_review_panel(widgets, app_state, analysis, SetContext(app_state))
    return card, widgets, analysis, app_state


def test_a_button_sets_the_verdict_and_marks_the_alert_dirty(tmp_path, monkeypatch):
    card, _, analysis, _ = _panel(monkeypatch, tmp_path)
    buttons, *_ = review_panel.verdict_widgets(card)
    assert analysis.verdict.value is None

    buttons["Confirmed"].fire_event("click", None)
    assert analysis.verdict.value == "Confirmed"
    assert analysis.dirty.value is True


def test_the_chosen_button_is_the_filled_one(tmp_path, monkeypatch):
    card, _, analysis, _ = _panel(monkeypatch, tmp_path)
    buttons, *_ = review_panel.verdict_widgets(card)
    analysis.verdict.set("Need revision")
    assert buttons["Need revision"].outlined is False
    assert buttons["Confirmed"].outlined is True


def test_a_false_positive_hides_the_driver_but_keeps_it_in_the_tree(tmp_path, monkeypatch):
    card, widgets, analysis, _ = _panel(monkeypatch, tmp_path)
    buttons, driver_box, note, *_ = review_panel.verdict_widgets(card)
    assert "display: none" not in driver_box.style_

    analysis.verdict.set("False Positive")
    assert "display: none" in driver_box.style_
    assert "display: none" not in note.style_
    assert widgets.driver_combobox in driver_box.children

    analysis.verdict.set("Confirmed")
    assert "display: none" not in driver_box.style_
    assert "display: none" in note.style_


def test_a_driver_change_marks_the_alert_dirty(tmp_path, monkeypatch):
    card, widgets, analysis, _ = _panel(monkeypatch, tmp_path)
    widgets.driver_combobox.v_model = ["mining"]
    widgets.driver_combobox.fire_event("change", ["mining"])
    assert analysis.dirty.value is True
    assert analysis.answers.value == {"loss_driver": ["mining"]}


def test_the_state_line_reads_unsaved_then_saved(tmp_path, monkeypatch):
    card, _, analysis, _ = _panel(monkeypatch, tmp_path)
    *_, state_line = review_panel.verdict_widgets(card)
    assert state_line.children == []

    analysis.dirty.set(True)
    assert state_line.children == [t("analysis_tile.verdict.unsaved")]

    analysis.dirty.set(False)
    analysis.last_save_time.set(datetime(2026, 10, 3, 14, 32).timestamp())
    assert state_line.children == [t("analysis_tile.verdict.saved_at", time="14:32")]


def test_an_alert_change_forgets_the_previous_alerts_save_time(tmp_path, monkeypatch):
    """Spec §4.4: *Saved HH:MM* means a save landed on **this** alert in this
    session, so an untouched alert must show the empty line and not inherit
    the previous one's stamp.

    ``reset_alert`` sets ``dirty`` to the ``False`` it already holds, which
    notifies nobody, so only the ``last_save_time`` subscription can carry the
    re-render — asserted on the rendered line, not on the reactive alone.
    """
    card, _, analysis, _ = _panel(monkeypatch, tmp_path)
    *_, state_line = review_panel.verdict_widgets(card)

    analysis.verdict.set("Confirmed")
    analysis.dirty.set(False)  # as a save that wrote leaves it
    analysis.last_save_time.set(datetime(2026, 10, 3, 14, 32).timestamp())
    assert state_line.children == [t("analysis_tile.verdict.saved_at", time="14:32")]

    analysis.reset_alert()  # the reviewer moves to another alert
    assert analysis.last_save_time.value == 0.0
    assert state_line.children == []


def test_re_clicking_the_chosen_verdict_does_not_mark_the_alert_unsaved(tmp_path, monkeypatch):
    """``dirty`` follows a verdict *change* (spec §4.4): pressing the answer
    that is already selected after a save must not claim unsaved changes."""
    card, _, analysis, _ = _panel(monkeypatch, tmp_path)
    buttons, *_ = review_panel.verdict_widgets(card)

    buttons["Confirmed"].fire_event("click", None)
    analysis.dirty.set(False)  # as a save that wrote leaves it
    buttons["Confirmed"].fire_event("click", None)
    assert analysis.verdict.value == "Confirmed"
    assert analysis.dirty.value is False

    buttons["Need revision"].fire_event("click", None)  # a real change still counts
    assert analysis.dirty.value is True


def test_save_and_next_asks_to_advance_and_plain_save_does_not(tmp_path, monkeypatch):
    calls = []
    card, _, _, _ = _panel(monkeypatch, tmp_path, saves=lambda b, then: calls.append(then))
    *_, save_next, save, _ = review_panel.verdict_widgets(card)

    save.fire_event("click", None)
    assert calls == [None]

    save_next.fire_event("click", None)
    assert calls[1] is not None


def test_a_refused_save_and_next_stays_on_the_alert(tmp_path, monkeypatch):
    """Review Focus: pressing **Save & next** on an alert with no verdict must
    refuse, leave the reviewer where they are and give the button back.

    The real ``make_save_runner`` runs, inline, against the real alert store —
    only ``then``-after-a-write keeps the queue from moving, so it is pinned
    here at the panel's own level and not merely trusted of the runner.
    """
    import pandas as pd

    from tests.test_feedback_notifications import _save_harness

    # The harness's own recorder, not a second one: ``_record_feedback`` also
    # re-points ``spawn_in_context`` at a real thread, and the save must have
    # finished by the time the assertions below run.
    app_state, analysis, widgets, model, feedback = _save_harness(tmp_path, monkeypatch)
    # Two alerts, so "stayed on this one" is a real assertion and not a clamp.
    one = app_state.alerts_gdf.value
    app_state.alerts_gdf.value = pd.concat([one, one], ignore_index=True)
    app_state.actual_alert_id.value = 0

    card = review_panel.build_review_panel(widgets, app_state, analysis, model)
    *_, save_next, _, _ = review_panel.verdict_widgets(card)

    save_next.fire_event("click", None)  # no verdict chosen
    assert app_state.actual_alert_id.value == 0
    assert app_state.alerts_gdf.value["status"].tolist() == ["Not reviewed"] * 2
    assert save_next.loading is False and save_next.disabled is False
    assert feedback.toasts() == [
        ("warning", t("analysis_tile.questionarie.confirmation_missing"))
    ]

    analysis.verdict.set("Confirmed")
    save_next.fire_event("click", None)
    assert app_state.actual_alert_id.value == 1
    assert app_state.alerts_gdf.value["status"].tolist() == ["Confirmed", "Not reviewed"]
    assert save_next.loading is False and save_next.disabled is False


def test_an_alert_change_seeds_the_verdict_from_the_row(tmp_path, monkeypatch):
    """A reviewed alert opens on its stored status; an unreviewed one opens
    with no answer, so nothing is ever saved by default. The seeding happens
    after ``reset_alert``, which clears it."""
    from gui.tile.analysis import analysis_tile

    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    app_state = AppState()
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    app_state.new_project_state(p)
    analysis = AnalysisState()
    widgets = SimpleNamespace()
    id_field = SimpleNamespace(v_model=None)

    gdf = gpd.GeoDataFrame(
        {"status": ["Confirmed", "Not reviewed"], "alert_id": [0, 1], "set_id": ["x", "x"],
         "alert_sources": ["GLAD-L"] * 2, "alert_date_min": [2026.010] * 2,
         "alert_date_max": [2026.020] * 2, "alert_polygon": [None, None],
         "description": [None, None], "bounding_box": [box(0, 0, 1, 1)] * 2},
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)], crs="EPSG:4326")
    app_state.alerts_gdf.value = gdf
    app_state.actual_alert_id.value = 0

    analysis_tile.view_actual_alert(widgets, app_state, analysis, SetContext(app_state), id_field)
    assert analysis.verdict.value == "Confirmed"
    assert analysis.dirty.value is False          # seeding is not a change
    assert id_field.v_model == 1                  # the 1-based number the counter shows

    app_state.actual_alert_id.value = 1
    analysis_tile.view_actual_alert(widgets, app_state, analysis, SetContext(app_state), id_field)
    assert analysis.verdict.value is None
    assert id_field.v_model == 2


def test_a_save_with_an_unapplied_drawing_is_refused_before_anything_is_written(
    tmp_path, monkeypatch
):
    """The reviewer draws (or adds a model's prediction) and presses Save &
    next without *Apply*: the polygon on the maps is not what ``defo_layer``
    holds, so the save would store the previous polygon -- or none -- over work
    still on screen, and Save & next would navigate it away. OLD disabled the
    review Save for the whole edit session; the refusal is that guard, as a
    message that names what to do. Nothing is written: not the store, not the
    row."""
    import gui.tile.analysis.export as export
    from gui.scripts.error_text import error_text
    from gui.tile.analysis.state import capture_alert

    app_state = AppState()
    gdf = gpd.GeoDataFrame({"status": ["Not reviewed"], "description": [""]},
                           geometry=[box(0, 0, 1, 1)], crs="EPSG:4326")
    app_state.alerts_gdf.value = gdf
    app_state.actual_alert_id.value = 0
    analysis = AnalysisState()
    analysis.verdict.set("Confirmed")
    analysis.drawing_unapplied.set(True)
    widgets = SimpleNamespace()
    model = SimpleNamespace(recipe_folder_path="")  # a recipe-era fake: nowhere to write
    alert = capture_alert(analysis, widgets, app_state)

    with pytest.raises(export.SaveRefused) as refusal:
        export._save_attributes(widgets, app_state, analysis, model, alert=alert)

    assert error_text(refusal.value) == t("analysis_tile.edition_labels.unapplied_drawing")
    assert gdf.at[0, "status"] == "Not reviewed", "the refused save changed the row"


class _AdminLookups:  # stands in for ``export.ee``: the FAO/GAUL lookups
    def __getattr__(self, name):
        return lambda *a, **k: self

    def getInfo(self):  # noqa: N802 - Earth Engine's name
        return "Brasil, "


def _review_row():
    """One alert row with every column a review Save writes, all empty."""
    gdf = gpd.GeoDataFrame(
        {"status": ["Not reviewed"], "description": [""],
         "before_img": [None], "after_img": [None],
         "before_img_info": [None], "after_img_info": [None],
         "admin1": [None], "admin2": [None], "admin3": [None],
         "alert_polygon": [None], "area_ha": [1.25]},
        geometry=[box(0, 0, 1, 1)], crs="EPSG:4326",
    )
    gdf["bounding_box"] = gdf.geometry.envelope
    return gdf


def _save_pieces(monkeypatch, gdf, *, verdict, drawn=None):
    """``(export, app_state, analysis, widgets, model, alert)`` for one direct
    ``_save_attributes`` call on ``gdf``, with the GEE admin lookups stubbed."""
    import gui.tile.analysis.export as export
    from gui.tile.analysis.state import capture_alert

    monkeypatch.setattr(export, "ee", _AdminLookups())
    app_state = AppState()
    # A project with the default questionnaire (Loss driver): the questions a
    # Save writes come with the answers (``capture_alert``).
    app_state.project.set(Project(name="d", created_at=datetime.now(), updated_at=datetime.now()))
    app_state.alerts_gdf.value = gdf
    app_state.actual_alert_id.value = 0
    analysis = AnalysisState()
    analysis.verdict.set(verdict)
    analysis.before_info.set(["Sentinel 2", 1700000000, None, "S2_before", "_010124"])
    analysis.after_info.set(["Sentinel 2", 1700000001, None, "S2_after", "_020224"])
    if drawn is not None:
        analysis.defo_layer.set(drawn)
    analysis.answers.set({"loss_driver": ["mining"]})
    widgets = SimpleNamespace(actual_bb=SimpleNamespace(geometry=lambda: None))
    model = SimpleNamespace(recipe_folder_path="")  # nowhere to write: no store
    return export, app_state, analysis, widgets, model, capture_alert(analysis, widgets, app_state)


def test_a_refused_save_leaves_the_live_review_row_untouched(tmp_path, monkeypatch):
    """OLD's "False positive but a polygon is drawn" refusal fires only after
    the status, the driver, the image fields and the admin lookups have each
    been written into the row -- so a refused save left the live table holding
    a review the reviewer was told was rejected. It is the table Overview's
    counts and markers read, and the one the NEXT alert's save persists whole,
    so those values reach the store later with nothing to explain them.
    """
    gdf = _review_row()
    drawn = gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)], crs="EPSG:4326")
    export, app_state, analysis, widgets, model, alert = _save_pieces(
        monkeypatch, gdf, verdict="False Positive", drawn=drawn
    )

    with pytest.raises(export.SaveRefused) as refusal:
        export._save_attributes(widgets, app_state, analysis, model, alert=alert)

    assert error_text(refusal.value) == t("analysis_tile.questionarie.false_positive_drawn")
    assert gdf.at[0, "status"] == "Not reviewed"
    assert gdf.at[0, "description"] == ""
    assert gdf.at[0, "before_img"] is None and gdf.at[0, "after_img"] is None
    assert gdf.at[0, "admin1"] is None


def test_a_failed_store_write_leaves_the_live_review_row_untouched(tmp_path, monkeypatch):
    """And the row is published only once the parquet has landed.

    A store write that fails (a full disk, a set deleted mid-write) used to
    leave the live table already carrying the review, so the app showed it as
    reviewed while nothing on disk said so -- and the error toast asked the
    reviewer to save again for a review the UI claimed was there.
    """
    gdf = _review_row()
    export, app_state, analysis, widgets, model, alert = _save_pieces(
        monkeypatch, gdf, verdict="Confirmed"
    )

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(export, "_save_alerts_to_gdf", _boom)

    with pytest.raises(OSError):
        export._save_attributes(widgets, app_state, analysis, model, alert=alert)

    assert gdf.at[0, "status"] == "Not reviewed"
    assert gdf.at[0, "description"] == ""
    assert gdf.at[0, "before_img"] is None


def test_a_save_that_wrote_publishes_every_field_to_the_live_row(tmp_path, monkeypatch):
    """...and a save that goes through still writes the row it persisted."""
    gdf = _review_row()
    export, app_state, analysis, widgets, model, alert = _save_pieces(
        monkeypatch, gdf, verdict="Confirmed"
    )
    written = []
    monkeypatch.setattr(export, "_save_alerts_to_gdf",
                        lambda staged, *a, **k: written.append(staged))

    assert export._save_attributes(widgets, app_state, analysis, model, alert=alert) is True

    assert gdf.at[0, "status"] == "Confirmed"
    assert gdf.at[0, "description"] == "mining"
    assert gdf.at[0, "before_img"] == "S2_before" and gdf.at[0, "after_img"] == "S2_after"
    assert gdf.at[0, "admin1"] == "Brasil,"  # the stub's list, less its trailing space
    # What was persisted carries the same review as the live row.
    [staged] = written
    assert staged.at[0, "status"] == "Confirmed"
    assert staged.at[0, "description"] == "mining"


def test_the_state_line_asks_for_the_apply_before_it_asks_for_a_save(tmp_path, monkeypatch):
    """The reviewer has to see why the save will not take: the save-state line
    reads the unapplied drawing ahead of its own "Unsaved changes", since
    applying is what clears both."""
    card, _, analysis, _ = _panel(monkeypatch, tmp_path)
    *_, state_line = review_panel.verdict_widgets(card)

    analysis.drawing_unapplied.set(True)
    analysis.dirty.set(True)
    assert state_line.children == [t("analysis_tile.edition_labels.unapplied_drawing")]
    assert "warning--text" in state_line.class_

    analysis.drawing_unapplied.set(False)
    assert state_line.children == [t("analysis_tile.verdict.unsaved")]
