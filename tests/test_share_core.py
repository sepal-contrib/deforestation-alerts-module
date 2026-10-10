"""share.py core (spec 2026-10-10 §5.1, §7.2, §7.3, §9): the error, the
header, the checked zip reader, the file names, the review columns and value
equality. Real zips in tmp_path."""

import ast
import json
import stat
import warnings
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

from deforestation_alerts.project import share
from deforestation_alerts.project.models import Question, default_questions
from deforestation_alerts.project.share import (
    FORMAT,
    FORMAT_VERSION,
    KIND_PACKAGE,
    KIND_RETURN,
    PACKAGE_OPTIONAL,
    PACKAGE_REQUIRED,
    RETURN_REQUIRED,
    ShareError,
    ShareHeader,
    evaluator_token,
    open_checked,
    package_filename,
    peek_header,
    read_header,
    return_filename,
    review_columns,
    same_value,
)

ROOT = Path(__file__).resolve().parents[1]
SID = "2026-01-01_2026-03-31__glad-l"


def _header(**over) -> dict:
    h = {"format": FORMAT, "format_version": FORMAT_VERSION, "kind": KIND_PACKAGE, "app_version": "1.1.1",
         "origin_project": "Creator proj", "set_id": SID, "set_fingerprint": "fp", "split_id": "s" * 32,
         "evaluator": "Ana", "evaluator_index": 1, "questions_digest": "d" * 64,
         "created_at": "2026-10-10T12:00:00"}
    h.update(over)
    return h


def _package_members(**header_over) -> dict:
    return {"share.json": json.dumps(_header(**header_over)), "project.json": "{}", "aoi.geojson": "{}",
            "alerts/alert_db.parquet": b"PAR1"}


def _zip(path, members: dict) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def _refusal(path, required=PACKAGE_REQUIRED, optional=PACKAGE_OPTIONAL) -> ShareError:
    with pytest.raises(ShareError) as e:
        with open_checked(path, required, optional):
            pass
    return e.value


# ---- constants and the error ------------------------------------------------

def test_the_allow_lists_are_the_spec_members():
    assert PACKAGE_REQUIRED == {"share.json", "project.json", "aoi.geojson", "alerts/alert_db.parquet"}
    assert PACKAGE_OPTIONAL == {"imagery/scenes.parquet", "imagery/tiles.parquet", "imagery/index.json",
                                "template/report.docx"}
    assert RETURN_REQUIRED == {"share.json", "reviews.parquet"}
    assert share.HEADER_MEMBER == "share.json"


def test_a_share_error_names_its_message_and_keeps_its_values():
    from gui.scripts.error_text import error_text

    exc = ShareError("share.err.missing_member", member="aoi.geojson")
    assert exc.key == "share.err.missing_member" and exc.params == {"member": "aoi.geojson"}
    assert isinstance(exc, ValueError) and str(exc) == "share.err.missing_member"
    assert error_text(exc) == "This zip is missing aoi.geojson."


def test_share_imports_nothing_from_the_gui():
    tree = ast.parse((ROOT / "deforestation_alerts/project/share.py").read_text(encoding="utf-8"))
    names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    names += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    heads = {n.split(".")[0] for n in names}
    assert not heads & {"gui", "solara", "reacton", "ipyvuetify", "pysepal", "ee"}


# ---- open_checked -------------------------------------------------------------

def test_open_checked_yields_the_zip_when_every_member_is_allowed(tmp_path):
    path = _zip(tmp_path / "p.zip", {**_package_members(), "template/report.docx": b"x"})
    with open_checked(path, PACKAGE_REQUIRED, PACKAGE_OPTIONAL) as zf:
        assert set(zf.namelist()) == PACKAGE_REQUIRED | {"template/report.docx"}
        assert read_header(zf).evaluator == "Ana"


@pytest.mark.parametrize("content", [b"not a zip at all", b""])
def test_a_file_that_is_not_a_zip_is_refused(tmp_path, content):
    path = tmp_path / "x.zip"
    path.write_bytes(content)
    assert _refusal(path).key == "share.err.not_a_zip"
    with pytest.raises(ShareError, match="share.err.not_a_zip"):
        peek_header(path)


