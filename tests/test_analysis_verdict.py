"""Block B: the three-button verdict, and what a review Save writes.

The loss driver left this block for the Questions section (spec D4): its
hiding for a false positive, its codes and its dirty mark are tested in
tests/test_analysis_questions_panel.py. Save & next, Save and the save-state
line left it for the panel's sticky footer (spec D6): tested in
tests/test_analysis_footer.py.
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
from gui.tile.analysis import review_save
from gui.tile.analysis.state import AnalysisState


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


def _panel(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    app_state = AppState()
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    app_state.new_project_state(p)
    analysis = AnalysisState()
    widgets = SimpleNamespace()
    card = review_panel.build_review_panel(widgets, app_state, analysis)
    return card, widgets, analysis, app_state


def test_a_button_sets_the_verdict_and_marks_the_alert_dirty(tmp_path, monkeypatch):
    card, _, analysis, _ = _panel(monkeypatch, tmp_path)
    buttons = review_panel.verdict_widgets(card)
    assert analysis.verdict.value is None

    buttons["Confirmed"].fire_event("click", None)
    assert analysis.verdict.value == "Confirmed"
    assert analysis.dirty.value is True


def test_the_chosen_button_is_the_filled_one(tmp_path, monkeypatch):
    card, _, analysis, _ = _panel(monkeypatch, tmp_path)
    buttons = review_panel.verdict_widgets(card)
    analysis.verdict.set("Need revision")
    assert buttons["Need revision"].outlined is False
    assert buttons["Confirmed"].outlined is True


def test_re_clicking_the_chosen_verdict_does_not_mark_the_alert_unsaved(tmp_path, monkeypatch):
    """``dirty`` follows a verdict *change* (spec §4.4): pressing the answer
    that is already selected after a save must not claim unsaved changes."""
    card, _, analysis, _ = _panel(monkeypatch, tmp_path)
    buttons = review_panel.verdict_widgets(card)

    buttons["Confirmed"].fire_event("click", None)
    analysis.dirty.set(False)  # as a save that wrote leaves it
    buttons["Confirmed"].fire_event("click", None)
    assert analysis.verdict.value == "Confirmed"
    assert analysis.dirty.value is False

    buttons["Need revision"].fire_event("click", None)  # a real change still counts
    assert analysis.dirty.value is True


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

    with pytest.raises(review_save.SaveRefused) as refusal:
        export._save_attributes(widgets, app_state, analysis, model, alert=alert)

    assert error_text(refusal.value) == t("analysis_tile.edition_labels.unapplied_drawing")
    assert gdf.at[0, "status"] == "Not reviewed", "the refused save changed the row"


class _AdminLookups:  # stands in for ``review_save.ee``: the FAO/GAUL lookups
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

    monkeypatch.setattr("gui.tile.analysis.review_save.ee", _AdminLookups())
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

    with pytest.raises(review_save.SaveRefused) as refusal:
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

    monkeypatch.setattr("gui.tile.analysis.review_save.write_store", _boom)

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
    monkeypatch.setattr("gui.tile.analysis.review_save.write_store",
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
