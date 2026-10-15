"""What an analysis widget tree still says in English after a language switch.

``english_leftovers(root, locale)`` walks every string under ``root`` that a
reviewer can see or a screen reader can read. It returns those that are an
English catalogue message which ``locale``'s catalogue translates
differently: text built in English and never written again.

It is a net, not a proof. It cannot see a date or a number formatted for the
old locale, so the owning tasks assert those exactly (``format_date`` /
``format_number`` in the new locale). The reviewer's own text can equal an
English message: exclude such a hit by its widget and trait in the test that
meets it, never by its text.
"""

import re
from datetime import datetime
from functools import lru_cache
from types import SimpleNamespace

from ipywidgets import Widget
from pysepal.i18n.loading import load_locale

from gui.i18n import MESSAGES_DIR

_TEXT_TRAITS = ("label", "hint", "placeholder")
_ATTRS = ("title", "aria-label")


@lru_cache(maxsize=None)
def _patterns(source, target):
    src, other = load_locale(MESSAGES_DIR, source), load_locale(MESSAGES_DIR, target)
    out = []
    for key, msg in src.messages.items():
        if other.messages.get(key, msg) == msg:
            continue  # untranslated or identical: nothing to catch
        literal = re.sub(r"\W", "", re.sub(r"\{[^}]*\}", "", msg))
        if len(literal) < 4:
            continue  # mostly placeholders: it would match anything
        rx = re.sub(r"\\\{[^}]*\\\}", ".+?", re.escape(msg))
        out.append((len(literal), key, re.compile(rx, re.S)))
    out.sort(key=lambda p: -p[0])  # the most specific template names the hit
    return tuple((key, n, rx) for n, key, rx in out)


def _strings(root):
    stack, seen = [root], set()
    while stack:
        w = stack.pop()
        if id(w) in seen:
            continue
        seen.add(id(w))
        kids = list(getattr(w, "children", None) or [])
        for slot in getattr(w, "v_slots", None) or []:
            c = slot.get("children")
            kids += c if isinstance(c, list) else [c]
        for k in kids:
            if isinstance(k, str):
                yield w, "children", k
            elif isinstance(k, Widget):
                stack.append(k)
        for trait in _TEXT_TRAITS:
            val = getattr(w, trait, None)
            if isinstance(val, str) and val:
                yield w, trait, val
        for m in getattr(w, "error_messages", None) or []:
            if isinstance(m, str):
                yield w, "error_messages", m
        for item in getattr(w, "items", None) or []:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                yield w, "items", item["text"]
        attrs = getattr(w, "attributes", None)
        if isinstance(attrs, dict):
            for a in _ATTRS:
                if isinstance(attrs.get(a), str):
                    yield w, a, attrs[a]
        labels = getattr(w, "labels", None)
        if isinstance(labels, dict):
            for val in labels.values():
                if isinstance(val, str):
                    yield w, "labels", val
        # a combobox's selected items carry their own text
        selected = getattr(w, "v_model", None)
        for item in selected if isinstance(selected, list) else [selected]:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                yield w, "v_model", item["text"]
        # HandleStrip: the bracket and each row's date flag
        gap = getattr(w, "gap", None)
        if isinstance(gap, str) and gap:
            yield w, "gap", gap
        for row in [*(getattr(w, "candidates", None) or []), getattr(w, "before", None), getattr(w, "after", None)]:
            if isinstance(row, dict) and isinstance(row.get("flag"), str):
                yield w, "flag", row["flag"]
        # pysepal's Markdown renders into a template
        template = getattr(w, "template", None)
        if isinstance(template, str) and type(w).__name__ != "HandleStrip":
            yield w, "template", re.sub(r"<[^>]+>", " ", template)


#: A message this long is searched for inside a string, not only matched
#: whole: it catches composed text ("Max cloud 40%", "(save first)") while a
#: short word ("Next") cannot hit inside unrelated text.
_SEARCH_FROM = 8


def leftovers_of(root, source, target):
    """``[(key, widget class, where, text)]``: each string under ``root``
    still in ``source``'s words although ``target`` words it differently."""
    pats = _patterns(source, target)
    hits = set()
    for w, where, text in _strings(root):
        text = text.strip()
        for key, literal_len, rx in pats:
            if rx.fullmatch(text) or (literal_len >= _SEARCH_FROM and rx.search(text)):
                hits.add((key, type(w).__name__, where, text))
                break
    return sorted(hits)


def english_leftovers(root, locale):
    """``leftovers_of(root, "en", locale)``."""
    return leftovers_of(root, "en", locale)


def build_layout(tmp_path, monkeypatch, questions=None):
    """The whole analysis screen as the shell builds it, headless (no Earth
    Engine), on a fresh project (with ``questions`` in place of the default
    ones, when given). Call it with the locale already set."""
    from deforestation_alerts.project.models import Project
    from gui.store.state_manager import AppState
    from gui.tile.analysis.analysis_tile import build_analysis_layout
    from gui.tile.analysis.state import AnalysisState

    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    app_state = AppState()
    project = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    if questions is not None:
        project.questions = questions
    app_state.new_project_state(project)
    analysis = AnalysisState()
    model = SimpleNamespace(recipe_folder_path=str(tmp_path), custom_report_template=None, combined_path=None,
                            alert_set=None, remember_position=lambda *_: None, record_review=lambda *_: None)
    layout = build_analysis_layout(aux_model=model, selected_alerts_model=model, analysis=analysis,
                                   app_tile_model=model, app_state=app_state, gee_interface=None)
    return SimpleNamespace(layout=layout, app_state=app_state, analysis=analysis)
