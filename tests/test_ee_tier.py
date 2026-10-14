"""The live Earth Engine tier is opt-in (spec §13): see ``tests/conftest.py``."""

import os

import pytest


def _conftest(request):
    return next(p for p in request.config.pluginmanager.get_plugins()
                if str(getattr(p, "__file__", "")).endswith(os.path.join("tests", "conftest.py")))


@pytest.mark.parametrize(("env", "markexpr", "runs"), [
    ({}, "", False),
    ({"DEFO_ALERTS_EE_TESTS": "0"}, "", False),
    ({"DEFO_ALERTS_EE_TESTS": ""}, "", False),
    ({"DEFO_ALERTS_EE_TESTS": "1"}, "", True),
    ({}, "ee", True),
    ({}, "ee and not slow", True),
    ({}, "not ee", False),
    ({}, "free", False),  # a word that only contains "ee"
])
def test_the_ee_tier_runs_only_when_asked(request, env, markexpr, runs):
    assert _conftest(request).ee_tier_selected(env, markexpr) is runs


def test_every_test_on_the_gee_fixture_is_marked_ee_and_skipped_unless_asked(request):
    conftest = _conftest(request)
    run_ee = conftest.ee_tier_selected(os.environ, request.config.getoption("markexpr", ""))
    live = [item for item in request.session.items if "gee" in getattr(item, "fixturenames", ())]
    for item in live:
        assert item.get_closest_marker("ee") is not None, item.nodeid
        skipped = any(m.kwargs.get("reason", "").startswith("live Earth Engine test")
                      for m in item.iter_markers("skip"))
        assert skipped is not run_ee, item.nodeid


def test_an_unmarked_test_cannot_reach_earth_engine():
    import ee.data

    with pytest.raises(RuntimeError, match="not marked `ee`"):
        ee.data._execute_cloud_call(None)
