"""Download & harmonize source rows, option B: one status or verb, and a subtitle."""

from datetime import datetime

import pytest
from pysepal.i18n import set_locale

from deforestation_alerts.engine.coverage import Note
from deforestation_alerts.engine.fingerprint import Status
from deforestation_alerts.engine.plan import PrepareRow
from deforestation_alerts.engine.why import Reason
from deforestation_alerts.project.models import Artifact
from gui.scripts.prepare_view import reason_text, source_state, source_subtitle


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


def _state(row, **over):
    kw = dict(file_source=False, stage=None, queued=False, pct=None, failed=False, locked=False,
              on_run=lambda: None)
    kw.update(over)
    return source_state(row, **kw)


def _subtitle(row, **over):
    kw = dict(file_source=False, raw_art=None, harm_art=None, reason=None, stage=None, queued=False, pct=None)
    kw.update(over)
    return source_subtitle(row, **kw)


def _art(kind, size, created_at=datetime(2026, 2, 3, 10, 0)):
    return Artifact(key=kind, kind=kind, rel_path="x.tif", fingerprint="f", inputs={}, content_id="c",
                    size_bytes=size, created_at=created_at)


def _verb(cell):
    assert cell["type"] == "verb", cell
    return (cell["label"], cell["color"], cell["icon"])


def test_ready_row_reads_ready_with_no_verb():
    row = PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT, stats={"alert_pixels": 1200})
    cell = _state(row)
    assert {k: cell[k] for k in ("type", "label", "icon", "color")} == {
        "type": "state", "label": "Ready", "icon": "mdi-check-circle", "color": "success"}
    text, tone = _subtitle(row, raw_art=_art("raw_alert", 3_000_000), harm_art=_art("harmonized", 2_400_000))
    assert text.startswith("downloaded ")
    assert text == "downloaded 2026-02-03 · 5.4 MB · 1,200 alert pixels" and tone is None


def test_a_ready_file_row_names_its_format_not_a_download():
    row = PrepareRow("tif", Status.CURRENT, Status.CURRENT, stats={"alert_pixels": 1})
    text, _ = _subtitle(row, file_source=True, raw_art=_art("raw_alert", 0), harm_art=_art("harmonized", 0))
    assert text == "GeoTIFF · 1 alert pixel"  # no size yet: no empty part


def test_missing_catalogue_row_offers_download():
    clicks = []
    cell = _state(PrepareRow("RADD", Status.MISSING, Status.MISSING), on_run=lambda: clicks.append(1))
    assert _verb(cell) == ("Download", "primary", "mdi-play")
    cell["on_click"]()
    assert clicks == [1]


def test_missing_file_row_offers_harmonize():
    """A GeoTIFF is read where it is: its pill never says Download."""
    row = PrepareRow("tif", Status.MISSING, Status.MISSING)
    assert _verb(_state(row, file_source=True)) == ("Harmonize", "primary", "mdi-play")
    assert _verb(_state(PrepareRow("RADD", Status.CURRENT, Status.MISSING))) == ("Harmonize", "primary", "mdi-play")
    assert _subtitle(row, file_source=True, reason=Reason("not_read")) == ("not harmonized yet", None)


def test_stale_raw_offers_download_in_warning():
    row = PrepareRow("RADD", Status.STALE, Status.STALE)
    assert _verb(_state(row)) == ("Download", "warning", "mdi-update")
    reason = Reason("window", (("old", "2025-10-03 – 2026-01-31"), ("new", "2025-11-02 – 2026-01-31")))
    assert _subtitle(row, reason=reason)[0] == (
        "the search window changed (2025-10-03 – 2026-01-31 → 2025-11-02 – 2026-01-31): download again")
    # a user file that changed is read again: harmonize, not download
    assert _verb(_state(row, file_source=True)) == ("Harmonize", "warning", "mdi-update")
    assert _subtitle(row, file_source=True, reason=Reason("file"))[0] == "your file changed on disk: harmonize again"


def test_stale_harm_offers_harmonize_in_warning():
    row = PrepareRow("RADD", Status.CURRENT, Status.STALE)
    assert _verb(_state(row)) == ("Harmonize", "warning", "mdi-update")
    reason = Reason("grid", (("old", "EPSG:32719 · 30 m"), ("new", "EPSG:32719 · 20 m")))
    assert _subtitle(row, reason=reason)[0] == (
        "the grid changed (EPSG:32719 · 30 m → EPSG:32719 · 20 m): harmonize again")
    assert _subtitle(row, reason=Reason("mask", (("name", "forest"),)))[0] == "mask forest changed: harmonize again"


def test_a_row_with_no_changed_term_falls_back_on_its_status():
    """Ruling 4: a file deleted on disk changes no fingerprint term."""
    assert _subtitle(PrepareRow("RADD", Status.MISSING, Status.MISSING))[0] == "not downloaded yet"
    assert _subtitle(PrepareRow("tif", Status.MISSING, Status.MISSING), file_source=True)[0] == "not harmonized yet"
    assert _subtitle(PrepareRow("RADD", Status.CURRENT, Status.MISSING))[0] == "not harmonized yet"


