"""The Questionnaire step (spec D3): the questions every alert is reviewed
with, in the order Analysis asks them, and the project's report template with
the placeholders it can use. The last workflow step, so it ends with *Start
analysis*."""

import reacton.ipyvuetify as rv
import solara

from deforestation_alerts.project.questions import REPORT_PLACEHOLDERS, attribute_columns, column
from gui.i18n import t, tn
from gui.scripts import questions as qs
from gui.scripts.notify_bridge import current_notifier
from gui.store.workflow_steps import active_set
from gui.widget.confirm_dialog import ConfirmDialog
from gui.widget.copy_chip import CopyChipWidget
from gui.widget.help import StepIntro
from gui.widget.product_table import ProductTable
from gui.widget.question_modal import QuestionModal
from gui.widget.report_template import ReportTemplateField
from gui.widget.text_style import MUTED

_VERDICT_KEYS = {"Confirmed": "analysis_tile.verdict.true", "False Positive": "analysis_tile.verdict.false",
                 "Need revision": "analysis_tile.verdict.revision"}


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
    if len(q.ask_for) == len(_VERDICT_KEYS):
        tags.append({"value": t("steps.questionnaire.tag_all_verdicts")})
    else:
        tags.extend({"value": t(_VERDICT_KEYS[v])} for v in q.ask_for)
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


@solara.component
def _StartAnalysis(app_state, ready):
    """The workflow's way into Analysis, after the last step's settings. It
    writes ``current_page_view``, the navigation signal every other way into
    Analysis writes; ``_NavBridge`` turns it into the analysis screen. Gated on
    an active alert set, with the reason under it."""
    solara.Button(t("workflow.start_analysis"), icon_name="mdi-image-search-outline", color="primary", block=True,
                  disabled=not ready, classes=["defo-start-analysis"],
                  on_click=lambda: app_state.current_page_view.set("analysis_tile"))
    if not ready:
        solara.Text(t("workflow.lock_analysis"), style=MUTED + "font-size:0.75rem;")


@solara.component
def QuestionnaireStep(app_state):
    p = app_state.project.value
    modal_open = solara.use_reactive(False)
    editing, set_editing = solara.use_state(None)
    pending_delete, set_pending_delete = solara.use_state(None)
    if p is None:
        solara.Info(t("workflow.lock_project"))
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

        app_state.update_project(name, _apply)

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

        app_state.update_project(name, _apply)

    def _delete():
        key = pending_delete
        set_pending_delete(None)
        app_state.update_project(name, lambda live: setattr(
            live, "questions", [q for q in live.questions if q.key != key]))

    def _edit(q):
        set_editing(q)
        modal_open.set(True)

    def _open_add():
        set_editing(None)
        modal_open.set(True)

    last = len(p.questions) - 1
    rows = [{"key": q.key,
             "cells": [{"value": qs.question_label(q), "chips": question_tags(q), "stack": True}],
             "notes": [{"text": "{" + q.key + "}", "tone": None}],
             "actions": [
                 {"kind": "move_up", "disabled": i == 0, "on_click": (lambda k=q.key: _move(k, -1))},
                 {"kind": "move_down", "disabled": i == last, "on_click": (lambda k=q.key: _move(k, 1))},
                 {"kind": "edit", "on_click": (lambda q=q: _edit(q))},
                 {"kind": "delete", "on_click": (lambda k=q.key: set_pending_delete(k))},
             ]} for i, q in enumerate(p.questions)]
    doomed = next((q for q in p.questions if q.key == pending_delete), None)

    with solara.Column(style="gap:16px;"):
        solara.Markdown(f"### {t('steps.questionnaire.title')}")
        StepIntro(t("steps.questionnaire.description"), t("steps.questionnaire.info_header"),
                  t("steps.questionnaire.info_md"))
        with rv.Card(outlined=True, class_="pa-3"):
            solara.Button(t("steps.questionnaire.add"), icon_name="mdi-plus", small=True, outlined=True,
                          block=True, on_click=_open_add)
            ProductTable(title=t("steps.questionnaire.questions_title"),
                         columns=[{"label": t("steps.questionnaire.col_question")}], rows=rows,
                         empty_text=t("steps.questionnaire.empty"))
        with rv.Card(outlined=True, class_="pa-3"):
            solara.Text(t("steps.questionnaire.template_title"), style="font-weight:600;")
            ReportTemplateField(app_state=app_state)
            _Placeholders(questions=p.questions)
        _StartAnalysis(app_state=app_state, ready=active_set(p) is not None)

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
