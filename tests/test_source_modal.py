import asyncio
import time
import threading
from datetime import date, datetime
from types import SimpleNamespace

import ipyvuetify as v
import numpy as np
import pytest
import rasterio
import reacton
import reacton.ipyvuetify as rv
import solara
from affine import Affine

from deforestation_alerts.project.models import (
    AoiRecord, BandMap, CustomSource, NativeGrid, Project, RegionCoverage, SourceCoverage, add_predefined_source,
)
from gui.i18n import t
from gui.store.state_manager import AppState
from gui.widget import source_modal as sm
from gui.widget.enter_key import ENTER_DEFAULT


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
    assets and folders): the test sets the ``asset`` or ``path`` reactive
    itself. It draws one empty marker (``dv-stub-raster-input``), so its place
    among the form's fields can be asserted."""
    _StubRasterInput.seen = SimpleNamespace(origin=origin, on_origin=on_origin, asset=asset, path=path)
    rv.Html(tag="div", class_="dv-stub-raster-input")


def _state(p):
    """An open project whose AOI geometry is its ``aoi.geojson`` sidecar
    (remote_facts.aoi_origin: "sidecar"); ``aoi_io.ee_aoi`` is stubbed."""
    p.aoi = p.aoi.model_copy(update={"geometry_file": "aoi.geojson"})
    state = AppState()
    state.project.value = p
    return state


def _modal(state, open_, added, saved, editing=None, gee=None):
    return sm.SourceModal(open_=open_, app_state=state, gee_interface=gee or object(),
                          on_add_predefined=added.append,
                          on_add_custom=lambda fields, cov: added.append((fields, cov)),
                          on_save_custom=lambda name, changes, cov: saved.append((name, changes, cov)),
                          editing=editing)


