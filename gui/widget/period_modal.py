"""New period / change dates modal (spec §6.4).

A form dialog (``gui/widget/form_dialog``, 420px: an outside click nudges it,
ESC cancels, Enter saves) with a start and an end date: pre-filled from the
latest period for a new one (``periods.prefill``), from the period's own dates
for an edit. ``end > start`` and the look-back (days, 0 or more) are checked as
the user types; the search window previews under them. A period with alert
sets has its dates locked (disabled): only its look-back can change. Add/Save calls
``on_submit(start, end, look_back)``, which applies the change and returns a message key
when the project refused it (a date clash, a period that got sets meanwhile):
that message shows inline and the dialog stays open; None closes it.

The buttons are ``solara.Button`` components, which wire their click through
``use_event`` themselves. A raw ``rv.Btn(on_click=…)`` would observe a trait
that does not exist and drop the click.
"""

from datetime import date, timedelta

import reacton.ipyvuetify as rv
import solara

from deforestation_alerts.project import periods
from gui.i18n import t
from gui.widget.enter_key import ENTER_DEFAULT
from gui.widget.form_dialog import form_card, use_form_dialog
from gui.widget.text_style import MUTED


@solara.component
def PeriodModal(open_, app_state, on_submit, editing=None, today=None):
    # The form frame (gui/widget/form_dialog): persistent, an outside click
    # nudges it, ESC closes it. Its hooks run on every render, open or not.
    with use_form_dialog(open_, "420px"):
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
    locked = current is not None and bool(periods.sets_of(p, current.id))
    look_back, set_look_back = solara.use_state(
        str(current.look_back_days if current is not None else p.look_back_days) if p is not None else "")
    error, set_error = solara.use_state(None)
    lb_error, days = periods.parse_look_back(look_back)
    check, dates = periods.parse_dates(start, end)

    def _save(*_):
        if check or lb_error:
            return  # the button is disabled: the error shows under the end date
        key = on_submit(start, end, look_back)
        if key:
            set_error(t(key))
        else:
            open_.set(False)

    period_name = f"{first[0]} → {first[1]}"
    if current is None:
        title = t("steps.period.new_title")
    elif locked:
        title = t("steps.period.edit_look_back_title", period=period_name)
    else:
        title = t("steps.period.edit_title", period=period_name)

    def _actions():
        # Enter presses it, from a date field too (gui/widget/enter_key). The
        # Enter listener skips a disabled button, so an invalid range is not sent.
        solara.Button(t("steps.period.save") if current is not None else t("steps.period.add"),
                      icon_name="mdi-content-save-outline" if current is not None else "mdi-plus",
                      color="primary", small=True, disabled=bool(check or lb_error), classes=[ENTER_DEFAULT], on_click=_save)

    # a refusal stays inline in the form (spec §11; form_card shows it)
    with form_card(title, on_cancel=lambda: open_.set(False), actions=_actions, error=error):
        rv.TextField(label=t("steps.period.start"), type="date", v_model=start, on_v_model=set_start,
                     dense=True, outlined=True, disabled=locked)
        rv.TextField(label=t("steps.period.end"), type="date", v_model=end, on_v_model=set_end, dense=True,
                     outlined=True, disabled=locked,
                     error_messages=[t(f"steps.period.error_{check}")] if check else [])
        rv.TextField(label=t("steps.period.look_back"), type="number", v_model=look_back, on_v_model=set_look_back,
                     dense=True, outlined=True, suffix=t("steps.period.days"),
                     error_messages=[t("steps.period.error_bad_look_back")] if lb_error else [],
                     hint=t("steps.period.look_back_hint"), persistent_hint=not lb_error)
        window_start = None
        if dates is not None and days is not None:
            try:
                window_start = dates[0] - timedelta(days=days)
            except OverflowError:  # a start so early that the look-back runs before year 1
                window_start = None
        if window_start is not None:
            rv.Html(tag="div", style_=MUTED, children=[
                t("steps.period.window", start=window_start.isoformat(), end=dates[1].isoformat())])
        if current is None and p is not None and p.masks and p.periods:
            rv.Html(tag="div", style_=MUTED, children=[t("steps.period.masks_copied")])
