import asyncio
import time

import ipyvuetify as v
import pytest
import reacton
import solara
from pysepal.solara.components.task_button import use_task_button

from gui.i18n import t
from gui.widget import form_dialog as fd
from gui.widget.enter_key import ENTER_DEFAULT


def _classes(widget):
    return (widget.class_ or "").split()


def _form(open_, closed, error=None, cancel_disabled=False, pressed=None):
    """A form on the frame: ``closed`` records ``on_close``, ``pressed`` the
    primary button."""
    pressed = [] if pressed is None else pressed

    @solara.component
    def Form():
        with fd.use_form_dialog(open_, "420px", on_close=lambda: closed.append(True)):
            if open_.value:
                def actions():
                    solara.Button("Add thing", color="primary", small=True, classes=[ENTER_DEFAULT],
                                  on_click=lambda: pressed.append(True))

                with fd.form_card("New thing", on_cancel=lambda: open_.set(False), actions=actions, error=error,
                                  cancel_disabled=cancel_disabled):
                    solara.InputText("Name", value="")
                    with fd.field_group("Format"):
                        solara.Text("radios here")

    return Form()


def test_the_dialog_is_a_persistent_eager_form_frame():
    open_, closed = solara.reactive(True), []
    box, rc = reacton.render(_form(open_, closed), handle_error=False)
    try:
        dialog = rc.find(v.Dialog).widget
        assert dialog.persistent is True and dialog.no_click_animation is True
        assert dialog.eager is True and dialog.max_width == "420px" and dialog.v_model is True
        assert dialog.content_class == ""  # no nudge before the first outside click
        assert "New thing" in str(rc.find(v.CardTitle).widget.children)
    finally:
        rc.close()


def test_an_outside_click_nudges_the_dialog_and_never_closes_it():
    open_, closed = solara.reactive(True), []
    box, rc = reacton.render(_form(open_, closed), handle_error=False)
    try:
        rc.find(v.Dialog).widget.fire_event("click:outside", None)
        assert rc.find(v.Dialog).widget.content_class == "da-dialog-nudge-a"
        rc.find(v.Dialog).widget.fire_event("click:outside", None)
        assert rc.find(v.Dialog).widget.content_class == "da-dialog-nudge-b"
        assert open_.value is True and closed == []
    finally:
        rc.close()


def test_esc_closes_the_dialog_and_calls_on_close():
    open_, closed = solara.reactive(True), []
    box, rc = reacton.render(_form(open_, closed), handle_error=False)
    try:
        rc.find(v.Dialog).widget.fire_event("keydown.esc", None)
        assert open_.value is False and closed == [True]
        assert not rc.find(v.Card).widgets  # the form is mounted per opening
    finally:
        rc.close()


def test_the_dialog_closing_itself_calls_on_close_and_opening_does_not():
    open_, closed = solara.reactive(True), []
    box, rc = reacton.render(_form(open_, closed), handle_error=False)
    try:
        dialog = rc.find(v.Dialog).widget
        dialog.v_model = True
        assert closed == []
        dialog.v_model = False
        assert open_.value is False and closed == [True]
    finally:
        rc.close()


def test_the_card_has_cancel_then_the_primary_button():
    open_, closed, pressed = solara.reactive(True), [], []
    box, rc = reacton.render(_form(open_, closed, pressed=pressed), handle_error=False)
    try:
        actions = rc.find(v.CardActions).widget
        assert "justify-content:flex-end" in actions.style_
        labels = [b.children for b in rc.find(v.CardActions).find(v.Btn).widgets]
        assert t("common.cancel") in labels[0] and "Add thing" in labels[1]
        [default] = [b for b in rc.find(v.Btn).widgets if ENTER_DEFAULT in _classes(b)]
        default.fire_event("click", None)
        assert pressed == [True]
        [cancel] = [b for b in rc.find(v.Btn).widgets if t("common.cancel") in b.children]
        assert cancel.text is True and cancel.small is True and cancel.disabled is False
        assert ENTER_DEFAULT not in _classes(cancel)
        cancel.fire_event("click", None)  # the caller's on_cancel
        assert open_.value is False and closed == []
    finally:
        rc.close()


def test_cancel_can_be_disabled():
    open_, closed = solara.reactive(True), []
    box, rc = reacton.render(_form(open_, closed, cancel_disabled=True), handle_error=False)
    try:
        [cancel] = [b for b in rc.find(v.Btn).widgets if t("common.cancel") in b.children]
        assert cancel.disabled is True
    finally:
        rc.close()


def test_the_fields_sit_in_one_column_with_the_css():
    open_, closed = solara.reactive(True), []
    box, rc = reacton.render(_form(open_, closed), handle_error=False)
    try:
        assert rc.find(v.Html, tag="div", style_=fd.FIELDS).widgets
        # solara.Style renders a VuetifyTemplate holding the <style>
        css = "".join(w.template for w in rc.find(v.VuetifyTemplate).widgets)
        assert ".da-field-group{" in css and "@keyframes da-dialog-nudge-a" in css
    finally:
        rc.close()


def test_no_error_no_alert_and_an_error_is_an_inline_error_alert():
    open_, closed = solara.reactive(True), []
    box, rc = reacton.render(_form(open_, closed), handle_error=False)
    try:
        assert not rc.find(v.Alert).widgets
    finally:
        rc.close()
    box, rc = reacton.render(_form(open_, closed, error="That name is taken"), handle_error=False)
    try:
        alert = rc.find(v.Alert).widget
        assert alert.type == "error" and alert.dense is True and alert.text is True
        assert rc.find(v.Alert).find(v.Html, tag="span").widget.children == ["That name is taken"]
        # inside the fields' column, under the fields
        assert rc.find(v.Html, style_=fd.FIELDS).find(v.Alert).widgets
    finally:
        rc.close()


