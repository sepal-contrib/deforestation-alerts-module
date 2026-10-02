from datetime import date, datetime

import pytest

from deforestation_alerts.engine import fingerprint as fp
from deforestation_alerts.engine.plan import plan_prepare, ready_sources
from deforestation_alerts.project import io
from deforestation_alerts.project.models import (
    AlertSet, AlertSetParams, AoiRecord, GridSpec, Period, Project, add_predefined_source,
)

JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
FEB = Period(id="2026-02-01_2026-02-28", start=date(2026, 2, 1), end=date(2026, 2, 28))


@pytest.fixture
def proj(tmp_path, cover):
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="g1", bounds=(0, 0, 1, 1))
    p.grid = GridSpec(epsg="EPSG:32721", resolution=10, xmin=0, ymax=0, width=10, height=10)
    add_predefined_source(p, "GLAD-L")
    add_predefined_source(p, "RADD")
    p.periods = {JAN.id: JAN, FEB.id: FEB}
    cover(p, "GLAD-L", "RADD")  # the runner and plan block a row without a coverage record
    pdir = tmp_path / p.name  # the folder io.save_project(p, root=tmp_path) writes to
    pdir.mkdir()
    return p, pdir


def _record(p, pdir, key, kind, inputs, name):
    f = pdir / name
    f.write_bytes(b"x")
    p.artifacts[key] = fp.make_artifact(key, kind, pdir, f, inputs)
    return p.artifacts[key]


def _prepare(p, pdir, period, source):
    _record(p, pdir, fp.raw_key(period.id, source), "raw_alert",
            fp.raw_alert_inputs(p, source, period), f"raw_{source}_{period.id}.tif")
    _record(p, pdir, fp.harm_key(period.id, source), "harmonized",
            fp.harmonized_inputs(p, source, period), f"harm_{source}_{period.id}.tif")


def test_fingerprint_is_order_independent():
    assert fp.fingerprint({"a": 1, "b": [1, 2]}) == fp.fingerprint({"b": [1, 2], "a": 1})


def test_raw_status_lifecycle(proj):
    p, pdir = proj
    key = fp.raw_key(JAN.id, "GLAD-L")
    assert fp.status(p, pdir, key, fp.raw_alert_inputs(p, "GLAD-L", JAN)) is fp.Status.MISSING
    _prepare(p, pdir, JAN, "GLAD-L")
    assert fp.status(p, pdir, key, fp.raw_alert_inputs(p, "GLAD-L", JAN)) is fp.Status.CURRENT
    p.look_back_days = 30  # the window changes
    assert fp.status(p, pdir, key, fp.raw_alert_inputs(p, "GLAD-L", JAN)) is fp.Status.STALE


def test_aoi_change_makes_raw_stale(proj):
    p, pdir = proj
    _prepare(p, pdir, JAN, "GLAD-L")
    key = fp.raw_key(JAN.id, "GLAD-L")
    p.aoi = p.aoi.model_copy(update={"digest": "g2"})
    assert fp.status(p, pdir, key, fp.raw_alert_inputs(p, "GLAD-L", JAN)) is fp.Status.STALE


def test_grid_change_leaves_raw_current_and_makes_harmonized_stale(proj):
    """Raw files are in the source's native projection: only harmonizing depends on the grid."""
    p, pdir = proj
    _prepare(p, pdir, JAN, "GLAD-L")
    rkey, hkey = fp.raw_key(JAN.id, "GLAD-L"), fp.harm_key(JAN.id, "GLAD-L")
    p.grid = p.grid.model_copy(update={"resolution": 30})
    assert fp.status(p, pdir, rkey, fp.raw_alert_inputs(p, "GLAD-L", JAN)) is fp.Status.CURRENT
    assert fp.status(p, pdir, hkey, fp.harmonized_inputs(p, "GLAD-L", JAN)) is fp.Status.STALE
    row = plan_prepare(p, pdir, JAN)[0]
    assert row.raw is fp.Status.CURRENT and row.needs_run and not row.ready


def test_native_projection_change_makes_raw_stale(proj, monkeypatch):
    from deforestation_alerts.engine import catalogue

    p, pdir = proj
    _prepare(p, pdir, JAN, "GLAD-L")
    key = fp.raw_key(JAN.id, "GLAD-L")
    monkeypatch.setitem(catalogue.NATIVE, "GLAD-L", ("EPSG:4326", 30.0))
    assert fp.status(p, pdir, key, fp.raw_alert_inputs(p, "GLAD-L", JAN)) is fp.Status.STALE


def test_deleted_file_is_missing(proj):
    p, pdir = proj
    _prepare(p, pdir, JAN, "GLAD-L")
    (pdir / p.artifacts[fp.raw_key(JAN.id, "GLAD-L")].rel_path).unlink()
    assert fp.status(p, pdir, fp.raw_key(JAN.id, "GLAD-L"),
                     fp.raw_alert_inputs(p, "GLAD-L", JAN)) is fp.Status.MISSING


