"""The Details dialog of a Download & harmonize row: what ``source_details`` /
``mask_details`` read from the project, and how ``DetailsDialog`` shows it."""

import uuid
from datetime import date, datetime

import pytest
import reacton
from pysepal.i18n import set_locale

from deforestation_alerts.engine import grid as gridmod
from deforestation_alerts.engine.fingerprint import (
    Status, harm_key, mask_key, mask_raw_inputs, mask_raw_key, raw_alert_inputs, raw_key,
)
from deforestation_alerts.engine.plan import MaskRow, PrepareRow
from deforestation_alerts.engine.why import Reason, mask_reason, source_reason
from deforestation_alerts.project.models import (
    AoiRecord, Artifact, BandMap, GridSpec, NativeGrid, Period, Project, RegionCoverage, SourceCoverage, add_custom_source,
    add_mask, add_predefined_source, set_period_mask,
)
from gui.scripts.prepare_view import mask_details, source_details
from gui.widget.details_dialog import DetailsDialog

JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31), look_back_days=30)
SIG30 = "EPSG:32719|30.0|300000.0|8000000.0|4812x3950"
WHEN = datetime(2026, 10, 21, 14, 2, 41)


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


def _art(key, kind, inputs, *, rel="periods/a.tif", size=0, stats=None):
    return Artifact(key=key, kind=kind, rel_path=rel, fingerprint="f", inputs=inputs, content_id=uuid.uuid4().hex,
                    size_bytes=size, stats=stats or {}, created_at=WHEN)


def _project(*, resolution=30.0, grid=None):
    p = Project(name="d", created_at=WHEN, updated_at=WHEN)
    p.periods[JAN.id] = JAN
    p.active_period = JAN.id
    bounds = (-70.0, -13.0, -69.5, -12.5)
    p.aoi = AoiRecord(spec={}, name="d", method="DRAW", digest="dig", bounds=bounds)
    p.grid = grid or gridmod.build_grid(bounds, "EPSG:32719", resolution)
    return p


def _record(p, source, period, grid_signature, masks, stats):
    """A raw and a harmonized record of ``source``, as a run would leave them."""
    raw = _art(raw_key(period.id, source), "raw_alert", raw_alert_inputs(p, source, period), rel="raw/x.tif",
               size=3_000_000)
    harm = _art(harm_key(period.id, source), "harmonized",
                {"stage": "harmonized", "raw": raw.content_id, "grid": grid_signature, "masks": masks},
                rel="periods/x.tif", size=2_400_000, stats=stats)
    p.artifacts = {**p.artifacts, raw.key: raw, harm.key: harm}
    return raw, harm


def _rows(section):
    return dict(section["rows"])


def test_ready_source_details():
    p = _project(grid=GridSpec(epsg="EPSG:32719", resolution=30.0, xmin=300000.0, ymax=8000000.0, width=4812,
                               height=3950))
    add_predefined_source(p, "GLAD-L")
    p.coverage["GLAD-L"] = SourceCoverage(aoi_digest="dig", checked_on=date(2026, 10, 20), fraction=0.98,
                                          regions=[RegionCoverage(name="SA", fraction=0.98, last=date(2026, 9, 28))])
    add_mask(p, name="forest", origin="file", ref="/m.tif", asset_type="FILE", band=1, native=None, mode="keep",
             period_id=JAN.id)
    _record(p, "GLAD-L", JAN, SIG30, [["forest", "keep", "c1"]],
            {"alert_pixels": 1203, "alert_pixels_unmasked": 1615, "date_min": 2026215, "date_max": 2026271})
    row = PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT)
    d = source_details(p, JAN, "GLAD-L", row, None, "Ready")
    assert d["lead"] == "Ready · 2026-01-01 → 2026-01-31" and d["why"] is None
    assert [s["title"] for s in d["sections"]] == ["Source", "Download", "Harmonize"]
    source, download, harmonize = d["sections"]
    assert _rows(source)["Coverage"] == "98% of the AOI · data up to 2026-09-28 · checked 2026-10-20"
    assert download["when"] == "2026-10-21 14:02"
    assert _rows(download)["Search window"] == "2025-12-02 – 2026-01-31"
    assert _rows(download)["Size"] == "3.0 MB"
    assert ("Grid used", "EPSG:32719 · 30 m · 4812 × 3950 px") in harmonize["rows"]
    assert ("Masks", "forest · keep") in harmonize["rows"]
    assert ("Alert pixels", "1,203 (1,615 before masks: 412 removed)") in harmonize["rows"]
    assert ("Alert dates", "2026-08-03 – 2026-09-28") in harmonize["rows"]
    assert ("Resampling", "nearest (keeps alert codes and dates exact)") in harmonize["rows"]


