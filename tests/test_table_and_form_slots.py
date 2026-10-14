"""The slots the compact Evaluation step needs (spec 2026-10-14 §4.2-§4.4):
a download action that is a link, a header action on a ProductTable, and a
leading action on a form card."""

import ipyvuetify as v
import reacton
import solara

from gui.i18n import t
from gui.widget.form_dialog import form_card
from gui.widget.product_table import ProductTable

DOWNLOAD = "mdi-cloud-download-outline"


def _strings(w):
    out = []
    for c in getattr(w, "children", None) or []:
        out.extend([c] if isinstance(c, str) else _strings(c))
    return out


def _render_row(action):
    row = {"key": "a", "cells": [{"value": "Ana"}], "actions": [action]}
    return reacton.render(ProductTable(title="T", columns=[{"label": "C"}], rows=[row], empty_text="-"),
                          handle_error=False)


def test_a_download_with_its_file_is_an_anchor_not_a_button():
    _box, rc = _render_row({"kind": "download", "href": "https://x/files/a.zip", "file": "a.zip",
                            "title": "Download Ana's package"})
    try:
        assert not [b for b in rc.find(v.Btn).widgets if DOWNLOAD in _strings(b)]
        (a,) = rc.find(v.Html, class_="defo-download-action").widgets
        assert a.tag == "a"
        assert a.attributes == {"title": "Download Ana's package", "href": "https://x/files/a.zip",
                                "target": "_blank", "download": "a.zip"}
        assert DOWNLOAD in _strings(a)
    finally:
        rc.close()


def test_a_download_without_its_file_is_dimmed_and_links_nowhere():
    _box, rc = _render_row({"kind": "download", "href": None, "title": "Generate the packages first."})
    try:
        (span,) = rc.find(v.Html, class_="defo-download-action").widgets
        assert span.tag == "span"
        assert "href" not in span.attributes
        assert span.attributes == {"title": "Generate the packages first.", "aria-disabled": "true"}
        assert "opacity:0.38" in span.style_.replace(" ", "")
    finally:
        rc.close()


def test_a_download_being_built_spins_in_its_place():
    _box, rc = _render_row({"kind": "download", "href": None, "loading": True, "title": "Ana"})
    try:
        (span,) = rc.find(v.Html, class_="defo-download-action").widgets
        assert span.tag == "span"
        assert rc.find(v.ProgressCircular).widgets
        assert DOWNLOAD not in _strings(span)
    finally:
        rc.close()


def test_an_outdated_download_takes_its_colour():
    _box, rc = _render_row({"kind": "download", "href": "u", "file": "a.zip", "color": "warning", "title": "x"})
    try:
        (icon,) = [i for i in rc.find(v.Icon).widgets if DOWNLOAD in i.children]
        assert icon.color == "warning"
    finally:
        rc.close()


def test_an_action_without_href_is_still_a_button():
    clicked = []
    _box, rc = _render_row({"kind": "download", "on_click": lambda: clicked.append(1)})
    try:
        (btn,) = [b for b in rc.find(v.Btn).widgets if DOWNLOAD in _strings(b)]
        btn.fire_event("click", None)
        assert clicked == [1]
        assert not rc.find(v.Html, class_="defo-download-action").widgets
    finally:
        rc.close()


def test_a_header_action_sits_in_the_title_row():
    clicked = []

    def _action():
        solara.Button("Edit split", on_click=lambda: clicked.append(1))

    _box, rc = reacton.render(ProductTable(title="T", columns=[{"label": "C"}], rows=[], empty_text="-",
                                           header_action=_action), handle_error=False)
    try:
        (btn,) = [b for b in rc.find(v.Btn).widgets if "Edit split" in _strings(b)]
        btn.fire_event("click", None)
        assert clicked == [1]
    finally:
        rc.close()


def test_a_forms_leading_action_sits_left_of_cancel():
    @solara.component
    def _Card():
        def _leading():
            solara.Button("Remove split")

        def _actions():
            solara.Button("Re-split")

        with form_card("T", on_cancel=lambda: None, actions=_actions, leading=_leading):
            solara.Text("body")

    _box, rc = reacton.render(_Card(), handle_error=False)
    try:
        bar = rc.find(v.CardActions).widget.children
        assert "Remove split" in _strings(bar[0])
        assert isinstance(bar[1], v.Spacer)
        assert t("common.cancel") in _strings(bar[2])
        assert "Re-split" in _strings(bar[3])
    finally:
        rc.close()


def test_a_download_that_becomes_ready_is_a_new_widget_not_the_span_reused():
    """ipyvue reads an Html widget's ``tag`` once, when its Vue component is built: the
    dimmed span's widget given ``tag="a"`` would stay a span in the browser (the cloud
    icon would carry an ``href`` and download nothing)."""
    pending = {"kind": "download", "href": None, "title": "Ana"}
    ready = {"kind": "download", "href": "https://x/files/a.zip", "file": "a.zip", "title": "Ana"}
    box, rc = _render_row(pending)
    try:
        (span,) = rc.find(v.Html, class_="defo-download-action").widgets
        row = {"key": "a", "cells": [{"value": "Ana"}], "actions": [ready]}
        rc.render(ProductTable(title="T", columns=[{"label": "C"}], rows=[row], empty_text="-"))
        (a,) = rc.find(v.Html, class_="defo-download-action").widgets
        assert a.tag == "a" and a is not span
    finally:
        rc.close()


def test_several_rows_with_the_same_download_state_render():
    """Reacton's keys are unique per render, so two dimmed links need keys of their own."""
    rows = [{"key": k, "cells": [{"value": k}], "actions": [{"kind": "download", "href": None, "title": k}]}
            for k in ("a", "b")]
    _box, rc = reacton.render(ProductTable(title="T", columns=[{"label": "C"}], rows=rows, empty_text="-"),
                        handle_error=False)
    try:
        assert len(rc.find(v.Html, class_="defo-download-action").widgets) == 2
    finally:
        rc.close()
