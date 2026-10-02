import asyncio
import threading
from datetime import date, datetime
from types import SimpleNamespace

import ipyvuetify as v
import numpy as np
import pytest
import rasterio
import reacton
import solara
from affine import Affine

from deforestation_alerts.project.models import (
    AoiRecord, BandMap, CustomSource, NativeGrid, Project, RegionCoverage, SourceCoverage, add_predefined_source,
)
from gui.i18n import t
from gui.store.state_manager import AppState
from gui.widget import source_modal as sm


def _p():
    p = Project(name="m", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="d", bounds=(-55.5, -24.5, -55.4, -24.4))
    return p


def _cov(fraction):
    return SourceCoverage(aoi_digest="d", checked_on=date(2026, 10, 1), fraction=fraction,
                          regions=[RegionCoverage(name="x", fraction=fraction)])


def test_predefined_items_disable_an_uncovered_source_with_its_reason():
    p = _p()
    add_predefined_source(p, "GLAD-L")
    p.coverage = {"RADD": _cov(1.0), "GLAD-S2": _cov(0.0)}
    items = {i["value"]: i for i in sm.predefined_items(p, pending=False)}
    assert set(items) == {"RADD", "GLAD-S2"}
    assert items["GLAD-S2"]["disabled"] and "90°W–30°W" in items["GLAD-S2"]["text"]
    assert not items["RADD"]["disabled"] and "covers the AOI" in items["RADD"]["text"]


def test_predefined_items_while_checking_and_after_a_failed_check():
    p = _p()
    items = sm.predefined_items(p, pending=True)
    assert all("checking coverage" in i["text"] and not i["disabled"] for i in items)
    items = sm.predefined_items(p, pending=False)  # no record and not checking: the check failed
    assert all("coverage not checked" in i["text"] and not i["disabled"] for i in items)


def _gee(answer):
    calls = []

    async def get_info_async(obj):
        calls.append(obj)
        return answer(obj)

    return SimpleNamespace(get_info_async=get_info_async), calls


@pytest.fixture
def requests(monkeypatch):
    monkeypatch.setattr(sm, "discovery_request", lambda ref, kind, bands: ("DISC", ref, kind, tuple(bands)))
    monkeypatch.setattr(sm, "coverage_request", lambda aoi, pre, custom: ("COV", tuple(c.name for c in custom)))
    monkeypatch.setattr(sm, "_bundle", lambda d: d)  # the ee.Dictionary of both, passed through


def _discovery(count=1):
    return {"count": count, "crs": "EPSG:4326", "scale": 10.0, "crss": ["EPSG:4326"], "min_scale": 10.0,
            "projection": {"transform": [8.98e-5, 0, -56, 0, -8.98e-5, -23]}, "date_format": None,
            "band_types": {"Alert": {"precision": "int"}, "Date": {"precision": "int"}}}


def _coverage_raw(name, fraction):
    return {name: {"fraction": fraction, "regions": [{"name": name, "fraction": fraction}]}}


def _kw(**over):
    kw = dict(name="forest radd", origin="gee", ref="users/x/radd", asset_type="IMAGE", fmt="radd",
              encoding="yymm", alert_band="Alert", date_band="Date")
    kw.update(over)
    return kw


def test_a_valid_earth_engine_source(requests):
    gee, calls = _gee(lambda obj: {"discovery": _discovery(), "coverage": _coverage_raw("forest radd", 0.4)})
    out = asyncio.run(sm.validate_custom(gee, _p(), "AOI", **_kw()))
    assert len(calls) == 1  # one Earth Engine request
    f = out["fields"]
    assert f["native"].crs == "EPSG:4326" and f["native"].scale == 10.0 and f["date_encoding"] == "yyddd"
    assert f["band_map"].alert == "Alert" and out["coverage"].fraction == 0.4