def test_a_failed_row_offers_retry():
    row = PrepareRow("RADD", Status.CURRENT, Status.MISSING)
    assert _verb(_state(row, failed=True))[:2] == ("Retry", "error")
    ready = PrepareRow("RADD", Status.CURRENT, Status.CURRENT)
    assert _state(ready, failed=True)["label"] == "Ready"  # a ready row's old error offers nothing


def test_running_row_shows_stage_and_percent():
    row = PrepareRow("RADD", Status.MISSING, Status.MISSING)
    cell = _state(row, stage="download", pct=42)
    assert (cell["type"], cell["label"], cell["progress"]) == ("state", "Downloading 42%", 42)
    assert _subtitle(row, stage="download", pct=42) == ("downloading 42% · then harmonize", None)
    assert _state(row, stage="download")["label"] == "Downloading"  # no percent yet
    cell = _state(row, stage="harmonize", pct=42)
    assert cell["label"] == "Harmonizing" and cell.get("progress") is None
    assert _subtitle(row, stage="harmonize")[0] == "harmonizing"
    # a GeoTIFF is read, not downloaded
    assert _state(row, file_source=True, stage="download", pct=42)["label"] == "Harmonizing"


def test_claimed_row_without_stage_reads_queued():
    row = PrepareRow("RADD", Status.MISSING, Status.MISSING)
    cell = _state(row, queued=True)
    assert (cell["type"], cell["icon"], cell["label"]) == ("state", "mdi-timer-sand", "Queued")
    assert _subtitle(row, queued=True)[0] == "waiting for the row above"


def test_blocked_and_missing_file_rows_read_their_status():
    row = PrepareRow("GLAD-S2", Status.MISSING, Status.MISSING, "not_covered",
                     (Note("coverage.not_covered_glad_s2", (("source", "GLAD-S2"),), "error"),))
    cell = _state(row)
    assert (cell["type"], cell["label"], cell["color"]) == ("state", "not covered", "grey")
    assert _subtitle(row)[0].startswith("Not covered: GLAD-S2 does not cover this AOI")
    gone = PrepareRow("tif", Status.MISSING, Status.CURRENT, file_missing="/x/tif.tif")
    cell = _state(gone, file_source=True)
    assert (cell["type"], cell["label"], cell["color"]) == ("state", "File missing", "error")
    assert _subtitle(gone, file_source=True) == ("file not found", "error")  # not the cell's words again


def test_a_review_copy_reads_text_not_verbs():
    assert _state(PrepareRow("RADD", Status.MISSING, Status.MISSING), locked=True)["label"] == "To download"
    cell = _state(PrepareRow("RADD", Status.CURRENT, Status.STALE), locked=True, failed=True)
    assert (cell["type"], cell["label"]) == ("state", "To harmonize")
    # a grey circle, not the play or update icon of a pill: nothing there looks clickable
    assert (cell["icon"], cell["color"]) == ("mdi-circle-outline", "grey")
    assert _state(PrepareRow("tif", Status.MISSING, Status.MISSING), locked=True, file_source=True)["label"] == (
        "To harmonize")


@pytest.mark.parametrize("locale", ["en", "es-ES", "fr", "pt-BR"])
def test_status_labels_fit_their_cell(locale):
    """At most 12 characters in every language, so the 128 px Status cell
    never wraps; the running percent may take Downloading to 16 at 100%."""
    from gui.i18n import t

    set_locale(locale)
    keys = ["ready", "queued", "downloading", "harmonizing", "aligning", "file_missing", "to_download",
            "to_harmonize", "to_align", "off"]
    labels = [t(f"steps.prepare.state_{k}") for k in keys]
    labels += [t(f"steps.prepare.verb_{k}") for k in ("download", "harmonize", "align", "retry")]
    assert all(len(label) <= 12 for label in labels), [x for x in labels if len(x) > 12]
    assert len(t("steps.prepare.state_downloading_pct", pct=100)) <= 16


def test_reason_text_reads_its_values():
    assert reason_text(Reason("mask_not_aligned", (("name", "forest"),))) == "a mask must be aligned first (forest)"


def test_every_reason_has_its_message():
    """``WHY_KEYS`` (the i18n scan's list) names every key ``engine.why`` returns."""
    import re
    from pathlib import Path

    from deforestation_alerts.engine import why
    from gui.scripts.prepare_view import WHY_KEYS

    built = set(re.findall(r'Reason\("([a-z_]+)"', Path(why.__file__).read_text(encoding="utf-8")))
    assert built <= set(WHY_KEYS)


def test_a_mask_not_aligned_reads_bare_without_a_harmonize_again_tail():
    """The Harmonize pill aligns the mask first: "harmonize again" would contradict it."""
    row = PrepareRow("RADD", Status.CURRENT, Status.STALE)
    reason = Reason("mask_not_aligned", (("name", "forest"),))
    assert _subtitle(row, reason=reason) == ("a mask must be aligned first (forest)", None)
