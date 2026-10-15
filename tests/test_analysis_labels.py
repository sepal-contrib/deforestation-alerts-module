"""A language switch rewrites the analysis screen's text in place (spec:
plan 2026-10-15-analysis-relabel). Each block is built in English, the
locale switches, and ``layout.relabel()`` runs, as ``_ScreenBridge`` does."""

import ipyvuetify as v
import pytest
from pysepal.i18n import set_locale

from gui.i18n import t
from gui.tile.analysis.analysis_tile import panel_sections  # noqa: F401  (Tasks 2-6)
from gui.tile.analysis.labels import Labels
from tests._i18n_sweep import build_layout, english_leftovers


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


def _switch(built, locale="fr"):
    """Switch the language and relabel, as ``_ScreenBridge`` does."""
    set_locale(locale)
    built.layout.relabel()


def test_add_runs_the_hook_now_and_on_every_run():
    calls = []
    labels = Labels()
    labels.add(lambda: calls.append(1))
    labels.add(lambda: calls.append(2), now=False)
    assert calls == [1]
    labels.run()
    assert calls == [1, 1, 2]


def test_a_failing_hook_does_not_stop_the_others(caplog):
    calls = []
    labels = Labels()

    def broken():
        raise RuntimeError("boom")

    labels.add(broken, now=False)
    labels.add(lambda: calls.append(1), now=False)
    labels.run()
    assert calls == [1]
    assert "relabelling failed" in caplog.text


def _topbar(layout):
    (bar,) = [w for w in _walk(layout.screen) if "defo-analysis-topbar" in (getattr(w, "class_", "") or "")]
    return bar


def _walk(root):
    out, stack = [], [root]
    while stack:
        w = stack.pop()
        out.append(w)
        stack += [k for k in (getattr(w, "children", None) or []) if not isinstance(k, str)]
    return out


def test_the_top_bar_chrome_follows_the_language(tmp_path, monkeypatch):
    built = build_layout(tmp_path, monkeypatch)
    bar = _topbar(built.layout)
    # the back button, the title and the help (its dialog included)
    chrome = [bar.children[0], bar.children[1]] + [w for w in _walk(bar) if isinstance(w, v.Dialog)]
    set_locale("fr")
    built.layout.relabel()
    for part in chrome:
        assert english_leftovers(part, "fr") == []


def test_the_about_dialog_stays_open_across_a_switch(tmp_path, monkeypatch):
    built = build_layout(tmp_path, monkeypatch)
    (dialog,) = [w for w in _walk(_topbar(built.layout)) if isinstance(w, v.Dialog)]
    dialog.v_model = True
    set_locale("fr")
    built.layout.relabel()
    assert dialog.v_model is True
    assert english_leftovers(dialog, "fr") == []


def test_repeated_relabels_keep_the_about_text_widget(tmp_path, monkeypatch):
    """``relabel`` runs on every screen change too: an unchanged text must not
    build (and leak) a new Markdown subtree each time."""
    built = build_layout(tmp_path, monkeypatch)
    (dialog,) = [w for w in _walk(_topbar(built.layout)) if isinstance(w, v.Dialog)]
    card_text = [w for w in _walk(dialog) if isinstance(w, v.CardText)][0]
    set_locale("fr")
    built.layout.relabel()
    (md,) = card_text.children
    built.layout.relabel()
    built.layout.relabel()
    assert card_text.children[0] is md


def test_the_sweep_catches_stale_text_in_every_field_it_reads():
    """The helper itself: one deliberately stale widget per field it reads,
    including composed text, so a later green sweep means something."""
    from pysepal import sepalwidgets as sw  # noqa: F401  (the Markdown case)
    from gui.i18n import t
    from gui.widget.handle_strip import HandleStrip

    set_locale("en")
    stale = {
        "children": v.Html(tag="span", children=[t("analysis_tile.nav.next_unreviewed")]),
        "label": v.Select(label=t("analysis_tile.filter.attribute")),
        "items": v.Select(items=[{"text": t("analysis_tile.filter.apply"), "value": 1}]),
        "v_model": v.Combobox(v_model=[{"text": t("analysis_tile.filter.clear"), "value": 1}]),
        "title": v.Btn(attributes={"title": t("analysis_tile.filter.button")}),
        "composed": v.Html(tag="span", children=[f"{t('analysis.timeline.max_cloud')} 40%"]),
        "save_first": v.Html(tag="span", children=[f"({t('analysis_tile.export_labels.save_first')})"]),
        "markdown": sw.Markdown(t("analysis_tile.info_md")),
    }
    strip = HandleStrip(lambda *_: None)
    strip.gap = t("analysis_tile.nav.next_unreviewed")
    stale["gap"] = strip
    for field, widget in stale.items():
        assert english_leftovers(widget, "fr"), f"the sweep misses stale {field}"


