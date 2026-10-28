"""The collapsible sections of the Analysis panel, and the assembled panel.

The body is hidden with ``display``, never dropped from the tree: it holds the
maps' draw controls and the CNN toolbars, whose imperative shown/disabled state
must survive a collapse, and the step container already destroys and rebuilds
this DOM.

The second half drives ``analysis_tile.build_controls`` -- the panel as the
tile assembles it: the head, block A, block B, the Questions and Refine
sections and the sticky footer (the Export section and the Save buttons, spec
D6), whose Save runs the export section's own save runner.
"""

from datetime import datetime
from types import SimpleNamespace

import ipyvuetify as v
import pytest

from deforestation_alerts.project.models import Project
from gui.i18n import t
from gui.store.state_manager import AppState
from gui.tile.analysis import analysis_tile
from gui.tile.analysis.section import build_section
from gui.tile.analysis.state import AnalysisState
from gui.widget.buttons import label_of
from tests._pane_helpers import make_panes


pytestmark = pytest.mark.usefixtures("english_locale")


def test_a_section_starts_collapsed_with_its_body_in_the_tree():
    body = v.Btn(disabled=True, children=["inner"])
    s = build_section("Refine polygon", body)
    assert s.is_open() is False
    assert "display: none" in s.body_box.style_
    assert body in s.body_box.children


def test_the_header_toggles_and_the_body_keeps_its_state():
    body = v.Btn(disabled=True, children=["inner"])
    s = build_section("Refine polygon", body)
    s.header.fire_event("click", None)
    assert s.is_open() is True and "display: none" not in s.body_box.style_
    body.disabled = False          # the reviewer starts an edit
    s.header.fire_event("click", None)
    assert s.is_open() is False
    s.header.fire_event("click", None)
    assert body.disabled is False  # the edit survived the collapse
    assert body in s.body_box.children


def test_the_badge_follows_what_it_is_told():
    s = build_section("Export this alert", v.Html(tag="div"), badge="available after save")
    assert s.badge.children == ["available after save"]
    s.set_badge("2 files ready", color="primary")
    assert s.badge.children == ["2 files ready"] and s.badge.class_ == "dat-badge dat-badge--done"
    # And takes it back: both consumers pass no colour for their NEUTRAL
    # states, which means "no colour" and not "keep the last one" -- the chip
    # is per-alert state, and an emphasis left standing is the previous
    # alert's. Same assertion as the facts status chip's
    # (tests/test_analysis_context.py: ``assert not chip.color``).
    s.set_badge("nothing drawn")
    assert s.badge.children == ["nothing drawn"] and s.badge.class_ == "dat-badge"
    s.set_badge("")
    assert s.badge.children == [] and "display: none" in s.badge.style_


def test_set_open_drives_the_same_state():
    s = build_section("Refine polygon", v.Html(tag="div"))
    s.set_open(True)
    assert s.is_open() is True and "display: none" not in s.body_box.style_
    s.set_open(False)
    assert s.is_open() is False


# ---------------------------------------------------------------------------
# The assembled panel (Task 10): head, block A, block B, the three sections
# ---------------------------------------------------------------------------
def _controls(tmp_path, monkeypatch):
    """``build_controls`` as the tile builds it, on stand-in models.

    The heavy widgets are only touched inside the edit/CNN handlers (as
    tests/test_feedback_notifications.py's ``edit_tools_card`` relies on), by
    the refine section's alert-change reset, which clears the views' drawing,
    and by ``wire_defo_layer``, which draws a set polygon on the views -- so a
    namespace with real ``panes`` (headless, ``tests/_pane_helpers``) is
    enough to assemble the panel and to drive a drawing and an alert change
    through it without a caught-and-logged traceback in the way.
    """
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    app_state = AppState()
    app_state.new_project_state(
        Project(name="d", created_at=datetime.now(), updated_at=datetime.now()))
    analysis = AnalysisState()
    model = SimpleNamespace(recipe_folder_path=str(tmp_path), custom_report_template=None,
                            alert_set=None, remember_position=lambda *_: None)
    controls = analysis_tile.build_controls(
        widgets=SimpleNamespace(panes=make_panes()),
        app_state=app_state, analysis=analysis,
        aux_model=model, app_tile_model=model, id_field=v.TextField(v_model=None))
    return SimpleNamespace(controls=controls, app_state=app_state, analysis=analysis,
                           sections=analysis_tile.panel_sections(controls))