def _file_source(p):
    add_custom_source(p, name="tif", origin="file", ref="/data/alerts.tif", asset_type="FILE", format="date_only",
                      date_encoding="yyyyddd", band_map=BandMap(alert=None, date=1),
                      native=NativeGrid(crs="EPSG:32719", scale=30.0))


def test_stale_file_source_details():
    p = _project(resolution=20.0)
    _file_source(p)
    _record(p, "tif", JAN, SIG30, [], {"alert_pixels": 5})
    reason = source_reason(p, JAN, "tif")
    row = PrepareRow("tif", Status.CURRENT, Status.STALE)
    d = source_details(p, JAN, "tif", row, reason, "To harmonize")
    assert d["why"] == "the grid changed (EPSG:32719 · 30 m → EPSG:32719 · 20 m)"
    source, download, harmonize = d["sections"]
    assert download["title"] == "Read from file"
    assert _rows(source)["Kind"] == "Custom · file on disk" and _rows(source)["Reference"] == "/data/alerts.tif"
    assert _rows(source)["Bands"] == "date: 1"
    grid = _rows(harmonize)["Grid used"]
    assert grid.startswith("EPSG:32719 · 30 m · 4812 × 3950 px") and grid.endswith("(now EPSG:32719 · 20 m)")
    assert _rows(harmonize)["Masks"] == "none on in this period"


def test_a_not_ready_row_without_a_reason_still_says_why():
    p = _project()
    add_predefined_source(p, "RADD")
    d = source_details(p, JAN, "RADD", PrepareRow("RADD", Status.MISSING, Status.MISSING), None, "To download")
    assert d["why"] == "not downloaded yet"


def test_unrun_source_details():
    p = _project()
    add_predefined_source(p, "RADD")
    row = PrepareRow("RADD", Status.MISSING, Status.MISSING)
    d = source_details(p, JAN, "RADD", row, Reason("not_downloaded"), "To download")
    _, download, harmonize = d["sections"]
    assert download["empty"] == "Not run yet" and download["rows"] == []
    assert harmonize["empty"] == "Not run yet" and harmonize["when"] is None


def test_mask_details_lists_periods_using_it():
    p = _project()
    feb = Period(id="2026-02-01_2026-02-28", start=date(2026, 2, 1), end=date(2026, 2, 28))
    p.periods[feb.id] = feb
    add_mask(p, name="forest", origin="gee", ref="users/x/forest", asset_type="IMAGE", band="b1",
             native=NativeGrid(crs="EPSG:4326", scale=27.829872698318393), mode="keep", period_id=JAN.id)
    set_period_mask(p, feb.id, "forest", on=True, mode="exclude")
    raw = _art(mask_raw_key("forest"), "raw_mask", mask_raw_inputs(p, "forest"), rel="masks/raw.tif", size=1_000_000)
    aligned = _art(mask_key("forest"), "mask", {"stage": "mask", "grid": SIG30}, rel="masks/forest.tif",
                   size=500_000, stats={"on_pixels": 777})
    p.artifacts = {**p.artifacts, raw.key: raw, aligned.key: aligned}
    row = MaskRow("forest", "gee", Status.CURRENT, Status.CURRENT, True, "keep")
    d = mask_details(p, "forest", row, mask_reason(p, "forest"), "Ready")
    assert d["title"] == "forest" and d["lead"] == "Ready"
    assert [s["title"] for s in d["sections"]] == ["Source", "Download", "Align"]
    source, download, align = d["sections"]
    assert _rows(source)["Band"] == "b1" and _rows(source)["Native grid"] == "EPSG:4326 · 28 m"
    assert _rows(source)["Used in"] == "2026-01-01 → 2026-01-31 · keep, 2026-02-01 → 2026-02-28 · exclude"
    assert _rows(download)["File"] == "masks/raw.tif"
    assert _rows(align)["Mask pixels"] == "777" and _rows(align)["Grid used"].startswith("EPSG:32719 · 30 m")


