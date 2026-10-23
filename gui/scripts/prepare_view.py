"""What a Download & harmonize source row shows (option B): one Status cell
holding a state or a verb pill, and a subtitle under the source's name.

Pure presentation: the row's ``PrepareRow``, its artifacts, its ``Reason``
(``engine.why``) and the job's report in, ``ProductTable`` cells out.

A user file (``is_file_source``) is read where it is, never downloaded: its
pill says Harmonize and its raw-side reasons read "harmonize again", so no
label promises a download that does not happen.
"""

from datetime import datetime
from typing import Callable, Optional

from deforestation_alerts.engine import catalogue
from deforestation_alerts.engine.coverage import NO_DATA, NOT_COVERED, UNCHECKED, pct_text, record_for
from deforestation_alerts.engine.fingerprint import Status, harm_key, is_file_source, mask_key, mask_raw_key, raw_key
from deforestation_alerts.engine.plan import MaskRow, PrepareRow
from deforestation_alerts.engine.why import Reason, grid_label
from deforestation_alerts.project.models import Artifact
from gui.i18n import format_number, size_text, t, tn
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


def source_label(src) -> str:
    """The name a source row and its Details dialog carry."""
    return t(catalogue.LABEL_KEYS[src.catalogue_key]) if src.kind == "predefined" else src.name


def row_status_label(row: PrepareRow, file_source: bool) -> str:
    """The row's status as words (the Details dialog's lead): its state, or
    what Run does next, without the button."""
    if row.file_missing:
        return t("steps.prepare.state_file_missing")
    if row.blocked:
        return status_text(_BLOCKED_STATUS[row.coverage])
    if row.ready:
        return t("steps.prepare.state_ready")
    return t(f"steps.prepare.state_to_{_next(row, file_source)[0]}")


# ---------------------------------------------------------------------------
# Details dialog (spec "Details dialog"): read-only key/value sections.
#
# ``Details`` = {"title", "lead", "why": Optional[str], "sections": [{"title",
# "icon", "when": Optional[str], "rows": [(label, value)], "empty":
# Optional[str]}]}. A section whose file was never written has no rows and
# says ``empty``. Values are read from the stored artifacts, so an out-of-date
# file shows what it was made with.
# ---------------------------------------------------------------------------

def _section(title: str, icon: str, art: Optional[Artifact], rows: list) -> dict:
    """``rows`` are (label, value) pairs, dropped when the value is empty;
    ``art``: the record the section describes, None when it was never written."""
    if art is None:
        return {"title": title, "icon": icon, "when": None, "rows": [], "empty": t("steps.prepare.not_run")}
    return {"title": title, "icon": icon, "when": art.created_at.strftime("%Y-%m-%d %H:%M"), "empty": None,
            "rows": [(label, value) for label, value in rows if value]}


def _dims(signature: str) -> str:
    width, height = signature.split("|")[-1].split("x")
    return f"{width} × {height} px"


def _grid_text(stored: Optional[str], current: Optional[str]) -> str:
    """``EPSG:32719 · 30 m · 4812 × 3950 px``, with ``(now …)`` while today's grid differs."""
    if not stored:
        return ""
    text = f"{grid_label(stored)} · {_dims(stored)}"
    if current and current != stored:
        now = grid_label(current) if grid_label(current) != grid_label(stored) else _dims(current)
        text += " " + t("steps.prepare.d_now", grid=now)
    return text


def _native_text(native) -> str:
    """A stored ``[crs, scale]`` (or a file's ``[crs, transform]``) as ``EPSG:32719 · 30 m``."""
    if not native:
        return ""
    crs, scale = native
    if isinstance(scale, (list, tuple)):
        scale = abs(scale[0]) if scale else None
    return grid_label(f"{crs}|{scale}") if scale else str(crs)


def _mode_text(mode: str) -> str:
    return t("steps.prepare.d_mode_keep" if mode == "keep" else "steps.prepare.d_mode_exclude")


def _period_text(period) -> str:
    return t("steps.period.range", start=period.start.isoformat(), end=period.end.isoformat())


def _iso_day(yyyyddd) -> str:
    return datetime.strptime(str(int(yyyyddd)), "%Y%j").date().isoformat()


def _pixels_text(stats: dict) -> str:
    kept = stats.get("alert_pixels")
    if kept is None:
        return ""
    before = stats.get("alert_pixels_unmasked")
    if before is None or before == kept:
        return format_number(kept)
    return t("steps.prepare.d_pixels_masked", kept=format_number(kept), before=format_number(before),
             removed=format_number(before - kept))


def _dates_text(stats: dict) -> str:
    lo, hi = stats.get("date_min"), stats.get("date_max")
    return f"{_iso_day(lo)} – {_iso_day(hi)}" if lo is not None and hi is not None else ""