def test_the_sweep_catches_a_stale_handle_flag():
    """A handle's row (``{id, x, flag}``, as ``timeline._render_strip`` builds
    it) whose flag still carries an English message."""
    from datetime import date

    from deforestation_alerts.scripts import scene_index
    from gui.i18n import t
    from gui.widget.handle_strip import HandleStrip
    from tests.test_timeline import S2

    strip = HandleStrip(lambda *_: None)
    rid = scene_index.row_id(S2, date.fromisoformat("2026-03-05"))
    strip.before = {"id": rid, "x": 12.5, "flag": f"5 Mar · {t('analysis_tile.nav.next_unreviewed')}"}
    assert english_leftovers(strip, "fr"), "the sweep misses a stale handle flag"
    set_locale("fr")
    strip.before = {"id": rid, "x": 12.5, "flag": f"5 mars · {t('analysis_tile.nav.next_unreviewed')}"}
    assert english_leftovers(strip, "fr") == [], "a fresh French flag is not a leftover"


def test_the_top_bar_and_the_facts_follow_the_language(tmp_path, monkeypatch):
    built = build_layout(tmp_path, monkeypatch)
    panel = panel_sections(built.layout.controls)
    banner, facts = built.layout.controls.children[0], built.layout.controls.children[1]
    _switch(built)
    for part in (panel.topnav, banner, facts):
        assert english_leftovers(part, "fr") == []


