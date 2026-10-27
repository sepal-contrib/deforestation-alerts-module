"""The Evaluators step (spec 2026-10-10 §4, §6.4): the split form, the
evaluators table, the package downloads, the import of a reviewed copy with
its preview, and the Return reviews panel of a review copy.

The services (split, build, read, merge) are swapped for recorders here:
they have their own tests (T3, T8, T11) and run for real end to end in
tests/test_review_sharing_e2e.py."""

import asyncio
import json
import threading
import time
import tomllib
import zipfile
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import ipyvuetify as v
import pytest
import reacton
import solara
from pysepal.i18n import set_locale
from pysepal.sepalwidgets.file_input import FileInput
from pysepal.solara.notifications import NotificationProvider
from pysepal.solara.notifications.bus import get_current_bus
from shapely.geometry import box

from deforestation_alerts.project import models, paths, share
from deforestation_alerts.project import split as split_mod
from deforestation_alerts.project.models import (
    AlertSet,
    AlertSetParams,
    AoiRecord,
    EvaluatorReturn,
    EvaluatorSplit,
    Period,
    Project,
    ReviewCopy,
    add_predefined_source,
)
from deforestation_alerts.project.share import ShareError
from deforestation_alerts.project.split import SplitError
from deforestation_alerts.scripts import local_files
from gui.i18n import t, tn
from gui.scripts.error_text import error_text
from gui.store.state_manager import AppState
from gui.tile import evaluators_step as es
from gui.tile.analysis.state import AnalysisState
from gui.widget.product_table import CARD_CONTENT_PX, fits