def test_the_field_group_is_a_fieldset_with_its_legend():
    open_, closed = solara.reactive(True), []
    box, rc = reacton.render(_form(open_, closed), handle_error=False)
    try:
        group = rc.find(v.Html, tag="fieldset").widget
        assert group.class_ == "da-field-group"
        legend = rc.find(v.Html, tag="fieldset").find(v.Html, tag="legend").widget
        assert legend.children == ["Format"]
        assert group.children[0] is legend  # the legend first, then the controls
    finally:
        rc.close()


# ------------------------------------------------------------ SubmitTaskButton
def _button(**kw):
    calls = []
    props = dict(label="Add source", on_start=lambda: calls.append("start"),
                 on_cancel=lambda: calls.append("cancel"))
    props.update(kw)
    box, rc = reacton.render(fd.SubmitTaskButton(**props), handle_error=False)
    return rc, rc.find(v.Btn).widget, calls


def test_idle_it_is_the_enter_default_and_starts():
    rc, btn, calls = _button(icon="mdi-plus")
    try:
        assert ENTER_DEFAULT in _classes(btn)
        assert btn.color == "primary" and btn.small is True and btn.disabled is False
        assert btn.children[-1] == "Add source"
        [icon] = [c for c in btn.children if isinstance(c, v.Icon)]
        assert icon.children == ["mdi-plus"] and icon.left is True
        assert not [c for c in btn.children if isinstance(c, v.ProgressCircular)]
        btn.fire_event("click", None)
        assert calls == ["start"]
    finally:
        rc.close()


def test_without_an_icon_the_label_is_alone():
    rc, btn, calls = _button()
    try:
        assert btn.children == ["Add source"]
    finally:
        rc.close()


def test_running_it_reads_cancel_with_a_spinner_and_is_not_the_enter_default():
    rc, btn, calls = _button(running=True, icon="mdi-plus")
    try:
        assert ENTER_DEFAULT not in _classes(btn)
        assert btn.color == "error" and btn.children[-1] == t("common.cancel")
        assert [c for c in btn.children if isinstance(c, v.ProgressCircular)]
        assert not [c for c in btn.children if isinstance(c, v.Icon)]
        btn.fire_event("click", None)
        assert calls == ["cancel"]
    finally:
        rc.close()


def test_a_custom_cancel_label_and_colour():
    rc, btn, calls = _button(running=True, cancel_label="Stop")
    try:
        assert btn.children[-1] == "Stop"
    finally:
        rc.close()
    rc, btn, calls = _button(color="warning")
    try:
        assert btn.color == "warning"
    finally:
        rc.close()


@pytest.mark.parametrize("running, disabled", [(False, True), (True, False)])
def test_external_busy_disables_only_the_start(running, disabled):
    rc, btn, calls = _button(running=running, external_busy=True)
    try:
        assert btn.disabled is disabled
    finally:
        rc.close()


async def _settle(cond, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(0.01)
        if cond():
            return True
    return False


def test_enter_finds_no_default_button_while_the_check_runs():
    """Enter presses ``button.da-enter-default`` (enter_key's template). While
    the check runs no button carries the class, so a second Enter cannot reach
    the Cancel the primary button has become: the check keeps running until it
    ends or Cancel is clicked."""
    release, started, done = asyncio.Event(), [], []

    @solara.component
    def Form():
        async def _check():
            started.append(True)
            await release.wait()
            done.append(True)
            return "ok"

        task = solara.lab.use_task(_check, dependencies=None, prefer_threaded=False, raise_error=False)
        fd.SubmitTaskButton(label="Add source", icon="mdi-plus", **use_task_button(task, on_start=task))

    def defaults(rc):
        return [b for b in rc.find(v.Btn).widgets if ENTER_DEFAULT in _classes(b)]

    async def main():
        box, rc = reacton.render(Form(), handle_error=False)
        try:
            [btn] = defaults(rc)
            btn.fire_event("click", None)  # Enter (or a click) starts the check
            assert await _settle(lambda: started and not defaults(rc))
            assert rc.find(v.Btn).widget.children[-1] == t("common.cancel")
            await asyncio.sleep(0.05)
            assert defaults(rc) == [] and done == []  # still running, still no default
            release.set()
            assert await _settle(lambda: done and defaults(rc))  # idle again: Enter is back
            assert defaults(rc)[0].children[-1] == "Add source"
        finally:
            rc.close()

    asyncio.run(main())


def test_cancel_stops_the_check_and_gives_enter_back():
    release, started, done = asyncio.Event(), [], []

    @solara.component
    def Form():
        async def _check():
            started.append(True)
            await release.wait()
            done.append(True)

        task = solara.lab.use_task(_check, dependencies=None, prefer_threaded=False, raise_error=False)
        fd.SubmitTaskButton(label="Add source", **use_task_button(task, on_start=task))

    def defaults(rc):
        return [b for b in rc.find(v.Btn).widgets if ENTER_DEFAULT in _classes(b)]

    async def main():
        box, rc = reacton.render(Form(), handle_error=False)
        try:
            rc.find(v.Btn).widget.fire_event("click", None)
            assert await _settle(lambda: started and not defaults(rc))
            rc.find(v.Btn).widget.fire_event("click", None)  # the same button, now Cancel
            assert await _settle(lambda: defaults(rc))
            release.set()
            await asyncio.sleep(0.05)
            assert done == []  # cancelled at its await
        finally:
            rc.close()

    asyncio.run(main())
