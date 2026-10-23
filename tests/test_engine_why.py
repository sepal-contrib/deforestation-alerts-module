"""Why a source or mask is out of date: the first changed term, in the spec's priority."""

from datetime import date, datetime

import pytest

from deforestation_alerts.engine import fingerprint as fp
from deforestation_alerts.engine.why import Reason, grid_label, mask_reason, source_reason
from deforestation_alerts.project.models import (
    AoiRecord, BandMap, GridSpec, NativeGrid, Period, Project, add_custom_source, add_mask, add_predefined_source,
    set_period_mask,
)

JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
GRID30 = GridSpec(epsg="EPSG:32719", resolution=30, xmin=300000, ymax=8700000, width=100, height=80)
GRID20 = GridSpec(epsg="EPSG:32719", resolution=20, xmin=300000, ymax=8700000, width=150, height=120)


def _p(tmp_path):
    p = Project(name="w", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="d", bounds=(0, 0, 1, 1))
    p.grid = GRID30
    add_predefined_source(p, "GLAD-L")
    add_custom_source(p, name="mine", origin="file", ref=str(tmp_path / "mine.tif"), asset_type="FILE",
                      format="radd", date_encoding="yyddd", band_map=BandMap(alert=1, date=2),
                      native=NativeGrid(crs="EPSG:32719", scale=30.0, transform=(30, 0, 300000, 0, -30, 8700000)))
    p.periods = {JAN.id: JAN}
    return p


def _record(p, key, kind, inputs):
    p.artifacts = {**p.artifacts, key: fp.make_artifact(key, kind, "/tmp", None, inputs)}
    return p.artifacts[key]


def _add_forest(p, *, on=True):
    add_mask(p, name="forest", origin="gee", ref="users/x/forest", asset_type="IMAGE", band="b1",
             native=NativeGrid(crs="EPSG:4326", scale=30.0), mode="keep", period_id=JAN.id)
    if not on:
        set_period_mask(p, JAN.id, "forest", on=False)


def _align_forest(p):
    if fp.mask_raw_key("forest") not in p.artifacts:
        _record(p, fp.mask_raw_key("forest"), "raw_mask", fp.mask_raw_inputs(p, "forest"))
    _record(p, fp.mask_key("forest"), "mask", fp.mask_inputs(p, "forest"))


def _download(p, source="GLAD-L", period=JAN):
    _record(p, fp.raw_key(period.id, source), "raw_alert", fp.raw_alert_inputs(p, source, period))


def _harmonize(p, source="GLAD-L", period=JAN):
    if fp.is_file_source(p.sources[source]):
        _record(p, fp.AOI_KEY, "aoi_mask", fp.aoi_raster_inputs(p))
    _record(p, fp.harm_key(period.id, source), "harmonized", fp.harmonized_inputs(p, source, period))


def _ready(p, source="GLAD-L"):
    _download(p, source)
    _harmonize(p, source)


def test_no_records_reads_not_downloaded(tmp_path):
    assert source_reason(_p(tmp_path), JAN, "GLAD-L") == Reason("not_downloaded")


def test_a_file_source_with_no_raw_reads_not_read(tmp_path):
    assert source_reason(_p(tmp_path), JAN, "mine") == Reason("not_read")


def test_raw_only_reads_not_harmonized(tmp_path):
    p = _p(tmp_path)
    _download(p)
    assert source_reason(p, JAN, "GLAD-L") == Reason("not_harmonized")


def test_current_rows_have_no_reason(tmp_path):
    p = _p(tmp_path)
    _add_forest(p)
    _align_forest(p)
    _ready(p)
    _ready(p, "mine")
    assert source_reason(p, JAN, "GLAD-L") is None
    assert source_reason(p, JAN, "mine") is None
    assert mask_reason(p, "forest") is None


def test_grid_change_reads_old_and_new(tmp_path):
    p = _p(tmp_path)
    _ready(p)
    p.grid = GRID20
    assert source_reason(p, JAN, "GLAD-L") == Reason(
        "grid", (("old", "EPSG:32719 · 30 m"), ("new", "EPSG:32719 · 20 m")))


def test_grid_change_with_a_mask_on_reads_grid_first(tmp_path):
    p = _p(tmp_path)
    _add_forest(p)
    _align_forest(p)
    _ready(p)
    p.grid = GRID20  # the aligned forest record is now stale: mask_not_aligned too
    assert not fp.mask_current(p, "forest")
    assert source_reason(p, JAN, "GLAD-L").key == "grid"


def test_a_switched_mask_names_it(tmp_path):
    p = _p(tmp_path)
    _add_forest(p, on=False)
    _ready(p)  # harmonized with masks []
    set_period_mask(p, JAN.id, "forest", on=True)
    _align_forest(p)
    assert source_reason(p, JAN, "GLAD-L") == Reason("mask", (("name", "forest"),))


def test_two_changed_masks_read_masks(tmp_path):
    p = _p(tmp_path)
    _add_forest(p, on=False)
    add_mask(p, name="water", origin="gee", ref="users/x/water", asset_type="IMAGE", band="b1",
             native=NativeGrid(crs="EPSG:4326", scale=30.0), mode="exclude", period_id=JAN.id)
    set_period_mask(p, JAN.id, "water", on=False)
    _ready(p)
    for name in ("forest", "water"):
        set_period_mask(p, JAN.id, name, on=True)
        _record(p, fp.mask_raw_key(name), "raw_mask", fp.mask_raw_inputs(p, name))
        _record(p, fp.mask_key(name), "mask", fp.mask_inputs(p, name))
    assert source_reason(p, JAN, "GLAD-L") == Reason("masks")


