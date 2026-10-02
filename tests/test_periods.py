"""Period rules (spec §5.1, §6.4): new periods, frozen dates, deletion."""

import uuid
from datetime import date, datetime

import pytest

from deforestation_alerts.engine import fingerprint as fp
from deforestation_alerts.project import cleanup, periods
from deforestation_alerts.project.models import (
    AlertSet, AlertSetParams, AoiRecord, Artifact, MaskLayer, NativeGrid, PeriodMask, Project, add_mask,
    add_predefined_source, set_period_mask,
)

DEC_ID, JAN_ID, FEB_ID = "2025-12-01_2025-12-31", "2026-01-01_2026-01-31", "2026-02-01_2026-02-28"


def _p():
    p = Project(name="per", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="d", bounds=(0, 0, 1, 1))
    add_predefined_source(p, "GLAD-L")
    return p


def _periods(p, *ranges):
    for a, b in ranges:
        assert periods.new_period(p, a, b) is None
    return p


def _file(pdir, rel):
    path = pdir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return rel


def _art(pdir, key, kind, rel):
    return Artifact(key=key, kind=kind, rel_path=_file(pdir, rel), fingerprint="f", inputs={},
                    content_id=uuid.uuid4().hex, created_at=datetime.now())


def _with_downloads(p, pdir, pid):
    """The period's raw and harmonized files, recorded as Download & harmonize leaves them."""
    raw, harm = fp.raw_key(pid, "GLAD-L"), fp.harm_key(pid, "GLAD-L")
    p.artifacts = {**p.artifacts,
                   raw: _art(pdir, raw, "raw_alert", f"periods/{pid}/raw/raw.tif"),
                   harm: _art(pdir, harm, "harmonized", f"periods/{pid}/harmonized/harmonized.tif")}


def _with_set(p, pdir, pid, created=None):
    """A set of the period: its three recorded files, plus tiles and an Analysis output nobody records."""
    sid = f"{pid}__glad-l"
    folder = f"periods/{pid}/sets/glad-l"
    keys = ((fp.combined_key(sid), "combined", "c.tif"), (fp.footprint_key(sid), "footprint", "footprint.tif"),
            (fp.store_key(sid), "store", "alert_db.parquet"))
    p.artifacts = {**p.artifacts, **{k: _art(pdir, k, kind, f"{folder}/{name}") for k, kind, name in keys}}
    _file(pdir, f"{folder}/alerts.pmtiles")
    _file(pdir, f"{folder}/alert_3_2026-01-02_2026-01-20.tif")
    p.alert_sets = {**p.alert_sets, sid: AlertSet(
        id=sid, period_id=pid, sources=["GLAD-L"], digit_map={0: "GLAD-L"}, params=AlertSetParams(),
        store_rel_path=f"{folder}/alert_db.parquet", combined_rel_path=f"{folder}/c.tif",
        footprint_rel_path=f"{folder}/footprint.tif", fingerprint="f", created_at=created or datetime.now())}
    return sid


def _files_under(pdir, pid):
    return sorted(x for x in (pdir / "periods" / pid).rglob("*") if x.is_file())


# ---------------------------------------------------------------------------
# New period
# ---------------------------------------------------------------------------
def test_a_new_period_is_checked_and_becomes_active():
    p = _p()
    before = p.periods
    assert periods.new_period(p, "nope", "2026-01-31") == "bad_date"
    assert periods.new_period(p, "2026-01-31", "2026-01-31") == "order"  # end > start (spec §6.4)
    assert p.periods is before and p.active_period is None  # untouched on an error
    assert periods.new_period(p, "2026-01-01", "2026-01-31") is None
    assert p.periods is not before and p.active_period == JAN_ID
    assert periods.new_period(p, "2026-01-01", "2026-01-31") == "exists"


