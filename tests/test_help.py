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
