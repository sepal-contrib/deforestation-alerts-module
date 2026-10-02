"""StepIntro: a step's description with an info button that opens its explanation."""

import ipyvuetify as v
import reacton

from gui.widget.help import StepIntro


def _strings(widget):
    out = []
    if isinstance(widget, str):
        return [widget]
    for child in getattr(widget, "children", None) or []:
        out += _strings(child)
    for attr in ("value", "template"):
        value = getattr(widget, attr, None)
        if isinstance(value, str):
            out.append(value)
    return out


def test_the_description_shows_and_the_button_opens_the_explanation():
    box, rc = reacton.render(StepIntro("What this step does.", "About the step", "**Bold** body"),
                             handle_error=False)
    assert "What this step does." in _strings(box)
    dialog = rc.find(v.Dialog).widget
    assert dialog.v_model is False
    rc.find(v.Btn, icon=True).widget.fire_event("click", None)
    assert rc.find(v.Dialog).widget.v_model is True
    text = "".join(_strings(rc.find(v.Dialog).widget))
    assert "About the step" in text and "<strong>Bold</strong> body" in text
    rc.close()


def test_the_analysis_info_popup_has_a_close_button_like_every_step():
    """Analysis's raw-widget twin of InfoButton: its popup closes with a Close
    button (Escape does not reach a dialog that never takes the focus; the
    slice-6 walk found it open with no way out but a click beside it)."""
    from gui.i18n import t
    from gui.tile.analysis.widgets import info_button

    box = info_button("Review alerts.", "About analysis", "Body")
    btn, dialog = box.children[1], box.children[2]
    btn.fire_event("click", None)
    assert dialog.v_model is True
    closes = [b for b in _buttons(dialog) if t("common.close") in _strings(b)]
    assert len(closes) == 1, _strings(dialog)
    closes[0].fire_event("click", None)
    assert dialog.v_model is False


def _buttons(widget):
    found = [widget] if isinstance(widget, v.Btn) else []
    for child in getattr(widget, "children", None) or []:
        if not isinstance(child, str):
            found += _buttons(child)
    return found
