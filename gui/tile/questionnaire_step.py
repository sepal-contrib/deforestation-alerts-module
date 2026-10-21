"""The Questionnaire step (spec D3): the questions every alert is reviewed
with, in the order Analysis asks them, and the project's report template with
the placeholders it can use. The last workflow step, so it ends with *Start
analysis*."""

from pathlib import Path

import reacton.ipyvuetify as rv
import solara

from deforestation_alerts.project.questions import REPORT_PLACEHOLDERS, attribute_columns, column
from deforestation_alerts.project.review_copy import read_only
from deforestation_alerts.project.review_status import VERDICT_LABEL_KEYS
from gui.i18n import t, tn
from gui.scripts import questions as qs
from gui.scripts.notify_bridge import current_notifier
from gui.scripts.setup_guard import refuse_setup, update_setup
from gui.store.workflow_steps import active_set
from gui.widget.confirm_dialog import ConfirmDialog
from gui.widget.copy_chip import CopyChipWidget
from gui.widget.help import StepIntro
from gui.widget.product_table import ProductTable
from gui.widget.question_modal import QuestionModal
from gui.widget.report_template import ReportTemplateField, template_state
from gui.widget.text_style import MUTED

def question_tags(q) -> list:
    """The chips under a question: how it is answered, whether it is required
    and the verdicts it is asked for."""
    tags = [{"value": t("steps.questionnaire.tag_several" if q.multiple else "steps.questionnaire.tag_one")}]
    if q.options:
        tags.append({"value": tn("steps.questionnaire.tag_options", len(q.options))})
    if q.allow_typed:
        tags.append({"value": t("steps.questionnaire.tag_typed")})
    if q.required:
        tags.append({"value": t("steps.questionnaire.tag_required"), "color": "error"})
    if len(q.ask_for) == len(VERDICT_LABEL_KEYS):
        tags.append({"value": t("steps.questionnaire.tag_all_verdicts")})
    else:
        tags.extend({"value": t(VERDICT_LABEL_KEYS[v])} for v in q.ask_for)
    return tags


def answered_count(gdf, q) -> int:
    """How many alerts of ``gdf`` hold an answer to ``q`` (0 without its column)."""
    col = column(q)
    if gdf is None or col not in getattr(gdf, "columns", ()):
        return 0
    return int(sum(1 for v in gdf[col] if isinstance(v, str) and v.strip()))


def _delete_message(gdf, q) -> str:
    """What deleting ``q`` does to the answers: with no alert set loaded there
    is no count to give, only the promise that saved answers are kept."""
    if q is None:
        return ""
    if gdf is None:
        return t("steps.questionnaire.delete_message_no_set")
    return tn("steps.questionnaire.delete_message", answered_count(gdf, q))


@solara.component
def _Placeholders(questions):
    def _chip(name, review):
        text = "{" + name + "}"
        # A click copies in the browser (CopyChipWidget); the kernel only toasts,
        # through the bus resolved at the click (this step renders detached).
        CopyChipWidget.element(text=text, review=review,
                               on_copied_count=lambda _n, text=text: current_notifier().info(
                                   t("steps.questionnaire.copied", placeholder=text))).key(text)

    solara.Text(t("steps.questionnaire.placeholders_label"), style=MUTED + "font-size:0.8rem;")
    with rv.Html(tag="div", style_="display:flex;flex-wrap:wrap;"):
        for name in REPORT_PLACEHOLDERS:
            _chip(name, False)
        # Each name once (a chip is keyed by its text): a hand-edited
        # project.json may repeat a key, or key a question ``verdict`` or after
        # a report placeholder, and reacton refuses a duplicate key.
        for name in dict.fromkeys(["verdict", *(q.key for q in questions)]):
            if name not in REPORT_PLACEHOLDERS:
                _chip(name, True)
    solara.Text(t("steps.questionnaire.placeholders_review"), style=MUTED + "font-size:0.75rem;")


#: the template line's chip per ``template_state``: its text and colour
_TEMPLATE_CHIPS = {"default": ("steps.questionnaire.template_chip_default", None),
                   "custom": ("steps.questionnaire.template_chip_custom", "primary"),
                   "missing": ("steps.questionnaire.template_chip_missing", "warning")}


