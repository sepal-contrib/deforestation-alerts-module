"""Built review zips found on disk (spec 2026-10-14 §4.4, §4.6): a zip counts
when its share.json names the current split (and evaluator); one built
before the questionnaire changed is outdated."""

import zipfile
from datetime import date, datetime

from deforestation_alerts.project import models, paths, share
from deforestation_alerts.project import split as split_mod
from deforestation_alerts.project.models import (
    AlertSet,
    AlertSetParams,
    EvaluatorSplit,
    Period,
    Project,
    ReviewCopy,
    add_predefined_source,
)
from gui.scripts import package_files as pf
from gui.scripts import set_store

START, END = date(2026, 8, 1), date(2026, 8, 31)
PERIOD = models.period_id(START, END)
SET_ID = models.set_id(PERIOD, ["GLAD-L"])


def _project(name="c", *, split_id="s1", review_copy=None):
    now = datetime.now()
    p = Project(name=name, created_at=now, updated_at=now)
    src = add_predefined_source(p, "GLAD-L")
    p.periods = {PERIOD: Period(id=PERIOD, start=START, end=END)}
    p.active_period = PERIOD
    pdir = paths.project_dir(name)
    sdir = paths.set_dir(pdir, PERIOD, ["GLAD-L"])
    split = EvaluatorSplit(split_id=split_id, evaluators=["Ana", "Bo"], created_at=now) if split_id else None
    p.alert_sets = {SET_ID: AlertSet(
        id=SET_ID, period_id=PERIOD, sources=["GLAD-L"], digit_map={src.digit: "GLAD-L"}, params=AlertSetParams(),
        store_rel_path=paths.rel(pdir, paths.store_path(sdir)),
        combined_rel_path=paths.rel(pdir, paths.combined_path(sdir, ["GLAD-L"], START, END)),
        footprint_rel_path=paths.rel(pdir, paths.footprint_path(sdir)),
        n_alerts=4, fingerprint="fp", created_at=now, split=split)}
    p.active_set = SET_ID
    p.review_copy = review_copy
    return p


def _zip(path, *, digest, kind=share.KIND_PACKAGE, split_id="s1", evaluator="Ana", index=1, origin="c"):
    """A zip holding only a share.json: all ``peek_header`` reads."""
    header = share.ShareHeader(format=share.FORMAT, format_version=share.FORMAT_VERSION, kind=kind,
                               app_version="test", origin_project=origin, set_id=SET_ID, set_fingerprint="fp",
                               split_id=split_id, evaluator=evaluator, evaluator_index=index,
                               questions_digest=digest, created_at=datetime.now())
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(share.HEADER_MEMBER, header.model_dump_json())
    return path


def _package_path(p, index, name):
    return set_store.set_folder(p, p.alert_sets[SET_ID]) / share.package_filename(p.name, SET_ID, index, name)


def test_without_a_split_there_is_nothing_to_find(data_root):
    p = _project(split_id=None)
    assert pf.package_zips(p, p.alert_sets[SET_ID]) == {}
    assert pf.package_zips(None, None) == {}


def test_a_package_of_the_current_split_is_found(data_root):
    p = _project()
    path = _zip(_package_path(p, 1, "Ana"), digest=split_mod.questions_digest(p.questions))
    found = pf.package_zips(p, p.alert_sets[SET_ID])
    assert set(found) == {"Ana"}
    assert found["Ana"].path == path and found["Ana"].outdated is False
    assert isinstance(found["Ana"].built_at, datetime)


def test_a_package_of_an_earlier_split_is_not_offered(data_root):
    p = _project()
    _zip(_package_path(p, 1, "Ana"), digest=split_mod.questions_digest(p.questions), split_id="old")
    assert pf.package_zips(p, p.alert_sets[SET_ID]) == {}


def test_a_package_built_before_the_questionnaire_changed_is_outdated(data_root):
    p = _project()
    _zip(_package_path(p, 2, "Bo"), digest="another-digest", evaluator="Bo", index=2)
    assert pf.package_zips(p, p.alert_sets[SET_ID])["Bo"].outdated is True


def test_a_broken_zip_is_not_offered(data_root):
    p = _project()
    path = _package_path(p, 1, "Ana")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PK")
    assert pf.package_zips(p, p.alert_sets[SET_ID]) == {}


def test_a_zip_whose_header_names_another_evaluator_is_skipped(data_root):
    p = _project()
    _zip(_package_path(p, 1, "Ana"), digest=split_mod.questions_digest(p.questions), evaluator="Bo")
    assert pf.package_zips(p, p.alert_sets[SET_ID]) == {}


def _copy():
    rc = ReviewCopy(evaluator="Bo", evaluator_index=2, set_id=SET_ID, split_id="s1", set_fingerprint="fp",
                    origin_project="c", imported_at=datetime.now())
    return _project("c__e02-bo", review_copy=rc)


def test_a_review_copys_reviewed_copy_is_found(data_root):
    p = _copy()
    path = set_store.set_folder(p, p.alert_sets[SET_ID]) / share.return_filename("c", SET_ID, 2, "Bo")
    _zip(path, digest="d", kind=share.KIND_RETURN, evaluator="Bo", index=2)
    found = pf.return_zip(p)
    assert found is not None and found.path == path


def test_no_reviewed_copy_outside_a_copy_or_before_one_is_built(data_root):
    assert pf.return_zip(_project()) is None
    assert pf.return_zip(None) is None
    assert pf.return_zip(_copy()) is None


def test_a_package_in_the_reviewed_copys_place_is_not_offered(data_root):
    p = _copy()
    path = set_store.set_folder(p, p.alert_sets[SET_ID]) / share.return_filename("c", SET_ID, 2, "Bo")
    _zip(path, digest="d", kind=share.KIND_PACKAGE, evaluator="Bo", index=2)
    assert pf.return_zip(p) is None


def test_a_zip_deleted_between_its_header_read_and_its_stat_is_not_offered(data_root, monkeypatch):
    """The scans run during the step's render: a vanished or broken zip is
    skipped, whatever it raises (``FileNotFoundError``, ``lzma.LZMAError``)."""
    p = _project()
    _zip(_package_path(p, 1, "Ana"), digest=split_mod.questions_digest(p.questions))

    def _gone(path):
        raise FileNotFoundError(path)

    monkeypatch.setattr(pf, "_built_at", _gone)
    assert pf.package_zips(p, p.alert_sets[SET_ID]) == {}
    c = _copy()
    path = set_store.set_folder(c, c.alert_sets[SET_ID]) / share.return_filename("c", SET_ID, 2, "Bo")
    _zip(path, digest="d", kind=share.KIND_RETURN, evaluator="Bo", index=2)
    assert pf.return_zip(c) is None