def test_a_file_mask_has_no_download_section():
    p = _project()
    add_mask(p, name="forest", origin="file", ref="/m.tif", asset_type="FILE", band=1, native=None, mode="keep",
             period_id=JAN.id)
    row = MaskRow("forest", "file", None, Status.MISSING, True, "keep")
    d = mask_details(p, "forest", row, Reason("not_aligned"), "To align")
    assert [s["title"] for s in d["sections"]] == ["Source", "Align"]
    assert d["why"] == "not aligned yet" and d["sections"][1]["empty"] == "Not run yet"


def _strings(widget):
    out = []
    for child in getattr(widget, "children", None) or []:
        out.extend([child] if isinstance(child, str) else _strings(child))
    return out


def test_dialog_renders_sections_and_why():
    details = {"title": "GLAD-L", "lead": "To harmonize · 2026-01", "why": "the grid changed",
               "sections": [{"title": "Source", "icon": "mdi-database-outline", "when": None,
                             "rows": [("Catalogue", "GLAD-L")], "empty": None},
                            {"title": "Download", "icon": "mdi-cloud-download-outline", "when": "2026-10-21 14:02",
                             "rows": [], "empty": "Not run yet"}]}
    closed = []
    _, rc = reacton.render(DetailsDialog(open=True, details=details, on_close=lambda: closed.append(1)),
                           handle_error=False)
    try:
        text = _strings(rc.container)
        for s in ("GLAD-L", "To harmonize · 2026-01", "Why:", "the grid changed", "Source", "Catalogue",
                  "2026-10-21 14:02", "Not run yet"):
            assert s in text, s
        next(b for b in rc.find(__import__("ipyvuetify").Btn).widgets if "Close" in b.children).fire_event(
            "click", None)
        assert closed == [1]
    finally:
        rc.close()
    _, rc = reacton.render(DetailsDialog(open=True, details={**details, "why": None}, on_close=lambda: None),
                           handle_error=False)
    try:
        assert "Why:" not in _strings(rc.container)
    finally:
        rc.close()


def test_native_grid_reads_metres_for_earth_engine_and_degrees_for_a_geographic_file():
    """An Earth Engine scale is in metres even in EPSG:4326; a file's transform
    in its CRS's units; a projected file reads metres."""
    p = _project()
    add_predefined_source(p, "GLAD-L")
    _record(p, "GLAD-L", JAN, SIG30, [], {})
    d = source_details(p, JAN, "GLAD-L", PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT), None, "Ready")
    assert _rows(d["sections"][1])["Native grid"] == "EPSG:4326 · 28 m"

    add_custom_source(p, name="geo", origin="file", ref="/data/geo.tif", asset_type="FILE", format="date_only",
                      date_encoding="yyyyddd", band_map=BandMap(alert=None, date=1),
                      native=NativeGrid(crs="EPSG:4326", scale=27.829872698318393,
                                        transform=(0.00025, 0.0, -70.0, 0.0, -0.00025, -12.5)))
    _record(p, "geo", JAN, SIG30, [], {})
    d = source_details(p, JAN, "geo", PrepareRow("geo", Status.CURRENT, Status.CURRENT), None, "Ready")
    assert _rows(d["sections"][0])["Native grid"] == "EPSG:4326 · 0.00025°"
    assert _rows(d["sections"][1])["Native grid"] == "EPSG:4326 · 0.00025°"

    _file_source(p)  # EPSG:32719, 30 m, no transform
    assert _rows(source_details(p, JAN, "tif", PrepareRow("tif", Status.MISSING, Status.MISSING), None,
                                "To harmonize")["sections"][0])["Native grid"] == "EPSG:32719 · 30 m"
