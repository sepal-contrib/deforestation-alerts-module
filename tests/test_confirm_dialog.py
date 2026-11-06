"""ConfirmDialog (spatial_risk's version): message, note, details, an opt-in
checkbox and a third button, each optional."""

import ipyvuetify as v
import reacton
import solara

from gui.i18n import t
from gui.widget.confirm_dialog import ConfirmDialog


def _strings(widget):
    out = []
    for child in getattr(widget, "children", None) or []:
        out.extend([child] if isinstance(child, str) else _strings(child))
    return out


def test_a_plain_confirmation_stays_narrow():
    calls = []
    _, rc = reacton.render(ConfirmDialog(open=True, on_cancel=lambda: calls.append("cancel"),
                                         on_confirm=lambda: calls.append("ok"), title="T", message="M"),
                           handle_error=False)
    try:
        dialog = rc.find(v.Dialog).widget
        assert dialog.max_width == "380px" and "M" in _strings(rc.container)
        assert not rc.find(v.Checkbox).widgets
        next(b for b in rc.find(v.Btn).widgets if t("dialog.confirm_default_label") in b.children).fire_event(
            "click", None)
        next(b for b in rc.find(v.Btn).widgets if t("common.cancel") in b.children).fire_event("click", None)
        assert calls == ["ok", "cancel"]
    finally:
        rc.close()


def test_details_note_checkbox_and_a_third_button():
    ticks, seconds = [], []

    @solara.component
    def Page():
        ConfirmDialog(open=True, on_cancel=lambda: None, on_confirm=lambda: None, message="M", note="why",
                      details=["#3 · Reviewed: confirmed · 1.50 ha", "#7 · Reviewed: false positive · 0.25 ha"],
                      checkbox_label="also files", checkbox_value=False, on_checkbox=ticks.append,
                      secondary_label="Keep", on_secondary=lambda: seconds.append(1))

    _, rc = reacton.render(Page(), handle_error=False)
    try:
        texts = _strings(rc.container)
        assert rc.find(v.Dialog).widget.max_width == "440px"
        assert {"why", "#3 · Reviewed: confirmed · 1.50 ha", "#7 · Reviewed: false positive · 0.25 ha"} <= set(texts)
        rc.find(v.Checkbox).widget.v_model = True
        assert ticks == [True]
        next(b for b in rc.find(v.Btn).widgets if "Keep" in b.children).fire_event("click", None)
        assert seconds == [1]
    finally:
        rc.close()


def test_the_message_note_and_each_detail_sit_on_their_own_line():
    """Slice-5 walk: inline spans ran the Replace list into the message
    ("…will be discarded.#0 · Reviewed: confirmed · 2.88 ha") and would run
    every listed alert into the next. Each is a block of its own."""
    lines = ["#3 · Reviewed: confirmed · 1.50 ha", "#7 · Reviewed: false positive · 0.25 ha"]
    _, rc = reacton.render(ConfirmDialog(open=True, on_cancel=lambda: None, on_confirm=lambda: None, message="M",
                                         note="why", details=lines), handle_error=False)
    try:
        tags = {w.children[0]: w.tag for w in rc.find(v.Html).widgets
                if len(w.children) == 1 and isinstance(w.children[0], str)}
        assert {text: tags.get(text) for text in ["M", "why", *lines]} == {text: "div" for text in ["M", "why", *lines]}
    finally:
        rc.close()


def test_the_title_breaks_between_words():
    """Slice-5 walk: "Delete the period 2025-08-01 → 2025-08-3 / 1?"."""
    _, rc = reacton.render(ConfirmDialog(open=True, on_cancel=lambda: None, on_confirm=lambda: None, title="T"),
                           handle_error=False)
    try:
        assert "word-break: normal" in rc.find(v.CardTitle).widget.style_
    finally:
        rc.close()
