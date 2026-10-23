"""What a Download & harmonize source row shows (option B): one Status cell
holding a state or a verb pill, and a subtitle under the source's name.

Pure presentation: the row's ``PrepareRow``, its artifacts, its ``Reason``
(``engine.why``) and the job's report in, ``ProductTable`` cells out.

A user file (``is_file_source``) is read where it is, never downloaded: its
pill says Harmonize and its raw-side reasons read "harmonize again", so no
label promises a download that does not happen.
"""

from typing import Callable, Optional

from deforestation_alerts.engine.coverage import NO_DATA, NOT_COVERED, UNCHECKED
from deforestation_alerts.engine.fingerprint import Status
from deforestation_alerts.engine.plan import PrepareRow
from deforestation_alerts.engine.why import Reason
from deforestation_alerts.project.models import Artifact
from gui.i18n import size_text, t, tn
from gui.scripts.coverage_ui import row_notes
from gui.widget.product_table import STATUS_ICONS, status_text

#: every ``Reason.key`` of ``engine.why``: its ``steps.prepare.why_*`` message
WHY_KEYS = ("not_downloaded", "not_read", "not_harmonized", "not_aligned", "grid", "aoi", "aoi_raster", "window",
            "file", "source", "mask_not_aligned", "mask", "masks", "raw")
_RAW_TERMS = frozenset({"aoi", "window", "file", "source"})  # a run downloads again
_BLOCKED_STATUS = {NOT_COVERED: "not_covered", NO_DATA: "no_data", UNCHECKED: "unchecked"}


def reason_text(reason: Reason) -> str:
    return t(f"steps.prepare.why_{reason.key}", **reason.kwargs())


def _state(icon: str, color: Optional[str], label: str, **extra) -> dict:
    return {"type": "state", "icon": icon, "color": color, "label": label, **extra}


def _stage(stage: Optional[str], file_source: bool) -> Optional[str]:
    """A user file is read, not downloaded: its whole run reads harmonizing."""
    return "harmonize" if stage == "download" and file_source else stage


def _next(row: PrepareRow, file_source: bool) -> tuple[str, bool]:
    """What Run does next (``download`` / ``harmonize``) and whether it redoes
    an out-of-date file (the warning pill)."""
    if row.raw is not Status.CURRENT:
        return ("harmonize" if file_source else "download"), row.raw is Status.STALE
    return "harmonize", row.harmonized is Status.STALE


def source_state(row: PrepareRow, *, file_source: bool, stage: Optional[str], queued: bool, pct: Optional[int],
                 failed: bool, locked: bool, on_run: Callable) -> dict:
    """The Status cell (spec "Status cell: status or verb"): a ``state`` cell,
    or the ``verb`` pill of the row's next run. ``stage``: the running job's
    (``download`` / ``harmonize``) while it is the row's turn; ``queued``: the
    row is claimed and waits for its turn; ``failed``: the row has an error."""
    stage = _stage(stage, file_source)
    if stage == "download":
        label = t("steps.prepare.state_downloading_pct", pct=pct) if pct is not None else t(
            "steps.prepare.state_downloading")
        return _state(STATUS_ICONS["running"], "primary", label, progress=pct)
    if stage == "harmonize":
        return _state(STATUS_ICONS["running"], "primary", t("steps.prepare.state_harmonizing"))
    if queued:
        return _state("mdi-timer-sand", "grey", t("steps.prepare.state_queued"))
    if row.file_missing:
        return _state(STATUS_ICONS["failed"], "error", t("steps.prepare.state_file_missing"))
    if row.blocked:
        status = _BLOCKED_STATUS[row.coverage]
        return _state(STATUS_ICONS[status], "grey", status_text(status))
    if row.ready:
        return _state(STATUS_ICONS["ready"], "success", t("steps.prepare.state_ready"))
    verb, stale = _next(row, file_source)
    icon, color = ("mdi-update", "warning") if stale else ("mdi-play", "primary")
    if locked:  # a review copy runs nothing: the state, no pill
        return _state(icon, color, t(f"steps.prepare.state_to_{verb}"))
    if failed:
        return {"type": "verb", "label": t("steps.prepare.verb_retry"), "icon": "mdi-refresh", "color": "error",
                "on_click": on_run}
    return {"type": "verb", "label": t(f"steps.prepare.verb_{verb}"), "icon": icon, "color": color, "on_click": on_run}


def _ready_text(row: PrepareRow, file_source: bool, raw_art: Optional[Artifact], harm_art: Optional[Artifact]) -> str:
    """``downloaded {date} · {size} · {n} alert pixels``; a user file names its format."""
    if file_source:
        origin = t("steps.prepare.sub_file")
    else:
        origin = t("steps.prepare.sub_downloaded", date=raw_art.created_at.date().isoformat()) if raw_art else ""
    size = size_text(sum(a.size_bytes for a in (raw_art, harm_art) if a is not None))
    pixels = (row.stats or {}).get("alert_pixels")
    count = tn("steps.prepare.sub_pixels", int(pixels)) if pixels is not None else ""
    return " · ".join(part for part in (origin, size, count) if part)


def _fallback(row: PrepareRow, file_source: bool) -> Reason:
    """A row out of date whose inputs all match (a file deleted on disk): its status says what is missing."""
    if row.raw is not Status.CURRENT:
        return Reason("not_read" if file_source else "not_downloaded")
    return Reason("not_harmonized")


def _reason_line(reason: Reason, file_source: bool) -> str:
    """The short reason: bare for ``not_*``, else what the run redoes."""
    why = reason_text(reason)
    if reason.key.startswith("not_"):
        return why
    again = "download" if reason.key in _RAW_TERMS and not file_source else "harmonize"
    return t(f"steps.prepare.sub_again_{again}", why=why)


def source_subtitle(row: PrepareRow, *, file_source: bool, raw_art: Optional[Artifact],
                    harm_art: Optional[Artifact], reason: Optional[Reason], stage: Optional[str], queued: bool,
                    pct: Optional[int]) -> tuple[str, Optional[str]]:
    """The grey line under the source's name (spec "Source cell"), and its
    tone (None, or ``error`` for a missing file). ``reason``: why the row is
    out of date (``engine.why.source_reason``); a download with no percent
    yet keeps it."""
    stage = _stage(stage, file_source)
    if stage == "download" and pct is not None:
        return t("steps.prepare.sub_downloading", pct=pct), None
    if stage == "harmonize":
        return t("steps.prepare.sub_harmonizing"), None
    if queued:
        return t("steps.prepare.sub_queued"), None
    if row.file_missing:
        return t("steps.prepare.state_file_missing"), "error"
    if row.blocked:
        return row_notes(row.coverage, row.notes)[0]["text"], None
    if row.ready:
        return _ready_text(row, file_source, raw_art, harm_art), None
    return _reason_line(reason or _fallback(row, file_source), file_source), None
