"""The frame every add/edit popup is built on (spec D1, D2), after spatial_risk's
``CreationDialog``: the alert source, mask, visualization-layer, period,
attribute-layer and project-name forms.

* ``use_form_dialog`` — the ``rv.Dialog``: persistent, so a click outside never
  closes it (an open v-select menu's backdrop click would otherwise take the
  whole form with it, see ``gui/widget/dialog_nudge``); that click nudges it
  instead, and ESC closes it.
* ``form_card`` — title, the fields in one column, the inline validation error,
  and a footer with Cancel then the caller's primary button.
* ``field_group`` — an outlined fieldset with a legend, for a radio group.
* ``SubmitTaskButton`` — the primary button of a form whose check is
  asynchronous: pysepal's ``TaskButtonComponent``, plus the Enter class while
  idle (``gui/widget/enter_key``).

Usage, inside a component (``use_form_dialog`` holds hooks: call it on every
render, before any ``if``)::

    with use_form_dialog(open_, "560px"):
        if open_.value:
            _Form(...)          # its render: ``with form_card(title, ...):`` fields
"""

from contextlib import contextmanager

import reacton.ipyvuetify as rv
import solara

from gui.i18n import t
from gui.widget.dialog_nudge import NUDGE_CSS, use_outside_click_nudge
from gui.widget.enter_key import ENTER_DEFAULT

# The outlined fieldset of ``field_group``: the border, radius and label colour
# of an outlined dense field, so a radio group reads as one more field.
FIELD_CSS = (
    ".da-field-group{border:1px solid rgba(0,0,0,0.38);border-radius:4px;padding:4px 12px 8px;margin:0;} "
    ".da-field-group>legend{padding:0 4px;font-size:0.75rem;color:rgba(0,0,0,0.6);} "
    ".theme--dark .da-field-group{border-color:rgba(255,255,255,0.24);} "
    ".theme--dark .da-field-group>legend{color:rgba(255,255,255,0.7);}"
)

# The fields' column (spatial_risk's ``solara.Column(style="gap:4px;")``); the
# top padding keeps the first outlined label clear of the title.
FIELDS = "display:flex;flex-direction:column;gap:4px;padding-top:8px;"


def use_form_dialog(open_, max_width="560px", on_close=None):
    """Hook: the form's ``rv.Dialog`` element, to be used as ``with``.

    ``open_`` is the ``solara.Reactive[bool]`` the caller owns. ``on_close``
    runs after the dialog closes itself (ESC, or the browser setting its
    ``v_model``); a Cancel button closes through the caller's own
    ``on_cancel``. Holds hooks — call it unconditionally."""

    def close(*_):
        open_.set(False)
        if on_close is not None:
            on_close()

    # ``persistent``: see the module docstring. Vuetify's own answer to the
    # outside click (the shake, ``no_click_animation=False``) also fires on ESC,
    # which closes the form here — so it is off, and the click is answered from
    # Python: the nudge class goes on the ``.v-dialog`` element (content_class).
    nudge, on_click_outside = use_outside_click_nudge(open_.value)
    dialog = rv.Dialog(v_model=open_.value, on_v_model=lambda v: None if v else close(), max_width=max_width,
                       eager=True, persistent=True, no_click_animation=True, content_class=nudge)
    rv.use_event(dialog, "click:outside", on_click_outside)
    # ESC back, which ``persistent`` disables: VDialog still emits keydown, and
    # an open VSelect stops ESC first, so ESC closes a dropdown before the form.
    rv.use_event(dialog, "keydown.esc", close)
    return dialog


@contextmanager
def form_card(title, on_cancel, actions, error=None, cancel_disabled=False):
    """The form's card; the ``with`` body renders the fields.

    ``actions()`` renders the primary button (one ``ENTER_DEFAULT`` button, or a
    ``SubmitTaskButton``) after Cancel. ``error`` is the inline validation
    error (``form_error``), shown under the fields."""
    with rv.Card():
        with rv.CardTitle():
            solara.Text(title)
        with rv.CardText():
            solara.Style(NUDGE_CSS + FIELD_CSS)
            with rv.Html(tag="div", style_=FIELDS):
                yield
                if error:
                    form_error(error)
        with rv.CardActions(style_="justify-content:flex-end;gap:8px;"):
            solara.Button(t("common.cancel"), text=True, small=True, disabled=cancel_disabled, on_click=on_cancel)
            actions()


def form_error(text):
    """The form's validation error. KEPT INLINE ON PURPOSE: it belongs to the
    form the user is filling in, so it is not a toast like every other
    message."""
    # ``type=``, not ``type_=``: reacton's Alert has no ``type_`` parameter, so
    # it would be swallowed and the alert render grey and iconless.
    with rv.Alert(type="error", dense=True, text=True) as alert:
        rv.Html(tag="span", children=[text])
    return alert


@contextmanager
def field_group(label):
    """An outlined fieldset with a legend; the ``with`` body renders its
    controls (a radio group with ``hide_details``)."""
    with rv.Html(tag="fieldset", class_="da-field-group"):
        rv.Html(tag="legend", children=[label])
        yield


@solara.component
def SubmitTaskButton(label, on_start, on_cancel, running=False, cancel_label=None, icon="", external_busy=False,
                     color="primary"):
    """pysepal's ``TaskButtonComponent`` (one button that reads Cancel while
    its task runs; spread ``use_task_button(task, on_start=...)`` into it),
    always small, plus Enter.

    Idle, it carries ``ENTER_DEFAULT``, so Enter presses it. Running, it does
    not: a second Enter must never stop the check the first one started, so
    while the check runs Enter finds no default button and does nothing.
    ``external_busy`` disables the start only; Cancel is never disabled."""
    if running:
        children = [rv.ProgressCircular(indeterminate=True, size=14, width=2, color="white", class_="mr-2")]
    else:
        children = [rv.Icon(left=True, small=True, children=[icon])] if icon else []
    text = (cancel_label or t("common.cancel")) if running else label
    btn = rv.Btn(children=[*children, text], color="error" if running else color, small=True,
                 disabled=(not running) and external_busy, class_="" if running else ENTER_DEFAULT)

    def _click(*_):
        (on_cancel if running else on_start)()

    rv.use_event(btn, "click", _click)
