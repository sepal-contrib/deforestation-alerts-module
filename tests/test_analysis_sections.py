"""The collapsible sections of the Analysis panel, and the assembled panel.

The body is hidden with ``display``, never dropped from the tree: it holds the
maps' draw controls and the CNN toolbars, whose imperative shown/disabled state
must survive a collapse, and the step container already destroys and rebuilds
this DOM.

The second half drives ``analysis_tile.build_controls`` -- the panel as the
tile assembles it: the head, block A, block B and the two sections, with the
export section's save runner shared with the verdict block.
"""

from datetime import datetime
from types import SimpleNamespace

import ipyvuetify as v
import pytest
from pysepal.i18n import set_locale

from deforestation_alerts.project.models import Project
from gui.i18n import t
from gui.store.state_manager import AppState
from gui.tile.analysis import analysis_tile
from gui.tile.analysis.section import build_section
from gui.tile.analysis.state import AnalysisState


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


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
    assert s.badge.children == ["2 files ready"] and s.badge.color == "primary"
    s.set_badge("")
    assert s.badge.children == [] and "display: none" in s.badge.style_


def test_set_open_drives_the_same_state():
    s = build_section("Refine polygon", v.Html(tag="div"))
    s.set_open(True)
    assert s.is_open() is True and "display: none" not in s.body_box.style_
    s.set_open(False)
    assert s.is_open() is False


# ---------------------------------------------------------------------------
# The assembled panel (Task 10): head, block A, block B, the two sections
# ---------------------------------------------------------------------------
def _controls(tmp_path, monkeypatch):
    """``build_controls`` as the tile builds it, on stand-in models.

    The heavy widgets are only touched inside the edit/CNN handlers (as
    tests/test_feedback_notifications.py's ``edit_tools_card`` relies on), so a
    bare namespace is enough to assemble the panel.
    """
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    app_state = AppState()
    app_state.new_project_state(
        Project(name="d", created_at=datetime.now(), updated_at=datetime.now()))
    analysis = AnalysisState()
    model = SimpleNamespace(recipe_folder_path=str(tmp_path), custom_report_template=None,
                            alert_set=None, remember_position=lambda *_: None)
    controls = analysis_tile.build_controls(
        widgets=SimpleNamespace(), app_state=app_state, analysis=analysis,
        aux_model=model, app_tile_model=model, id_field=v.TextField(v_model=None))
    return SimpleNamespace(controls=controls, app_state=app_state, analysis=analysis,
                           sections=analysis_tile.panel_sections(controls))


def test_the_panel_is_the_head_the_two_blocks_and_the_two_sections(tmp_path, monkeypatch):
    """The six stacked always-open cards are gone: what is left is the head
    (help, set selector, set-status banner), block A, block B and the two
    collapsible sections, in the order of the spec's §3-§6.
    """
    from gui.tile.analysis import context, review_panel

    panel = _controls(tmp_path, monkeypatch)
    *head, block_a, block_b, refine, export = panel.controls.children
    assert len(head) == 3                       # help + selector + banner
    assert context.facts_widgets(block_a)       # block A owns the facts grid
    assert review_panel.verdict_widgets(block_b)  # block B owns the verdict
    assert [refine, export] == [panel.sections.refine.root, panel.sections.export.root]


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

    Its ``apply_save`` is what enables *Download alert data* and moves the
    export badge on; a verdict block that made a second runner would leave that
    button dark for ever, with nothing to say why. So the Save in block B must
    run the runner the tile passed in, and the block must not build one.
    """
    from gui.tile.analysis import export as export_mod
    from gui.tile.analysis import review_panel
    from tests.test_analysis_download import _alerts_gdf, _build_button

    made = []
    monkeypatch.setattr(review_panel, "make_save_runner",
                        lambda *a, **k: made.append(1) or (lambda *b, **kw: None))
    monkeypatch.setattr(export_mod, "spawn_in_context", lambda target, *a, **k: target())
    monkeypatch.setattr(export_mod, "_save_attributes", lambda *a, **k: True)

    panel = _controls(tmp_path, monkeypatch)
    panel.app_state.alerts_gdf.value = _alerts_gdf()
    panel.app_state.actual_alert_id.value = 0
    build_btn = _build_button(panel.sections.export_parts.body)
    _, _, _, save_next, save, _ = review_panel.verdict_widgets(panel.controls.children[4])

    assert build_btn.disabled is True
    save.fire_event("click", None)

    assert made == []                      # block B built no runner of its own
    assert build_btn.disabled is False     # the export section's apply_save ran
    assert panel.sections.export.badge.children == [t("analysis_tile.sections.export_build")]
    assert save_next is not save
