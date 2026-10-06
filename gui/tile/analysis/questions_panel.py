"""The Questions section of the Analysis panel (spec D4): every question of
the project, in its order, in one collapsible section closed by default.

Architecture layer: view (raw-widget builder, no ``@solara.component``).

Raw ipyvuetify, built once per session like the rest of the panel. The body
is rebuilt whenever the project's questions change (compared by value, so an
auto-save publish is not a change). Answers live in ``AnalysisState.answers``
-- the controls only show and write them -- so a rebuild, a verdict change or
an alert change never loses a draft.

A control writes the answers only when its ``change`` event carries something
other than what the answers already hold: showing answers set from outside (an
alert load, a reset) is a Python ``v_model`` write, and should the front end
ever echo it back as a ``change``, that echo is not an edit and must not mark
the alert unsaved.
"""

from types import SimpleNamespace

import ipyvuetify as v

from gui.i18n import t
from gui.scripts import questions as qs
from gui.tile.analysis.section import build_section
from gui.tile.analysis.state import touch
from gui.widget.text_style import MUTED

_HIDDEN = "display: none;"


def badge_text(questions, answers, verdict):
    """``(text, colour)`` of the section's badge: the questions asked for
    ``verdict`` that have an answer, out of those asked, and ``"warning"`` when
    one of them is required and unanswered (the save would refuse it). No
    question asked -- none in the project, or none for this verdict -- reads
    "no questions", not "0 of 0 answered"."""
    shown = [q for q in questions if qs.visible(q, verdict)]
    if not shown:
        return t("analysis_tile.questions.badge_none"), None
    answered = [q for q in shown if answers.get(q.key)]
    missing = any(q.required and not answers.get(q.key) for q in shown)
    key = "analysis_tile.questions.badge_missing" if missing else "analysis_tile.questions.badge"
    return t(key, answered=len(answered), total=len(shown)), ("warning" if missing else None)


def _title(q):
    children = [qs.question_label(q)]
    if q.required:
        children.append(v.Html(tag="span", class_="error--text ml-1", children=["*"]))
    return v.Html(tag="div", class_="body-2 mb-1", children=children)


def _close(widget):
    """Close ``widget`` and every widget below it (``close`` alone leaves the
    children's comms open)."""
    for child in getattr(widget, "children", None) or []:
        if not isinstance(child, str):
            _close(child)
    widget.close()


def _control(q, analysis, live):
    """``q``'s control. ``live(ctl)`` says whether it is still the section's:
    a control replaced by a rebuild never writes again."""
    def _write(codes_):
        if not live(ctl):
            return
        current = analysis.answers.value or {}
        if current.get(q.key, []) == codes_:
            return  # an echo of what is shown, not an edit
        analysis.answers.set({**current, q.key: codes_})
        touch(analysis)

    if q.allow_typed:
        # Codes as values, labels as text, objects in and out: Vuetify 2's
        # combobox draws a chip from the selected value itself.
        ctl = v.Combobox(items=qs.items(q), v_model=[] if q.multiple else None, return_object=True,
                         multiple=q.multiple, chips=True, small_chips=True, clearable=True, dense=True,
                         hide_details=True, label=t("analysis_tile.questions.typed_hint"))
        ctl.on_event("change", lambda _w, _e, data: _write(qs.codes(q, qs.encode(q, data))))
    elif q.multiple:
        ctl = v.ChipGroup(multiple=True, column=True, v_model=[], active_class="primary--text",
                          children=[v.Chip(filter=True, outlined=True, small=True, value=o.code,
                                           children=[qs.option_label(o)]) for o in q.options])
        ctl.on_event("change", lambda _w, _e, data: _write(list(data or [])))
    else:
        ctl = v.RadioGroup(v_model=None, dense=True, hide_details=True, class_="mt-0",
                           children=[v.Radio(label=qs.option_label(o), value=o.code) for o in q.options])
        ctl.on_event("change", lambda _w, _e, data: _write([data] if data else []))
    return ctl


def _show(q, ctl, codes_):
    if isinstance(ctl, v.Combobox):
        items = qs.as_items(q, codes_)
        ctl.v_model = items if q.multiple else (items[0] if items else None)
    elif isinstance(ctl, v.ChipGroup):
        ctl.v_model = list(codes_)
    else:
        ctl.v_model = codes_[0] if codes_ else None


def build_questions_section(app_state, analysis):
    """Build the section; return its ``build_section`` namespace plus the
    ``controls`` and ``wrappers`` of the current questions, by key (both
    dicts are updated in place by a rebuild)."""
    section = build_section(t("analysis_tile.sections.questions"), v.Html(tag="div"))
    controls, wrappers = {}, {}
    current = {"questions": None}
    # Says why the body is empty: no question in the project, or none asked
    # for the chosen verdict.
    note = v.Html(tag="div", class_="caption", style_=MUTED, children=[])

    def _sync(*_):
        questions = current["questions"] or ()
        answers = analysis.answers.value or {}
        verdict = analysis.verdict.value
        shown = 0
        for q in questions:
            asked = qs.visible(q, verdict)
            shown += asked
            wrappers[q.key].style_ = "" if asked else _HIDDEN
            _show(q, controls[q.key], answers.get(q.key, []))
        if shown:
            note.children, note.style_ = [], _HIDDEN
        else:
            key = "analysis_tile.questions.none_for_verdict" if questions else "analysis_tile.questions.none"
            note.children, note.style_ = [t(key)], MUTED
        section.set_badge(*badge_text(questions, answers, verdict))

    def _rebuild(*_):
        p = app_state.project.value
        questions = tuple(p.questions) if p is not None else ()
        if questions == current["questions"]:
            return
        current["questions"] = questions
        old = list(wrappers.values())
        controls.clear()
        wrappers.clear()
        for q in questions:
            controls[q.key] = _control(q, analysis, live=lambda ctl, key=q.key: controls.get(key) is ctl)
            wrappers[q.key] = v.Html(tag="div", class_="mb-3", children=[_title(q), controls[q.key]])
        section.body_box.children = [*wrappers.values(), note]
        # Off the tree now: close their comms (each rebuild would leak a
        # set otherwise); ``live`` already keeps their handlers from writing.
        for wrapper in old:
            _close(wrapper)
        _sync()

    # Registered once: the panel is built once per session.
    app_state.project.subscribe(_rebuild)
    analysis.answers.subscribe(_sync)
    analysis.verdict.subscribe(_sync)
    _rebuild()
    return SimpleNamespace(root=section.root, set_open=section.set_open, is_open=section.is_open,
                           set_badge=section.set_badge, badge=section.badge, body_box=section.body_box,
                           controls=controls, wrappers=wrappers)
