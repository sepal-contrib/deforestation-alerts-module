"""Every review Save stamps ``reviewed_by`` / ``reviewed_at``; a reset clears them;
a merge does not compare them."""

import pandas as pd

from deforestation_alerts.project import review_groups
from deforestation_alerts.project.share import read_return
from gui.tile.analysis import review_save
from tests.test_analysis_group_save import _save, _setup, _stored
from tests.test_review_return_merge import _creator, _return, _store

NOW = "2026-11-11T10:00:00Z"


def _stamped(tmp_path, monkeypatch, reviewer, **kw):
    monkeypatch.setattr(review_save, "stamp_now", lambda: NOW)
    h = _setup(tmp_path, monkeypatch, **kw)
    h.s.reviewer = reviewer
    return h


def test_save_writes_reviewer_and_time(tmp_path, monkeypatch):
    h = _stamped(tmp_path, monkeypatch, "ana")
    _save(h, 0)
    row = _stored(h).iloc[0]
    assert (row["reviewed_by"], row["reviewed_at"]) == ("ana", NOW)


def test_group_save_stamps_every_row(tmp_path, monkeypatch):
    h = _stamped(tmp_path, monkeypatch, "ana", grouping=True)
    _save(h, 0, {1, 2})
    stored = _stored(h)
    assert stored["reviewed_by"].tolist()[:3] == ["ana"] * 3
    assert stored["reviewed_at"].tolist()[:3] == [NOW] * 3


def test_unknown_reviewer_still_saves(tmp_path, monkeypatch):
    h = _stamped(tmp_path, monkeypatch, "")
    wrote, _ = _save(h, 0)
    assert wrote is True and _stored(h).iloc[0]["reviewed_by"] == ""


def test_reset_clears_stamps(tmp_path, monkeypatch):
    h = _stamped(tmp_path, monkeypatch, "ana")
    _save(h, 0)
    values = review_groups.reset_values(_stored(h), 0)
    assert values["reviewed_by"] == "" and values["reviewed_at"] == ""


def test_return_differing_only_in_stamps_is_unchanged(tmp_path, monkeypatch):
    stamp = lambda by, at: {"reviewed_by": [by] * 4, "reviewed_at": [at] * 4, "admin1": ["A"] * 4}  # noqa: E731
    p, _ = _creator(tmp_path, monkeypatch, columns=stamp("bo", "2026-01-01T00:00:00Z"))
    zp = _return(tmp_path, columns=stamp("ana", "2026-02-02T00:00:00Z") | {"admin1": ["B"] * 4})
    preview = read_return(zp, p, _store(p))
    assert 1 in preview.unchanged and pd.Series(preview.columns).isin(["reviewed_at"]).any()