def test_a_source_that_misses_the_aoi_is_refused(requests):
    gee, _ = _gee(lambda obj: {"discovery": _discovery(), "coverage": _coverage_raw("forest radd", 0.0)})
    assert asyncio.run(sm.validate_custom(gee, _p(), "AOI", **_kw())) == {"error": "steps.alerts.err_not_covered"}


def test_a_bad_name_is_refused_before_any_request(requests):
    gee, calls = _gee(lambda obj: {})
    assert asyncio.run(sm.validate_custom(gee, _p(), "AOI", **_kw(name="RADD"))) == {
        "error": "steps.alerts.name_reserved"}
    assert calls == []


def test_an_empty_collection_is_refused(requests):
    gee, _ = _gee(lambda obj: {"discovery": {"count": 0}, "coverage": _coverage_raw("forest radd", 1.0)})
    out = asyncio.run(sm.validate_custom(gee, _p(), "AOI", **_kw(asset_type="IMAGE_COLLECTION")))
    assert out == {"error": "steps.alerts.err_no_images"}


def test_date_only_needs_only_a_date_band_and_keeps_its_encoding(requests):
    disc = _discovery()
    disc["band_types"] = {"disturbance_date": {"precision": "int"}}
    gee, calls = _gee(lambda obj: {"discovery": disc, "coverage": _coverage_raw("monthly", 1.0)})
    out = asyncio.run(sm.validate_custom(gee, _p(), "AOI", **_kw(name="monthly", fmt="date_only",
                                                                 alert_band=None, date_band="disturbance_date")))
    assert out["fields"]["band_map"].alert is None and out["fields"]["date_encoding"] == "yymm"
    assert calls[0]["discovery"][3] == ("disturbance_date",)


def test_a_geotiff_source(tmp_path, requests):
    path = tmp_path / "ccdc.tif"
    with rasterio.open(path, "w", driver="GTiff", width=10, height=10, count=2, dtype="float32", crs="EPSG:4326",
                       transform=Affine(0.0002, 0, -55.5, 0, -0.0002, -24.4)) as dst:
        dst.write(np.zeros((2, 10, 10), "float32"))
    gee, calls = _gee(lambda obj: _coverage_raw("ccdc tif", 0.01))
    out = asyncio.run(sm.validate_custom(gee, _p(), "AOI", **_kw(name="ccdc tif", origin="file", ref=str(path),
                                                                 asset_type="FILE", fmt="ccdc", alert_band=1,
                                                                 date_band=2)))
    assert out["fields"]["native"].transform[0] == 0.0002 and out["fields"]["date_encoding"] == "decimal_year"
    assert out["fields"]["native"].bounds is not None and calls == [("COV", ("ccdc tif",))]


# ---------------------------------------------------------------- edge cases
def _tif(path, dtype="int16", count=2, descriptions=None):
    with rasterio.open(path, "w", driver="GTiff", width=10, height=10, count=count, dtype=dtype, crs="EPSG:4326",
                       transform=Affine(0.0002, 0, -55.5, 0, -0.0002, -24.4)) as dst:
        if descriptions:
            dst.descriptions = tuple(descriptions)
    return path


def test_a_complex_geotiff_band_is_refused_with_a_message_key(tmp_path, requests):
    """Task 11 left ``_numeric`` raising TypeError on complex_int16: the modal
    would have shown Python's message instead of the inline refusal."""
    gee, calls = _gee(lambda obj: _coverage_raw("cplx", 1.0))
    out = asyncio.run(sm.validate_custom(gee, _p(), "AOI", **_kw(
        name="cplx", origin="file", ref=str(_tif(tmp_path / "c.tif", dtype="complex_int16")), asset_type="FILE",
        alert_band=1, date_band=2)))
    assert out == {"error": "steps.alerts.err_not_numeric"} and calls == []


def test_an_unreadable_file_is_refused_with_a_message_key(tmp_path, requests):
    (tmp_path / "x.tif").write_text("not a tiff")
    gee, calls = _gee(lambda obj: {})
    out = asyncio.run(sm.validate_custom(gee, _p(), "AOI", **_kw(
        name="x", origin="file", ref=str(tmp_path / "x.tif"), asset_type="FILE", alert_band=1, date_band=2)))
    assert out == {"error": "steps.alerts.err_unreadable"} and calls == []


