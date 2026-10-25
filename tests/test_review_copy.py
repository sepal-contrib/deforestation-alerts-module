"""Review-only mode (spec §6.3, §6.5): a project imported from a review package
keeps its setup. Every protected action boundary refuses with the banner text
and publishes nothing; the steps disable the controls; the set reads CURRENT
from its store alone; the AOI is read from the sidecar for every AOI kind; the
banner sits above every step; Duplicate is hidden. Allowed: the
``questions_expanded`` switch (and Analysis Save, tested with the merge)."""

import ast
import asyncio
import threading
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import ipyvuetify as v
import pytest
import reacton
import solara
from pysepal.solara.notifications import NotificationProvider
from pysepal.solara.notifications.bus import get_current_bus

from deforestation_alerts.engine.fingerprint import Status, period_set_statuses, set_status
from deforestation_alerts.engine.integrate import IntegrationJob
from deforestation_alerts.engine.plan import MaskRow, PrepareRow
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import (
    AlertSet, AlertSetParams, AoiRecord, GeeVisLayer, NativeGrid, Period, Project, ReviewCopy, add_mask,
    add_predefined_source,
)
from deforestation_alerts.project.review_copy import ReadOnlyProject, read_only, refuse_if_read_only
from gui.i18n import t
from gui.scripts import prepare_jobs, remote_facts
from gui.scripts.error_text import error_text
from gui.scripts.integrate_jobs import run_integration_job
from gui.scripts.map_layers import MapLayers
from gui.scripts.setup_guard import banner_text, refuse_setup, update_setup
from gui.store.state_manager import AppState

PER = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
STORE = "periods/2026-01-01_2026-01-31/sets/glad-l/alert_db.parquet"
BANNER = "Review copy for Ana, from orig. Setup is locked."
ROOT = Path(__file__).resolve().parents[1]


def _project(name="c", *, copy=True, aoi_digest="admin:2317"):
    """A project shaped like an imported review copy: one source, period and
    set, an AOI with its sidecar, a visualization layer and a mask."""
    p = Project(name=name, created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="ADMIN1", digest=aoi_digest, bounds=(0, 0, 1, 1),
                      geometry_file=paths.AOI_SIDECAR)
    add_predefined_source(p, "GLAD-L")
    p.periods = {PER.id: PER}
    p.active_period = PER.id
    add_mask(p, name="forest", origin="gee", ref="UMD/x", asset_type="IMAGE", band="b",
             native=NativeGrid(crs="EPSG:4326", scale=30.0), mode="keep", period_id=PER.id)
    p.vis_layers = {"s2": GeeVisLayer(name="s2", asset="x", bands=["B4", "B3", "B2"], show_in_analysis=True)}
    p.alert_sets["x"] = AlertSet(id="x", period_id=PER.id, sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                                 params=AlertSetParams(), store_rel_path=STORE,
                                 combined_rel_path=STORE.replace("alert_db.parquet", "c.tif"),
                                 footprint_rel_path=STORE.replace("alert_db.parquet", "footprint.tif"),
                                 fingerprint="f", created_at=datetime.now())
    p.active_set = "x"
    if copy:
        p.review_copy = ReviewCopy(evaluator="Ana", evaluator_index=1, set_id="x", split_id="sp",
                                   set_fingerprint="f", origin_project="orig", imported_at=datetime.now())
    return p


def _state(tmp_path, monkeypatch, *, copy=True):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    s = AppState()
    s.new_project_state(_project(copy=copy))
    return s


def _warnings():
    bus = get_current_bus()
    return [x.message for x in bus.toasts.value if x.type.value == "warning"] if bus is not None else []


class _Map:
    def add_layer(self, layer, key=""):
        pass

    def remove_layer(self, key, none_ok=False):
        pass


def _recorders(monkeypatch, module, *names):
    """Swap ``module``'s presentation children for recorders of their kwargs."""
    seen = {n: [] for n in names}

    def make(n):
        @solara.component
        def _Rec(**kw):
            seen[n].append(kw)
        return _Rec

    for n in names:
        monkeypatch.setattr(module, n, make(n))
    return seen


def _action(rows, key, kind):
    row = next(r for r in rows if r["key"] == key)
    return next(a for a in row["actions"] if a["kind"] == kind)