async def _settle(cond, timeout=10.0):
    """Poll ``cond`` until it holds or a wall-clock deadline passes (a count of
    sleeps gives up early on a loaded machine)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(0.01)
        if cond():
            return True
    return False


def _bands_listed(rc, label):
    """The band list has loaded: the band select offers items."""
    return any(w.items for w in rc.find(v.Select, label=label).widgets)


def _go_custom(rc, name):
    rc.find(v.Select, label=t("steps.alerts.mode_label")).widget.v_model = "custom"
    field = rc.find(v.TextField, label=t("steps.alerts.name_label")).widgets[0]
    field.v_model = name


def _source(rc):
    """The Source select above the picker."""
    return rc.find(v.Select, label=t("steps.raster_input.field_source")).widgets[0]


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
            _source(rc).v_model = "file"
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
            assert not rc.find(v.Select, label=t("steps.alerts.mode_label")).widgets  # an edit is always custom
            # the bands load; the edit keeps its own mapping
            assert await _settle(lambda: _bands_listed(rc, t("steps.alerts.band_alert")))
            assert _icons(_submit_btn(rc, t("steps.alerts.save"))) == ["mdi-content-save-outline"]
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
            _source(rc).v_model = "file"
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


# ---------------------------------------------------------------- fix round 1
def _hanging(monkeypatch):
    """A validator that hangs until released, recording its calls and
    whether it ran to the end."""
    asked, done, release = [], [], asyncio.Event()

    async def fake_validate(gee_interface, project, aoi_fc, **kw):
        asked.append(kw)
        await release.wait()
        done.append(kw)
        return {"fields": {"name": kw["name"]}, "coverage": _cov(1.0)}

    monkeypatch.setattr(sm, "RasterInput", _StubRasterInput)
    monkeypatch.setattr(sm, "validate_custom", fake_validate)
    monkeypatch.setattr(sm.aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    return asked, done, release


def _dialog_cancel(rc):
    """The dialog's own Cancel (the TaskButtonComponent's Cancel has a spinner)."""
    [btn] = [b for b in rc.find(v.Btn).widgets if t("common.cancel") in b.children
             and not any(isinstance(c, v.ProgressCircular) for c in b.children)]
    return btn


@pytest.mark.parametrize("close", ["esc", "cancel"])
def test_closing_the_dialog_during_a_check_adds_nothing(monkeypatch, close):
    """use_task does not cancel on unmount: closing the dialog must stop the
    check, or it adds the source the user cancelled."""
    asked, done, release = _hanging(monkeypatch)
    state, open_, added, saved = _state(_p()), solara.reactive(True), [], []

    async def main():
        box, rc = reacton.render(_modal(state, open_, added, saved), handle_error=False)
        try:
            _go_custom(rc, "late")
            await asyncio.sleep(0.05)
            _submit_btn(rc).fire_event("click", None)
            assert await _settle(lambda: len(asked) == 1)
            # the fields are frozen while their values are checked
            assert rc.find(v.TextField, label=t("steps.alerts.name_label")).widgets[0].disabled is True
            assert _source(rc).disabled is True
            if close == "esc":
                rc.find(v.Dialog).widgets[0].fire_event("keydown.esc", None)
            else:
                _dialog_cancel(rc).fire_event("click", None)
            assert open_.value is False
            release.set()
            await asyncio.sleep(0.1)
            assert added == [] and saved == [] and done == []  # cancelled at its await
        finally:
            rc.close()

    asyncio.run(main())


def test_a_check_that_ends_after_the_dialog_closed_adds_nothing(monkeypatch):
    """The second guard: a check the unmount could not cancel (it got past
    its last await) does not call back once the dialog is closed."""
    import solara.tasks

    asked, done, release = _hanging(monkeypatch)
    monkeypatch.setattr(solara.tasks.TaskAsyncio, "cancel", lambda self: None)  # the unmount cannot stop it
    state, open_, added, saved = _state(_p()), solara.reactive(True), [], []

    async def main():
        box, rc = reacton.render(_modal(state, open_, added, saved), handle_error=False)
        try:
            _go_custom(rc, "late")
            await asyncio.sleep(0.05)
            _submit_btn(rc).fire_event("click", None)
            assert await _settle(lambda: len(asked) == 1)
            rc.find(v.Dialog).widgets[0].fire_event("keydown.esc", None)
            release.set()
            assert await _settle(lambda: done)  # the check ran to its end...
            await asyncio.sleep(0.05)
            assert added == [] and saved == []  # ...and called nothing back
        finally:
            rc.close()

    asyncio.run(main())


def test_an_aoi_change_during_the_check_refuses_inline(monkeypatch):
    asked, done, release = _hanging(monkeypatch)
    state, open_, added, saved = _state(_p()), solara.reactive(True), [], []

    def _shown():
        return [w for w in rc.find(v.Html).widgets if t("steps.alerts.err_aoi_changed") in str(w.children)]

    async def main():
        nonlocal rc
        box, rc = reacton.render(_modal(state, open_, added, saved), handle_error=False)
        try:
            _go_custom(rc, "moved")
            await asyncio.sleep(0.05)
            _submit_btn(rc).fire_event("click", None)
            assert await _settle(lambda: len(asked) == 1)
            state.update_project("m", lambda live: setattr(
                live, "aoi", live.aoi.model_copy(update={"digest": "d2"})))
            release.set()
            assert await _settle(lambda: bool(_shown()))
            assert added == [] and open_.value is True
        finally:
            rc.close()

    rc = None
    asyncio.run(main())


def test_the_check_waits_for_this_aois_geometry(monkeypatch):
    """No sidecar and no selection of this AOI yet (remote_facts.aoi_origin
    is None): an inline error, and nothing is asked of Earth Engine."""
    asked, done, release = _hanging(monkeypatch)
    state, open_, added, saved = _state(_p()), solara.reactive(True), [], []
    state.project.value.aoi = state.project.value.aoi.model_copy(update={"geometry_file": None})

    def _shown():
        return [w for w in rc.find(v.Html).widgets if t("steps.alerts.err_aoi_not_ready") in str(w.children)]

    async def main():
        nonlocal rc
        box, rc = reacton.render(_modal(state, open_, added, saved), handle_error=False)
        try:
            _go_custom(rc, "early")
            await asyncio.sleep(0.05)
            _submit_btn(rc).fire_event("click", None)
            assert await _settle(lambda: bool(_shown()))
            assert asked == [] and added == [] and open_.value is True
        finally:
            rc.close()

    rc = None
    asyncio.run(main())


def test_the_sidecar_is_read_off_the_event_loop(monkeypatch):
    threads = []
    asked, done, release = _hanging(monkeypatch)
    release.set()
    monkeypatch.setattr(sm.aoi_io, "ee_aoi",
                        lambda result, pdir: threads.append(threading.current_thread()) or "AOI")
    state, open_, added, saved = _state(_p()), solara.reactive(True), [], []

    async def main():
        box, rc = reacton.render(_modal(state, open_, added, saved), handle_error=False)
        try:
            _go_custom(rc, "side")
            await asyncio.sleep(0.05)
            _submit_btn(rc).fire_event("click", None)
            assert await _settle(lambda: open_.value is False)
            assert len(threads) == 1 and threads[0] is not threading.main_thread()
            assert [a[0] for a in added] == [{"name": "side"}]
        finally:
            rc.close()

    asyncio.run(main())


# ---------------------------------------------------------------- the form frame
def _defaults(rc):
    """The buttons Enter would press (gui/widget/enter_key)."""
    return [b for b in rc.find(v.Btn).widgets if ENTER_DEFAULT in (b.class_ or "").split()]


def _icons(btn):
    return [c.children[0] for c in btn.children if isinstance(c, v.Icon)]


def test_the_source_form_sits_on_the_form_frame():
    state, open_, added, saved = _state(_p()), solara.reactive(True), [], []

    async def main():  # the form's band-list task runs on this loop
        _box, rc = reacton.render(_modal(state, open_, added, saved), handle_error=False)
        try:
            dialog = rc.find(v.Dialog).widget
            assert (dialog.max_width, dialog.persistent, dialog.no_click_animation, dialog.eager) == (
                "560px", True, True, True)
            mode = rc.find(v.Select, label=t("steps.alerts.mode_label")).widget
            assert mode.v_model == "predefined" and mode.outlined and mode.dense
            assert (mode.hint, mode.persistent_hint) == (t("steps.alerts.mode_hint"), True)
            assert [(i["value"], i["disabled"]) for i in mode.items if "disabled" in i] == [("custom", False)]
            predefined = rc.find(v.Select, label=t("steps.alerts.predefined_label")).widget
            assert (predefined.hint, predefined.persistent_hint) == (t("steps.alerts.predefined_hint"), True)
            add = _submit_btn(rc)
            assert _defaults(rc) == [add] and _icons(add) == ["mdi-plus"]
            dialog.fire_event("click:outside", None)
            assert "da-dialog-nudge-a" in (rc.find(v.Dialog).widget.content_class or "").split()
            assert open_.value is True  # an outside click nudges the form, it does not close it
        finally:
            rc.close()

    asyncio.run(main())


def test_enter_finds_no_default_button_while_the_check_runs(monkeypatch):
    """Review Focus 1: while the check runs, the primary button reads Cancel
    and carries no Enter class, so a second Enter cannot stop the check."""
    asked, done, release = _hanging(monkeypatch)
    state, open_, added, saved = _state(_p()), solara.reactive(True), [], []

    async def main():
        _box, rc = reacton.render(_modal(state, open_, added, saved), handle_error=False)
        try:
            _go_custom(rc, "enter")
            await asyncio.sleep(0.05)
            assert _defaults(rc) == [_submit_btn(rc)]  # idle: Add is the one default
            _submit_btn(rc).fire_event("click", None)
            assert await _settle(lambda: len(asked) == 1)
            [stop] = [b for b in rc.find(v.Btn).widgets
                      if any(isinstance(c, v.ProgressCircular) for c in b.children)]
            assert t("common.cancel") in stop.children
            assert _defaults(rc) == []  # Enter presses nothing mid-check
            release.set()
            assert await _settle(lambda: open_.value is False)
            assert len(done) == 1 and [a[0] for a in added] == [{"name": "enter"}]
        finally:
            rc.close()

    asyncio.run(main())


def test_at_the_cap_custom_is_offered_disabled_with_the_reason_as_its_hint():
    p = _p()
    p.sources = {f"c{i}": CustomSource(name=f"c{i}", origin="gee", ref=f"users/x/c{i}", format="radd",
                                       native=NativeGrid(crs="EPSG:4326", scale=10.0), digit=3 + i)
                 for i in range(sm.MAX_CUSTOM)}
    state, open_, added, saved = _state(p), solara.reactive(True), [], []

    async def main():
        _box, rc = reacton.render(_modal(state, open_, added, saved), handle_error=False)
        try:
            mode = rc.find(v.Select, label=t("steps.alerts.mode_label")).widget
            [custom] = [i for i in mode.items if i["value"] == "custom"]
            assert custom["disabled"] is True and custom["text"] == t("steps.alerts.mode_custom", n=sm.MAX_CUSTOM)
            assert (mode.hint, mode.persistent_hint) == (t("steps.alerts.cap_reached"), True)
            assert mode.v_model == "predefined"
        finally:
            rc.close()

    asyncio.run(main())


# ---------------------------------------------------------------- the custom form (spec 2026-10-23-source-form)
ASSET = {"asset_id": "users/x/a", "type": "IMAGE", "column": "ALL", "value": None}
OTHER = {"asset_id": "users/x/b", "type": "IMAGE", "column": "ALL", "value": None}
AOI_BOUNDS = (-55.5, -24.5, -55.4, -24.4)  # _p()'s


def _ee(monkeypatch, bands, dates=None, gates=None):
    """An Earth Engine stand-in. The band list of an asset is ``bands`` (a
    dict per asset id, or one list for any); the date values of ``(asset id,
    band)`` are ``dates[...]``: ``(lowest, highest)``, None for no dated pixel,
    or an exception to raise. A request waits for ``gates[asset id]`` (band
    list) or ``gates[(asset id, band)]`` (date values) when given."""
    from gui.widget import raster_input

    monkeypatch.setattr(raster_input.user_raster, "band_list_request", lambda ref, kind: ("BANDS", ref, kind))
    monkeypatch.setattr(sm.date_probes, "date_range_request",
                        lambda ref, kind, band, bounds: ("DATES", ref, kind, band, bounds))
    calls = []

    async def get_info_async(obj):
        calls.append(obj)
        key = obj[1] if obj[0] == "BANDS" else (obj[1], obj[3])
        if gates and key in gates:
            await gates[key].wait()
        if obj[0] == "BANDS":
            return list(bands[obj[1]] if isinstance(bands, dict) else bands)
        span = (dates or {})[key]
        if isinstance(span, Exception):
            raise span
        return {"min": None, "max": None} if span is None else {"min": span[0], "max": span[1]}

    return SimpleNamespace(get_info_async=get_info_async, calls=calls)


def _date_reads(gee):
    return [c for c in gee.calls if c[0] == "DATES"]


def _render_custom(monkeypatch, gee, p=None, editing=None):
    """The custom form, named "mine" (an edit opens on its own name); Add
    records what it would check and refuses."""
    monkeypatch.setattr(sm, "RasterInput", _StubRasterInput)
    asked = []

    async def fake_validate(gee_interface, project, aoi_fc, **kw):
        asked.append(kw)
        return {"error": "steps.alerts.err_not_covered"}

    monkeypatch.setattr(sm, "validate_custom", fake_validate)
    monkeypatch.setattr(sm.aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    state, open_, added, saved = _state(p or _p()), solara.reactive(True), [], []
    _box, rc = reacton.render(_modal(state, open_, added, saved, editing=editing, gee=gee), handle_error=False)
    if editing is None:
        _go_custom(rc, "mine")
    return rc, SimpleNamespace(asked=asked, state=state, open_=open_)


def _select(rc, key):
    """The select labelled ``t(key)``, or None while it is not shown."""
    found = rc.find(v.Select, label=t(key)).widgets
    return found[0] if found else None


def _fmt(rc):
    return _select(rc, "steps.alerts.format_label")


def _alert(rc):
    return _select(rc, "steps.alerts.band_alert")


def _date(rc):
    return _select(rc, "steps.alerts.band_date")


def _enc(rc):
    return _select(rc, "steps.alerts.encoding_label")


def _texts(rc):
    return " ".join(str(w.children) for w in rc.find(v.Html).widgets)


def _item_texts(select):
    return {i["value"]: i["text"] for i in select.items}


def _suggested(text):
    return t("steps.layers.suggested", text=text)


def _enc_text(e):
    return t(f"steps.alerts.encoding_{e}")


def _nothing_below_the_picker(rc):
    return _fmt(rc) is None and _alert(rc) is None and _date(rc) is None and _enc(rc) is None


async def _submit_and_read(rc, seen):
    _submit_btn(rc).fire_event("click", None)
    assert await _settle(lambda: bool(seen.asked))
    return seen.asked[-1]


def _dated_tif(path, layers):
    """A WGS84 GeoTIFF inside ``_p()``'s AOI, one int32 band per layer of
    values (cycled over its 10x10 pixels), no band descriptions."""
    with rasterio.open(path, "w", driver="GTiff", width=10, height=10, count=len(layers), dtype="int32",
                       crs="EPSG:4326", transform=Affine(0.0002, 0, -55.5, 0, -0.0002, -24.4)) as dst:
        for i, values in enumerate(layers, start=1):
            dst.write(np.resize(np.asarray(values, "int32"), (10, 10)), i)
    return path


def test_number_text_reads_whole_values_as_integers_and_others_with_two_decimals():
    assert sm.number_text(25003.0) == "25003"
    assert sm.number_text(2025032) == "2025032"
    assert sm.number_text(2025.0234) == "2025.02"
    assert sm.number_text(2025.816) == "2025.82"


def test_custom_form_order_name_source_picker(monkeypatch):
    from gui.widget.form_dialog import FIELDS

    async def main():
        rc, _ = _render_custom(monkeypatch, _ee(monkeypatch, ["Alert", "Date"]))
        try:
            [fields] = rc.find(v.Html, style_=FIELDS).widgets
            name = rc.find(v.TextField, label=t("steps.alerts.name_label")).widgets[0]
            [picker] = rc.find(v.Html, class_="dv-stub-raster-input").widgets
            order = fields.children
            assert order.index(name) < order.index(_source(rc)) < order.index(picker)
            assert [i["value"] for i in _source(rc).items] == list(sm.SOURCE_ORIGINS) == ["gee", "file"]
            assert _nothing_below_the_picker(rc)  # no raster: nothing, not even the Alert format
            assert t("steps.raster_input.bands_loading") not in _texts(rc)
        finally:
            rc.close()

    asyncio.run(main())


def test_nothing_below_the_picker_until_the_bands_are_read(monkeypatch):
    release = asyncio.Event()

    async def main():
        gee = _ee(monkeypatch, ["Alert", "Date"], gates={"users/x/a": release})
        rc, seen = _render_custom(monkeypatch, gee)
        try:
            _StubRasterInput.seen.asset.set({**ASSET, "type": "IMAGE_COLLECTION"})
            loading = t("steps.raster_input.bands_loading")
            assert await _settle(lambda: loading in _texts(rc))
            assert _nothing_below_the_picker(rc)
            assert t("steps.alerts.collection_help") in _texts(rc)  # the note does not wait for the bands
            assert _submit_btn(rc).disabled is True  # Save waits for the bands
            release.set()
            assert await _settle(lambda: _fmt(rc) is not None)
            assert loading not in _texts(rc) and _submit_btn(rc).disabled is False
        finally:
            release.set()
            rc.close()

    asyncio.run(main())


def test_a_failed_band_read_shows_its_error_line_only(monkeypatch):
    async def main():
        gee = _ee(monkeypatch, {})  # no band list for any asset: a KeyError
        rc, _ = _render_custom(monkeypatch, gee)
        try:
            _StubRasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: bool(rc.find(v.Html, class_="error--text").widgets))
            assert _nothing_below_the_picker(rc) and _submit_btn(rc).disabled is True
        finally:
            rc.close()

    asyncio.run(main())


def test_one_band_is_a_line_then_the_encoding_read_from_its_values(monkeypatch):
    async def main():
        gee = _ee(monkeypatch, ["alert_date"], dates={("users/x/a", "alert_date"): (25003, 25296)})
        rc, seen = _render_custom(monkeypatch, gee)
        try:
            _StubRasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: _enc(rc) is not None and _enc(rc).v_model == "yyddd")
            assert t("steps.alerts.band_one", name="alert_date") in _texts(rc)
            assert _fmt(rc) is None and _alert(rc) is None and _date(rc) is None
            enc = _enc(rc)
            assert _item_texts(enc)["yyddd"] == _suggested(_enc_text("yyddd"))
            assert _item_texts(enc)["yymm"] == _enc_text("yymm")
            assert (enc.hint, enc.persistent_hint) == (
                t("steps.alerts.dates_read", lo="25003", hi="25296", encoding="yyddd"), True)
            # the values are read over the AOI's bounds
            assert _date_reads(gee) == [("DATES", "users/x/a", "IMAGE", "alert_date", AOI_BOUNDS)]
            kw = await _submit_and_read(rc, seen)
            assert (kw["fmt"], kw["alert_band"], kw["date_band"], kw["encoding"]) == (
                "date_only", None, "alert_date", "yyddd")
        finally:
            rc.close()

    asyncio.run(main())


def test_radd_band_names_suggest_radd_and_its_bands(monkeypatch):
    async def main():
        rc, seen = _render_custom(monkeypatch, _ee(monkeypatch, ["Alert", "Date", "extra"]))
        try:
            _StubRasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: _fmt(rc) is not None and _fmt(rc).v_model == "radd")
            fmt = _fmt(rc)
            assert _item_texts(fmt) == {
                "radd": _suggested(t("steps.alerts.format_radd")), "ccdc": t("steps.alerts.format_ccdc"),
                "pyeo": t("steps.alerts.format_pyeo"), "date_only": t("steps.alerts.format_date_only")}
            assert (fmt.hint, fmt.persistent_hint, fmt.dense, fmt.outlined) == (
                t("steps.alerts.format_help_radd"), True, True, True)
            assert _alert(rc).v_model == "Alert" and _date(rc).v_model == "Date"
            assert _item_texts(_alert(rc)) == {"Alert": _suggested("Alert"), "Date": "Date", "extra": "extra"}
            assert _item_texts(_date(rc))["Date"] == _suggested("Date")
            assert _date(rc).hint == t("steps.alerts.date_band_suggested", count=3, name="Date")
            assert _enc(rc) is None  # a preset's encoding is its own
            kw = await _submit_and_read(rc, seen)
            assert (kw["fmt"], kw["alert_band"], kw["date_band"]) == ("radd", "Alert", "Date")
        finally:
            rc.close()

    asyncio.run(main())


def test_ccdc_with_one_preset_band_fills_that_one_and_leaves_the_other_empty(monkeypatch):
    async def main():
        rc, _ = _render_custom(monkeypatch, _ee(monkeypatch, ["first_detection_date", "magnitude"],
                                                dates={("users/x/a", "first_detection_date"): (2025.1, 2025.5)}))
        try:
            _StubRasterInput.seen.asset.set(ASSET)
            # not every CCDC band is there: Date only, on the band that says "date"
            assert await _settle(lambda: _fmt(rc) is not None and _fmt(rc).v_model == "date_only")
            _fmt(rc).v_model = "ccdc"
            assert await _settle(lambda: _alert(rc) is not None)
            assert _date(rc).v_model == "first_detection_date"
            assert _item_texts(_date(rc))["first_detection_date"] == _suggested("first_detection_date")
            alert = _alert(rc)
            assert alert.v_model is None
            assert (alert.hint, alert.persistent_hint) == (t("steps.alerts.alert_band_pick"), True)
            assert _enc(rc) is None  # a preset's encoding is its own
            alert.v_model = "magnitude"
            assert _alert(rc).hint == ""  # picked: no hint
        finally:
            rc.close()

    asyncio.run(main())


def test_date_only_suggests_the_one_date_band_and_the_encoding_from_its_values(monkeypatch):
    async def main():
        gee = _ee(monkeypatch, ["magnitude", "change_date", "confidence"],
                  dates={("users/x/a", "change_date"): (2025.0234, 2025.816),
                         ("users/x/a", "confidence"): (1, 87)})
        rc, seen = _render_custom(monkeypatch, gee)
        try:
            _StubRasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: _enc(rc) is not None and _enc(rc).v_model == "decimal_year")
            assert _fmt(rc).v_model == "date_only"
            assert _item_texts(_fmt(rc))["date_only"] == _suggested(t("steps.alerts.format_date_only"))
            assert _alert(rc) is None
            date = _date(rc)
            assert date.v_model == "change_date" and _item_texts(date)["change_date"] == _suggested("change_date")
            assert (date.hint, date.persistent_hint) == (
                t("steps.alerts.date_band_suggested", count=3, name="change_date"), True)
            assert _enc(rc).hint == t("steps.alerts.dates_read", lo="2025.02", hi="2025.82",
                                      encoding="decimal year")
            assert _submit_btn(rc).disabled is False
            # another band: its values do not read as dates
            _date(rc).v_model = "confidence"
            assert await _settle(lambda: _enc(rc) is not None and _enc(rc).hint == t(
                "steps.alerts.dates_odd", lo="1", hi="87"))
            assert _enc(rc).v_model is None
            assert _item_texts(_enc(rc)) == {e: _enc_text(e) for e in sm.ENCODINGS}  # nothing suggested
            assert _date(rc).hint == t("steps.alerts.date_band_help")
            assert _submit_btn(rc).disabled is True  # no encoding
            _enc(rc).v_model = "yymm"
            assert _submit_btn(rc).disabled is False
            kw = await _submit_and_read(rc, seen)
            assert (kw["fmt"], kw["date_band"], kw["encoding"]) == ("date_only", "confidence", "yymm")
        finally:
            rc.close()

    asyncio.run(main())


def test_no_dated_pixel_over_the_aoi_asks_for_the_encoding(monkeypatch):
    async def main():
        gee = _ee(monkeypatch, ["alert_date"], dates={("users/x/a", "alert_date"): None})
        rc, _ = _render_custom(monkeypatch, gee)
        try:
            _StubRasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: _enc(rc) is not None and _enc(rc).hint == t("steps.alerts.dates_none"))
            assert _enc(rc).v_model is None and _submit_btn(rc).disabled is True
        finally:
            rc.close()

    asyncio.run(main())


def test_a_failed_date_read_is_a_line_and_the_user_picks(monkeypatch):
    async def main():
        gee = _ee(monkeypatch, ["alert_date"], dates={("users/x/a", "alert_date"): RuntimeError("boom")})
        rc, _ = _render_custom(monkeypatch, gee)
        try:
            _StubRasterInput.seen.asset.set(ASSET)
            shown = t("steps.alerts.dates_failed", error="boom")
            assert await _settle(lambda: bool(rc.find(v.Html, class_="error--text", children=[shown]).widgets))
            enc = _enc(rc)
            assert enc is not None and enc.v_model is None
            assert enc.hint == t("steps.alerts.encoding_help")
            assert _submit_btn(rc).disabled is True
            enc.v_model = "yymm"
            assert _submit_btn(rc).disabled is False
        finally:
            rc.close()

    asyncio.run(main())


def test_reading_the_dates_shows_its_line_and_holds_save(monkeypatch):
    release = asyncio.Event()

    async def main():
        gee = _ee(monkeypatch, ["alert_date"], dates={("users/x/a", "alert_date"): (2501, 2510)},
                  gates={("users/x/a", "alert_date"): release})
        rc, _ = _render_custom(monkeypatch, gee)
        try:
            _StubRasterInput.seen.asset.set(ASSET)
            reading = t("steps.alerts.dates_loading")
            assert await _settle(lambda: reading in _texts(rc))
            assert _enc(rc) is None and _submit_btn(rc).disabled is True
            release.set()
            assert await _settle(lambda: _enc(rc) is not None and _enc(rc).v_model == "yymm")
            assert reading not in _texts(rc) and _submit_btn(rc).disabled is False
            assert _enc(rc).hint == t("steps.alerts.dates_read", lo="2501", hi="2510", encoding="yymm")
        finally:
            release.set()
            rc.close()

    asyncio.run(main())


def test_a_new_date_band_reads_again_and_a_stale_read_is_dropped(monkeypatch):
    slow = asyncio.Event()

    async def main():
        gee = _ee(monkeypatch, ["alert_date", "other"],
                  dates={("users/x/a", "alert_date"): (25003, 25296), ("users/x/a", "other"): (2501, 2510)},
                  gates={("users/x/a", "alert_date"): slow})
        rc, seen = _render_custom(monkeypatch, gee)
        try:
            _StubRasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: t("steps.alerts.dates_loading") in _texts(rc))
            _date(rc).v_model = "other"  # while the first band's values are read
            assert await _settle(lambda: _enc(rc) is not None and _enc(rc).v_model == "yymm")
            slow.set()  # the first read ends late: it does not land on the new band
            await asyncio.sleep(0.1)
            assert _enc(rc).v_model == "yymm"
            assert _enc(rc).hint == t("steps.alerts.dates_read", lo="2501", hi="2510", encoding="yymm")
            assert ("users/x/a", "other") in [(c[1], c[3]) for c in _date_reads(gee)]
        finally:
            slow.set()
            rc.close()

    asyncio.run(main())


def test_a_late_read_does_not_land_on_a_newer_raster(monkeypatch):
    slow = asyncio.Event()

    async def main():
        gee = _ee(monkeypatch, {"users/x/a": ["alert_date"], "users/x/b": ["alert_date"]},
                  dates={("users/x/a", "alert_date"): (25003, 25296), ("users/x/b", "alert_date"): (2501, 2510)},
                  gates={("users/x/a", "alert_date"): slow})
        rc, _ = _render_custom(monkeypatch, gee)
        try:
            _StubRasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: t("steps.alerts.dates_loading") in _texts(rc))
            _StubRasterInput.seen.asset.set(OTHER)
            assert await _settle(lambda: _enc(rc) is not None and _enc(rc).v_model == "yymm")
            slow.set()
            await asyncio.sleep(0.1)
            assert _enc(rc).v_model == "yymm"
        finally:
            slow.set()
            rc.close()

    asyncio.run(main())


def test_a_geotiff_without_band_names_leaves_the_date_band_to_the_user(tmp_path, monkeypatch):
    path = _dated_tif(tmp_path / "stack.tif", [[0, 87], [2025032, 2025301], [1, 3]])

    async def main():
        rc, seen = _render_custom(monkeypatch, _ee(monkeypatch, []))
        try:
            _source(rc).v_model = "file"
            _StubRasterInput.seen.path.set(str(path))
            assert await _settle(lambda: _date(rc) is not None)
            assert _fmt(rc).v_model == "date_only"
            date = _date(rc)
            assert date.v_model is None  # never the first band
            assert [(i["text"], i["value"]) for i in date.items] == [("Band 1", 1), ("Band 2", 2), ("Band 3", 3)]
            assert (date.hint, date.persistent_hint) == (t("steps.alerts.date_band_pick"), True)
            assert _enc(rc) is None and _submit_btn(rc).disabled is True
            date.v_model = 2  # the GeoTIFF's values are read too
            assert await _settle(lambda: _enc(rc) is not None and _enc(rc).v_model == "yyyyddd")
            assert _enc(rc).hint == t("steps.alerts.dates_read", lo="2025032", hi="2025301", encoding="yyyyddd")
            assert _submit_btn(rc).disabled is False
            kw = await _submit_and_read(rc, seen)
            assert (kw["origin"], kw["fmt"], kw["date_band"], kw["encoding"]) == ("file", "date_only", 2, "yyyyddd")
        finally:
            rc.close()

    asyncio.run(main())


def test_a_source_change_clears_the_raster_and_its_bands(monkeypatch):
    async def main():
        rc, seen = _render_custom(monkeypatch, _ee(monkeypatch, ["Alert", "Date"]))
        try:
            _StubRasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: _fmt(rc) is not None and _fmt(rc).v_model == "radd")
            _source(rc).v_model = "file"
            assert _StubRasterInput.seen.origin == "file"
            assert _StubRasterInput.seen.asset.value is None and _StubRasterInput.seen.path.value == ""
            assert _nothing_below_the_picker(rc)
            _source(rc).v_model = "gee"  # the asset is not picked again
            await asyncio.sleep(0.05)
            assert _StubRasterInput.seen.asset.value is None and _nothing_below_the_picker(rc)
            kw = await _submit_and_read(rc, seen)
            assert (kw["ref"], kw["fmt"], kw["alert_band"], kw["date_band"], kw["encoding"]) == (
                None, None, None, None, None)
        finally:
            rc.close()

    asyncio.run(main())


def test_switching_the_format_away_and_back_reapplies_its_defaults(monkeypatch):
    async def main():
        gee = _ee(monkeypatch, ["Alert", "Date", "extra"], dates={("users/x/a", "Date"): (25003, 25296)})
        rc, _ = _render_custom(monkeypatch, gee)
        try:
            _StubRasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: _alert(rc) is not None and _alert(rc).v_model == "Alert")
            _alert(rc).v_model = "extra"
            _date(rc).v_model = "extra"
            _fmt(rc).v_model = "date_only"
            # Date only's defaults: the band that says "date", the encoding from its values
            assert await _settle(lambda: _enc(rc) is not None and _enc(rc).v_model == "yyddd")
            assert _alert(rc) is None and _date(rc).v_model == "Date"
            _fmt(rc).v_model = "radd"
            assert await _settle(lambda: _alert(rc) is not None)
            assert (_alert(rc).v_model, _date(rc).v_model) == ("Alert", "Date") and _enc(rc) is None
        finally:
            rc.close()

    asyncio.run(main())


def _edited(fmt="date_only", encoding="yymm", alert=None, date="d"):
    p = _p()
    p.sources = {"mine": CustomSource(name="mine", origin="gee", ref="users/x/a", asset_type="IMAGE", format=fmt,
                                      date_encoding=encoding, band_map=BandMap(alert=alert, date=date),
                                      native=NativeGrid(crs="EPSG:4326", scale=10.0), digit=3)}
    return p


def test_an_edit_opens_on_its_saved_values_and_reads_only_on_a_new_date_band(monkeypatch):
    async def main():
        gee = _ee(monkeypatch, ["d", "other_date"],
                  dates={("users/x/a", "d"): (25003, 25296), ("users/x/a", "other_date"): (2025032, 2025301)})
        rc, seen = _render_custom(monkeypatch, gee, p=_edited(), editing="mine")
        try:
            assert await _settle(lambda: _date(rc) is not None and _enc(rc) is not None)
            await asyncio.sleep(0.1)
            assert (_fmt(rc).v_model, _date(rc).v_model, _enc(rc).v_model) == ("date_only", "d", "yymm")
            assert _enc(rc).hint == t("steps.alerts.encoding_help")
            assert _item_texts(_enc(rc)) == {e: _enc_text(e) for e in sm.ENCODINGS}  # nothing suggested
            assert _date_reads(gee) == []  # no read of the saved band
            assert _submit_btn(rc, t("steps.alerts.save")).disabled is False
            _date(rc).v_model = "other_date"
            assert await _settle(lambda: _enc(rc) is not None and _enc(rc).v_model == "yyyyddd")
            assert _item_texts(_enc(rc))["yyyyddd"] == _suggested(_enc_text("yyyyddd"))
        finally:
            rc.close()

    asyncio.run(main())


def test_the_raster_block_with_no_raster_draws_nothing_and_reads_nothing(monkeypatch):
    """The seam the points/polygons branch uses: its own origin passes no
    raster, and the raster block stays empty and idle."""
    gee = _ee(monkeypatch, ["Alert", "Date"], dates={})
    state = _state(_p())
    open_ = solara.reactive(True)

    @solara.component
    def Host():
        raster = sm.use_raster_fields(gee, state, open_, None, "gee", None, None)
        with rv.Html(tag="div", class_="dv-raster-block"):
            raster.draw(False)
        Host.raster = raster

    async def main():
        _box, rc = reacton.render(Host(), handle_error=False)
        try:
            await asyncio.sleep(0.1)
            [block] = rc.find(v.Html, class_="dv-raster-block").widgets
            assert block.children == []
            assert gee.calls == [] and Host.raster.save_blocked is False
        finally:
            rc.close()

    asyncio.run(main())