def test_the_panel_is_the_head_the_two_blocks_and_the_three_sections(tmp_path, monkeypatch):
    """The six stacked always-open cards are gone: what is left is the head
    (help, set selector, set-status banner), block A, block B and the
    collapsible sections -- Questions (spec D4), Refine, Earlier alerts -- in
    the order of the spec's §3-§6; Export is the dock's (user, 2026-10-26).
    """
    from gui.tile.analysis import context, review_panel
    from gui.tile.analysis.footer import footer_widgets

    panel = _controls(tmp_path, monkeypatch)
    banner, block_a, block_b, questions, refine, earlier, _nearby = panel.controls.children
    # the help, the set selector and the navigation went to the top bar
    assert panel.sections.topnav is not None
    assert earlier is panel.sections.earlier.root
    # What each block OWNS, named. ``facts_widgets`` returns a tuple and
    # ``verdict_widgets`` a dict, both checked by their contents below: a bare
    # ``assert <the accessor's result>`` only ever catches the AttributeError
    # of the slots being absent -- give either accessor a ``getattr(..., {})``
    # default and such a line silently stops checking anything.
    source, detected, chip = context.facts_widgets(block_a)
    assert isinstance(chip, v.Chip)                       # the status chip
    assert (source.tag, detected.tag) == ("span", "span")  # the two fact values
    buttons = review_panel.verdict_widgets(block_b)
    # The three statuses the store, the PMTiles paint values and Overview's
    # counts all read -- the verdict IS the stored string, never a label.
    assert set(buttons) == {"Confirmed", "False Positive", "Need revision"}
    # Save and Next sit in the dock, under the Export section; the dock is
    # not one of the scrolling controls (``build_screen`` places it). The
    # literal English (the fixture pins the locale), not ``t()`` of the same
    # key, which passes on the ``⟦key⟧`` placeholder too.
    footer = panel.sections.footer.root
    assert footer not in panel.controls.children
    save, nxt, _ = footer_widgets(footer)
    assert label_of(save) == "Save" and label_of(nxt) == "Next"
    assert [questions, refine] == [panel.sections.questions.root, panel.sections.refine.root]
    assert footer.children[0] is panel.sections.export.root


def test_the_questions_section_starts_collapsed_on_the_panels_answers(tmp_path, monkeypatch):
    """Spec D4: closed by default, one control per project question (the
    default questionnaire: Loss driver), showing the panel's own answers."""
    panel = _controls(tmp_path, monkeypatch)
    questions = panel.sections.questions
    assert questions.is_open() is False and "display: none" in questions.body_box.style_
    assert set(questions.controls) == {"loss_driver"}
    panel.analysis.answers.set({"loss_driver": ["mining"]})
    assert questions.controls["loss_driver"].v_model == [{"text": "Mining", "value": "mining"}]


def test_both_sections_start_collapsed_with_their_bodies_built(tmp_path, monkeypatch):
    """The bodies are built once, into the collapsed sections, and never
    rebuilt: the draw controls and the CNN toolbars live in the refine body and
    the download toolbar in the export body, and their imperative state is the
    edit session itself."""
    from gui.tile.analysis import edit_tools

    panel = _controls(tmp_path, monkeypatch)
    refine, export = panel.sections.refine, panel.sections.export
    assert refine.is_open() is False and export.is_open() is False
    assert "display: none" in refine.body_box.style_
    assert "display: none" in export.body_box.style_
    (refine_body,) = refine.body_box.children
    assert set(edit_tools.edit_widgets(refine_body)) >= {"start_btn", "dl1_run"}
    assert export.body_box.children == [panel.sections.export_parts.body]


def test_each_section_got_its_own_badge_setter(tmp_path, monkeypatch):
    """Both bodies were handed their section's ``set_badge``, so the headers
    say what is inside them without being opened."""
    panel = _controls(tmp_path, monkeypatch)
    assert panel.sections.refine.badge.children == [t("analysis_tile.sections.refine_empty")]
    assert panel.sections.export.badge.children == [
        t("analysis_tile.sections.export_after_save")]