def _source_section(p, source: str) -> dict:
    src = p.sources[source]
    if src.kind == "predefined":
        cov = record_for(p, source)
        parts = []
        if cov is not None:
            lasts = [r.last for r in cov.regions if r.last is not None]
            parts = [t("steps.prepare.d_coverage", pct=pct_text(cov.fraction)),
                     t("steps.prepare.d_data_up_to", last=max(lasts).isoformat()) if lasts else "",
                     t("steps.prepare.d_checked", date=cov.checked_on.isoformat())]
        rows = [(t("steps.prepare.f_catalogue"), t(catalogue.LABEL_KEYS[src.catalogue_key])),
                (t("steps.prepare.f_coverage"), " · ".join(x for x in parts if x))]
    else:
        formats = {"radd": t("steps.alerts.format_radd"), "ccdc": t("steps.alerts.format_ccdc"),
                   "pyeo": t("steps.alerts.format_pyeo"), "date_only": t("steps.alerts.format_date_only")}
        bands = " · ".join(x for x in (
            t("steps.prepare.d_band_alert", band=src.band_map.alert) if src.band_map.alert is not None else "",
            t("steps.prepare.d_band_date", band=src.band_map.date)) if x)
        rows = [(t("steps.prepare.f_kind"), t("steps.prepare.d_kind_file" if src.origin == "file" else "steps.prepare.d_kind_ee")),
                (t("steps.prepare.f_ref"), src.ref), (t("steps.prepare.f_format"), formats[src.format]),
                (t("steps.prepare.f_encoding"), src.date_encoding),
                (t("steps.prepare.f_bands"), bands),
                (t("steps.prepare.f_native"), grid_label(f"{src.native.crs}|{src.native.scale}") if src.native else "")]
    return {"title": t("steps.prepare.sec_source"), "icon": "mdi-database-outline", "when": None, "empty": None,
            "rows": [(label, value) for label, value in rows if value]}


def _window_text(window) -> str:
    return " – ".join(window) if window else ""


def source_details(p, period, source: str, row: PrepareRow, reason: Optional[Reason], status_label: str) -> dict:
    """The Details dialog of a source row. ``reason``: ``engine.why.source_reason``."""
    file_source = is_file_source(p.sources[source])
    raw, harm = p.artifacts.get(raw_key(period.id, source)), p.artifacts.get(harm_key(period.id, source))
    raw = None if raw is not None and raw.no_data else raw
    harm = None if harm is not None and harm.no_data else harm
    why = None
    if not (row.ready or row.blocked):
        why = reason_text(reason or _fallback(row, file_source))
    download = _section(
        t("steps.prepare.sec_read" if file_source else "steps.prepare.sec_download"), "mdi-cloud-download-outline", raw,
        [] if raw is None else [
            (t("steps.prepare.f_window"), _window_text(raw.inputs.get("window"))), (t("steps.prepare.f_native"), _native_text(raw.inputs.get("native"))),
            (t("steps.prepare.f_size"), size_text(raw.size_bytes)), (t("steps.prepare.f_file"), raw.rel_path)])
    harmonize = _section(t("steps.prepare.sec_harmonize"), "mdi-grid", harm, [] if harm is None else [
        (t("steps.prepare.f_grid"), _grid_text(harm.inputs.get("grid"), p.grid.signature if p.grid else None)),
        (t("steps.prepare.f_resampling"), t("steps.prepare.d_nearest")),
        (t("steps.prepare.f_masks"), ", ".join(f"{name} · {_mode_text(mode)}" for name, mode, *_ in harm.inputs.get("masks") or [])
         or t("steps.prepare.masks_none")),
        (t("steps.prepare.f_pixels"), _pixels_text(harm.stats)), (t("steps.prepare.f_dates"), _dates_text(harm.stats)),
        (t("steps.prepare.f_size"), size_text(harm.size_bytes)), (t("steps.prepare.f_file"), harm.rel_path)])
    return {"title": source_label(p.sources[source]),
            "lead": f"{status_label} · {_period_text(period)}",
            "why": why, "sections": [_source_section(p, source), download, harmonize]}


def mask_details(p, name: str, row: MaskRow, reason: Optional[Reason], status_label: str) -> dict:
    """The Details dialog of a mask row. ``reason``: ``engine.why.mask_reason``."""
    mask = p.masks[name]
    raw, aligned = p.artifacts.get(mask_raw_key(name)), p.artifacts.get(mask_key(name))
    used = [f"{_period_text(q)} · {_mode_text(c.mode)}"
            for q in sorted(p.periods.values(), key=lambda q: q.start) for c in [q.masks.get(name)] if c and c.on]
    source = {"title": t("steps.prepare.sec_source"), "icon": "mdi-database-outline", "when": None, "empty": None,
              "rows": [(label, value) for label, value in (
                  (t("steps.prepare.f_ref"), mask.ref), (t("steps.prepare.f_band"), str(mask.band)),
                  (t("steps.prepare.f_native"), grid_label(f"{mask.native.crs}|{mask.native.scale}") if mask.native else ""),
                  (t("steps.prepare.f_periods"), ", ".join(used))) if value]}
    sections = [source]
    if mask.origin == "gee":
        sections.append(_section(t("steps.prepare.sec_download"), "mdi-cloud-download-outline", raw, [] if raw is None else [
            (t("steps.prepare.f_native"), _native_text(raw.inputs.get("native"))), (t("steps.prepare.f_size"), size_text(raw.size_bytes)),
            (t("steps.prepare.f_file"), raw.rel_path)]))
    sections.append(_section(t("steps.prepare.sec_align"), "mdi-grid", aligned, [] if aligned is None else [
        (t("steps.prepare.f_grid"), _grid_text(aligned.inputs.get("grid"), p.grid.signature if p.grid else None)),
        (t("steps.prepare.f_on_pixels"), format_number(aligned.stats["on_pixels"]) if "on_pixels" in aligned.stats else ""),
        (t("steps.prepare.f_size"), size_text(aligned.size_bytes)), (t("steps.prepare.f_file"), aligned.rel_path)]))
    why = None
    if not (row.ready or row.file_missing):
        why = reason_text(reason or Reason("not_aligned"))
    return {"title": name, "lead": status_label, "why": why, "sections": sections}
