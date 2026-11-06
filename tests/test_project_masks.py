from datetime import date, datetime

import pytest

from deforestation_alerts.project import cleanup
from deforestation_alerts.project.models import (
    AlertSet, AlertSetParams, NativeGrid, Period, PeriodMask, Project, add_mask, latest_period_masks,
    remove_mask, set_period_mask, sets_using_mask, update_mask,
)

JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
FEB = Period(id="2026-02-01_2026-02-28", start=date(2026, 2, 1), end=date(2026, 2, 28))


def _p():
    p = Project(name="m", created_at=datetime.now(), updated_at=datetime.now())
    p.periods = {JAN.id: JAN, FEB.id: FEB}
    return p


def _add(p, name="forest", period=FEB.id, mode="keep"):
    return add_mask(p, name=name, origin="gee", ref="users/x/fnf", asset_type="IMAGE", band="b1",
                    native=NativeGrid(crs="EPSG:4326", scale=30.0), mode=mode, period_id=period)


def test_a_new_mask_is_on_in_its_period_only():
    p = _p()
    periods_before = p.periods
    _add(p)
    assert p.periods is not periods_before
    assert p.periods[FEB.id].masks == {"forest": PeriodMask(on=True, mode="keep")}
    assert p.periods[JAN.id].masks == {}


def test_switching_replaces_the_containers():
    p = _p()
    _add(p)
    before = p.periods[FEB.id]
    set_period_mask(p, FEB.id, "forest", mode="exclude")
    set_period_mask(p, FEB.id, "forest", on=False)
    assert p.periods[FEB.id] is not before and before.masks["forest"].mode == "keep"
    assert p.periods[FEB.id].masks["forest"] == PeriodMask(on=False, mode="exclude")


def test_round_trip():
    p = _p()
    _add(p)
    back = Project.model_validate_json(p.model_dump_json())
    assert back == p and isinstance(back.periods[FEB.id].masks["forest"], PeriodMask)


def test_edit_refused_once_a_set_used_the_mask_and_delete_drops_every_choice():
    p = _p()
    _add(p)
    set_period_mask(p, JAN.id, "forest", on=True)
    update_mask(p, "forest", band="b2")
    assert p.masks["forest"].band == "b2"
    p.alert_sets = {"s": AlertSet(id="s", period_id=FEB.id, sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                                  params=AlertSetParams(), masks_used=[{"name": "forest", "mode": "keep",
                                                                        "content_id": "c"}],
                                  store_rel_path="a", combined_rel_path="c", footprint_rel_path="f",
                                  fingerprint="f", created_at=datetime.now())}
    assert sets_using_mask(p, "forest") == ["s"]
    with pytest.raises(ValueError, match="steps.prepare.mask_edit_used"):
        update_mask(p, "forest", band="b3")
    remove_mask(p, "forest")
    assert p.masks == {} and all(per.masks == {} for per in p.periods.values())
    assert p.alert_sets["s"].masks_used[0]["name"] == "forest"  # sets keep their record


def test_latest_period_masks_copies_the_latest_start():
    p = _p()
    _add(p, period=JAN.id, mode="exclude")
    _add(p, name="water", period=FEB.id)
    copy = latest_period_masks(p)
    assert copy == {"water": PeriodMask(on=True, mode="keep")} and copy is not p.periods[FEB.id].masks
    assert latest_period_masks(Project(name="e", created_at=datetime.now(), updated_at=datetime.now())) == {}


def test_mask_artifact_keys():
    from deforestation_alerts.engine.fingerprint import mask_key, mask_raw_key
    from deforestation_alerts.project.models import Artifact

    p = _p()
    _add(p)
    p.artifacts = {k: Artifact(key=k, kind="mask", rel_path=f"{k}.tif", fingerprint="f", inputs={},
                               content_id="c", created_at=datetime.now())
                   for k in (mask_key("forest"), mask_raw_key("forest"), "raw:x:GLAD-L")}
    assert sorted(cleanup.mask_artifact_keys(p, "forest")) == ["mask:forest", "mask_raw:forest"]