def test_forced_redownload_makes_harmonized_stale(proj):
    p, pdir = proj
    _prepare(p, pdir, JAN, "GLAD-L")
    hkey = fp.harm_key(JAN.id, "GLAD-L")
    assert fp.status(p, pdir, hkey, fp.harmonized_inputs(p, "GLAD-L", JAN)) is fp.Status.CURRENT
    # same inputs, new pixels: a Re-download writes a new content_id
    _record(p, pdir, fp.raw_key(JAN.id, "GLAD-L"), "raw_alert",
            fp.raw_alert_inputs(p, "GLAD-L", JAN), "raw_again.tif")
    assert fp.status(p, pdir, hkey, fp.harmonized_inputs(p, "GLAD-L", JAN)) is fp.Status.STALE


def _set(p, pdir, period, sources):
    sid = f"{period.id}__x"
    params = AlertSetParams()
    inputs = fp.set_inputs(p, period, sources, params)
    (pdir / f"s_{period.id}.parquet").write_bytes(b"x")
    s = AlertSet(id=sid, period_id=period.id, sources=sources, digit_map={0: "GLAD-L"}, params=params,
                 store_rel_path=f"s_{period.id}.parquet", combined_rel_path="c.tif",
                 footprint_rel_path=f"fp_{period.id}.tif",
                 fingerprint=fp.fingerprint(inputs), created_at=datetime.now())
    p.alert_sets[sid] = s
    _record(p, pdir, fp.footprint_key(sid), "footprint", {"set": sid}, f"fp_{period.id}.tif")
    return s


def test_new_earlier_set_makes_later_set_stale(proj):
    p, pdir = proj
    _prepare(p, pdir, FEB, "GLAD-L")
    feb = _set(p, pdir, FEB, ["GLAD-L"])
    assert fp.set_status(p, pdir, feb) is fp.Status.CURRENT
    _prepare(p, pdir, JAN, "GLAD-L")
    _set(p, pdir, JAN, ["GLAD-L"])  # an earlier period gets a set afterwards
    assert fp.set_status(p, pdir, feb) is fp.Status.STALE


def test_reopened_project_with_deleted_files_offers_a_rerun(proj, tmp_path):
    p, pdir = proj
    _prepare(p, pdir, JAN, "GLAD-L")
    jan = _set(p, pdir, JAN, ["GLAD-L"])
    io.save_project(p, root=tmp_path)
    p = io.load_project(p.name, root=tmp_path)
    # the manifest round trip keeps every fingerprint: nothing goes out of date on reopen
    assert plan_prepare(p, pdir, JAN)[0].ready
    assert fp.set_status(p, pdir, p.alert_sets[jan.id]) is fp.Status.CURRENT

    (pdir / p.artifacts[fp.harm_key(JAN.id, "GLAD-L")].rel_path).unlink()
    (pdir / jan.store_rel_path).unlink()
    row = plan_prepare(p, pdir, JAN)[0]
    assert row.harmonized is fp.Status.MISSING and row.needs_run and not row.ready
    assert fp.set_status(p, pdir, p.alert_sets[jan.id]) is fp.Status.MISSING


def test_plan_and_ready_sources(proj):
    p, pdir = proj
    _prepare(p, pdir, JAN, "GLAD-L")
    rows = plan_prepare(p, pdir, JAN)
    assert [r.source for r in rows] == ["GLAD-L", "RADD"]  # digit order
    assert rows[0].ready and not rows[0].needs_run
    assert rows[1].raw is fp.Status.MISSING and rows[1].needs_run
    assert ready_sources(p, pdir, JAN) == ["GLAD-L"]


def test_a_slice1_no_data_record_reads_missing(proj):
    p, pdir = proj
    key = fp.raw_key(JAN.id, "RADD")
    art = fp.make_artifact(key, "raw_alert", pdir, None, fp.raw_alert_inputs(p, "RADD", JAN))
    p.artifacts[key] = art.model_copy(update={"no_data": True})
    assert fp.status(p, pdir, key, fp.raw_alert_inputs(p, "RADD", JAN)) is fp.Status.MISSING


def test_an_empty_rel_path_reads_missing(proj):
    p, pdir = proj
    key = fp.raw_key(JAN.id, "RADD")
    p.artifacts[key] = fp.make_artifact(key, "raw_alert", pdir, None, fp.raw_alert_inputs(p, "RADD", JAN))
    assert fp.status(p, pdir, key, fp.raw_alert_inputs(p, "RADD", JAN)) is fp.Status.MISSING


def test_a_look_back_change_makes_a_set_stale_before_any_rerun(proj):
    p, pdir = proj
    _prepare(p, pdir, JAN, "GLAD-L")
    jan = _set(p, pdir, JAN, ["GLAD-L"])
    assert fp.set_status(p, pdir, jan) is fp.Status.CURRENT
    p.look_back_days = 30  # the search window changes: the raw download is out of date
    assert fp.set_status(p, pdir, jan) is fp.Status.STALE


def test_a_deleted_harmonized_file_is_not_an_input_change(proj):
    """A file gone from disk reads MISSING in its row; the set built from it
    keeps its own state (here CURRENT: its store is there)."""
    p, pdir = proj
    _prepare(p, pdir, JAN, "GLAD-L")
    jan = _set(p, pdir, JAN, ["GLAD-L"])
    (pdir / p.artifacts[fp.harm_key(JAN.id, "GLAD-L")].rel_path).unlink()
    assert fp.set_status(p, pdir, jan) is fp.Status.CURRENT