START, END = date(2026, 8, 1), date(2026, 8, 31)
PERIOD = models.period_id(START, END)
SET_ID = models.set_id(PERIOD, ["GLAD-L"])
FC = {"type": "FeatureCollection", "features": [{
    "type": "Feature", "properties": {},
    "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}}]}
DOWNLOAD = "mdi-cloud-download-outline"


def _split(packaged=False, returns=None):
    return EvaluatorSplit(split_id="s1", evaluators=["Ana", "Bo"], created_at=datetime.now(), packaged=packaged,
                          returns=returns or {})


def _project(name="c", *, split=None, review_copy=None, sidecar=True, multi=True):
    """A project whose active set has a canonical folder; with ``sidecar``,
    a drawn AOI with its ``aoi.geojson``, else an Earth Engine one without."""
    now = datetime.now()
    p = Project(name=name, created_at=now, updated_at=now)
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="d" if sidecar else "admin:1",
                      bounds=(0, 0, 1, 1), geometry_file=paths.AOI_SIDECAR if sidecar else None)
    src = add_predefined_source(p, "GLAD-L")
    p.periods = {PERIOD: Period(id=PERIOD, start=START, end=END)}
    p.active_period = PERIOD
    pdir = paths.project_dir(name)
    sdir = paths.set_dir(pdir, PERIOD, ["GLAD-L"])
    p.alert_sets = {SET_ID: AlertSet(
        id=SET_ID, period_id=PERIOD, sources=["GLAD-L"], digit_map={src.digit: "GLAD-L"}, params=AlertSetParams(),
        store_rel_path=paths.rel(pdir, paths.store_path(sdir)),
        combined_rel_path=paths.rel(pdir, paths.combined_path(sdir, ["GLAD-L"], START, END)),
        footprint_rel_path=paths.rel(pdir, paths.footprint_path(sdir)),
        n_alerts=4, fingerprint="fp", created_at=now, split=split)}
    p.active_set = SET_ID
    p.review_copy = review_copy
    p.multi_evaluator = multi
    if sidecar:
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / paths.AOI_SIDECAR).write_text(json.dumps(FC), encoding="utf-8")
    return p


def _gdf(evaluators=("Ana", "Bo", "Ana", ""), statuses=("Confirmed", "Not reviewed", "Not reviewed", "Confirmed")):
    return gpd.GeoDataFrame({"alert_id": [11, 12, 13, 14], "set_id": [SET_ID] * 4, "status": list(statuses),
                             "evaluator": list(evaluators)}, geometry=[box(0, 0, 1, 1)] * 4, crs="EPSG:4326")


def _state(p, gdf=None):
    set_locale("en")
    s = AppState()
    s.load_project_state(p)
    s.alerts_gdf.set(gdf)
    return s


def _record_split(app_state, made):
    """What ``split_service.apply_split`` publishes when the project stays open."""
    def _apply(live):
        s = live.alert_sets[SET_ID]
        live.alert_sets = {**live.alert_sets, SET_ID: s.model_copy(update={"split": made})}

    app_state.update_project(app_state.project.value.name, _apply)


def _record_return(app_state, evaluator, record):
    """What ``merge_service.merge_return`` publishes when the project stays open."""
    def _apply(live):
        s = live.alert_sets[SET_ID]
        made = s.split.model_copy(update={"returns": {**s.split.returns, evaluator: record}})
        live.alert_sets = {**live.alert_sets, SET_ID: s.model_copy(update={"split": made})}

    app_state.update_project(app_state.project.value.name, _apply)


@solara.component
def _Harness(state, analysis):
    NotificationProvider()
    es.EvaluatorsStep(app_state=state, app_tile_model=SimpleNamespace(), analysis=analysis, gee_interface=None)


def _render(state):
    return reacton.render(_Harness(state=state, analysis=AnalysisState()), handle_error=False)


def _strings(w):
    out = []
    for c in getattr(w, "children", None) or []:
        out.extend([c] if isinstance(c, str) else _strings(c))
    return out


def _button(rc, label):
    return next(b for b in rc.find(v.Btn).widgets if label in _strings(b))


def _toasts(kind):
    return [x.message for x in get_current_bus().toasts.value if x.type.value == kind]


def _open_split(rc):
    """The split modal, from the table header (spec 2026-10-14 §4.3)."""
    (btn,) = [b for b in rc.find(v.Btn).widgets if "defo-split-open" in (b.class_ or "")]
    btn.fire_event("click", None)


def _open_merge(rc):
    """The merge modal, from the table footer (spec 2026-10-14 §4.5)."""
    _button(rc, t("evaluators.merge_open")).fire_event("click", None)


def _name_rows(rc):
    """The names mode's fields, one per evaluator, in order."""
    return [f for f in rc.find(v.TextField).widgets if "defo-name-row" in (f.class_ or "")]


def _names_mode(rc, *names):
    """Switch the open split modal to Names and type ``names`` in its rows."""
    rc.find(v.RadioGroup).widget.v_model = "names"
    for i, name in enumerate(names):
        if i >= len(_name_rows(rc)):
            _button(rc, t("evaluators.add_name")).fire_event("click", None)
        _name_rows(rc)[i].v_model = name


def _hint(rc):
    """The note under the review-mode select: what the chosen mode means."""
    return rc.find(v.Html, class_="defo-mode-note").widget.children[0]


async def _settle(done, what, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not done():
        if time.monotonic() > deadline:
            raise AssertionError(f"never happened within {timeout:g} s: {what}")
        await asyncio.sleep(0.01)


def _preview(evaluator="Bo"):
    return SimpleNamespace(header=SimpleNamespace(evaluator=evaluator), rows=None, new=[12], conflicts=[13],
                           unchanged=[], ignored=[99], columns=["status"], extra_columns=["junk"],
                           questions_changed=True, n_groups=0, class_groups={})


def _clouds(rc):
    return rc.find(v.Html, class_="defo-download-action").widgets


def _generate_btn(rc):
    (btn,) = [b for b in rc.find(v.Btn).widgets if "defo-generate" in (b.class_ or "")]
    return btn


def _package_zip(p_, set_id, evaluator, out_dir, *, split_id=None, digest=None):
    """What ``share.build_package`` leaves in the set folder, as far as the
    step reads it: the zip under its name, with its share.json."""
    sp = p_.alert_sets[set_id].split
    index = sp.evaluators.index(evaluator) + 1
    header = share.ShareHeader(format=share.FORMAT, format_version=share.FORMAT_VERSION, kind=share.KIND_PACKAGE,
                               app_version="test", origin_project=p_.name, set_id=set_id, set_fingerprint="fp",
                               split_id=split_id or sp.split_id, evaluator=evaluator, evaluator_index=index,
                               questions_digest=digest if digest is not None
                               else split_mod.questions_digest(p_.questions),
                               created_at=datetime.now())
    path = Path(out_dir) / share.package_filename(p_.name, set_id, index, evaluator)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(share.HEADER_MEMBER, header.model_dump_json())
    return path


def _return_zip(p_, out_dir):
    rc_ = p_.review_copy
    header = share.ShareHeader(format=share.FORMAT, format_version=share.FORMAT_VERSION, kind=share.KIND_RETURN,
                               app_version="test", origin_project=rc_.origin_project, set_id=rc_.set_id,
                               set_fingerprint="fp", split_id=rc_.split_id, evaluator=rc_.evaluator,
                               evaluator_index=rc_.evaluator_index, questions_digest="d", created_at=datetime.now(),
                               returned_at=datetime.now())
    path = Path(out_dir) / share.return_filename(rc_.origin_project, rc_.set_id, rc_.evaluator_index, rc_.evaluator)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(share.HEADER_MEMBER, header.model_dump_json())
    return path


def _generate_return_btn(rc):
    (btn,) = [b for b in rc.find(v.Btn).widgets if "defo-generate-return" in (b.class_ or "")]
    return btn


def _set_folder():
    return paths.set_dir(paths.project_dir("c"), PERIOD, ["GLAD-L"])


# ---------------------------------------------------------------- helpers
def test_the_package_header_carries_the_apps_version():
    with open(Path(es.__file__).resolve().parents[2] / "pyproject.toml", "rb") as f:
        assert es.app_version() == tomllib.load(f)["project"]["version"]


def test_a_count_or_a_list_names_the_evaluators():
    set_locale("en")
    assert es.typed_evaluators("count", " 3 ", []) == ["Evaluator 1", "Evaluator 2", "Evaluator 3"]
    assert es.typed_evaluators("names", "", ["Ana", "", "  Bo "]) == ["Ana", "Bo"]
    with pytest.raises(SplitError) as err:
        es.typed_evaluators("count", "two", [])
    assert err.value.key == "evaluators.err.bad_count"  # T3's key, rendered by error_text
    assert error_text(err.value) == t("evaluators.err.bad_count", max=split_mod.MAX_EVALUATORS)
    with pytest.raises(SplitError):
        es.typed_evaluators("names", "", ["Ana", "ana"])  # a casefold duplicate


def test_a_duplicate_is_flagged_on_its_second_row():
    assert es.duplicate_rows(["Ana", "", " ana ", "Bo", "ANA"]) == {2, 4}
    assert es.duplicate_rows(["Ana", "Bo", "", ""]) == set()


def test_the_table_counts_each_evaluators_alerts_and_the_unassigned_ones():
    merged = datetime(2026, 10, 9, 14, 0)
    split = _split(returns={"Ana": EvaluatorReturn(merged_at=merged, taken=1, unchanged=0, conflicts=0, ignored=0)})
    rows, unassigned = es.evaluator_rows(split, _gdf())
    assert rows == [{"name": "Ana", "assigned": 2, "reviewed": 1, "merged_at": merged},
                    {"name": "Bo", "assigned": 1, "reviewed": 0, "merged_at": None}]
    assert unassigned == 1
    assert [r["assigned"] for r in es.evaluator_rows(split, None)[0]] == [0, 0]  # alerts not loaded yet
    assert es.evaluator_rows(None, _gdf()) == ([], 0)


def test_the_evaluators_table_fits_the_step_card():
    assert fits(es.table_columns(), 1, CARD_CONTENT_PX)


def test_a_package_needs_the_aoi_geometry(data_root):
    p = _project()
    assert es.aoi_ready(p, paths.project_dir("c"), None)
    ee = _project("e", sidecar=False)
    # An Earth Engine AOI before the AOI step's replay handed its selection over.
    assert not es.aoi_ready(ee, paths.project_dir("e"), None)


def test_a_package_lands_in_the_set_folder(data_root, monkeypatch):
    p = _project(split=_split())
    seen = {}

    def _build(p_, pdir, set_id, evaluator, aoi, out_dir, *, app_version):
        seen.update(pdir=Path(pdir), set_id=set_id, evaluator=evaluator, aoi=aoi, out_dir=Path(out_dir),
                    app_version=app_version)
        made = Path(out_dir) / "c__x__e01-ana__review.zip"
        made.parent.mkdir(parents=True, exist_ok=True)
        made.write_bytes(b"PK")
        return made

    monkeypatch.setattr(es.share, "build_package", _build)
    folder = paths.set_dir(paths.project_dir("c"), PERIOD, ["GLAD-L"])
    path = es.build_package_file(p, paths.project_dir("c"), SET_ID, "Ana", FC)
    assert path == folder / "c__x__e01-ana__review.zip"
    assert seen == {"pdir": paths.project_dir("c"), "set_id": SET_ID, "evaluator": "Ana", "aoi": FC,
                    "out_dir": folder, "app_version": es.app_version()}
    entry = es.link_entry(path)
    assert entry["file"] == path.name and entry["url"].endswith("/" + local_files.home_relative(path))


def test_a_failure_reads_as_its_keyed_text_or_names_the_file():
    set_locale("en")
    refusal = ShareError("share.merge_err_changed")
    assert es.failure_text(refusal, "bo.zip") == t("share.merge_err_changed")
    crash = OSError("disk full")
    assert es.failure_text(crash, "bo.zip") == t("evaluators.merge_failed", file="bo.zip", error="disk full")


# ----------------------------------------------------------------- render
def test_the_step_waits_for_an_alert_set(data_root):
    p = _project()
    p.active_set = None
    _box, rc = _render(_state(p))
    try:
        assert t("workflow.lock_analysis") in _strings(rc.container)
        assert not [b for b in rc.find(v.Btn).widgets if t("evaluators.split") in _strings(b)]
    finally:
        rc.close()


def _mode_box(rc, key):
    """The Solo (``"solo"``) or Multiple evaluators (``"multi"``) checkbox."""
    return rc.find(v.Checkbox, class_=f"defo-mode-{key}")


def _checked(rc):
    return [k for k in ("solo", "multi") if _mode_box(rc, k).widget.v_model]


def test_the_review_mode_is_two_checkboxes_with_their_icons(data_root):
    _box, rc = _render(_state(_project(multi=None), _gdf(evaluators=("",) * 4)))
    try:
        labels = [_strings(w) for w in rc.find(v.Html, class_="defo-mode-label").widgets]
        assert labels == [["mdi-account", t("evaluators.review_mode_solo")],
                          ["mdi-account-group", t("evaluators.review_mode_multi")]]
        assert _checked(rc) == []  # no mode chosen yet
        assert _hint(rc) == t("evaluators.mode_unset_note")
    finally:
        rc.close()


def test_a_solo_project_hides_the_split_and_still_starts_analysis(data_root):
    _box, rc = _render(_state(_project(multi=False), _gdf(evaluators=("",) * 4)))
    try:
        texts = _strings(rc.container)
        assert _checked(rc) == ["solo"]
        assert _hint(rc) == t("evaluators.solo_note")
        assert t("evaluators.split_title") not in texts
        assert not [b for b in rc.find(v.Btn).widgets if t("evaluators.split") in _strings(b)]
        assert not rc.find(FileInput).widgets
        assert _button(rc, t("workflow.start_analysis"))
    finally:
        rc.close()


def test_start_analysis_opens_the_analysis_screen(data_root):
    """The step ends with the workflow's one *Start analysis*, which writes the
    navigation signal (``_NavBridge`` opens the screen)."""
    s = _state(_project(multi=False), _gdf(evaluators=("",) * 4))
    _box, rc = _render(s)
    try:
        (start,) = [w for w in rc.find(v.Btn).widgets if "defo-start-analysis" in (w.class_ or "")]
        assert not start.disabled
        start.fire_event("click", None)
        assert s.current_page_view.value == "analysis_tile"
    finally:
        rc.close()


def test_switching_to_multiple_evaluators_is_saved_and_shows_the_split(data_root):
    s = _state(_project(multi=False), _gdf(evaluators=("",) * 4))
    _box, rc = _render(s)
    try:
        _mode_box(rc, "multi").widget.v_model = True
        assert s.project.value.multi_evaluator is True
        assert _checked(rc) == ["multi"]
        assert t("evaluators.open_split") in _strings(rc.container)
        assert _hint(rc) == t("evaluators.multi_note")
        _mode_box(rc, "solo").widget.v_model = True
        assert s.project.value.multi_evaluator is False
        assert _checked(rc) == ["solo"]
        assert t("evaluators.open_split") not in _strings(rc.container)
    finally:
        rc.close()


def test_unchecking_the_mode_leaves_none_chosen(data_root):
    s = _state(_project(multi=None), _gdf(evaluators=("",) * 4))
    _box, rc = _render(s)
    try:
        _mode_box(rc, "solo").widget.v_model = True
        assert s.project.value.multi_evaluator is False
        _mode_box(rc, "solo").widget.v_model = False
        assert s.project.value.multi_evaluator is None
        assert _checked(rc) == []
        assert _hint(rc) == t("evaluators.mode_unset_note")
    finally:
        rc.close()


def test_a_click_on_the_option_text_toggles_its_box(data_root):
    s = _state(_project(multi=None), _gdf(evaluators=("",) * 4))
    _box, rc = _render(s)
    try:
        solo, _multi = rc.find(v.Html, class_="defo-mode-label").widgets
        solo.fire_event("click", None)
        assert s.project.value.multi_evaluator is False and _checked(rc) == ["solo"]
        rc.find(v.Html, class_="defo-mode-label").widgets[0].fire_event("click", None)
        assert s.project.value.multi_evaluator is None and _checked(rc) == []
    finally:
        rc.close()


def test_a_split_holds_the_mode_on_multiple_evaluators(data_root):
    _box, rc = _render(_state(_project(split=_split()), _gdf()))
    try:
        assert _checked(rc) == ["multi"]
        assert all(_mode_box(rc, k).widget.disabled is True for k in ("solo", "multi"))
        assert _hint(rc) == t("evaluators.multi_locked")
    finally:
        rc.close()


def test_a_review_copy_has_no_review_mode(data_root):
    copy = ReviewCopy(evaluator="Bo", evaluator_index=2, set_id=SET_ID, split_id="s1", set_fingerprint="fp",
                      origin_project="c", imported_at=datetime.now())
    _box, rc = _render(_state(_project("c__e02-bo", split=_split(), review_copy=copy, multi=False), _gdf()))
    try:
        assert not _mode_box(rc, "solo").widgets and not _mode_box(rc, "multi").widgets
        assert t("evaluators.return_title") in _strings(rc.container)
    finally:
        rc.close()


def test_split_hands_the_typed_names_to_the_service(data_root, monkeypatch):
    calls = []

    def _apply(app_state, model, names, include_reviewed):
        calls.append((names, include_reviewed))
        made = EvaluatorSplit(split_id="s2", evaluators=names, created_at=datetime.now())
        _record_split(app_state, made)
        return made

    monkeypatch.setattr(es.split_service, "apply_split", _apply)
    s = _state(_project(), _gdf(evaluators=("",) * 4))

    async def main():
        _box, rc = _render(s)
        try:
            _open_split(rc)
            rc.find(v.TextField, label=t("evaluators.count_label")).widget.v_model = "3"
            rc.find(v.Checkbox, label=t("evaluators.include_reviewed")).widget.v_model = True
            _button(rc, t("evaluators.split")).fire_event("click", None)
            await _settle(lambda: calls, "the split")
            assert calls == [(["Evaluator 1", "Evaluator 2", "Evaluator 3"], True)]
            await _settle(lambda: tn("evaluators.split_done", 3) in _toasts("success"), "the split toast")
        finally:
            rc.close()

    asyncio.run(main())


def test_a_split_the_project_did_not_record_is_not_reported_as_done(data_root, monkeypatch):
    """``apply_split`` returns its split even when the project switched before
    the record landed (the store keeps the column: an orphan)."""
    monkeypatch.setattr(es.split_service, "apply_split",
                        lambda st, m, names, inc: EvaluatorSplit(split_id="s2", evaluators=names,
                                                                 created_at=datetime.now()))
    s = _state(_project(), _gdf(evaluators=("",) * 4))

    async def main():
        _box, rc = _render(s)
        try:
            _open_split(rc)
            _button(rc, t("evaluators.split")).fire_event("click", None)
            await _settle(lambda: t("evaluators.split_unrecorded", project="c") in _toasts("warning"), "the warning")
            assert not _toasts("success")
        finally:
            rc.close()

    asyncio.run(main())


def test_names_mode_lists_one_field_per_evaluator(data_root):
    _box, rc = _render(_state(_project(), _gdf(evaluators=("",) * 4)))
    try:
        _open_split(rc)
        rc.find(v.RadioGroup).widget.v_model = "names"
        rows = _name_rows(rc)
        assert [r.label for r in rows] == [t("evaluators.name_field", n=1), t("evaluators.name_field", n=2)]
        assert [r.v_model for r in rows] == ["", ""]
        assert _button(rc, t("evaluators.split")).disabled  # no name yet, and no error shown for it
        assert not any(r.error_messages for r in rows)
        _names_mode(rc, "Ana", "Bo", "Cy")
        assert [r.label for r in _name_rows(rc)][-1] == t("evaluators.name_field", n=3)
        removes = [b for b in rc.find(v.Btn).widgets if "defo-name-remove" in (b.class_ or "")]
        removes[0].fire_event("click", None)
        assert [r.v_model for r in _name_rows(rc)] == ["Bo", "Cy"]
        assert [r.label for r in _name_rows(rc)] == [t("evaluators.name_field", n=1),
                                                     t("evaluators.name_field", n=2)]
        assert not _button(rc, t("evaluators.split")).disabled
    finally:
        rc.close()


def _widgets(w):
    out = [w]
    for c in getattr(w, "children", None) or []:
        if not isinstance(c, str):
            out.extend(_widgets(c))
    return out


def test_the_names_and_add_evaluator_sit_in_one_card_as_the_questions_options(data_root):
    _box, rc = _render(_state(_project(), _gdf(evaluators=("",) * 4)))
    try:
        _open_split(rc)
        rc.find(v.RadioGroup).widget.v_model = "names"
        (card,) = [f for f in rc.find(v.Html, tag="fieldset").widgets
                   if _strings(f)[:1] == [t("evaluators.names_group")]]
        inside = _widgets(card)
        assert all(row in inside for row in _name_rows(rc))
        assert _button(rc, t("evaluators.add_name")) in inside
    finally:
        rc.close()


def test_names_mode_stops_adding_at_the_most_evaluators(data_root):
    _box, rc = _render(_state(_project(), _gdf(evaluators=("",) * 4)))
    try:
        _open_split(rc)
        rc.find(v.RadioGroup).widget.v_model = "names"
        add = lambda: _button(rc, t("evaluators.add_name"))  # noqa: E731
        while not add().disabled:
            add().fire_event("click", None)
        assert len(_name_rows(rc)) == split_mod.MAX_EVALUATORS
    finally:
        rc.close()


def test_split_hands_the_named_evaluators_to_the_service(data_root, monkeypatch):
    calls = []

    def _apply(app_state, model, names, include_reviewed):
        calls.append(names)
        made = EvaluatorSplit(split_id="s2", evaluators=names, created_at=datetime.now())
        _record_split(app_state, made)
        return made

    monkeypatch.setattr(es.split_service, "apply_split", _apply)
    s = _state(_project(), _gdf(evaluators=("",) * 4))

    async def main():
        _box, rc = _render(s)
        try:
            _open_split(rc)
            _names_mode(rc, " Ana ", "", "Bo")
            _button(rc, t("evaluators.split")).fire_event("click", None)
            await _settle(lambda: calls, "the split")
            assert calls == [["Ana", "Bo"]]
        finally:
            rc.close()

    asyncio.run(main())


def test_a_duplicate_name_is_refused_inline(data_root):
    s = _state(_project(), _gdf(evaluators=("",) * 4))
    _box, rc = _render(s)
    try:
        _open_split(rc)
        _names_mode(rc, "Ana", "ana")
        first, second = _name_rows(rc)
        assert not first.error_messages
        assert second.error_messages == [t("evaluators.err.duplicate_name", name="ana")]
        assert _button(rc, t("evaluators.split")).disabled
    finally:
        rc.close()


@pytest.mark.parametrize("action", ["resplit", "remove"])
def test_a_packaged_split_warns_in_the_modal_and_one_click_proceeds(data_root, monkeypatch, action):
    calls = []
    monkeypatch.setattr(es.split_service, "apply_split",
                        lambda st, m, names, inc: calls.append("resplit") or _split())
    monkeypatch.setattr(es.split_service, "remove_split", lambda st, m: calls.append("remove"))
    s = _state(_project(split=_split(packaged=True)), _gdf())

    async def main():
        _box, rc = _render(s)
        try:
            assert t("evaluators.packaged_warning") not in _strings(rc.container)
            _open_split(rc)
            assert t("evaluators.packaged_warning") in _strings(rc.container)
            _button(rc, t(f"evaluators.{action}")).fire_event("click", None)
            await _settle(lambda: calls == [action], f"the {action}")
            assert t("evaluators.packaged_warning") not in _strings(rc.container)  # the modal closed
        finally:
            rc.close()

    asyncio.run(main())


def test_the_modal_offers_remove_only_with_a_split(data_root):
    _box, rc = _render(_state(_project(), _gdf(evaluators=("",) * 4)))
    try:
        assert t("evaluators.open_split") in _strings(rc.container)
        _open_split(rc)
        texts = _strings(rc.container)
        assert t("evaluators.split_title") in texts and t("evaluators.packaged_warning") not in texts
        assert not [b for b in rc.find(v.Btn).widgets if t("evaluators.remove") in _strings(b)]
    finally:
        rc.close()
    _box, rc = _render(_state(_project(split=_split()), _gdf()))
    try:
        assert t("evaluators.edit_split") in _strings(rc.container)
        _open_split(rc)
        assert t("evaluators.edit_title") in _strings(rc.container)
        assert _button(rc, t("evaluators.remove")) and _button(rc, t("evaluators.resplit"))
        assert t("evaluators.packaged_warning") not in _strings(rc.container)  # nothing was packaged
    finally:
        rc.close()


@pytest.mark.parametrize("lands", [True, False])
def test_remove_says_removed_only_when_the_record_is_gone(data_root, monkeypatch, lands):
    """``remove_split`` can end without recording anything (no evaluator
    column and no alerts on hand, or another project opened meanwhile): the
    step then says so instead of "Split removed" (the T12 convention)."""
    s = _state(_project(split=_split()), _gdf())

    def _remove(app_state, model):
        if lands:
            _record_split(app_state, None)

    monkeypatch.setattr(es.split_service, "remove_split", _remove)

    async def main():
        _box, rc = _render(s)
        try:
            _open_split(rc)
            _button(rc, t("evaluators.remove")).fire_event("click", None)
            if lands:
                await _settle(lambda: t("evaluators.removed") in _toasts("success"), "the success toast")
                assert not _toasts("warning")
            else:
                await _settle(lambda: t("evaluators.remove_unrecorded", project="c") in _toasts("warning"),
                              "the warning")
                assert not _toasts("success")
        finally:
            rc.close()

    asyncio.run(main())


def test_a_split_no_package_was_built_for_is_replaced_at_once(data_root, monkeypatch):
    calls = []
    monkeypatch.setattr(es.split_service, "apply_split",
                        lambda st, m, names, inc: calls.append(names) or _split())
    s = _state(_project(split=_split()), _gdf())

    async def main():
        _box, rc = _render(s)
        try:
            _open_split(rc)
            _button(rc, t("evaluators.resplit")).fire_event("click", None)
            await _settle(lambda: calls == [["Evaluator 1", "Evaluator 2"]], "the re-split")
        finally:
            rc.close()

    asyncio.run(main())


def test_the_table_lists_the_evaluators_and_the_unassigned_alerts(data_root):
    _box, rc = _render(_state(_project(split=_split()), _gdf()))
    try:
        assert {"Ana", "Bo", t("evaluators.unassigned")} <= set(_strings(rc.container))
        clouds = _clouds(rc)
        assert len(clouds) == 2 and all(c.attributes.get("aria-disabled") == "true" for c in clouds)
        assert all(c.attributes["title"] == t("evaluators.generate_first") for c in clouds)
    finally:
        rc.close()


def test_assignments_without_a_split_record_offer_remove_split(data_root, monkeypatch):
    monkeypatch.setattr(es.split_service, "orphan_assignments", lambda p, gdf: True)
    _box, rc = _render(_state(_project(), _gdf()))
    try:
        assert t("evaluators.orphan") in _strings(rc.container)
        _open_split(rc)
        assert not _button(rc, t("evaluators.remove")).disabled
    finally:
        rc.close()


def test_an_earth_engine_aoi_not_loaded_yet_holds_generate(data_root):
    _box, rc = _render(_state(_project(split=_split(), sidecar=False), _gdf()))
    try:
        assert _generate_btn(rc).disabled
        assert t("evaluators.aoi_waiting") in _strings(rc.container)
    finally:
        rc.close()


def test_generate_builds_every_evaluators_package_and_the_clouds_download_them(data_root, monkeypatch):
    built = []

    def _build(p_, pdir, set_id, evaluator, aoi, out_dir, *, app_version):
        assert aoi == FC  # the sidecar, fetched once for the run
        built.append(evaluator)
        return _package_zip(p_, set_id, evaluator, out_dir)

    monkeypatch.setattr(es.share, "build_package", _build)
    s = _state(_project(split=_split()), _gdf())

    async def main():
        _box, rc = _render(s)
        try:
            assert t("evaluators.generate") in _strings(_generate_btn(rc))
            _generate_btn(rc).fire_event("click", None)
            await _settle(lambda: len([c for c in _clouds(rc) if c.tag == "a"]) == 2, "both cloud links")
            assert built == ["Ana", "Bo"]
            ana, bo = _clouds(rc)
            path = _set_folder() / share.package_filename("c", SET_ID, 1, "Ana")
            assert ana.attributes["href"].endswith("/" + local_files.home_relative(path))
            assert ana.attributes["download"] == path.name
            assert ana.attributes["title"] == t("evaluators.package_download", evaluator="Ana")
            split = s.project.value.alert_sets[SET_ID].split
            assert split.packaged and split.questions_digest == split_mod.questions_digest(s.project.value.questions)
            assert tn("evaluators.packages_ready", 2) in _toasts("success")
            assert t("evaluators.regenerate") in _strings(_generate_btn(rc))
            assert any(x.startswith(t("evaluators.built_at", when="")[:5]) for x in _strings(rc.container))
        finally:
            rc.close()

    asyncio.run(main())


def test_generate_stops_at_the_first_failure_and_keeps_what_it_built(data_root, monkeypatch):
    built = []

    def _build(p_, pdir, set_id, evaluator, aoi, out_dir, *, app_version):
        if evaluator == "Bo":
            raise ShareError("share.err.no_aoi")
        built.append(evaluator)
        return _package_zip(p_, set_id, evaluator, out_dir)

    monkeypatch.setattr(es.share, "build_package", _build)
    s = _state(_project(split=_split()), _gdf())

    async def main():
        _box, rc = _render(s)
        try:
            _generate_btn(rc).fire_event("click", None)
            await _settle(lambda: t("share.err.no_aoi") in _toasts("error"), "the error toast")
            await _settle(lambda: [c.tag for c in _clouds(rc)] == ["a", "span"], "Ana's link, Bo dim")
            assert built == ["Ana"] and not _toasts("success")
            assert s.project.value.alert_sets[SET_ID].split.packaged  # Ana's zip is out there
        finally:
            rc.close()

    asyncio.run(main())


def test_a_zip_built_earlier_is_offered_after_a_reload(data_root):
    p = _project(split=_split(packaged=True))
    _package_zip(p, SET_ID, "Bo", _set_folder())
    _box, rc = _render(_state(p, _gdf()))
    try:
        assert [c.tag for c in _clouds(rc)] == ["span", "a"]
        assert t("evaluators.regenerate") in _strings(_generate_btn(rc))
    finally:
        rc.close()


def test_a_zip_built_before_the_questionnaire_changed_is_amber(data_root):
    p = _project(split=_split(packaged=True))
    _package_zip(p, SET_ID, "Ana", _set_folder(), digest="an-older-questionnaire")
    _box, rc = _render(_state(p, _gdf()))
    try:
        ana = _clouds(rc)[0]
        assert ana.tag == "a" and ana.attributes["title"] == t("evaluators.package_outdated")
        (icon,) = [i for i in rc.find(v.Icon).widgets if DOWNLOAD in i.children and i.color == "warning"]
    finally:
        rc.close()


def test_a_zip_of_an_earlier_split_leaves_the_cloud_dim(data_root):
    p = _project(split=_split())
    _package_zip(p, SET_ID, "Ana", _set_folder(), split_id="an-earlier-split")
    _box, rc = _render(_state(p, _gdf()))
    try:
        assert [c.tag for c in _clouds(rc)] == ["span", "span"]
    finally:
        rc.close()


def test_a_reviewed_copy_is_previewed_then_merged_with_the_chosen_side(data_root, monkeypatch):
    preview = _preview()
    read, merged = [], []

    def _read(path, p, store):
        read.append((str(path), p.name, len(store)))
        return preview

    def _merge(app_state, model, analysis, made, choice):
        merged.append((made, choice))
        record = EvaluatorReturn(merged_at=datetime.now(), taken=2, unchanged=0, conflicts=1, choice=choice,
                                 ignored=1)
        _record_return(app_state, made.header.evaluator, record)
        return record

    monkeypatch.setattr(es.share, "read_return", _read)
    monkeypatch.setattr(es.merge_service, "merge_return", _merge)
    s = _state(_project(split=_split()), _gdf())
    s.actual_alert_id.set(2)  # alert 13 (row 2) is open in Analysis
    zip_path = data_root / "bo__reviewed.zip"
    zip_path.write_bytes(b"PK")

    async def main():
        _box, rc = _render(s)
        try:
            _open_merge(rc)
            rc.find(FileInput).widget.v_model = str(zip_path)
            await _settle(lambda: read, "the preview read")
            assert read == [(str(zip_path), "c", 4)]
            await _settle(lambda: t("evaluators.merge") in _strings(rc.container), "the preview dialog")
            texts = _strings(rc.container)
            assert t("share.merge_summary", evaluator="Bo", new=1, conflicts=1, unchanged=0, ignored=1) in texts
            assert t("share.merge_questions_changed") in texts
            assert t("share.merge_extra_columns", columns="junk") in texts
            assert t("share.merge_selected_reload", n=3) not in texts  # Keep mine (the default) leaves alert 13
            rc.find(v.RadioGroup, v_model="mine").widget.v_model = "theirs"
            assert t("share.merge_selected_reload", n=3) in _strings(rc.container)
            _button(rc, t("evaluators.merge")).fire_event("click", None)
            await _settle(lambda: merged, "the merge")
            assert merged == [(preview, "theirs")]
            await _settle(lambda: t("evaluators.merged", taken=2, evaluator="Bo") in _toasts("success"), "the toast")
            await _settle(lambda: t("evaluators.merge") not in _strings(rc.container), "the dialog closed")
        finally:
            rc.close()

    asyncio.run(main())


async def _open_preview(rc, zip_path):
    _open_merge(rc)
    rc.find(FileInput).widget.v_model = str(zip_path)
    await _settle(lambda: t("evaluators.merge") in _strings(rc.container), "the preview")
    _button(rc, t("evaluators.merge")).fire_event("click", None)


def test_a_merge_the_project_did_not_record_says_to_merge_again(data_root, monkeypatch):
    """``merge_return`` returns its record even when the project switched
    before it landed: the rows are merged, the record is not."""
    monkeypatch.setattr(es.share, "read_return", lambda path, p, store: _preview())
    monkeypatch.setattr(es.merge_service, "merge_return",
                        lambda st, m, a, made, choice: EvaluatorReturn(merged_at=datetime.now(), taken=2,
                                                                       unchanged=0, conflicts=1, ignored=1))
    s = _state(_project(split=_split()), _gdf())
    zip_path = data_root / "bo__reviewed.zip"
    zip_path.write_bytes(b"PK")

    async def main():
        _box, rc = _render(s)
        try:
            await _open_preview(rc, zip_path)
            await _settle(lambda: t("evaluators.merged_unrecorded", evaluator="Bo", project="c") in _toasts("warning"),
                          "the warning")
            assert not _toasts("success")
        finally:
            rc.close()

    asyncio.run(main())


@pytest.mark.parametrize("stage", ["read", "merge"])
def test_an_unexpected_failure_reading_or_merging_a_copy_is_toasted(data_root, monkeypatch, stage):
    def _boom(*args):
        raise OSError("disk full")

    monkeypatch.setattr(es.share, "read_return", _boom if stage == "read" else (lambda path, p, store: _preview()))
    monkeypatch.setattr(es.merge_service, "merge_return", _boom)
    s = _state(_project(split=_split()), _gdf())
    zip_path = data_root / "bo__reviewed.zip"
    zip_path.write_bytes(b"PK")
    expected = t("evaluators.merge_failed", file="bo__reviewed.zip", error="disk full")

    async def main():
        _box, rc = _render(s)
        try:
            if stage == "read":
                _open_merge(rc)
                rc.find(FileInput).widget.v_model = str(zip_path)
            else:
                await _open_preview(rc, zip_path)
            await _settle(lambda: expected in _toasts("error"), "the error toast")
            if stage == "read":  # the modal stays on the picker for another pick
                assert rc.find(FileInput).widgets
            if stage == "merge":  # the preview stays open on the copy
                assert t("evaluators.merge") in _strings(rc.container)
        finally:
            rc.close()

    asyncio.run(main())


def test_a_review_copy_generates_its_reviewed_copy_then_the_cloud_downloads_it(data_root, monkeypatch):
    seen = {}

    def _build(p, pdir, out_dir, *, app_version):
        seen.update(out_dir=Path(out_dir), evaluator=p.review_copy.evaluator)
        return _return_zip(p, out_dir)

    monkeypatch.setattr(es.share, "build_return", _build)
    copy = ReviewCopy(evaluator="Bo", evaluator_index=2, set_id=SET_ID, split_id="s1", set_fingerprint="fp",
                      origin_project="c", imported_at=datetime.now())
    s = _state(_project("c__e02-bo", split=_split(), review_copy=copy),
               _gdf(evaluators=("Ana", "Bo", "Ana", "Bo"),
                    statuses=("Confirmed", "Confirmed", "Not reviewed", "Not reviewed")))

    async def main():
        _box, rc = _render(s)
        try:
            texts = _strings(rc.container)
            assert t("evaluators.return_progress", reviewed="1", assigned="2") in texts
            assert t("evaluators.return_note") in texts
            assert not [b for b in rc.find(v.Btn).widgets if t("evaluators.split") in _strings(b)]
            assert not rc.find(FileInput).widgets  # no import in a copy
            (cloud,) = _clouds(rc)
            assert cloud.tag == "span" and cloud.attributes["title"] == t("evaluators.generate_return_first")
            assert t("evaluators.generate_return") in _strings(_generate_return_btn(rc))
            _generate_return_btn(rc).fire_event("click", None)
            await _settle(lambda: _clouds(rc)[0].tag == "a", "the reviewed copy's link")
            folder = paths.set_dir(paths.project_dir("c__e02-bo"), PERIOD, ["GLAD-L"])
            assert seen == {"out_dir": folder, "evaluator": "Bo"}
            (cloud,) = _clouds(rc)
            assert cloud.attributes["download"] == share.return_filename("c", SET_ID, 2, "Bo")
            assert t("evaluators.regenerate_return") in _strings(_generate_return_btn(rc))
            assert t("evaluators.return_ready") in _toasts("success")
        finally:
            rc.close()

    asyncio.run(main())


def test_a_reviewed_copy_built_earlier_is_offered_after_a_reload(data_root):
    copy = ReviewCopy(evaluator="Bo", evaluator_index=2, set_id=SET_ID, split_id="s1", set_fingerprint="fp",
                      origin_project="c", imported_at=datetime.now())
    p = _project("c__e02-bo", split=_split(), review_copy=copy)
    _return_zip(p, paths.set_dir(paths.project_dir("c__e02-bo"), PERIOD, ["GLAD-L"]))
    _box, rc = _render(_state(p, _gdf(evaluators=("Ana", "Bo", "Ana", "Bo"))))
    try:
        assert _clouds(rc)[0].tag == "a"
        assert any(x.startswith(t("evaluators.built_at", when="")[:5]) for x in _strings(rc.container))
    finally:
        rc.close()


# ------------------------------------------------------- re-entry, switches
def _blocking(calls, release, result=None):
    """A service stand-in that records its call and holds its worker thread
    until ``release`` is set, as a long split, merge or build does."""
    def _run(*args, **kwargs):
        calls.append(args)
        release.wait(10)
        return result(*args) if callable(result) else result

    return _run


def test_a_second_merge_click_while_one_runs_is_dropped(data_root, monkeypatch):
    calls, release = [], threading.Event()
    monkeypatch.setattr(es.share, "read_return", lambda path, p, store: _preview())
    monkeypatch.setattr(es.merge_service, "merge_return", _blocking(
        calls, release, EvaluatorReturn(merged_at=datetime.now(), taken=2, unchanged=0, conflicts=1, ignored=1)))
    s = _state(_project(split=_split()), _gdf())
    zip_path = data_root / "bo__reviewed.zip"
    zip_path.write_bytes(b"PK")

    async def main():
        _box, rc = _render(s)
        try:
            await _open_preview(rc, zip_path)
            merge = _button(rc, t("evaluators.merge"))
            merge.fire_event("click", None)  # the same click, or Enter, before the render disables it
            await _settle(lambda: calls, "the merge")
            _button(rc, t("evaluators.merge")).fire_event("click", None)  # while it runs
            await asyncio.sleep(0.1)
            assert len(calls) == 1
            release.set()
            await _settle(lambda: _toasts("warning"), "the merge's end")
            assert len(calls) == 1
        finally:
            release.set()
            rc.close()

    asyncio.run(main())


def test_esc_while_a_merge_runs_keeps_the_modal_on_it(data_root, monkeypatch):
    calls, release = [], threading.Event()
    monkeypatch.setattr(es.share, "read_return", lambda path, p, store: _preview())
    monkeypatch.setattr(es.merge_service, "merge_return", _blocking(
        calls, release, EvaluatorReturn(merged_at=datetime.now(), taken=2, unchanged=0, conflicts=1, ignored=1)))
    s = _state(_project(split=_split()), _gdf())
    zip_path = data_root / "bo__reviewed.zip"
    zip_path.write_bytes(b"PK")

    async def main():
        _box, rc = _render(s)
        try:
            await _open_preview(rc, zip_path)
            await _settle(lambda: calls, "the merge")
            (dialog,) = [d for d in rc.find(v.Dialog).widgets if d.v_model]
            dialog.fire_event("keydown.esc", None)
            assert dialog.v_model and t("evaluators.merge") in _strings(rc.container)
            release.set()
            await _settle(lambda: _toasts("warning"), "the merge's end")
        finally:
            release.set()
            rc.close()

    asyncio.run(main())


def test_the_split_button_spins_while_a_split_runs(data_root, monkeypatch):
    calls, release = [], threading.Event()
    monkeypatch.setattr(es.split_service, "apply_split", _blocking(calls, release, lambda st, m, names, inc: _split()))
    s = _state(_project(), _gdf(evaluators=("",) * 4))

    async def main():
        _box, rc = _render(s)
        try:
            _open_split(rc)
            _button(rc, t("evaluators.split")).fire_event("click", None)
            await _settle(lambda: calls, "the split")
            (btn,) = [b for b in rc.find(v.Btn).widgets if "defo-split-open" in (b.class_ or "")]
            assert btn.loading
        finally:
            release.set()
            rc.close()

    asyncio.run(main())


def test_generate_spins_while_the_aoi_is_fetched(data_root, monkeypatch):
    calls, release = [], threading.Event()
    monkeypatch.setattr(es.share, "aoi_geojson_for_package", _blocking(calls, release, FC))
    monkeypatch.setattr(es.share, "build_package",
                        lambda p_, pdir, set_id, evaluator, aoi, out_dir, *, app_version:
                        _package_zip(p_, set_id, evaluator, out_dir))
    s = _state(_project(split=_split()), _gdf())

    async def main():
        _box, rc = _render(s)
        try:
            assert not _generate_btn(rc).loading
            _generate_btn(rc).fire_event("click", None)
            await _settle(lambda: calls, "the AOI fetch")
            await asyncio.sleep(0.05)
            assert _generate_btn(rc).loading and t("evaluators.generate") in _strings(_generate_btn(rc))
            release.set()
            await _settle(lambda: tn("evaluators.packages_ready", 2) in _toasts("success"), "the run's end")
            assert not _generate_btn(rc).loading
        finally:
            release.set()
            rc.close()

    asyncio.run(main())


def test_a_second_split_click_while_one_runs_is_dropped(data_root, monkeypatch):
    calls, release = [], threading.Event()
    monkeypatch.setattr(es.split_service, "apply_split", _blocking(calls, release, lambda st, m, names, inc: _split()))
    s = _state(_project(), _gdf(evaluators=("",) * 4))

    async def main():
        _box, rc = _render(s)
        try:
            _open_split(rc)
            _button(rc, t("evaluators.split")).fire_event("click", None)
            await _settle(lambda: calls, "the split")
            _open_split(rc)  # Solara still calls on_click on a button it rendered disabled
            _button(rc, t("evaluators.split")).fire_event("click", None)
            await asyncio.sleep(0.1)
            assert len(calls) == 1
        finally:
            release.set()
            rc.close()

    asyncio.run(main())


def test_a_second_generate_click_while_one_runs_is_dropped(data_root, monkeypatch):
    calls, release = [], threading.Event()

    def _made(p_, pdir, set_id, evaluator, aoi, out_dir, *, app_version=None):
        return _package_zip(p_, set_id, evaluator, out_dir)

    build = _blocking(calls, release, _made)
    monkeypatch.setattr(es.share, "build_package", lambda *a, app_version: build(*a))
    s = _state(_project(split=_split()), _gdf())

    async def main():
        _box, rc = _render(s)
        try:
            _generate_btn(rc).fire_event("click", None)
            await _settle(lambda: calls, "Ana's build")
            assert t("evaluators.generating", i=1, n=2) in _strings(_generate_btn(rc))
            assert rc.find(v.ProgressCircular).widgets  # Ana's row spins
            _generate_btn(rc).fire_event("click", None)  # while it runs
            release.set()
            await _settle(lambda: tn("evaluators.packages_ready", 2) in _toasts("success"), "the run's end")
            assert [c[3] for c in calls] == ["Ana", "Bo"]
        finally:
            release.set()
            rc.close()

    asyncio.run(main())


def test_a_split_replaced_while_generating_is_not_reported_recorded(data_root, monkeypatch):
    s = _state(_project(split=_split()), _gdf())

    def _build(p_, pdir, set_id, evaluator, aoi, out_dir, *, app_version):
        # The split is replaced while the zips are made.
        _record_split(s, EvaluatorSplit(split_id="s9", evaluators=["Ana", "Bo"], created_at=datetime.now()))
        return _package_zip(p_, set_id, evaluator, out_dir)

    monkeypatch.setattr(es.share, "build_package", _build)

    async def main():
        _box, rc = _render(s)
        try:
            _generate_btn(rc).fire_event("click", None)
            await _settle(lambda: t("evaluators.packages_unrecorded") in _toasts("warning"), "the warning")
            assert not _toasts("success")
            assert not s.project.value.alert_sets[SET_ID].split.packaged
            assert [c.tag for c in _clouds(rc)] == ["span", "span"]  # built for s1, the table shows s9
        finally:
            rc.close()

    asyncio.run(main())


def test_switching_project_while_generating_shows_no_build_in_the_other(data_root, monkeypatch):
    """The panel is not remounted on a project switch: a run started in "c"
    must not show as building in "d", whose split has the same names."""
    calls, release = [], threading.Event()

    def _made(p_, pdir, set_id, evaluator, aoi, out_dir, *, app_version=None):
        return _package_zip(p_, set_id, evaluator, out_dir)

    build = _blocking(calls, release, _made)
    monkeypatch.setattr(es.share, "build_package", lambda *a, app_version: build(*a))
    s = _state(_project(split=_split()), _gdf())

    async def main():
        _box, rc = _render(s)
        try:
            _generate_btn(rc).fire_event("click", None)
            await _settle(lambda: calls, "Ana's build in c")
            s.load_project_state(_project("d", split=_split()))
            s.alerts_gdf.set(_gdf())
            await asyncio.sleep(0.05)
            label = _strings(_generate_btn(rc))
            assert not [x for x in label if x in (t("evaluators.generating", i=1, n=2),
                                                  t("evaluators.generating", i=2, n=2))]
            assert not rc.find(v.ProgressCircular).widgets
            release.set()
            await _settle(lambda: t("evaluators.packages_unrecorded") in _toasts("warning"), "c's run ends")
            assert not _toasts("success")
            assert not rc.find(v.ProgressCircular).widgets
        finally:
            release.set()
            rc.close()

    asyncio.run(main())


def test_a_return_build_shows_only_in_the_copy_it_was_started_for(data_root, monkeypatch):
    calls, release = [], threading.Event()
    build = _blocking(calls, release, lambda p, pdir, out_dir: _return_zip(p, out_dir))
    monkeypatch.setattr(es.share, "build_return", lambda *a, app_version: build(*a))

    def _copy(name, evaluator, index):
        rc_ = ReviewCopy(evaluator=evaluator, evaluator_index=index, set_id=SET_ID, split_id="s1",
                         set_fingerprint="fp", origin_project="c", imported_at=datetime.now())
        return _project(name, split=_split(), review_copy=rc_)

    s = _state(_copy("c__e01-ana", "Ana", 1), _gdf())

    async def main():
        _box, rc = _render(s)
        try:
            _generate_return_btn(rc).fire_event("click", None)
            await _settle(lambda: calls, "Ana's reviewed copy build")
            assert rc.find(v.ProgressCircular).widgets  # the cloud spins in Ana's copy
            s.load_project_state(_copy("c__e02-bo", "Bo", 2))
            s.alerts_gdf.set(_gdf())
            await asyncio.sleep(0.05)
            assert not rc.find(v.ProgressCircular).widgets
            release.set()
            await _settle(lambda: not _generate_return_btn(rc).disabled, "Ana's build ends")
            await asyncio.sleep(0.05)
            assert t("evaluators.return_ready") not in _toasts("success")
        finally:
            release.set()
            rc.close()

    asyncio.run(main())


def test_zips_on_disk_warn_before_a_resplit_even_if_not_recorded(data_root):
    """A run that ended after a project switch built zips that the split
    does not record as packaged: they may have been sent, so warn."""
    p = _project(split=_split())
    assert not p.alert_sets[SET_ID].split.packaged
    _package_zip(p, SET_ID, "Ana", _set_folder())
    _box, rc = _render(_state(p, _gdf()))
    try:
        _open_split(rc)
        assert t("evaluators.packaged_warning") in _strings(rc.container)
    finally:
        rc.close()


def test_the_split_form_starts_afresh_in_another_project(data_root):
    s = _state(_project(), _gdf(evaluators=("",) * 4))
    _box, rc = _render(s)
    try:
        _open_split(rc)
        _names_mode(rc, "Ana", "Bo")
        s.load_project_state(_project("d"))
        s.alerts_gdf.set(_gdf(evaluators=("",) * 4))
        assert t("evaluators.split_title") not in _strings(rc.container)  # the modal closed
        _open_split(rc)
        assert not _name_rows(rc)
        assert rc.find(v.TextField, label=t("evaluators.count_label")).widget.v_model == "2"
    finally:
        rc.close()


def test_the_split_modal_keeps_what_was_typed_when_reopened(data_root):
    _box, rc = _render(_state(_project(), _gdf(evaluators=("",) * 4)))
    try:
        _open_split(rc)
        rc.find(v.TextField, label=t("evaluators.count_label")).widget.v_model = "3"
        _button(rc, t("common.cancel")).fire_event("click", None)
        _open_split(rc)
        assert rc.find(v.TextField, label=t("evaluators.count_label")).widget.v_model == "3"
    finally:
        rc.close()


def test_cancelling_the_merge_drops_the_preview(data_root, monkeypatch):
    monkeypatch.setattr(es.share, "read_return", lambda path, p, store: _preview())
    s = _state(_project(split=_split()), _gdf())
    zip_path = data_root / "bo__reviewed.zip"
    zip_path.write_bytes(b"PK")

    async def main():
        _box, rc = _render(s)
        try:
            _open_merge(rc)
            rc.find(FileInput).widget.v_model = str(zip_path)
            await _settle(lambda: t("evaluators.merge") in _strings(rc.container), "the preview")
            _button(rc, t("common.cancel")).fire_event("click", None)
            assert t("evaluators.merge") not in _strings(rc.container)
            _open_merge(rc)  # a fresh pick, not the dropped preview
            assert rc.find(FileInput).widgets and t("evaluators.merge") not in _strings(rc.container)
        finally:
            rc.close()

    asyncio.run(main())


def test_cancelling_the_merge_while_a_copy_is_read_drops_it(data_root, monkeypatch):
    calls, release = [], threading.Event()
    monkeypatch.setattr(es.share, "read_return", _blocking(calls, release, lambda path, p, store: _preview()))
    s = _state(_project(split=_split()), _gdf())
    zip_path = data_root / "bo__reviewed.zip"
    zip_path.write_bytes(b"PK")

    async def main():
        _box, rc = _render(s)
        try:
            _open_merge(rc)
            rc.find(FileInput).widget.v_model = str(zip_path)
            await _settle(lambda: calls, "the read")
            _button(rc, t("common.cancel")).fire_event("click", None)
            release.set()
            await asyncio.sleep(0.2)  # the read's worker returns
            _open_merge(rc)
            assert rc.find(FileInput).widgets and t("evaluators.merge") not in _strings(rc.container)
        finally:
            release.set()
            rc.close()

    asyncio.run(main())


def test_without_a_split_there_is_nothing_to_merge(data_root):
    _box, rc = _render(_state(_project(), _gdf(evaluators=("",) * 4)))
    try:
        assert t("evaluators.merge_open") not in _strings(rc.container)
        assert not rc.find(FileInput).widgets
    finally:
        rc.close()


def test_the_step_is_named_evaluation():
    set_locale("en")
    assert t("app.drawer_item.evaluators") == "Evaluation"
    assert t("steps.evaluators.info_header") == "Evaluation"


def test_the_return_panel_offers_only_this_copys_reviewed_copy(data_root, monkeypatch):
    monkeypatch.setattr(es.share, "build_return", lambda p, pdir, out_dir, *, app_version: _return_zip(p, out_dir))

    def _copy(name, evaluator, index):
        rc_ = ReviewCopy(evaluator=evaluator, evaluator_index=index, set_id=SET_ID, split_id="s1",
                         set_fingerprint="fp", origin_project="c", imported_at=datetime.now())
        return _project(name, split=_split(), review_copy=rc_)

    s = _state(_copy("c__e01-ana", "Ana", 1), _gdf())

    async def main():
        _box, rc = _render(s)
        try:
            _generate_return_btn(rc).fire_event("click", None)
            await _settle(lambda: _clouds(rc)[0].tag == "a", "Ana's reviewed copy")
            s.load_project_state(_copy("c__e02-bo", "Bo", 2))
            s.alerts_gdf.set(_gdf())
            await asyncio.sleep(0.05)
            assert _clouds(rc)[0].tag == "span"  # Ana's zip is not offered in Bo's copy
        finally:
            rc.close()

    asyncio.run(main())


def _walk(w):
    yield w
    for c in getattr(w, "children", None) or []:
        if not isinstance(c, str):
            yield from _walk(c)


def test_the_return_panels_title_and_note_are_blocks(data_root):
    """Found in the browser walk: solara.Text is an inline span, so the title,
    progress and note ran together on one line."""
    copy = ReviewCopy(evaluator="Bo", evaluator_index=2, set_id=SET_ID, split_id="s1", set_fingerprint="fp",
                      origin_project="c", imported_at=datetime.now())
    s = _state(_project("c__e02-bo", split=_split(), review_copy=copy), _gdf(evaluators=("Ana", "Bo", "Ana", "Bo")))
    _box, rc = _render(s)
    try:
        for text in (t("evaluators.return_title"), t("evaluators.return_note")):
            line = next(w for w in _walk(rc.container)
                        if text in [c for c in getattr(w, "children", []) if isinstance(c, str)])
            assert "display:block" in line.style_.replace(" ", ""), text
    finally:
        rc.close()


# ---------------------------------------------------------------------------
# Review nearby alerts together (spec 2026-10-26 §3.2, §3.3)
# ---------------------------------------------------------------------------
def _group_box(rc):
    return rc.find(v.Checkbox, class_="defo-group-option")


def _group_radius(rc):
    return rc.find(v.TextField, class_="defo-group-radius")


def _group_include(rc):
    return rc.find(v.Checkbox, class_="defo-group-include")


def _group_note(rc):
    return rc.find(v.Html, class_="defo-group-note").widget.children[0]


def _grouped(p, *, radius=500.0, include=False):
    p.group_review = models.GroupReview(enabled=True, radius_m=radius, include_reviewed=include)
    return p


@pytest.mark.parametrize("multi", [None, False, True])
def test_the_group_option_is_off_by_default_and_saves_when_ticked(data_root, multi):
    s = _state(_project(multi=multi), _gdf(evaluators=("",) * 4))
    _box, rc = _render(s)
    try:
        assert _group_box(rc).widget.v_model is False and not _group_box(rc).widget.disabled
        labels = [_strings(w) for w in rc.find(v.Html, class_="defo-group-label").widgets]
        assert labels == [["mdi-vector-union", t("evaluators.group_label")]]
        assert not _group_radius(rc).widgets and not _group_include(rc).widgets
        assert _group_note(rc) == t("evaluators.group_note")
        _group_box(rc).widget.v_model = True
        assert s.project.value.group_review.enabled is True
        assert _group_radius(rc).widget.v_model == "500" and _group_radius(rc).widget.suffix == "m"
        assert _group_include(rc).widget.v_model is False
        assert t("evaluators.group_include_reviewed_hint") in _strings(rc.container)
        _group_include(rc).widget.v_model = True
        assert s.project.value.group_review.include_reviewed is True
        _group_box(rc).widget.v_model = False
        assert s.project.value.group_review.enabled is False
    finally:
        rc.close()


def test_a_click_on_the_group_text_toggles_its_box(data_root):
    s = _state(_project(multi=False), _gdf(evaluators=("",) * 4))
    _box, rc = _render(s)
    try:
        rc.find(v.Html, class_="defo-group-label").widget.fire_event("click", None)
        assert s.project.value.group_review.enabled is True
    finally:
        rc.close()


def test_a_bad_distance_shows_an_error_and_keeps_the_last_value(data_root):
    s = _state(_grouped(_project(multi=False)), _gdf(evaluators=("",) * 4))
    _box, rc = _render(s)
    try:
        _group_radius(rc).widget.v_model = "5001"
        assert _group_radius(rc).widget.error_messages == [t("evaluators.group_radius_error")]
        assert _group_radius(rc).widget.v_model == "5001"  # what was typed stays to be fixed
        assert s.project.value.group_review.radius_m == 500.0
        _group_radius(rc).widget.v_model = "300"
        assert s.project.value.group_review.radius_m == 300.0
        assert not _group_radius(rc).widget.error_messages
    finally:
        rc.close()


def test_saved_groups_lock_the_option_on(data_root):
    p = _grouped(_project(multi=False))
    p.alert_sets[SET_ID].n_groups = 2
    _box, rc = _render(_state(p, _gdf(evaluators=("",) * 4)))
    try:
        assert _group_box(rc).widget.v_model is True and _group_box(rc).widget.disabled is True
        assert _group_radius(rc).widget.disabled is True
        assert not _group_include(rc).widget.disabled
        assert _group_note(rc) == tn("evaluators.group_locked_groups", 2)
    finally:
        rc.close()


def test_saved_groups_show_the_option_ticked_when_it_was_left_off(data_root):
    """Review I1: groups saved while ``enabled`` is False (another set's
    merge) still hold the option on, ticked, with its distance."""
    p = _project(multi=False)
    p.alert_sets[SET_ID].n_groups = 2
    _box, rc = _render(_state(p, _gdf(evaluators=("",) * 4)))
    try:
        assert _group_box(rc).widget.v_model is True and _group_box(rc).widget.disabled is True
        assert _group_radius(rc).widget.v_model == "500" and _group_radius(rc).widget.disabled is True
    finally:
        rc.close()


def test_saved_groups_refuse_storing_the_option_off(data_root):
    p = _grouped(_project(multi=False))
    p.alert_sets[SET_ID].n_groups = 2
    s = _state(p, _gdf(evaluators=("",) * 4))
    _box, rc = _render(s)
    try:
        _group_box(rc).widget.v_model = False
        assert s.project.value.group_review.enabled is True
        _group_include(rc).widget.v_model = True  # the free control still saves
        assert s.project.value.group_review.include_reviewed is True
    finally:
        rc.close()


@pytest.mark.parametrize("radius, note", [(None, "evaluators.group_locked_split_off"),
                                          (250.0, "evaluators.group_locked_split_on")])
def test_a_split_locks_the_option_distance_and_include(data_root, radius, note):
    made = _split().model_copy(update={"group_radius_m": radius, "group_include_reviewed": radius is not None})
    _box, rc = _render(_state(_grouped(_project(split=made)), _gdf()))
    try:
        assert _group_box(rc).widget.disabled is True
        assert _group_note(rc) == t(note)
        if radius is None:  # the split's record, not the project's setting
            assert _group_box(rc).widget.v_model is False and not _group_radius(rc).widgets
        else:
            assert _group_box(rc).widget.v_model is True
            assert _group_radius(rc).widget.v_model == "250" and _group_radius(rc).widget.disabled is True
            assert _group_include(rc).widget.v_model is True and _group_include(rc).widget.disabled is True
    finally:
        rc.close()


def test_a_review_copy_shows_the_setting_read_only(data_root):
    copy = ReviewCopy(evaluator="Bo", evaluator_index=2, set_id=SET_ID, split_id="s1", set_fingerprint="fp",
                      origin_project="c", imported_at=datetime.now())
    made = _split().model_copy(update={"group_radius_m": 750.0})
    p = _project("c__e02-bo", split=made, review_copy=copy, multi=False)
    s = _state(p, _gdf())
    _box, rc = _render(s)
    try:
        assert t("evaluators.return_title") in _strings(rc.container)
        assert _group_box(rc).widget.v_model is True and _group_box(rc).widget.disabled is True
        assert _group_radius(rc).widget.v_model == "750" and _group_radius(rc).widget.disabled is True
        assert _group_include(rc).widget.v_model is False and _group_include(rc).widget.disabled is True
        assert _group_note(rc) == t("evaluators.group_copy_note")
        rc.find(v.Html, class_="defo-group-label").widget.fire_event("click", None)
        assert s.project.value.group_review.enabled is False
    finally:
        rc.close()
