"""The add/edit question form of the Questionnaire step (spec D3), on the
form frame (``gui/widget/form_dialog.py``, 420px).

The form edits a *draft* (plain dicts) and turns it into a ``Question`` only
on Save (``finish_question``): the key and new option codes are made then, and
an edit keeps the question's key and every existing option's code, so a
renamed question or option never orphans the answers already stored (U5).
"""

from typing import Optional

import reacton.ipyvuetify as rv
import solara

from deforestation_alerts.project.models import VERDICTS, Question, QuestionOption
from deforestation_alerts.project.questions import new_code, new_key
from gui.i18n import t
from gui.scripts import questions as qs
from gui.widget.enter_key import ENTER_DEFAULT
from gui.widget.form_dialog import field_group, form_card, use_form_dialog
from gui.widget.text_style import MUTED

_VERDICT_KEYS = {"Confirmed": "analysis_tile.verdict.true", "False Positive": "analysis_tile.verdict.false",
                 "Need revision": "analysis_tile.verdict.revision"}


def empty_draft() -> dict:
    return {"label": "", "multiple": False, "allow_typed": False, "required": False,
            "ask_for": list(VERDICTS), "options": []}


def draft_of(q: Question) -> dict:
    """An existing question as a draft, built-in labels shown in the current language."""
    return {"label": qs.question_label(q), "multiple": q.multiple, "allow_typed": q.allow_typed,
            "required": q.required, "ask_for": list(q.ask_for),
            "options": [{"code": o.code, "label": qs.option_label(o), "label_key": o.label_key}
                        for o in q.options]}


def question_error(draft: dict) -> Optional[str]:
    """The message key of what keeps ``draft`` from being saved, or None."""
    if not draft["label"].strip():
        return "steps.questionnaire.err_label"
    labels = [o["label"].strip() for o in draft["options"]]
    if not labels and not draft["allow_typed"]:
        return "steps.questionnaire.err_options"
    if any(not x for x in labels):
        return "steps.questionnaire.err_option_blank"
    # Answers are stored joined with ", ", so a comma would read back as two answers.
    if any("," in x for x in labels):
        return "steps.questionnaire.err_option_comma"
    if len({x.casefold() for x in labels}) != len(labels):
        return "steps.questionnaire.err_option_duplicate"
    if not draft["ask_for"]:
        return "steps.questionnaire.err_ask_for"
    return None


def _kept_label(text: str, label_key: Optional[str]):
    """``(label, label_key)``: a built-in label left as shown keeps its key."""
    if label_key and text.strip() == t(label_key):
        return "", label_key
    return text.strip(), None


def finish_question(draft: dict, questions: list, editing: Optional[Question]) -> Question:
    """The ``Question`` ``draft`` describes: a new one gets its key and codes
    made here; an edit keeps ``editing``'s key and every existing option's code."""
    others = [q for q in questions if editing is None or q.key != editing.key]
    options = []
    for o in draft["options"]:
        label, label_key = _kept_label(o["label"], o.get("label_key"))
        code = o["code"] or new_code(label, options)
        options.append(QuestionOption(code=code, label=label, label_key=label_key))
    label, label_key = _kept_label(draft["label"], editing.label_key if editing else None)
    return Question(
        key=editing.key if editing else new_key(draft["label"], others),
        label=label, label_key=label_key, multiple=draft["multiple"], options=options,
        allow_typed=draft["allow_typed"], required=draft["required"],
        ask_for=[v for v in VERDICTS if v in draft["ask_for"]],
    )