def _button(rc, label):
    return next(w for w in rc.find(v.Btn).widgets if label in w.children)


# ------------------------------------------------------------------ the rule
def test_read_only_is_the_review_copy_field():
    assert read_only(_project()) and not read_only(_project(copy=False)) and not read_only(None)


def test_the_refusal_reads_as_the_banner():
    with pytest.raises(ReadOnlyProject) as caught:
        refuse_if_read_only(_project())
    assert error_text(caught.value) == BANNER == banner_text(_project())
    assert isinstance(caught.value, ValueError)
    refuse_if_read_only(_project(copy=False))  # a plain project passes
    refuse_if_read_only(None)


def test_update_setup_refuses_a_copy_without_publishing(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    published, warned = [], []
    s.project.subscribe(published.append)
    notifier = SimpleNamespace(warning=warned.append)
    assert update_setup(s, "c", lambda live: setattr(live, "report_template", "/x.docx"), notifier) is False
    assert published == [] and s.project.value.report_template is None and warned == [BANNER]
    assert refuse_setup(s, notifier) is True and warned == [BANNER, BANNER]


def test_update_setup_applies_to_a_plain_project(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch, copy=False)
    warned = []
    notifier = SimpleNamespace(warning=warned.append)
    assert update_setup(s, "c", lambda live: setattr(live, "report_template", "/x.docx"), notifier) is True
    assert s.project.value.report_template == "/x.docx" and warned == []
    assert refuse_setup(s, notifier) is False
    with pytest.raises(KeyError):  # every other error still propagates
        update_setup(s, "c", lambda live: {}["nope"], notifier)


# --------------------------------------------------------- status and origin
def test_a_copys_set_is_current_from_its_store_alone(tmp_path):
    p = _project()
    assert set_status(p, tmp_path, p.alert_sets["x"]) is Status.MISSING
    assert period_set_statuses(p, tmp_path, PER) == {"x": Status.MISSING}
    store = tmp_path / STORE
    store.parent.mkdir(parents=True)
    store.write_bytes(b"x")
    assert set_status(p, tmp_path, p.alert_sets["x"]) is Status.CURRENT
    assert period_set_statuses(p, tmp_path, PER) == {"x": Status.CURRENT}
    plain = _project(copy=False)  # no harmonized artifacts: a plain project reads out of date
    assert set_status(plain, tmp_path, plain.alert_sets["x"]) is Status.STALE


@pytest.mark.parametrize("digest", ["admin:2317", "asset:users/x/aoi", "d1"])
def test_a_copy_reads_its_aoi_from_the_sidecar_for_every_kind(digest):
    p = _project(aoi_digest=digest)
    picked = SimpleNamespace(admin="2317", asset_id=None, method="ADMIN1")
    assert remote_facts.aoi_origin(p, picked) == "sidecar"
    p.aoi = p.aoi.model_copy(update={"geometry_file": None})
    assert remote_facts.aoi_origin(p, picked) is None


# ------------------------------------------------------------- the AST guard
#: every module whose setup writes go through ``update_setup``; the count is
#: the ``update_project`` calls each may still make (allowed actions only)
PROTECTED = {
    "gui/tile/alerts_step.py": 0,
    "gui/tile/period_step.py": 0,
    "gui/tile/masks_card.py": 0,
    "gui/tile/layers_step.py": 0,
    "gui/tile/questionnaire_step.py": 1,  # questions_expanded (spec §6.3: allowed)
    "gui/widget/report_template.py": 0,
    "gui/widget/grid_form.py": 0,
}


@pytest.mark.parametrize("rel, allowed", sorted(PROTECTED.items()))
def test_protected_modules_write_through_update_setup(rel, allowed):
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "update_project"]
    assert len(calls) == allowed, rel
    assert any(isinstance(n, ast.Name) and n.id == "update_setup" for n in ast.walk(tree)), rel