def test_an_unaligned_mask_names_it(tmp_path):
    p = _p(tmp_path)
    _add_forest(p)
    _download(p)
    _record(p, fp.harm_key(JAN.id, "GLAD-L"), "harmonized",
            {"stage": "harmonized", "raw": p.artifacts[fp.raw_key(JAN.id, "GLAD-L")].content_id,
             "grid": GRID30.signature, "masks": []})
    assert source_reason(p, JAN, "GLAD-L") == Reason("mask_not_aligned", (("name", "forest"),))


def test_an_unaligned_mask_with_no_harmonized_record_names_it(tmp_path):
    p = _p(tmp_path)
    _add_forest(p)
    _download(p)
    assert source_reason(p, JAN, "GLAD-L") == Reason("mask_not_aligned", (("name", "forest"),))


def test_a_new_look_back_reads_window(tmp_path):
    p = _p(tmp_path)
    _ready(p)
    wider = JAN.model_copy(update={"look_back_days": 30})
    p.periods = {JAN.id: wider}
    assert source_reason(p, wider, "GLAD-L") == Reason(
        "window", (("old", "2026-01-01 – 2026-01-31"), ("new", "2025-12-02 – 2026-01-31")))


def test_a_new_look_back_with_no_harmonized_record_reads_window(tmp_path):
    p = _p(tmp_path)
    _download(p)
    wider = JAN.model_copy(update={"look_back_days": 30})
    p.periods = {JAN.id: wider}
    assert source_reason(p, wider, "GLAD-L").key == "window"


def test_a_new_aoi_reads_aoi_before_the_window(tmp_path):
    p = _p(tmp_path)
    _ready(p)
    p.aoi = p.aoi.model_copy(update={"digest": "other"})
    wider = JAN.model_copy(update={"look_back_days": 30})
    p.periods = {JAN.id: wider}
    assert source_reason(p, wider, "GLAD-L") == Reason("aoi")


def test_a_rebuilt_aoi_raster_reads_aoi_raster(tmp_path):
    p = _p(tmp_path)
    _ready(p, "mine")
    _record(p, fp.AOI_KEY, "aoi_mask", fp.aoi_raster_inputs(p))  # same inputs, new content id
    assert source_reason(p, JAN, "mine") == Reason("aoi_raster")


def test_a_changed_user_file_reads_file(tmp_path, monkeypatch):
    p = _p(tmp_path)
    _ready(p, "mine")
    monkeypatch.setattr(fp, "_file_stamp", lambda path: {"size": 2, "mtime_ns": 99})
    assert source_reason(p, JAN, "mine") == Reason("file")


def test_a_changed_source_setting_reads_source(tmp_path):
    p = _p(tmp_path)
    _ready(p, "mine")
    p.sources = {**p.sources, "mine": p.sources["mine"].model_copy(update={"date_encoding": "yymm"})}
    assert source_reason(p, JAN, "mine") == Reason("source")


def test_a_forced_redownload_reads_raw(tmp_path):
    p = _p(tmp_path)
    _ready(p)
    _download(p)  # same inputs, new content id
    assert source_reason(p, JAN, "GLAD-L") == Reason("raw")


def test_mask_reasons(tmp_path):
    p = _p(tmp_path)
    _add_forest(p)
    assert mask_reason(p, "forest") == Reason("not_downloaded")
    _record(p, fp.mask_raw_key("forest"), "raw_mask", fp.mask_raw_inputs(p, "forest"))
    assert mask_reason(p, "forest") == Reason("not_aligned")
    _align_forest(p)
    p.grid = GRID20
    assert mask_reason(p, "forest") == Reason(
        "grid", (("old", "EPSG:32719 · 30 m"), ("new", "EPSG:32719 · 20 m")))
    p.grid = GRID30
    _record(p, fp.mask_raw_key("forest"), "raw_mask", fp.mask_raw_inputs(p, "forest"))
    assert mask_reason(p, "forest") == Reason("raw")


def test_file_mask_reasons(tmp_path, monkeypatch):
    p = _p(tmp_path)
    add_mask(p, name="own", origin="file", ref=str(tmp_path / "own.tif"), asset_type="FILE", band=1,
             native=None, mode="keep", period_id=JAN.id)
    assert mask_reason(p, "own") == Reason("not_aligned")
    _record(p, fp.AOI_KEY, "aoi_mask", fp.aoi_raster_inputs(p))
    _record(p, fp.mask_key("own"), "mask", fp.mask_inputs(p, "own"))
    assert mask_reason(p, "own") is None
    monkeypatch.setattr(fp, "_file_stamp", lambda path: {"size": 2, "mtime_ns": 99})
    assert mask_reason(p, "own") == Reason("file")
    _record(p, fp.AOI_KEY, "aoi_mask", fp.aoi_raster_inputs(p))
    assert mask_reason(p, "own") == Reason("aoi_raster")


@pytest.mark.parametrize("signature, label", [
    ("EPSG:4326|0.00025|-57.0|-9.9|400x300", "EPSG:4326 · 0.00025"),
    ("EPSG:32719|30.0|300000.0|8700000.0|100x80", "EPSG:32719 · 30 m"),
    ("EPSG:32719|12.5|300000.0|8700000.0|100x80", "EPSG:32719 · 12.5 m"),
])
def test_grid_label(signature, label):
    assert grid_label(signature) == label
