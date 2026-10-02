# tests/test_scene_query.py
from datetime import date, datetime, timedelta, timezone

import pytest

from deforestation_alerts.scripts import scene_index, scene_query

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
WANT = (date(2026, 3, 3), date(2026, 10, 23))


def _manifest(start, end, *, age_hours=1.0, digest="abc", version=scene_index.INDEX_VERSION):
    return scene_index.SceneManifest(
        version=version,
        aoi_digest=digest,
        refreshed=NOW - timedelta(hours=age_hours),
        covered=scene_index.Coverage(start=start, end=end),
        counts={},
    )


def test_no_manifest_means_rebuild():
    assert scene_query.needs_refresh(None, WANT, digest="abc", now=NOW) == "rebuild"


def test_a_different_aoi_means_rebuild():
    m = _manifest(*WANT, digest="other")
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == "rebuild"


def test_an_older_index_version_means_rebuild():
    m = _manifest(*WANT, version=scene_index.INDEX_VERSION - 1)
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == "rebuild"


def test_a_fresh_covering_index_needs_nothing():
    m = _manifest(date(2026, 1, 1), date(2026, 12, 31))
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == ""


def test_a_window_past_the_covered_end_is_a_widen_not_a_rebuild():
    m = _manifest(date(2026, 1, 1), date(2026, 9, 1))
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == "widen"


def test_coverage_beats_age_when_both_apply():
    m = _manifest(date(2026, 1, 1), date(2026, 9, 1), age_hours=40)
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == "widen"


def test_a_covering_index_older_than_the_ttl_needs_its_tail():
    m = _manifest(date(2026, 1, 1), date(2026, 12, 31), age_hours=24.5)
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == "tail"


def test_exactly_at_the_ttl_is_not_yet_stale():
    m = _manifest(date(2026, 1, 1), date(2026, 12, 31), age_hours=24.0)
    assert scene_query.needs_refresh(m, WANT, digest="abc", now=NOW) == ""


def test_a_rebuild_fetches_the_whole_wanted_window():
    assert scene_query.refresh_window(None, WANT, "rebuild") == WANT


def test_a_tail_refetch_starts_ten_days_before_the_covered_end():
    m = _manifest(date(2026, 1, 1), date(2026, 9, 20))
    assert scene_query.refresh_window(m, WANT, "tail") == (date(2026, 9, 10), date(2026, 10, 23))


def test_a_widen_takes_the_later_side_first():
    m = _manifest(date(2026, 5, 1), date(2026, 9, 1))
    assert scene_query.refresh_window(m, WANT, "widen") == (date(2026, 9, 1), date(2026, 10, 23))


def test_a_widen_takes_the_earlier_side_once_the_later_one_is_covered():
    m = _manifest(date(2026, 5, 1), date(2026, 11, 1))
    assert scene_query.refresh_window(m, WANT, "widen") == (date(2026, 3, 3), date(2026, 5, 1))


def test_nothing_to_fetch_raises_rather_than_returning_an_empty_range():
    m = _manifest(date(2026, 1, 1), date(2026, 12, 31))
    with pytest.raises(ValueError):
        scene_query.refresh_window(m, WANT, "")


def test_scene_query_never_imports_earth_engine():
    src = open(scene_query.__file__).read()
    assert "import ee" not in src and "from ee" not in src