# ------------------------------------------------------- the step boundaries
def test_alerts_step_disables_and_refuses_source_changes(tmp_path, monkeypatch):
    from gui.tile import alerts_step

    s = _state(tmp_path, monkeypatch)
    seen = _recorders(monkeypatch, alerts_step, "ProductTable", "ConfirmDialog", "SourceModal")

    @solara.component
    def Page():
        NotificationProvider()
        alerts_step.AlertsStep(app_state=s, map_=SimpleNamespace(), gee_interface=None, layers=MapLayers(_Map()))

    _box, rc = reacton.render(Page(), handle_error=False)
    try:
        assert _button(rc, t("steps.alerts.add")).disabled
        assert _action(seen["ProductTable"][-1]["rows"], "GLAD-L", "delete")["disabled"]
        _button(rc, t("steps.alerts.add")).fire_event("click", None)  # a stale click
        assert not seen["SourceModal"][-1]["open_"].value
        seen["SourceModal"][-1]["on_add_predefined"]("RADD")  # the modal's callback, as a backstop
        _action(seen["ProductTable"][-1]["rows"], "GLAD-L", "delete")["on_click"]()
        seen["ConfirmDialog"][-1]["on_confirm"]()
        assert list(s.project.value.sources) == ["GLAD-L"]
        assert BANNER in _warnings()
    finally:
        rc.close()


def test_period_step_disables_and_refuses_period_changes(tmp_path, monkeypatch):
    from gui.tile import period_step

    s = _state(tmp_path, monkeypatch)
    seen = _recorders(monkeypatch, period_step, "ProductTable", "ConfirmDialog", "PeriodModal")

    @solara.component
    def Page():
        NotificationProvider()
        period_step.PeriodStep(app_state=s, layers=MapLayers(_Map()))

    _box, rc = reacton.render(Page(), handle_error=False)
    try:
        assert _button(rc, t("steps.period.new")).disabled
        rows = seen["ProductTable"][-1]["rows"]
        assert all(_action(rows, PER.id, k)["disabled"] for k in ("select", "edit", "delete"))
        _button(rc, t("steps.period.new")).fire_event("click", None)
        assert not seen["PeriodModal"][-1]["open_"].value
        # the modal's submit, as a backstop: a new period is refused
        assert seen["PeriodModal"][-1]["on_submit"]("2026-02-01", "2026-02-28", 90) is None
        _action(rows, PER.id, "delete")["on_click"]()
        assert list(s.project.value.periods) == [PER.id] and BANNER in _warnings()
    finally:
        rc.close()


def test_masks_refuse_switches_and_disable_their_actions(tmp_path, monkeypatch):
    from gui.tile import masks_card
    from tests.test_masks_card import _rows

    s = _state(tmp_path, monkeypatch)
    warned = []
    monkeypatch.setattr("gui.scripts.setup_guard.current_notifier", lambda: SimpleNamespace(warning=warned.append))
    before = s.project.value.periods[PER.id].masks["forest"]
    assert masks_card.switch_mask(s, "c", PER.id, "forest", on=False) is False
    assert s.project.value.periods[PER.id].masks["forest"] == before and warned == [BANNER]
    row = _rows([MaskRow("forest", "gee", Status.MISSING, Status.MISSING, True, "keep")], locked=True)["forest"]
    # no pill, and every item of the ⋮ menu greyed; Details still open
    assert row["cells"][2]["type"] == "state"
    [menu] = [a for a in row["actions"] if a["kind"] == "menu"]
    assert menu["items"] and all(i["disabled"] for i in menu["items"])
    assert not any(a.get("disabled") for a in row["actions"] if a["kind"] == "info")