def test_a_new_period_copies_the_choices_of_the_period_with_the_latest_start():
    p = _periods(_p(), ("2026-01-01", "2026-01-31"))
    add_mask(p, name="forest", origin="gee", ref="UMD/x", asset_type="IMAGE", band="b",
             native=NativeGrid(crs="EPSG:4326", scale=30.0), mode="exclude", period_id=JAN_ID)
    assert periods.new_period(p, "2026-02-01", "2026-02-28") is None
    assert p.periods[FEB_ID].masks == {"forest": PeriodMask(on=True, mode="exclude")}
    set_period_mask(p, FEB_ID, "forest", mode="keep")  # a copy: January keeps its own choice
    assert p.periods[JAN_ID].masks["forest"].mode == "exclude"
    assert periods.new_period(p, "2025-12-01", "2025-12-31") is None  # an earlier period still copies the latest start
    assert p.periods[DEC_ID].masks == {"forest": PeriodMask(on=True, mode="keep")}


def test_the_first_period_starts_with_every_mask_off():
    p = _p()
    p.masks = {"forest": MaskLayer(name="forest", origin="gee", ref="UMD/x")}  # a duplicate: masks, no periods (D10)
    assert periods.new_period(p, "2026-01-01", "2026-01-31") is None
    assert p.periods[JAN_ID].masks == {}


def test_prefill_follows_the_latest_period():
    today = date(2026, 9, 30)
    assert periods.prefill(_p(), today) == ("2026-09-01", "2026-09-30")
    p = _periods(_p(), ("2026-02-01", "2026-02-28"), ("2026-01-01", "2026-01-31"))
    assert periods.prefill(p, today) == ("2026-03-01", "2026-09-30")


# ---------------------------------------------------------------------------
# Frozen dates
# ---------------------------------------------------------------------------
def test_dates_are_frozen_once_the_period_has_sets(tmp_path):
    p = _periods(_p(), ("2026-01-01", "2026-01-31"))
    _with_set(p, tmp_path, JAN_ID)
    periods_before, artifacts_before = p.periods, p.artifacts
    assert periods.edit_period(p, JAN_ID, "2026-01-01", "2026-01-15") == ("locked", [])
    assert p.periods is periods_before and p.artifacts is artifacts_before


def test_changing_the_dates_drops_the_old_windows_files_and_keeps_the_masks(tmp_path):
    p = _periods(_p(), ("2026-01-01", "2026-01-31"), ("2026-02-01", "2026-02-28"))
    p.periods = {**p.periods, JAN_ID: p.periods[JAN_ID].model_copy(
        update={"masks": {"forest": PeriodMask(on=True, mode="keep")}})}
    periods.select_period(p, JAN_ID)
    _with_downloads(p, tmp_path, JAN_ID)
    _with_downloads(p, tmp_path, FEB_ID)
    assert periods.edit_period(p, JAN_ID, "2026-02-01", "2026-02-28") == ("exists", [])
    error, gone = periods.edit_period(p, JAN_ID, "2026-01-01", "2026-01-15")
    assert error is None and len(gone) == 2
    new_id = "2026-01-01_2026-01-15"
    assert p.active_period == new_id and JAN_ID not in p.periods
    assert p.periods[new_id].masks == {"forest": PeriodMask(on=True, mode="keep")}
    assert not [k for k in p.artifacts if JAN_ID in k] and len(p.artifacts) == 2  # February's stay
    cleanup.delete_files(tmp_path, gone)
    cleanup.delete_period_dir(tmp_path, JAN_ID)
    assert not (tmp_path / "periods" / JAN_ID).exists() and len(_files_under(tmp_path, FEB_ID)) == 2
    assert periods.edit_period(p, new_id, "2026-01-01", "2026-01-15") == (None, [])  # same dates: nothing


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------
def test_a_period_with_sets_is_deleted_only_when_it_is_the_latest(tmp_path):
    p = _periods(_p(), ("2026-01-01", "2026-01-31"), ("2026-02-01", "2026-02-28"))
    jan = _with_set(p, tmp_path, JAN_ID)
    _with_downloads(p, tmp_path, FEB_ID)
    feb = _with_set(p, tmp_path, FEB_ID)
    p.active_set = feb
    before = (p.periods, p.alert_sets, p.artifacts)
    assert periods.delete_error(p, JAN_ID) == "not_latest"
    assert periods.delete_period(p, JAN_ID) == ("not_latest", [])
    assert all(now is then for now, then in zip((p.periods, p.alert_sets, p.artifacts), before))

    assert periods.delete_error(p, FEB_ID) is None
    error, gone = periods.delete_period(p, FEB_ID)
    assert error is None and len(gone) == 5  # raw, harmonized, combined, footprint, store
    assert list(p.periods) == [JAN_ID] and list(p.alert_sets) == [jan]
    assert p.active_period == JAN_ID and p.active_set == jan
    assert not [k for k in p.artifacts if FEB_ID in k]
    cleanup.delete_files(tmp_path, gone)
    cleanup.delete_period_dir(tmp_path, FEB_ID)
    assert not (tmp_path / "periods" / FEB_ID).exists()  # tiles and Analysis outputs too
    assert len(_files_under(tmp_path, JAN_ID)) == 5  # January's files are untouched


