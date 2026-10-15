"""A language switch rewrites the analysis screen's text in place (spec:
plan 2026-10-15-analysis-relabel). Each block is built in English, the
locale switches, and ``layout.relabel()`` runs, as ``_ScreenBridge`` does."""

import ipyvuetify as v
import pytest
from pysepal.i18n import set_locale

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
