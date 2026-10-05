import ipyvuetify as v
import reacton

from gui.widget.product_table import ProductTable


def _texts(widget, cls):
    found = []
    if isinstance(widget, v.Html) and cls in (widget.class_ or ""):
        found.append("".join(str(c) for c in widget.children))
    for c in getattr(widget, "children", []) or []:
        if hasattr(c, "children"):
            found += _texts(c, cls)
    return found


def test_notes_render_with_their_tone():
    rows = [{"key": "a", "cells": [{"value": "GLAD-S2"}], "actions": [],
             "notes": [{"text": "Not covered: x", "tone": "error"}, {"text": "covers 35% of the AOI", "tone": "warning"},
                       {"text": "ready · 0 alerts found", "tone": "info"}],
             "error": "boom"}]
    box, rc = reacton.render(ProductTable(title="t", columns=[{"label": "c"}], rows=rows, empty_text="-"))
    try:
        assert _texts(box, "error--text") == ["Not covered: x", "boom"]
        assert _texts(box, "warning--text") == ["covers 35% of the AOI"]
        assert _texts(box, "info--text") == ["ready · 0 alerts found"]
    finally:
        rc.close()


def test_the_coverage_texts_of_a_table():
    from datetime import date

    from deforestation_alerts.engine.coverage import NO_DATA, NOT_COVERED, PARTIAL, UNCHECKED, Note, window_coverage
    from deforestation_alerts.project.models import RegionCoverage, SourceCoverage
    from gui.i18n import t
    from gui.scripts import coverage_ui

    def cov(fraction, *regions):
        return SourceCoverage(aoi_digest="d", checked_on=date(2026, 10, 1), fraction=fraction, regions=list(regions))

    a, b = date(2019, 1, 1), date(2026, 9, 30)
    full = cov(1.0, RegionCoverage(name="sa", fraction=1.0, first=a, last=b))
    assert coverage_ui.chip_cell(full) == {"type": "chip", "value": t("coverage.chip_full"), "color": "success"}
    assert coverage_ui.chip_cell(cov(0.35)) == {"type": "chip", "value": t("coverage.chip_partial", pct="35"),
                                                "color": "warning"}
    assert coverage_ui.chip_cell(None, pending=True) == {"type": "chip", "value": t("coverage.chip_checking")}
    assert coverage_ui.chip_cell(None)["color"] == "error"

    assert coverage_ui.dates_text(full) == "2019-01-01 → 2026-09-30"
    two = cov(1.0, RegionCoverage(name="sa", fraction=0.5, first=a, last=b),
              RegionCoverage(name="ca", fraction=0.5, first=date(2020, 1, 1), last=b))
    assert coverage_ui.dates_text(two) == "sa: 2019-01-01 → 2026-09-30; ca: 2020-01-01 → 2026-09-30"
    assert coverage_ui.dates_text(cov(1.0, RegionCoverage(name="tif", fraction=1.0))) == ""

    note = Note("coverage.partial_share", (("source", "RADD"), ("pct", "35")))
    assert coverage_ui.note_text(note) == t("coverage.partial_share", pct="35")
    assert coverage_ui.row_notes(PARTIAL, (note,)) == [{"text": coverage_ui.note_text(note), "tone": "warning"}]
    state, notes = window_coverage(cov(0.0), "GLAD-S2", a, b)
    assert state == NOT_COVERED
    assert coverage_ui.row_notes(state, notes) == [
        {"text": t("coverage.not_covered_row", reason=t("coverage.not_covered_glad_s2")), "tone": "error"}]
    state, notes = window_coverage(full, "RADD", date(2027, 1, 1), date(2027, 1, 31))
    assert state == NO_DATA and coverage_ui.row_notes(state, notes)[0]["text"] == t(
        "coverage.no_data_row", reason=t("coverage.no_data_until", source="RADD", last="2026-09-30"))
    assert coverage_ui.row_notes(UNCHECKED, ()) == [{"text": t("coverage.unchecked_row"), "tone": "error"}]


def test_the_result_line_of_a_harmonized_row():
    from gui.i18n import t, tn
    from gui.scripts.coverage_ui import result_note

    assert result_note(None) is None and result_note({"on_pixels": 3}) is None
    assert result_note({"alert_pixels": 1234, "alert_pixels_unmasked": 2000}) == {
        "text": tn("steps.prepare.result_pixels", 1234), "tone": None}
    assert result_note({"alert_pixels": 0, "alert_pixels_unmasked": 2000}) == {
        "text": tn("steps.prepare.result_all_masked", 2000), "tone": "warning"}
    assert result_note({"alert_pixels": 0, "alert_pixels_unmasked": 0}) == {
        "text": "ready · 0 alerts found", "tone": "info"}


def test_a_select_action_is_a_radio():
    from gui.widget.product_table import action_icon

    assert action_icon("select", True) == "mdi-radiobox-marked"
    assert action_icon("select", False) == "mdi-radiobox-blank"
    rows = [{"key": k, "cells": [{"value": k}], "actions": [{"kind": "select", "is_on": k == "a", "on_click": None}]}
            for k in ("a", "b")]
    box, rc = reacton.render(ProductTable(title="t", columns=[{"label": "c"}], rows=rows, empty_text="-"))
    try:
        radios = [(b.color, b.children[0].children) for b in rc.find(v.Btn).widgets if b.children
                  and isinstance(b.children[0], v.Icon) and "mdi-radiobox" in b.children[0].children[0]]
        assert radios == [("primary", ["mdi-radiobox-marked"]), ("grey darken-1", ["mdi-radiobox-blank"])]
    finally:
        rc.close()


def test_a_chip_clips_its_text_with_an_ellipsis_and_shows_it_whole_on_hover():
    # A coverage chip sits in a 140 px column; a long translation must end in
    # an ellipsis there, not be cut mid-letter, and stay readable on hover.
    text = "vérification de la couverture d'une très longue zone…"
    rows = [{"key": "a", "cells": [{"value": "GLAD-L"}, {"type": "chip", "value": text}], "actions": []}]
    box, rc = reacton.render(ProductTable(title="t", columns=[{"label": "c"}, {"label": "cov", "width": "140px"}],
                                          rows=rows, empty_text="-"))
    try:
        (chip,) = rc.find(v.Chip).widgets
        assert chip.attributes.get("title") == text
        assert "max-width:100%" in (chip.style_ or "")
        (span,) = [c for c in chip.children if isinstance(c, v.Html)]
        assert span.children == [text]
        assert all(rule in span.style_ for rule in ("overflow:hidden", "text-overflow:ellipsis", "white-space:nowrap"))
    finally:
        rc.close()
