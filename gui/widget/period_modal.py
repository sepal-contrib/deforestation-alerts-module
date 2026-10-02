"""New period / change dates modal (spec §6.4).

A persistent dialog (ESC cancels) with a start and an end date: pre-filled
from the latest period for a new one (``periods.prefill``), from the period's
own dates for an edit. ``end > start`` is checked as the user types. Save calls
``on_submit(start, end)``, which applies the change and returns a message key
when the project refused it (a date clash, a period that got sets meanwhile):
that message shows inline and the dialog stays open; None closes it.

Inside the ``rv.*`` contexts the texts are ``rv.Html`` spans and the buttons
``rv.Btn`` wired through ``rv.use_event`` (a raw ``rv.Btn(on_click=…)``
observes a trait that does not exist and drops the click).
"""

from datetime import date, timedelta

import reacton.ipyvuetify as rv
import solara

from deforestation_alerts.project import periods
from gui.i18n import t
from gui.widget.text_style import MUTED


@solara.component
def PeriodModal(open_, app_state, on_submit, editing=None, today=None):
    dialog = rv.Dialog(v_model=open_.value, on_v_model=open_.set, max_width="420px", eager=True, persistent=True)
    rv.use_event(dialog, "keydown.esc", lambda *_: open_.set(False))
    with dialog:
        if open_.value:
            _PeriodForm(open_=open_, app_state=app_state, on_submit=on_submit, editing=editing,
                        today=today or date.today())


@solara.component
def _PeriodForm(open_, app_state, on_submit, editing, today):
    p = app_state.project.value
    current = p.periods.get(editing) if p is not None and editing else None
    if current is not None:
        first = (current.start.isoformat(), current.end.isoformat())
    else:
        first = periods.prefill(p, today) if p is not None else ("", "")
    start, set_start = solara.use_state(first[0])
    end, set_end = solara.use_state(first[1])
    error, set_error = solara.use_state(None)
    check, dates = periods.parse_dates(start, end)

    def _save(*_):
        if check:
            return  # the button is disabled: the error shows under the end date
        key = on_submit(start, end)
        if key:
            set_error(t(key))
        else:
            open_.set(False)

    title = (t("steps.period.edit_title", period=f"{first[0]} → {first[1]}") if current is not None
             else t("steps.period.new_title"))
    with rv.Card():
        with rv.CardTitle():
            rv.Html(tag="span", children=[title])
        with rv.CardText():
            rv.TextField(label=t("steps.period.start"), type="date", v_model=start, on_v_model=set_start,
                         dense=True, outlined=True)
            rv.TextField(label=t("steps.period.end"), type="date", v_model=end, on_v_model=set_end, dense=True,
                         outlined=True, error_messages=[t(f"steps.period.error_{check}")] if check else [])
            if dates is not None and p is not None:
                a, b = dates
                rv.Html(tag="div", style_=MUTED, children=[
                    t("steps.period.window", start=(a - timedelta(days=p.look_back_days)).isoformat(),
                      end=b.isoformat())])
            if current is None and p is not None and p.masks and p.periods:
                rv.Html(tag="div", style_=MUTED, children=[t("steps.period.masks_copied")])
            if error:
                rv.Html(tag="div", class_="error--text", children=[error])
        with rv.CardActions(style_="justify-content:flex-end;gap:8px;"):
            cancel = rv.Btn(children=[t("common.cancel")], text=True, small=True)
            save = rv.Btn(children=[t("steps.period.save")], color="primary", small=True, disabled=bool(check))
    rv.use_event(cancel, "click", lambda *_: open_.set(False))
    rv.use_event(save, "click", _save)
