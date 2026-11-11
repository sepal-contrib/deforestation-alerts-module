"""The radar composite and captions of an alert's files: the radar pair on
screen at the click (``state.capture``), pinned or following, staged as it is
(Earth Engine replaced at ``stage_alert_image``)."""

from types import SimpleNamespace

import pandas as pd
import pytest

from tests.test_alert_gif_job import _info
from gui.tile.analysis import report_images

S1A, S1D = "Sentinel 1 ascending", "Sentinel 1 descending"


@pytest.fixture()
def staged(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(report_images, "stage_alert_image",
                        lambda folder, aid, b, a, ib, ia, grid, area: calls.append((b[3], a[3], ib, ia, area))
                        or tmp_path / "radar.tif")
    return calls


def _alert(before=None, after=None, pinned=(False, False), images=("img_b", "img_a")):
    return SimpleNamespace(alerts=pd.DataFrame({"alert_id": [1, 2, 3, 4]}), alert_id=3, grid="grid", before_radar_info=before, after_radar_info=after,
                           before_radar_image=images[0], after_radar_image=images[1],
                           radar_pinned={"before": pinned[0], "after": pinned[1]})


def test_a_following_radar_pair_is_staged_and_captioned_as_nearest(staged, tmp_path):
    alert = _alert(_info(S1D, "2026-03-25"), _info(S1A, "2026-09-05"), pinned=(False, True))
    radar, missing, extra = report_images.stage_report_images(alert, tmp_path, "large")
    assert radar == tmp_path / "radar.tif" and missing == {}
    assert staged == [(f"{S1D} 2026-03-25 ", f"{S1A} 2026-09-05 ", "img_b", "img_a", "large")]
    assert extra["before_radar"] == f"{S1D} 2026-03-25 (nearest radar image, other pass)"
    assert extra["after_radar"] == f"{S1A} 2026-09-05"  # pinned
    same = _alert(_info(S1A, "2026-03-25"), _info(S1A, "2026-09-05"))
    _, _, extra = report_images.stage_report_images(same, tmp_path)
    assert extra["before_radar"] == f"{S1A} 2026-03-25 (nearest radar image)"
    assert extra["after_radar"] == f"{S1A} 2026-09-05 (nearest radar image)"
    assert extra["radar_summary"] == (f"Radar images: {S1A} 2026-03-25 (nearest radar image) (before) and "
                                      f"{S1A} 2026-09-05 (nearest radar image) (after).")


def test_a_pinned_radar_pair_is_captioned_by_name_only(staged, tmp_path):
    alert = _alert(_info(S1D, "2026-03-25"), _info(S1A, "2026-09-05"), pinned=(True, True))
    _, _, extra = report_images.stage_report_images(alert, tmp_path)
    assert (extra["before_radar"], extra["after_radar"]) == (f"{S1D} 2026-03-25", f"{S1A} 2026-09-05")


def test_a_radar_side_with_no_image_gets_the_note(staged, tmp_path):
    alert = _alert(None, _info(S1A, "2026-09-05"), pinned=(False, True), images=("stale", "img_a"))
    radar, missing, extra = report_images.stage_report_images(alert, tmp_path)
    # the side without info has no image (its stale ee.Image is not used): the after one fills the file
    assert staged == [(f"{S1A} 2026-09-05 ", f"{S1A} 2026-09-05 ", "img_a", "img_a", "normal")]
    assert radar is not None and set(missing) == {"[Placeholder for Radar 1]"}
    assert extra["before_radar"] == "" and extra["after_radar"] == f"{S1A} 2026-09-05"


def test_without_radar_infos_nothing_is_staged_even_with_stale_images(staged, tmp_path):
    radar, missing, extra = report_images.stage_report_images(_alert(images=("stale_b", "stale_a")), tmp_path)
    assert (radar, missing, staged) == (None, {}, [])
    assert extra == {}
