"""The group-review setting: model defaults, the effective grouping, the locks
and the radius parser (spec 2026-10-26)."""

from datetime import datetime

import pytest

from deforestation_alerts.project.grouping import (
    GroupLock,
    Grouping,
    effective,
    lock_state,
    parse_radius,
)
from deforestation_alerts.project.models import (
    AlertSet,
    AlertSetParams,
    EvaluatorSplit,
    GroupReview,
    Project,
)

NOW = datetime(2026, 1, 1)


def _split(**kw):
    return EvaluatorSplit(split_id="s1", evaluators=["Ana", "Bo"], created_at=NOW, **kw)


def _set(sid="a", split=None, n_groups=0):
    return AlertSet(id=sid, period_id="p", sources=["GLAD-L"], digit_map={1: "GLAD-L"}, params=AlertSetParams(),
                    store_rel_path="s", combined_rel_path="c", footprint_rel_path="f", fingerprint="fp",
                    created_at=NOW, split=split, n_groups=n_groups)


def _project(enabled=False, radius=500.0, include=False, sets=None, active=None):
    p = Project(name="g", created_at=NOW, updated_at=NOW,
                group_review=GroupReview(enabled=enabled, radius_m=radius, include_reviewed=include))
    p.alert_sets = sets or {}
    p.active_set = active
    return p


def _manifest_without(key):
    data = _project().model_dump(mode="json")
    data.pop(key)
    return data


def test_an_old_manifest_reads_grouping_off():
    p = Project.model_validate(_manifest_without("group_review"))
    assert p.group_review == GroupReview() and not p.group_review.enabled
    assert p.group_review.radius_m == 300.0 and p.group_review.include_reviewed is False


def test_effective_follows_the_project_without_a_split():
    p, s = _project(enabled=True, radius=300.0, include=True), _set()
    assert effective(p, s) == Grouping(300.0, True)
    assert effective(_project(enabled=False), s) is None
    assert effective(None, s) is None and effective(p, None) is None


def test_effective_clamps_a_stored_radius():
    assert effective(_project(enabled=True, radius=1.0), _set()).radius_m == 10.0
    assert effective(_project(enabled=True, radius=9e9), _set()).radius_m == 5000.0


def test_effective_follows_the_split_record_once_split():
    p = _project(enabled=True, radius=300.0)
    assert effective(p, _set(split=_split(group_radius_m=None))) is None
    assert effective(_project(enabled=False),
                     _set(split=_split(group_radius_m=800.0, group_include_reviewed=True))) == Grouping(800.0, True)


def test_saved_groups_hold_an_unsplit_set_on_whatever_enabled_says():
    """Spec §3.3: an unsplit set that holds groups is always effective-on,
    also when ``enabled`` was left off (review I1)."""
    sets = {"a": _set("a", n_groups=3), "b": _set("b")}
    p = _project(enabled=False, radius=300.0, include=True, sets=sets, active="b")
    assert effective(p, p.alert_sets["a"]) == Grouping(300.0, True)
    assert effective(p, p.alert_sets["b"]) == Grouping(300.0, True)
    # a split keeps reading its own record
    assert effective(p, _set("c", split=_split(group_radius_m=None))) is None


def test_two_sets_cannot_leave_groups_with_grouping_off():
    """Review I1, steps 1-4: split A with grouping on, turn grouping off on
    B, merge groups into A, remove A's split: A's grouping is on."""
    p = _project(enabled=True, sets={"a": _set("a", split=_split(group_radius_m=500.0)), "b": _set("b")},
                 active="a")
    p.active_set = "b"  # 2. B is free: no set holds groups yet
    assert lock_state(p, p.alert_sets["b"]).option is False
    p.group_review = p.group_review.model_copy(update={"enabled": False})
    p.alert_sets["a"].n_groups = 3  # 3. a format-2 merge into A
    lock = lock_state(p, p.alert_sets["b"])
    assert (lock.option, lock.reason) == (True, "groups")
    assert effective(p, p.alert_sets["b"]) == Grouping(500.0, False)
    p.active_set = "a"
    p.alert_sets["a"].split = None  # 4. Remove split
    assert effective(p, p.alert_sets["a"]) == Grouping(500.0, False)


def test_nothing_is_locked_before_groups_or_a_split():
    assert lock_state(_project(enabled=True), _set()) == GroupLock(False, False, False, None, 0)


def test_saved_groups_in_any_set_lock_the_option_on():
    p = _project(enabled=True, sets={"a": _set("a", n_groups=0), "b": _set("b", n_groups=3)}, active="a")
    lock = lock_state(p, p.alert_sets["a"])
    assert (lock.option, lock.radius, lock.include, lock.reason, lock.n_groups) == (True, True, False, "groups", 3)


def test_a_split_locks_all_three():
    lock = lock_state(_project(enabled=False), _set(split=_split()))
    assert (lock.option, lock.radius, lock.include, lock.reason) == (True, True, True, "split")


@pytest.mark.parametrize("text, value", [("500", 500.0), (" 10 ", 10.0), ("5000", 5000.0), ("12,5", 12.5),
                                         ("9.9", None), ("5001", None), ("-5", None), ("", None), ("abc", None),
                                         ("nan", None), ("inf", None)])
def test_parse_radius_takes_metres_within_range_only(text, value):
    assert parse_radius(text) == value
