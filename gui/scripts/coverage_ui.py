"""Coverage notes, chips and harmonize results as translated table content."""

from deforestation_alerts.engine.coverage import (
    NO_DATA, NOT_COVERED, UNCHECKED, chip_note, covering_dates, Note,
)
from gui.i18n import t

#: a note's tone -> the chip colour
TONE_COLORS = {"success": "success", "warning": "warning", "error": "error", "info": "info", None: None}


def note_text(note: Note) -> str:
    return t(note.key, **note.kwargs())


def chip_cell(cov, pending: bool = False) -> dict:
    if cov is None and pending:
        return {"type": "chip", "value": t("coverage.chip_checking")}
    note = chip_note(cov)
    return {"type": "chip", "value": note_text(note), "color": TONE_COLORS.get(note.tone)}


def dates_text(cov) -> str:
    dated = covering_dates(cov)
    if not dated:
        return ""
    spans = {(first, last) for _, first, last in dated}
    if len(spans) == 1:
        first, last = spans.pop()
        return t("coverage.dates", first=first.isoformat(), last=last.isoformat())
    return "; ".join(t("coverage.dates_region", region=r, first=a.isoformat(), last=b.isoformat())
                     for r, a, b in dated)


def row_notes(state: str, notes) -> list:
    """Under-row notes of a Download & harmonize row (spec §7.3)."""
    if state == NOT_COVERED:
        return [{"text": t("coverage.not_covered_row", reason=note_text(notes[0])), "tone": "error"}]
    if state == NO_DATA:
        return [{"text": t("coverage.no_data_row", reason=note_text(notes[0])), "tone": "error"}]
    if state == UNCHECKED:
        return [{"text": t("coverage.unchecked_row"), "tone": "error"}]
    return [{"text": note_text(n), "tone": n.tone} for n in notes]


def result_note(stats) -> dict | None:
    """What a harmonized row found (spec §7.3), in alert pixels."""
    if not stats or "alert_pixels" not in stats:
        return None
    kept, before = int(stats["alert_pixels"]), int(stats.get("alert_pixels_unmasked", stats["alert_pixels"]))
    if kept:
        return {"text": t("steps.prepare.result_pixels", n=f"{kept:,}"), "tone": None}
    if before:
        return {"text": t("steps.prepare.result_all_masked", n=f"{before:,}"), "tone": "warning"}
    return {"text": t("steps.prepare.result_none"), "tone": "info"}
