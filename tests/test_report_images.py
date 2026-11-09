"""The report's images when a pick is radar: the stand-ins planned at the click
and the composites staged by the worker (Earth Engine replaced at its seams)."""

from types import SimpleNamespace

from tests.test_alert_gif_job import BBOX, S2, _info, _tables
from gui.tile.analysis import report_images

S1A, S1D = "Sentinel 1 ascending", "Sentinel 1 descending"
ROWS = [(S1D, "2026-03-25", None), (S2, "2026-04-01", 5.0), (S1A, "2026-04-05", None),
        (S1A, "2026-09-05", None), (S2, "2026-09-20", 5.0)]


def test_an_optical_pair_plans_nothing():
    assert report_images.plan_report_images(_tables(ROWS), BBOX, _info(S2, "2026-04-01"),
                                            _info(S2, "2026-09-20"), 40.0) is None


def test_a_radar_after_pick_gets_an_optical_stand_in_and_a_radar_partner():
    plan = report_images.plan_report_images(_tables(ROWS), BBOX, _info(S2, "2026-04-01"),
                                            _info(S1A, "2026-09-05"), 40.0)
    before, after = plan.optical
    assert before.keys is None and before.mark == ""  # the reviewer's own pick
    assert after.info[3] == f"{S2} 2026-09-20 " and after.keys == ("k4",) and after.mark.endswith("report_nearest_optical")
    # no ascending image on or before 04-01: the descending one stands in, marked as the other pass
    assert plan.radar[0].info[3] == f"{S1D} 2026-03-25 " and plan.radar[0].mark.endswith("report_nearest_radar_other")
    assert plan.radar[1].keys is None
    assert plan.radar_sides == ("after",)


def test_staging_rebuilds_the_stand_ins_and_notes_a_missing_side(monkeypatch, tmp_path):
    plan = report_images.plan_report_images(_tables(ROWS[2:]), BBOX, _info(S1A, "2026-04-05"),
                                            _info(S1A, "2026-09-05"), 40.0)
    assert plan.optical[0] is None  # nothing optical on or before 04-05 in these rows
    rebuilt, staged = [], []
    monkeypatch.setattr(report_images.imagery, "rebuild_image",
                        lambda source, keys, grid: rebuilt.append((source, keys)) or f"img:{source}")
    monkeypatch.setattr(report_images, "stage_alert_image",
                        lambda folder, aid, b, a, ib, ia, grid, area: staged.append((b[3], a[3], ib, ia))
                        or tmp_path / f"{len(staged)}.tif")
    alert = SimpleNamespace(alert_id=3, grid="grid", before_image="pick_b", after_image="pick_a")

    optical, radar, missing, extra = report_images.stage_report_images(plan, alert, tmp_path)

    assert rebuilt == [(S2, ["k2"])]  # keys renumber from ROWS[2:]
    # the optical file holds the one after stand-in twice; the radar one the two picks
    assert staged == [(f"{S2} 2026-09-20 ", f"{S2} 2026-09-20 ", f"img:{S2}", f"img:{S2}"),
                      (f"{S1A} 2026-04-05 ", f"{S1A} 2026-09-05 ", "pick_b", "pick_a")]
    assert optical is not None and radar is not None
    assert set(missing) == {"[Placeholder for Image 1]", "[Placeholder for Image 4]"}
    assert extra["after_img"].startswith(f"{S2} 2026-09-20 (") and "before_img" not in extra
    assert extra["before_radar"] == f"{S1A} 2026-04-05" and extra["image_note"]
    assert extra["radar_summary"] == f"Radar images: {S1A} 2026-04-05 (before) and {S1A} 2026-09-05 (after)."