def test_a_copys_earth_engine_mask_preview_is_disabled_and_refused(tmp_path, monkeypatch):
    """Spec §6.3: a copy never sends the creator's mask reference to Earth
    Engine. An unaligned EE mask's map toggle is disabled, and a stale click
    is refused with the banner; ``build_mask_image`` is never called."""
    from gui.tile import masks_card
    from tests.test_masks_card import _card, _rows, _sync_spawn

    _sync_spawn(monkeypatch)
    built = []
    monkeypatch.setattr(masks_card.aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    monkeypatch.setattr(masks_card, "build_mask_image", lambda mask, aoi: built.append(mask.ref))
    unaligned = MaskRow("forest", "gee", Status.MISSING, Status.MISSING, True, "keep")
    locked = _rows([unaligned], locked=True)["forest"]
    assert _action([locked], "forest", "map_toggle")["disabled"]
    aligned = _rows([MaskRow("forest", "gee", Status.CURRENT, Status.CURRENT, True, "keep")], locked=True)
    assert not _action(list(aligned.values()), "forest", "map_toggle")["disabled"]  # a local raster stays
    s = _state(tmp_path, monkeypatch)

    async def main():
        rc, seen, _modal, _confirm = _card(monkeypatch, s, rows=[unaligned])
        try:
            _action(seen["rows"], "forest", "map_toggle")["on_click"]()  # a stale click
            assert built == [] and BANNER in _warnings()
        finally:
            rc.close()

    asyncio.run(main())


def test_layers_step_disables_and_refuses_layer_changes(tmp_path, monkeypatch):
    from gui.tile import layers_step

    s = _state(tmp_path, monkeypatch)
    seen = _recorders(monkeypatch, layers_step, "ProductTable", "ConfirmDialog", "VisLayerModal")

    @solara.component
    def Page():
        NotificationProvider()
        layers_step.LayersStep(app_state=s, map_=SimpleNamespace(), gee_interface=None, layers=MapLayers(_Map()))

    _box, rc = reacton.render(Page(), handle_error=False)
    try:
        assert _button(rc, t("steps.layers.add")).disabled
        rows = seen["ProductTable"][-1]["rows"]
        assert _action(rows, "s2", "edit")["disabled"] and _action(rows, "s2", "delete")["disabled"]
        _action(rows, "s2", "delete")["on_click"]()
        seen["ConfirmDialog"][-1]["on_confirm"]()
        seen["VisLayerModal"][-1]["on_save"](GeeVisLayer(name="new", asset="y", bands=["B1"]), None)
        assert list(s.project.value.vis_layers) == ["s2"] and BANNER in _warnings()
    finally:
        rc.close()


def test_questionnaire_refuses_changes_but_keeps_the_expanded_switch(tmp_path, monkeypatch):
    from gui.tile import questionnaire_step as qstep
    from tests.test_questionnaire_step import _strings

    s = _state(tmp_path, monkeypatch)
    warned = []
    monkeypatch.setattr("gui.scripts.setup_guard.current_notifier", lambda: SimpleNamespace(warning=warned.append))
    keys = [q.key for q in s.project.value.questions]
    _box, rc = reacton.render(qstep.QuestionnaireStep(app_state=s), handle_error=False)
    try:
        assert _button(rc, t("steps.questionnaire.add")).disabled
        delete = [b for b in rc.find(v.Btn).widgets if "mdi-delete-outline" in _strings(b)]
        assert delete and all(b.disabled for b in delete)
        delete[0].fire_event("click", None)  # a stale click opens the confirmation...
        confirm = [b for b in rc.find(v.Btn).widgets if t("common.delete") in b.children]
        confirm[-1].fire_event("click", None)  # ...whose delete is refused
        assert [q.key for q in s.project.value.questions] == keys and BANNER in warned
        rc.find(v.Switch).widget.v_model = True  # allowed in a copy
        assert s.project.value.questions_expanded is True
    finally:
        rc.close()


def test_the_report_template_pick_is_refused(tmp_path, monkeypatch):
    from gui.widget import report_template

    s = _state(tmp_path, monkeypatch)
    docx = tmp_path / "mine.docx"
    docx.write_bytes(b"x")
    warned = []
    monkeypatch.setattr("gui.scripts.setup_guard.current_notifier", lambda: SimpleNamespace(warning=warned.append))
    seen = _recorders(monkeypatch, report_template, "FileInputComponent")
    _box, rc = reacton.render(report_template.ReportTemplateField(app_state=s), handle_error=False)
    try:
        seen["FileInputComponent"][-1]["on_value"](str(docx))
        assert s.project.value.report_template is None and warned == [BANNER]
    finally:
        rc.close()


def test_the_grid_save_is_refused(tmp_path, monkeypatch):
    from gui.widget import grid_form

    s = _state(tmp_path, monkeypatch)
    s.update_project("c", lambda live: live.alert_sets.clear())  # unlocked: only the copy holds the grid

    @solara.component
    def Page():
        NotificationProvider()
        grid_form.GridForm(app_state=s)

    _box, rc = reacton.render(Page(), handle_error=False)
    try:
        assert not [w for w in rc.find(v.Btn).widgets if t("steps.prepare.grid_save") in w.children]
    finally:
        rc.close()

    @solara.component
    def Fields():  # the Save behind a form rendered before the project became a copy
        NotificationProvider()
        grid_form._GridFields(app_state=s, name="c", bounds=(0.0, 0.0, 1.0, 1.0), grid=None, locked=False,
                              default_res=30.0)

    _box, rc = reacton.render(Fields(), handle_error=False)
    try:
        _button(rc, t("steps.prepare.grid_save")).fire_event("click", None)
        assert s.project.value.grid is None and BANNER in _warnings()
    finally:
        rc.close()


def test_an_aoi_pick_is_refused(tmp_path, monkeypatch, no_aoiview):
    import geopandas as gpd
    from shapely.geometry import box

    from tests.test_aoi_step_attach import _Result, _attach, _gee

    s = _state(tmp_path, monkeypatch)
    attach, rc = _attach(s, _gee([]))
    try:
        gdf = gpd.GeoDataFrame(geometry=[box(5, 5, 6, 6)], crs="EPSG:4326")
        result = _Result(gdf=gdf, admin=None, method="DRAW", name="new", feature_collection=None,
                         spec=SimpleNamespace(to_dict=lambda: {"method": "DRAW"}, asset_id=None, column=None))
        asyncio.run(attach(result))
        assert s.project.value.aoi.digest == "admin:2317" and BANNER in _warnings()
        assert t("steps.aoi.locked") not in _warnings()
    finally:
        rc.close()


@pytest.fixture
def no_aoiview(monkeypatch):
    import gui.tile.aoi_step as aoi_step_mod

    monkeypatch.setattr(aoi_step_mod, "AoiView", lambda **kw: None)


def test_prepare_rows_are_disabled_and_the_runner_refuses(tmp_path, monkeypatch):
    from tests.test_prepare_rows import _build

    out = _build([PrepareRow("RADD", Status.MISSING, Status.MISSING, coverage="covered")], locked=True)["RADD"]
    assert out["cells"][1]["type"] == "state"  # no pill to run it
    assert all(a["disabled"] for a in out["actions"] if a["kind"] in ("refresh", "delete"))
    s = _state(tmp_path, monkeypatch)
    ctx = prepare_jobs.PrepareContext(app_state=s, layers=None, map_=None, notifier=None, name="c", pdir=tmp_path,
                                      period=PER, meta={}, aoi=None, cancel=threading.Event())
    with pytest.raises(ReadOnlyProject):
        prepare_jobs.run_rows(ctx, [("source", "GLAD-L")])


def test_integrate_refuses_to_run_in_a_copy(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    job = IntegrationJob(project=s.project.value.model_copy(deep=True), pdir=tmp_path, period=PER,
                         sources=["GLAD-L"], params=AlertSetParams())
    monkeypatch.setattr("gui.scripts.integrate_jobs.run_integration",
                        lambda *a, **k: pytest.fail("the engine ran in a review copy"))
    with pytest.raises(ReadOnlyProject):
        run_integration_job(s, SimpleNamespace(), job)


def test_a_copy_builds_no_package(data_root, tmp_path):
    from deforestation_alerts.project.share import build_package
    from tests._share_helpers import AOI_FC, APP_VERSION, SET1, make_copy

    p, pdir = make_copy(data_root, statuses=["Not reviewed"] * 2, assigned=["Ana", "Bo"])
    with pytest.raises(ReadOnlyProject):
        build_package(p, pdir, SET1, "Ana", AOI_FC, tmp_path / "out", app_version=APP_VERSION)
    assert not (tmp_path / "out").exists()


# ------------------------------------------------------ banner and Duplicate
def test_the_banner_shows_above_the_steps_of_a_copy_only(tmp_path, monkeypatch):
    from gui.widget.workflow_header import WorkflowHeader
    from tests.test_project_dialog import _texts

    s = _state(tmp_path, monkeypatch)
    box, rc = reacton.render(WorkflowHeader(app_state=s), handle_error=False)
    try:
        assert [a for a in rc.find(v.Alert).widgets if _texts(a) == [BANNER]]
        s.new_project_state(_project(name="plain", copy=False))
        assert not rc.find(v.Alert).widgets
    finally:
        rc.close()


# ------------------------------------- fix round 1: no remote reads, no gaps
async def _settle(cond, rounds=200):
    for _ in range(rounds):
        await asyncio.sleep(0.01)
        if cond():
            return True
    return False


def test_opening_a_copy_checks_no_coverage_even_on_check_again(tmp_path, monkeypatch):
    """The creator's source refs (here a private asset) are never resolved:
    neither the load nor "Check again" asks for coverage, nor writes it."""
    from deforestation_alerts.project.models import BandMap, add_custom_source
    from gui.scripts import remote_facts as rf

    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    p = _project()
    add_custom_source(p, name="mine", origin="gee", ref="users/creator/private", asset_type="IMAGE",
                      format="radd", date_encoding="yyddd", band_map=BandMap(alert=1, date=2),
                      native=NativeGrid(crs="EPSG:4326", scale=10.0))
    assert p.coverage == {} and rf.coverage_needed(p, frozenset()) == ((), ())
    s = AppState()
    s.new_project_state(p)
    asked = []

    async def get_info_async(obj):
        asked.append(obj)
        return {"bands": ["conf26"]}

    # ``coverage_request`` is what resolves a custom ref (``custom_footprint``)
    monkeypatch.setattr(rf, "coverage_request", lambda *a: pytest.fail("a copy's coverage was requested"))
    monkeypatch.setattr(rf.catalogue, "meta_request", lambda key: "META")

    @solara.component
    def Page():
        NotificationProvider()
        rf.RemoteFactsLoader(app_state=s, gee_interface=SimpleNamespace(get_info_async=get_info_async))

    async def main():
        _box, rc = reacton.render(Page(), handle_error=False)
        try:
            assert await _settle(lambda: s.catalogue_meta.value)  # the loader's tasks did run
            s.coverage_retry.set(s.coverage_retry.value + 1)  # "Check again"
            await _settle(lambda: False, rounds=20)
            assert asked == ["META"] and s.project.value.coverage == {}
            assert s.coverage_error.value is None and s.coverage_pending.value is False
        finally:
            rc.close()

    asyncio.run(main())


def test_a_copys_aoi_step_replays_nothing(tmp_path, monkeypatch):
    """The creator's AOI (an admin code here, as a spec to replay) is never
    replayed through GAUL or Earth Engine: no picker, ``aoi_result`` unset."""
    from gui.tile import aoi_step

    mounted = []

    @solara.component
    def _View(**kw):
        mounted.append(kw.get("spec"))

    monkeypatch.setattr(aoi_step, "AoiView", _View)
    monkeypatch.setattr(aoi_step.AoiSpec, "from_dict", classmethod(lambda cls, d: ("SPEC", d)))
    s = _state(tmp_path, monkeypatch)
    s.update_project("c", lambda live: setattr(live, "aoi", live.aoi.model_copy(
        update={"spec": {"method": "ADMIN1", "admin": "2317"}})))
    gee = SimpleNamespace(get_info_async=lambda obj: pytest.fail("Earth Engine was asked for a copy's AOI"))

    @solara.component
    def Page():
        NotificationProvider()
        aoi_step.AoiStep(app_state=s, map_=SimpleNamespace(), gee_interface=gee, layers=MapLayers(_Map()))

    _box, rc = reacton.render(Page(), handle_error=False)
    try:
        assert mounted == [] and s.aoi_result.value is None
        assert [w for w in rc.find(v.Html).widgets if w.children == ["a"]]  # the AOI's name, read-only
        plain = _project(name="plain", copy=False)  # the same AOI in a plain project is replayed
        plain.aoi = s.project.value.aoi
        s.new_project_state(plain)
        assert mounted and mounted[-1] == ("SPEC", {"method": "ADMIN1", "admin": "2317"})
    finally:
        rc.close()


def test_a_refusal_survives_the_project_closing_before_its_warning(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    real = s.update_project

    def closing(name, fn):
        try:
            return real(name, fn)
        finally:
            s.project.set(None)  # closed between the refusal and its warning

    monkeypatch.setattr(s, "update_project", closing)
    warned = []
    assert update_setup(s, "c", lambda live: None, SimpleNamespace(warning=warned.append)) is False
    assert warned == [BANNER]