@solara.component
def _QuestionFields(open_, questions, editing, on_save):
    """The form's card. Mounted only while the form is open, so every opening
    starts from the question being edited (or an empty draft)."""
    draft, set_draft = solara.use_state(draft_of(editing) if editing else empty_draft())
    error = question_error(draft)

    def _set(**changes):
        set_draft({**draft, **changes})

    def _set_option(i, text):
        options = [dict(o) for o in draft["options"]]
        options[i]["label"] = text or ""
        _set(options=options)

    def _move(i, step):
        options = list(draft["options"])
        j = i + step
        if 0 <= j < len(options):
            options[i], options[j] = options[j], options[i]
            _set(options=options)

    def _save():
        if question_error(draft):
            return
        q = finish_question(draft, questions, editing)
        on_save(q, editing.key if editing else None)
        open_.set(False)

    def _actions():
        if editing:
            label, icon = t("common.save"), "mdi-content-save-outline"
        else:
            label, icon = t("steps.questionnaire.add"), "mdi-plus"
        solara.Button(label, icon_name=icon, color="primary", small=True, disabled=error is not None,
                      classes=[ENTER_DEFAULT], on_click=_save)

    title = t("steps.questionnaire.edit_title") if editing else t("steps.questionnaire.add_title")
    # Not before a label is typed: an empty form is not wrong yet.
    with form_card(title, on_cancel=lambda: open_.set(False), actions=_actions,
                   error=t(error) if error and draft["label"] else None):
        rv.TextField(label=t("steps.questionnaire.field_label"), v_model=draft["label"],
                     on_v_model=lambda x: _set(label=x or ""), dense=True, outlined=True, autofocus=True,
                     hide_details=True)
        with field_group(t("steps.questionnaire.answer_label")):
            with rv.RadioGroup(v_model="several" if draft["multiple"] else "one", row=True, dense=True,
                               hide_details=True, on_v_model=lambda x: _set(multiple=x == "several")):
                rv.Radio(label=t("steps.questionnaire.answer_one"), value="one")
                rv.Radio(label=t("steps.questionnaire.answer_several"), value="several")
        with field_group(t("steps.questionnaire.options_label")):
            for i, o in enumerate(draft["options"]):
                with rv.Html(tag="div", style_="display:flex;align-items:center;gap:2px;"):
                    rv.TextField(label=t("steps.questionnaire.field_option", n=i + 1), v_model=o["label"],
                                 on_v_model=lambda x, i=i: _set_option(i, x), dense=True, hide_details=True)
                    solara.Button("", icon_name="mdi-arrow-up", icon=True, x_small=True, disabled=i == 0,
                                  on_click=lambda i=i: _move(i, -1))
                    solara.Button("", icon_name="mdi-arrow-down", icon=True, x_small=True,
                                  disabled=i == len(draft["options"]) - 1, on_click=lambda i=i: _move(i, 1))
                    solara.Button("", icon_name="mdi-close", icon=True, x_small=True,
                                  on_click=lambda i=i: _set(options=[x for j, x in enumerate(draft["options"])
                                                                     if j != i]))
            solara.Button(t("steps.questionnaire.add_option"), icon_name="mdi-plus", text=True, small=True,
                          color="primary",
                          on_click=lambda: _set(options=[*draft["options"],
                                                         {"code": None, "label": "", "label_key": None}]))
        rv.Switch(label=t("steps.questionnaire.allow_typed"), v_model=draft["allow_typed"], dense=True,
                  hide_details=True, on_v_model=lambda x: _set(allow_typed=bool(x)))
        rv.Switch(label=t("steps.questionnaire.required"), v_model=draft["required"], dense=True,
                  hide_details=True, on_v_model=lambda x: _set(required=bool(x)))
        with field_group(t("steps.questionnaire.ask_for_label")):
            for verdict in VERDICTS:
                rv.Checkbox(label=t(_VERDICT_KEYS[verdict]), v_model=verdict in draft["ask_for"], dense=True,
                            hide_details=True,
                            on_v_model=lambda on, verdict=verdict: _set(ask_for=[
                                x for x in VERDICTS
                                if (x == verdict and on) or (x != verdict and x in draft["ask_for"])]))
        key = editing.key if editing else (new_key(draft["label"], questions) if draft["label"].strip() else "…")
        solara.Text(t("steps.questionnaire.placeholder_line", placeholder="{" + key + "}"),
                    style=MUTED + "font-size:0.8rem;")


@solara.component
def QuestionModal(open_, questions, editing, on_save):
    """``editing``: the ``Question`` being edited, or None to add one.
    ``on_save(question, previous_key)``; the form closes itself after."""
    with use_form_dialog(open_, max_width="420px"):
        if open_.value:
            _QuestionFields(open_=open_, questions=questions, editing=editing, on_save=on_save)
