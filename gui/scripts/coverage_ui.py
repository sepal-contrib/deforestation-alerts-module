"""Coverage notes, chips and harmonize results as translated table content,
and the Check again button of the Alerts and Harmonize steps."""

import solara

from deforestation_alerts.engine.coverage import (
    FULL, NO_DATA, NOT_COVERED, UNCHECKED, Note, covering_dates, pct_text, record_for, window_coverage,
)
from gui.i18n import t, tn

#: a chip's tone -> its colour
TONE_COLORS = {"ok": "success", "warning": "warning", "error": "error", None: None}
_DATE_NOTES = ("coverage.partial_starts", "coverage.partial_until", "coverage.partial_gap")


def note_text(note: Note) -> str:
    return t(note.key, **note.kwargs())


def coverage_chips(p, name: str, period, pending: bool = False) -> list:
    """An Alerts row's chips (spec §2): ``[{"label", "tone"}]``, tone ``ok`` /
    ``warning`` / ``error``. AOI: the share of the AOI the source covers.
    Dates: whether it has data for the period's search window, with its last
    data date; none for a source with no catalogue dates (a custom one).
    ``pending``: the coverage check runs, so a source without a record reads
    checking, not unchecked."""
    cov = record_for(p, name)
    if cov is None:
        label = t("coverage.chip_checking" if pending else "coverage.unchecked")
        return [{"label": label, "tone": None if pending else "error"}]
    tone = "error" if cov.fraction <= 0 else "ok" if cov.fraction >= FULL else "warning"
    chips = [{"label": t("steps.alerts.chip_aoi", pct=pct_text(cov.fraction)), "tone": tone}]
    dated = covering_dates(cov)
    if not dated or cov.fraction <= 0:
        return chips
    state, notes = window_coverage(cov, name, *period.search_window())
    late = [n for n in notes if n.key in _DATE_NOTES]
    if state == NO_DATA:
        first = notes[0]
        label = (t("steps.alerts.chip_dates_none", last=first.kwargs()["last"])
                 if first.key == "coverage.no_data_until" else note_text(first))
        chips.append({"label": label, "tone": "error"})
    elif late:
        until = next((n for n in late if n.key == "coverage.partial_until"), None)
        label = t("steps.alerts.chip_dates_part", last=until.kwargs()["last"]) if until else note_text(late[0])
        chips.append({"label": label, "tone": "warning"})
    else:
        chips.append({"label": t("steps.alerts.chip_dates_ok", last=max(d[2] for d in dated).isoformat()),
                      "tone": "ok"})
    return chips


def row_notes(state: str, notes) -> list:
    """Under-row notes of a download/harmonize row (spec §7.3)."""
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
        return {"text": tn("steps.prepare.result_pixels", kept), "tone": None}
    if before:
        return {"text": tn("steps.prepare.result_all_masked", before), "tone": "warning"}
    return {"text": t("steps.prepare.result_none"), "tone": "info"}


@solara.component
def CheckAgainButton(app_state, show: bool):
    """Check again (spec §6.2 "Failure"): restarts the coverage check and the
    AOI-sidecar fetch (``coverage_retry``); disabled while either runs. A
    leaf: the pending flags re-render only the button."""
    if not show:
        return
    pending = app_state.coverage_pending.value or app_state.sidecar_pending.value
    solara.Button(t("coverage.check_again"), color="primary", small=True, outlined=True, icon_name="mdi-refresh", disabled=pending,
                  on_click=lambda: app_state.coverage_retry.set(app_state.coverage_retry.value + 1))