def test_a_missing_file_is_refused_as_not_a_zip(tmp_path):
    assert _refusal(tmp_path / "gone.zip").key == "share.err.not_a_zip"


def test_a_member_outside_the_allow_list_is_refused(tmp_path):
    exc = _refusal(_zip(tmp_path / "p.zip", {**_package_members(), "notes.txt": "hi"}))
    assert (exc.key, exc.params) == ("share.err.unexpected_member", {"member": "notes.txt"})


def test_a_directory_entry_is_refused(tmp_path):
    exc = _refusal(_zip(tmp_path / "p.zip", {**_package_members(), "alerts/": ""}))
    assert (exc.key, exc.params) == ("share.err.unexpected_member", {"member": "alerts/"})


def test_a_package_member_in_a_return_is_refused(tmp_path):
    path = _zip(tmp_path / "r.zip", {"share.json": json.dumps(_header(kind=KIND_RETURN)),
                                     "reviews.parquet": b"x", "project.json": "{}"})
    exc = _refusal(path, RETURN_REQUIRED, frozenset())
    assert (exc.key, exc.params) == ("share.err.unexpected_member", {"member": "project.json"})


def test_a_missing_required_member_is_refused(tmp_path):
    members = _package_members()
    del members["aoi.geojson"]
    exc = _refusal(_zip(tmp_path / "p.zip", members))
    assert (exc.key, exc.params) == ("share.err.missing_member", {"member": "aoi.geojson"})


@pytest.mark.parametrize("name", ["/share.json", "../share.json", "alerts/../../x", "alerts\\alert_db.parquet",
                                  "C:/x"])
def test_unsafe_member_names_are_refused(tmp_path, name):
    exc = _refusal(_zip(tmp_path / "p.zip", {**_package_members(), name: b"x"}))
    assert (exc.key, exc.params) == ("share.err.unsafe_member", {"member": name})


def test_a_duplicate_member_is_refused(tmp_path):
    path = tmp_path / "p.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile warns "Duplicate name"
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in _package_members().items():
                zf.writestr(name, data)
            zf.writestr("project.json", "{}")
    exc = _refusal(path)
    assert (exc.key, exc.params) == ("share.err.unsafe_member", {"member": "project.json"})


def test_a_symlink_member_is_refused(tmp_path):
    path = tmp_path / "p.zip"
    members = _package_members()
    del members["aoi.geojson"]
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
        link = zipfile.ZipInfo("aoi.geojson")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "/etc/passwd")
    exc = _refusal(path)
    assert (exc.key, exc.params) == ("share.err.unsafe_member", {"member": "aoi.geojson"})


def test_a_member_larger_than_the_cap_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(share, "MAX_MEMBER_BYTES", 10)
    exc = _refusal(_zip(tmp_path / "p.zip", {**_package_members(), "template/report.docx": b"x" * 11}))
    assert exc.key == "share.err.too_large"


def test_members_larger_than_the_total_cap_together_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(share, "MAX_TOTAL_BYTES", 600)
    exc = _refusal(_zip(tmp_path / "p.zip", {**_package_members(), "template/report.docx": b"x" * 400}))
    assert exc.key == "share.err.too_large"


# ---- the header -----------------------------------------------------------------

def test_the_header_round_trips_through_a_zip(tmp_path):
    h = ShareHeader.model_validate(_header(kind=KIND_RETURN, returned_at="2026-10-11T08:00:00"))
    path = _zip(tmp_path / "r.zip", {"share.json": h.model_dump_json(), "reviews.parquet": b"x"})
    back = peek_header(path)
    assert back == h
    assert back.created_at == datetime(2026, 10, 10, 12, 0) and back.returned_at == datetime(2026, 10, 11, 8, 0)


def test_a_package_header_has_no_return_time(tmp_path):
    assert peek_header(_zip(tmp_path / "p.zip", _package_members())).returned_at is None