@solara.component
def _TemplateCard(app_state, questions):
    """The report template (spec 2026-10-14 §3): one line naming it, and the
    picker with the placeholders under its caret. A custom file that is gone
    holds the card open (the caret disabled), so it is picked again or
    cleared. Whether it is open is this card's own state, never the project's."""
    p = app_state.project.value
    expanded, set_expanded = solara.use_state(False)
    state = template_state(p.report_template)
    shown = expanded or state == "missing"
    name = Path(p.report_template).name if p.report_template else t("steps.questionnaire.template_default")
    chip_key, chip_color = _TEMPLATE_CHIPS[state]
    with rv.Card(outlined=True, class_="pa-3"):
        with solara.Row(style="align-items:center;gap:8px;flex-wrap:nowrap;"):
            rv.Icon(small=True, children=["mdi-file-word-outline"])
            with rv.Html(tag="div", style_="flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;"
                                           "white-space:nowrap;"):
                solara.Text(t("steps.questionnaire.template_title") + " · ", style=MUTED + "font-size:0.8rem;")
                solara.Text(name, style="font-weight:600;")
            rv.Chip(x_small=True, outlined=True, color=chip_color, children=[t(chip_key)])
            solara.Button("", icon_name="mdi-chevron-up" if shown else "mdi-chevron-down", icon=True, text=True,
                          x_small=True, disabled=state == "missing", classes=["defo-template-toggle"],
                          on_click=lambda: set_expanded(not expanded))
        if state == "missing":
            solara.Text(t("steps.questionnaire.template_missing"), classes=["warning--text"],
                        style="display:block;font-size:0.75rem;")
        if shown:
            with solara.Column(style="gap:8px;margin-top:8px;"):
                ReportTemplateField(app_state=app_state)
                _Placeholders(questions=questions)


@solara.component
def QuestionnaireStep(app_state):
    p = app_state.project.value
    modal_open = solara.use_reactive(False)
    editing, set_editing = solara.use_state(None)
    pending_delete, set_pending_delete = solara.use_state(None)
    if p is None:
        solara.Info(t("workflow.lock_project"))
        return
    if active_set(p) is None:  # the step's gate (workflow_steps): after Integrate & vectorize
        solara.Info(t("workflow.lock_analysis"))
        return
    name = p.name

    def _save(question, previous_key):
        def _apply(live):
            out = list(live.questions)
            at = next((i for i, q in enumerate(out) if q.key == previous_key), None)
            if at is None:
                out.append(question)
            else:
                out[at] = question
            live.questions = out

        update_setup(app_state, name, _apply)

    def _move(key, step):
        def _apply(live):
            out = list(live.questions)
            i = next((i for i, q in enumerate(out) if q.key == key), None)
            if i is None:
                return
            j = i + step
            if 0 <= j < len(out):
                out[i], out[j] = out[j], out[i]
                live.questions = out

        update_setup(app_state, name, _apply)

    def _delete():
        key = pending_delete
        set_pending_delete(None)
        update_setup(app_state, name, lambda live: setattr(
            live, "questions", [q for q in live.questions if q.key != key]))

    def _edit(q):
        if refuse_setup(app_state):
            return
        set_editing(q)
        modal_open.set(True)

    def _open_add():
        if refuse_setup(app_state):
            return
        set_editing(None)
        modal_open.set(True)

    ro = read_only(p)  # a review copy asks the creator's questions (spec §6.3)
    last = len(p.questions) - 1
    rows = [{"key": q.key,
             "cells": [{"value": qs.question_label(q), "chips": question_tags(q), "stack": True}],
             "notes": [{"text": "{" + q.key + "}", "tone": None}],
             "actions": [
                 {"kind": "move_up", "disabled": ro or i == 0, "on_click": (lambda k=q.key: _move(k, -1))},
                 {"kind": "move_down", "disabled": ro or i == last, "on_click": (lambda k=q.key: _move(k, 1))},
                 {"kind": "edit", "disabled": ro, "on_click": (lambda q=q: _edit(q))},
                 {"kind": "delete", "disabled": ro, "on_click": (lambda k=q.key: set_pending_delete(k))},
             ]} for i, q in enumerate(p.questions)]
    doomed = next((q for q in p.questions if q.key == pending_delete), None)

    with solara.Column(style="gap:16px;"):
        StepIntro(t("steps.questionnaire.description"), t("steps.questionnaire.info_header"),
                  t("steps.questionnaire.info_md"))
        with rv.Card(outlined=True, class_="pa-3"):
            solara.Button(t("steps.questionnaire.add"), icon_name="mdi-plus", color="primary", small=True, outlined=True,
                          block=True, disabled=ro, on_click=_open_add)
            ProductTable(title=t("steps.questionnaire.questions_title"),
                         columns=[{"label": t("steps.questionnaire.col_question")}], rows=rows,
                         empty_text=t("steps.questionnaire.empty"))
            rv.Switch(label=t("steps.questionnaire.expanded"), v_model=p.questions_expanded,
                      on_v_model=lambda on: app_state.update_project(
                          name, lambda live: setattr(live, "questions_expanded", bool(on))),
                      dense=True, hide_details=True, class_="mt-3")
            solara.Text(t("steps.questionnaire.expanded_hint"), style=MUTED + "font-size:0.75rem;")
        _TemplateCard(app_state=app_state, questions=p.questions)

    QuestionModal(open_=modal_open, questions=p.questions, editing=editing, on_save=_save,
                  columns=attribute_columns(p))
    ConfirmDialog(
        open=doomed is not None,
        on_cancel=lambda: set_pending_delete(None),
        on_confirm=_delete,
        title=t("steps.questionnaire.delete_title", name=qs.question_label(doomed) if doomed else ""),
        message=_delete_message(app_state.alerts_gdf.value, doomed),
        confirm_label=t("common.delete"),
    )