def test_a_geotiff_mapped_by_band_name_is_refused(tmp_path, requests):
    """Switching from an Earth Engine asset to a file can leave names mapped:
    a GeoTIFF is mapped by its 1-based band indexes."""
    gee, calls = _gee(lambda obj: {})
    out = asyncio.run(sm.validate_custom(gee, _p(), "AOI", **_kw(
        name="tif", origin="file", ref=str(_tif(tmp_path / "n.tif")), asset_type="FILE")))
    assert out == {"error": "steps.alerts.err_band_map"} and calls == []


def test_a_missing_band_map_or_reference_is_refused_before_any_request(requests):
    gee, calls = _gee(lambda obj: {})
    assert asyncio.run(sm.validate_custom(gee, _p(), "AOI", **_kw(ref=None))) == {"error": "steps.alerts.err_no_ref"}
    assert asyncio.run(sm.validate_custom(gee, _p(), "AOI", **_kw(alert_band=None))) == {
        "error": "steps.alerts.err_band_map"}
    assert asyncio.run(sm.validate_custom(gee, _p(), "AOI", **_kw(fmt=None))) == {"error": "steps.alerts.err_band_map"}
    assert asyncio.run(sm.validate_custom(gee, _p(), "AOI", **_kw(asset_type="TABLE"))) == {
        "error": "steps.alerts.err_wrong_type"}
    assert calls == []


def test_an_edit_keeps_its_own_name(requests):
    p = _p()
    p.sources = {"forest radd": CustomSource(name="forest radd", origin="gee", ref="users/x/radd", format="radd",
                                             native=NativeGrid(crs="EPSG:4326", scale=10.0), digit=3)}
    gee, _ = _gee(lambda obj: {"discovery": _discovery(), "coverage": _coverage_raw("forest radd", 1.0)})
    assert asyncio.run(sm.validate_custom(gee, p, "AOI", **_kw()))["error"] == "steps.alerts.name_duplicate"
    out = asyncio.run(sm.validate_custom(gee, p, "AOI", **_kw(), editing="forest radd"))
    assert out["fields"]["name"] == "forest radd"


# ---------------------------------------------------------------- the modal, rendered
@solara.component
def _StubRasterInput(gee_interface, origin, on_origin, asset, path, asset_types=("IMAGE", "IMAGE_COLLECTION"),
                     disabled=False):
    """Stands in for pysepal's asset and file pickers (they list the user's
    assets and folders): the test sets the ``path`` reactive itself."""
    _StubRasterInput.seen = SimpleNamespace(origin=origin, on_origin=on_origin, asset=asset, path=path)


def _state(p):
    state = AppState()
    state.project.value = p
    return state


def _modal(state, open_, added, saved, editing=None):
    return sm.SourceModal(open_=open_, app_state=state, gee_interface=object(),
                          on_add_predefined=added.append,
                          on_add_custom=lambda fields, cov: added.append((fields, cov)),
                          on_save_custom=lambda name, changes, cov: saved.append((name, changes, cov)),
                          editing=editing)


async def _settle(cond, rounds=300):
    for _ in range(rounds):
        await asyncio.sleep(0.01)
        if cond():
            return True
    return False


def _go_custom(rc, name):
    toggle = rc.find(v.BtnToggle).widgets[0]
    toggle.v_model = "custom"
    field = rc.find(v.TextField, label=t("steps.alerts.name_label")).widgets[0]
    field.v_model = name


def _submit_btn(rc, label=None):
    """The TaskButtonComponent's button, found by its label."""
    label = label or t("steps.alerts.add")
    [btn] = [b for b in rc.find(v.Btn).widgets if label in b.children]
    return btn