def test_the_set_selector_imports_first_in_a_fresh_interpreter():
    """``gui.widget`` must not import the tile package (a cycle through
    ``analysis_tile``, which imports the selector)."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, "-c", "import gui.widget.set_selector"],
                         capture_output=True, text=True, cwd=root)
    assert out.returncode == 0, out.stderr[-800:]


def test_the_panel_blocks_follow_the_language(tmp_path, monkeypatch):
    built = build_layout(tmp_path, monkeypatch)
    panel = panel_sections(built.layout.controls)
    verdict = built.layout.controls.children[2]
    _switch(built)
    for part in (verdict, panel.questions.root, panel.earlier.root, *panel.footer.root.children[1:]):
        assert english_leftovers(part, "fr") == []
    # Refine's and Export's titles only: their badges and bodies are Tasks 4 and 5
    assert panel.refine.title_span.children == [t("analysis_tile.sections.refine")]
    assert panel.export.title_span.children == [t("analysis_tile.sections.export")]


def _three_kinds():
    """A radio, a chip group and a typed combobox, all on built-in options
    (labels from the catalogue, so a switch must re-word them)."""
    from deforestation_alerts.project.models import DRIVER_OPTIONS, Question, QuestionOption

    opts = [QuestionOption(code=c, label_key=k) for c, k in DRIVER_OPTIONS[:3]]
    label_key = "analysis_tile.questionarie.loss_driver_label"
    return [Question(key="one", label_key=label_key, options=opts),
            Question(key="some", label_key=label_key, multiple=True, options=opts),
            Question(key="typed", label_key=label_key, multiple=True, allow_typed=True, options=opts)]


def test_answers_survive_a_switch_and_nothing_is_marked_unsaved(tmp_path, monkeypatch):
    """Review Focus 4: a radio, a chip group and a typed combobox."""
    from deforestation_alerts.project.models import DRIVER_OPTIONS

    built = build_layout(tmp_path, monkeypatch, questions=_three_kinds())
    panel = panel_sections(built.layout.controls)
    ctls = dict(panel.questions.controls)
    assert (type(ctls["one"]), type(ctls["some"]), type(ctls["typed"])) == (v.RadioGroup, v.ChipGroup, v.Combobox)
    (agri, agri_key), (grazing, grazing_key), (logging, logging_key) = DRIVER_OPTIONS[:3]
    answers = {"one": [logging], "some": [agri, logging], "typed": [grazing, "Roads"]}
    built.analysis.answers.set(answers)
    built.analysis.dirty.set(False)
    rev = built.analysis.edit_rev.value
    _switch(built)
    # the same answers, the same controls, nothing to save
    assert built.analysis.answers.value == answers
    assert built.analysis.dirty.value is False and built.analysis.edit_rev.value == rev
    assert all(panel.questions.controls[k] is ctls[k] for k in ctls)
    # shown in the new words; the typed entry is the reviewer's text, kept as is
    assert ctls["one"].v_model == logging
    assert ctls["one"].children[2].label == t(logging_key)
    assert ctls["some"].v_model == [agri, logging]
    assert ctls["some"].children[0].children == [t(agri_key)]
    assert ctls["typed"].v_model == [{"text": t(grazing_key), "value": grazing}, "Roads"]
    assert english_leftovers(panel.questions.root, "fr") == []


def test_a_save_in_flight_keeps_its_buttons_held_across_a_switch(tmp_path, monkeypatch):
    """Review Focus 5."""
    built = build_layout(tmp_path, monkeypatch)
    footer = panel_sections(built.layout.controls).footer
    footer.save.disabled = footer.save_next.disabled = True
    footer.save.loading = True
    icon = footer.save_next.children[-1]
    _switch(built)
    assert footer.save.disabled is True and footer.save.loading is True
    assert footer.save_next.disabled is True
    assert footer.save_next.children[-1] is icon
    assert footer.save_next.children[0] == t("analysis_tile.verdict.save_next")  # read in French


def test_refine_polygon_follows_the_language(tmp_path, monkeypatch):
    built = build_layout(tmp_path, monkeypatch)
    refine = panel_sections(built.layout.controls).refine
    _switch(built)
    assert english_leftovers(refine.root, "fr") == []


_SQUARE = {"type": "Feature", "properties": {},
           "geometry": {"type": "Polygon", "coordinates": [
               [[0, 0], [0, 0.01], [0.01, 0.01], [0.01, 0], [0, 0]]]}}


def test_a_switch_mid_drawing_keeps_the_session(tmp_path, monkeypatch):
    """Review Focus 2, plus Review Focus 1 for the stop dialog: the reviewer
    has drawn, pressed Stop, and the "unsaved drawing" question is open."""
    from gui.tile.analysis.edit_tools import edit_widgets
    from tests._pane_helpers import reviewer_edits

    built = build_layout(tmp_path, monkeypatch)
    panes, analysis = built.layout.widgets.panes, built.analysis
    refine = panel_sections(built.layout.controls).refine
    ns = edit_widgets(refine.body_box.children[0])
    ns["start_btn"].fire_event("click", None)
    reviewer_edits(panes, [_SQUARE])
    assert analysis.drawing_unapplied.value is True, "the seed did not take"
    ns["stop_btn"].fire_event("click", None)
    assert ns["stop_dialog"].v_model is True, "Stop asks about the unsaved drawing"

    buttons = {k: ns[k].disabled for k in ("start_btn", "save_btn", "stop_btn")}
    envelope = dict(panes.widget.draw)
    kept = (panes.widget.draw_edit_mode, analysis.drawing_unapplied.value,
            analysis.dirty.value, analysis.edit_rev.value)
    _switch(built)
    assert ns["stop_dialog"].v_model is True
    assert {k: ns[k].disabled for k in buttons} == buttons
    assert panes.widget.draw == envelope, "the drawing (and its gen / seq) is untouched"
    assert (panes.widget.draw_edit_mode, analysis.drawing_unapplied.value,
            analysis.dirty.value, analysis.edit_rev.value) == kept
    assert english_leftovers(ns["stop_dialog"], "fr") == []


def test_a_models_found_line_and_the_badge_follow_the_language(tmp_path, monkeypatch):
    import geopandas as gpd
    from shapely.geometry import box

    from gui.i18n import format_number, tn
    from gui.tile.analysis.edit_tools import edit_widgets

    built = build_layout(tmp_path, monkeypatch)
    refine = panel_sections(built.layout.controls).refine
    ns = edit_widgets(refine.body_box.children[0])
    built.analysis.prediction_shapes.set({1: (3, 12.5)})
    built.analysis.defo_layer.set(gpd.GeoDataFrame(geometry=[box(0, 0, 0.01, 0.01)], crs="EPSG:4326"))
    _switch(built)
    assert ns["dl1_found"].children == [t("analysis_tile.model_labels.found", patches=tn(
        "analysis_tile.model_labels.patches", 3), area=format_number(12.5, 1))]
    assert ns["dl2_found"].children == []
    assert english_leftovers(refine.root, "fr") == []