def test_the_verdict_save_drives_the_export_sections_own_runner(tmp_path, monkeypatch):
    """Ruling 28: there is ONE save runner in the panel, the export section's.

    Its ``apply_save`` is what enables *Build files* and moves the export
    badge on; a footer that made a second runner would leave that row dark
    for ever, with nothing to say why. So the footer's Save must run the
    runner the tile passed in, and the panel must build only that one, and
    run it on every save.
    """
    from gui.tile.analysis import export as export_mod
    from gui.tile.analysis.footer import footer_widgets
    from tests.conftest import _alerts_gdf, _build_button

    made = []
    real_runner = export_mod.make_save_runner

    def _counted(*a, **k):
        made.append(1)
        return real_runner(*a, **k)

    monkeypatch.setattr(export_mod, "make_save_runner", _counted)
    monkeypatch.setattr(export_mod, "spawn_in_context", lambda target, *a, **k: target())
    monkeypatch.setattr(export_mod, "_save_attributes", lambda *a, **k: True)

    panel = _controls(tmp_path, monkeypatch)
    assert made == [1]                     # the export section's, and no other
    panel.app_state.alerts_gdf.value = _alerts_gdf()
    panel.app_state.actual_alert_id.value = 0
    build_btn = _build_button(panel.sections.export_parts.body)
    save, _next, _ = footer_widgets(panel.sections.footer.root)

    assert build_btn.disabled is True
    save.fire_event("click", None)

    assert build_btn.disabled is False     # the export section's apply_save ran
    # The literal English, as the export badge's own tests use since Task 9's
    # round: ``t()`` of the same key on both sides passes on the ``⟦key⟧``
    # placeholder a missing or misshapen key renders.
    assert panel.sections.export.badge.children == ["ready to build"]

    # And again on the next alert: the same runner, not one made per alert.
    # Driven from the section's empty state, so the two effects below are
    # this click's and not the previous one's still standing.
    panel.analysis.reset_alert()           # the reviewer moved on
    assert build_btn.disabled is True
    assert panel.sections.export.badge.children == ["available after save"]

    save.fire_event("click", None)

    assert made == [1]
    assert build_btn.disabled is False
    assert panel.sections.export.badge.children == ["ready to build"]


def test_an_alert_change_takes_the_refine_badges_emphasis_with_it(tmp_path, monkeypatch):
    """The badge COLOUR is per-alert state too: a reviewer who drew on alert 1
    and moved to alert 2 must not meet an emphasised *nothing drawn* on a
    section that holds nothing of this alert. Driven through the assembled
    panel (not ``set_badge`` alone) because the leak is in what the two
    neutral call sites mean by passing no colour.
    """
    import geopandas as gpd
    from shapely.geometry import box as shapely_box

    panel = _controls(tmp_path, monkeypatch)
    refine = panel.sections.refine
    assert refine.badge.children == [t("analysis_tile.sections.refine_empty")]
    assert refine.badge.class_ == "dat-badge"          # a fresh panel carries no emphasis

    gdf = gpd.GeoDataFrame(geometry=[shapely_box(0, 0, 0.01, 0.01)], crs="EPSG:4326")
    panel.analysis.defo_layer.set(gdf)     # the reviewer drew on alert 1
    assert refine.badge.class_ == "dat-badge dat-badge--done"
    assert "ha" in refine.badge.children[0]

    panel.analysis.reset_alert()           # and moved to alert 2

    assert refine.badge.children == [t("analysis_tile.sections.refine_empty")]
    assert refine.badge.class_ == "dat-badge"


def test_the_top_bar_navigation_holds_the_selector_the_queue_and_the_buttons(tmp_path, monkeypatch):
    from gui.tile.analysis import context

    panel = _controls(tmp_path, monkeypatch)
    block_a = panel.controls.children[1]
    prev, nxt, jump, menu, _go = context.nav_widgets(block_a)
    counter, tally, bar = context.queue_widgets(block_a)

    def walk(w):
        yield w
        for c in getattr(w, "children", None) or []:
            if not isinstance(c, str):
                yield from walk(c)

    inside = list(walk(panel.sections.topnav))
    assert all(any(x is w for x in inside) for w in (prev, nxt, menu, tally, bar))
    assert any(isinstance(w, v.Select) for w in inside)  # the alert-set selector
    # and none of them is in the panel any more
    in_panel = list(walk(panel.controls))
    assert not any(x is prev or x is counter for x in in_panel)