def test_a_custom_geotiff_is_validated_added_inside_the_task_and_the_modal_closes(tmp_path, monkeypatch):
    path = _tif(tmp_path / "radd.tif", descriptions=("Alert", "Date"))
    asked = []

    async def fake_validate(gee_interface, project, aoi_fc, **kw):
        asked.append(kw)
        return {"fields": {"name": kw["name"]}, "coverage": _cov(1.0)}

    monkeypatch.setattr(sm, "RasterInput", _StubRasterInput)
    monkeypatch.setattr(sm, "validate_custom", fake_validate)
    monkeypatch.setattr(sm.aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    state, open_, added, saved = _state(_p()), solara.reactive(True), [], []

    async def main():
        box, rc = reacton.render(_modal(state, open_, added, saved), handle_error=False)
        try:
            _go_custom(rc, "my radd")
            _StubRasterInput.seen.on_origin("file")
            _StubRasterInput.seen.path.set(str(path))
            # the bands are read and the RADD preset suggested, by band index
            assert await _settle(lambda: rc.find(v.Select, label=t("steps.alerts.band_date")).widgets
                                 and rc.find(v.Select, label=t("steps.alerts.band_date")).widgets[0].v_model == 2)
            assert rc.find(v.Select, label=t("steps.alerts.band_alert")).widgets[0].v_model == 1
            _submit_btn(rc).fire_event("click", None)
            assert await _settle(lambda: open_.value is False)
            assert len(asked) == 1 and len(added) == 1 and saved == []
            kw = asked[0]
            assert (kw["origin"], kw["ref"], kw["asset_type"], kw["fmt"]) == ("file", str(path), "FILE", "radd")
            assert (kw["alert_band"], kw["date_band"], kw["editing"]) == (1, 2, None)
            assert added[0][0] == {"name": "my radd"}
        finally:
            rc.close()

    asyncio.run(main())


def test_a_second_click_while_checking_stops_the_check_and_rearms_the_add(monkeypatch):
    """Spec §11: while the check runs the button reads Cancel and stops it;
    the one-Add guard is re-armed, so the next click checks again."""
    asked, release = [], asyncio.Event()

    async def fake_validate(gee_interface, project, aoi_fc, **kw):
        asked.append(kw)
        if len(asked) == 1:
            await release.wait()  # the first check hangs until cancelled
        return {"fields": {"name": kw["name"]}, "coverage": _cov(1.0)}

    monkeypatch.setattr(sm, "RasterInput", _StubRasterInput)
    monkeypatch.setattr(sm, "validate_custom", fake_validate)
    monkeypatch.setattr(sm.aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    state, open_, added, saved = _state(_p()), solara.reactive(True), [], []

    async def main():
        box, rc = reacton.render(_modal(state, open_, added, saved), handle_error=False)
        try:
            _go_custom(rc, "slow")
            await asyncio.sleep(0.05)
            _submit_btn(rc).fire_event("click", None)
            assert await _settle(lambda: len(asked) == 1)
            # the same button, now Cancel with a spinner (the dialog's own Cancel has none)
            [stop] = [b for b in rc.find(v.Btn).widgets
                      if any(isinstance(c, v.ProgressCircular) for c in b.children)]
            assert t("common.cancel") in stop.children
            stop.fire_event("click", None)
            assert await _settle(lambda: [b for b in rc.find(v.Btn).widgets if t("steps.alerts.add") in b.children])
            assert added == [] and open_.value is True
            _submit_btn(rc).fire_event("click", None)
            assert await _settle(lambda: open_.value is False)
            assert len(asked) == 2 and [a[0] for a in added] == [{"name": "slow"}]
        finally:
            rc.close()

    asyncio.run(main())


def test_a_refused_source_shows_its_error_inline_and_can_be_sent_again(tmp_path, monkeypatch):
    asked = []

    async def fake_validate(gee_interface, project, aoi_fc, **kw):
        asked.append(kw)
        return {"error": "steps.alerts.err_not_covered"}

    monkeypatch.setattr(sm, "RasterInput", _StubRasterInput)
    monkeypatch.setattr(sm, "validate_custom", fake_validate)
    monkeypatch.setattr(sm.aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    state, open_, added, saved = _state(_p()), solara.reactive(True), [], []

    def _shown():
        return [w for w in rc.find(v.Html).widgets if t("steps.alerts.err_not_covered") in str(w.children)]

    async def main():
        nonlocal rc
        box, rc = reacton.render(_modal(state, open_, added, saved), handle_error=False)
        try:
            _go_custom(rc, "elsewhere")
            await asyncio.sleep(0.05)
            _submit_btn(rc).fire_event("click", None)
            assert await _settle(lambda: bool(_shown()))
            assert open_.value is True and added == []
            _submit_btn(rc).fire_event("click", None)  # re-armed
            assert await _settle(lambda: len(asked) == 2)
        finally:
            rc.close()

    rc = None
    asyncio.run(main())


def test_an_edit_saves_through_on_save_custom(tmp_path, monkeypatch):
    path = _tif(tmp_path / "e.tif")
    p = _p()
    p.sources = {"mine": CustomSource(name="mine", origin="file", ref=str(path), asset_type="FILE", format="ccdc",
                                      date_encoding="decimal_year", band_map=BandMap(alert=2, date=1),
                                      native=NativeGrid(crs="EPSG:4326", scale=22.0, bounds=(0, 0, 1, 1)), digit=3)}
    asked = []

    async def fake_validate(gee_interface, project, aoi_fc, **kw):
        asked.append(kw)
        return {"fields": {"name": "mine"}, "coverage": _cov(1.0)}

    monkeypatch.setattr(sm, "RasterInput", _StubRasterInput)
    monkeypatch.setattr(sm, "validate_custom", fake_validate)
    monkeypatch.setattr(sm.aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    state, open_, added, saved = _state(p), solara.reactive(True), [], []

    async def main():
        box, rc = reacton.render(_modal(state, open_, added, saved, editing="mine"), handle_error=False)
        try:
            assert not rc.find(v.BtnToggle).widgets  # an edit is always custom
            await asyncio.sleep(0.1)  # the bands load; the edit keeps its own mapping
            _submit_btn(rc, t("steps.alerts.save")).fire_event("click", None)
            assert await _settle(lambda: open_.value is False)
            assert [s[0] for s in saved] == ["mine"] and added == []
            assert (asked[0]["alert_band"], asked[0]["date_band"], asked[0]["fmt"]) == (2, 1, "ccdc")
            assert asked[0]["editing"] == "mine" and asked[0]["ref"] == str(path)
        finally:
            rc.close()

    asyncio.run(main())


def test_the_open_form_survives_a_worker_publish(tmp_path, monkeypatch):
    """Global Constraints: a worker's publish re-renders the open form in the
    worker's thread, where no task may (re)start."""
    monkeypatch.setattr(sm, "RasterInput", _StubRasterInput)
    state, open_, added, saved = _state(_p()), solara.reactive(True), [], []
    errors = []

    async def main():
        box, rc = reacton.render(_modal(state, open_, added, saved), handle_error=False)
        try:
            _go_custom(rc, "x")
            _StubRasterInput.seen.on_origin("file")
            _StubRasterInput.seen.path.set(str(_tif(tmp_path / "w.tif")))
            await asyncio.sleep(0.1)

            def worker():
                try:
                    state.update_project("m", lambda live: setattr(live, "coverage", {"RADD": _cov(1.0)}))
                    state.coverage_pending.set(True)
                except Exception as exc:  # noqa: BLE001 - reported below
                    errors.append(exc)

            th = threading.Thread(target=worker)
            th.start()
            th.join()
            assert errors == []
            assert rc.find(v.TextField, label=t("steps.alerts.name_label")).widgets[0].v_model == "x"
        finally:
            rc.close()

    asyncio.run(main())