@pytest.mark.parametrize("header, key", [
    ({**_header(), "format": "something-else"}, "share.err.wrong_format"),
    ([1, 2], "share.err.wrong_format"),
    ({**_header(), "format_version": 2}, "share.err.newer_format"),
    ({k: v for k, v in _header().items() if k != "split_id"}, "share.err.bad_header"),
    ({**_header(), "evaluator_index": "first"}, "share.err.bad_header"),
])
def test_a_bad_header_is_refused(tmp_path, header, key):
    path = _zip(tmp_path / "p.zip", {**_package_members(), "share.json": json.dumps(header)})
    with pytest.raises(ShareError) as e:
        peek_header(path)
    assert e.value.key == key


def test_a_newer_format_names_both_versions(tmp_path):
    path = _zip(tmp_path / "p.zip", _package_members(format_version=2))
    with pytest.raises(ShareError) as e:
        peek_header(path)
    assert e.value.params == {"found": 2, "expected": 1}


def test_a_header_that_is_not_json_is_refused(tmp_path):
    path = _zip(tmp_path / "p.zip", {**_package_members(), "share.json": "{nope"})
    with pytest.raises(ShareError) as e:
        peek_header(path)
    assert e.value.key == "share.err.bad_header"


def test_an_oversized_header_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(share, "MAX_HEADER_BYTES", 10)
    with pytest.raises(ShareError) as e:
        peek_header(_zip(tmp_path / "p.zip", _package_members()))
    assert e.value.key == "share.err.bad_header"


def test_a_zip_without_a_header_reads_as_no_header(tmp_path):
    with pytest.raises(ShareError) as e:
        peek_header(_zip(tmp_path / "x.zip", {"readme.txt": "hi"}))
    assert e.value.key == "share.err.no_header"


# ---- file names -------------------------------------------------------------------

def test_the_evaluator_token_carries_the_index():
    assert evaluator_token(1, "Ana") == "e01-ana"
    assert evaluator_token(12, "Ana Maria") == "e12-ana-maria"
    assert evaluator_token(3, "李") == "e03-na"  # no Latin letters: the index alone tells copies apart


def test_names_with_colliding_tokens_get_distinct_file_names():
    a = package_filename("Creator proj", SID, 1, "Ana Maria")
    b = package_filename("Creator proj", SID, 2, "Ana-Maria")
    assert a == f"creator-proj__{SID}__e01-ana-maria__review.zip"
    assert b == f"creator-proj__{SID}__e02-ana-maria__review.zip"


def test_the_return_file_name():
    assert return_filename("Creator proj", SID, 2, "Bo") == f"creator-proj__{SID}__e02-bo__reviewed.zip"


# ---- review columns -----------------------------------------------------------------

def test_review_columns_follow_the_questions():
    qs = [*default_questions(), Question(key="cause", label="Cause"), Question(key="cause", label="Again")]
    assert review_columns(qs) == ["status", "description", "q_cause", "before_img", "before_img_info", "after_img",
                                  "after_img_info", "admin1", "admin2", "admin3", "alert_polygon", "area_ha"]
    assert review_columns([]) == ["status", "before_img", "before_img_info", "after_img", "after_img_info",
                                  "admin1", "admin2", "admin3", "alert_polygon", "area_ha"]


# ---- value equality ------------------------------------------------------------------

@pytest.mark.parametrize("a, b", [
    (None, np.nan), (None, ""), (pd.NA, None), ("", np.nan),
    (1.0, 1.0 + 1e-12), (2, 2.0), (np.int64(3), 3),
    ("Confirmed", "Confirmed"),
    (box(0, 0, 1, 1), box(0, 0, 1, 1)),
])
def test_values_that_are_the_same(a, b):
    assert same_value(a, b) and same_value(b, a)


@pytest.mark.parametrize("a, b", [
    (None, "x"), ("", 0), (1.0, 1.001), ("Confirmed", "False Positive"),
    (box(0, 0, 1, 1), box(0, 0, 2, 2)), (box(0, 0, 1, 1), None), (Point(0, 0), "POINT (0 0)"),
])
def test_values_that_differ(a, b):
    assert not same_value(a, b) and not same_value(b, a)