def test_a_period_without_sets_can_always_be_deleted(tmp_path):
    p = _periods(_p(), ("2025-12-01", "2025-12-31"), ("2026-01-01", "2026-01-31"))
    _with_set(p, tmp_path, JAN_ID)
    _with_downloads(p, tmp_path, DEC_ID)
    assert periods.delete_error(p, DEC_ID) is None
    error, gone = periods.delete_period(p, DEC_ID)
    assert error is None and len(gone) == 2 and list(p.periods) == [JAN_ID]


def test_deleting_the_last_period_clears_the_active_ones(tmp_path):
    p = _periods(_p(), ("2026-01-01", "2026-01-31"))
    p.active_set = _with_set(p, tmp_path, JAN_ID)
    assert periods.delete_period(p, JAN_ID)[0] is None
    assert p.periods == {} and p.alert_sets == {} and p.active_period is None and p.active_set is None


def test_deleting_replaces_the_shared_containers(tmp_path):
    p = _periods(_p(), ("2026-01-01", "2026-01-31"))
    _with_set(p, tmp_path, JAN_ID)
    snapshot = p.model_copy()
    iterating = iter(snapshot.alert_sets.values())
    periods.delete_period(p, JAN_ID)
    assert len(list(iterating)) == 1 and list(snapshot.periods) == [JAN_ID]


# ---------------------------------------------------------------------------
# Look-back, selection, ordering
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["-1", "x", "1.5", ""])
def test_a_bad_look_back_is_refused(value):
    p = _p()
    assert periods.set_look_back(p, value) == "bad_look_back" and p.look_back_days == 90


def test_look_back_and_selection():
    p = _periods(_p(), ("2026-01-01", "2026-01-31"), ("2026-02-01", "2026-02-28"))
    assert periods.set_look_back(p, " 30 ") is None and p.look_back_days == 30
    periods.select_period(p, JAN_ID)
    assert p.active_period == JAN_ID
    periods.select_period(p, "gone")
    assert p.active_period == JAN_ID


def test_newest_first(tmp_path):
    p = _periods(_p(), ("2026-01-01", "2026-01-31"), ("2026-02-01", "2026-02-28"), ("2025-12-01", "2025-12-31"))
    assert [per.id for per in periods.periods_newest_first(p)] == [FEB_ID, JAN_ID, DEC_ID]
    assert periods.latest_period(p).id == FEB_ID
    jan = _with_set(p, tmp_path, JAN_ID, created=datetime(2026, 3, 1))
    feb = _with_set(p, tmp_path, FEB_ID, created=datetime(2026, 2, 1))  # built earlier, newer period
    assert [s.id for s in periods.sets_newest_first(p)] == [feb, jan]
    assert periods.sets_of(p, JAN_ID) == [jan] and periods.sets_of(p, DEC_ID) == []
