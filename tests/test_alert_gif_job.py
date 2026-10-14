"""The GIF job: which frames, how they are labelled, and the render loop.
Offline: Earth Engine is replaced at the three seams build_gif calls."""

import io
from datetime import date, datetime, timezone
from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pytest
import solara
from PIL import Image
from shapely.geometry import box

from deforestation_alerts.scripts import scene_index
from gui.tile.analysis import gif as gif_mod
from gui.tile.analysis import paths
from gui.tile.analysis.state import AnalysisState

S2, LS, PL = scene_index.SOURCE_S2, scene_index.SOURCE_LANDSAT, scene_index.SOURCE_PLANET
TILES = gpd.GeoDataFrame({"tile": ["W"]}, geometry=gpd.GeoSeries([box(0, 0, 2, 2)], crs=scene_index.WGS84),
                         crs=scene_index.WGS84)
BBOX = box(0.5, 0.5, 1.5, 1.5)


def _tables(rows):
    scenes = pd.DataFrame([(src, pd.Timestamp(d), cloud, "W", f"k{i}") for i, (src, d, cloud) in enumerate(rows)],
                          columns=list(scene_index.SCENE_COLUMNS)).astype({"date": "datetime64[ns]"})
    return scene_index.SceneTables(scenes=scenes, tiles=TILES)


def _info(source, d):
    day = date.fromisoformat(d)
    ms = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)
    return [source, ms, "1.00", f"{source} {d} ", day.strftime("_%d%m%y")]


ROWS = [(S2, "2026-02-05", 5.0), (S2, "2026-03-05", 95.0), (LS, "2026-03-10", 5.0), (S2, "2026-04-05", 5.0),
        (S2, "2026-09-05", 5.0), (S2, "2026-10-05", 5.0), (PL, "2026-10-01", None), (S2, "2026-11-05", 5.0),
        (S2, "2026-12-05", 5.0)]


def test_the_frames_follow_the_timelines_filter():
    frames = gif_mod.plan_frames(_tables(ROWS), BBOX, _info(S2, "2026-04-05"), _info(S2, "2026-09-05"),
                                 frozenset({S2, LS, PL}), 40.0)
    assert [(f.source, f.day.isoformat(), f.role) for f in frames] == [
        (S2, "2026-02-05", ""), (LS, "2026-03-10", ""), (S2, "2026-04-05", "before"),
        (S2, "2026-09-05", "after"), (S2, "2026-10-05", ""), (S2, "2026-11-05", "")]
    assert all(f.keys for f in frames)
    # Landsat switched off: the next Sentinel-2 date older than the 95% one stands in
    off = gif_mod.plan_frames(_tables(ROWS), BBOX, _info(S2, "2026-04-05"), _info(S2, "2026-09-05"),
                              frozenset({S2}), 40.0)
    assert [f.day.isoformat() for f in off][:2] == ["2026-02-05", "2026-04-05"]


def test_a_pick_from_a_switched_off_collection_is_still_a_frame():
    frames = gif_mod.plan_frames(_tables(ROWS), BBOX, _info(LS, "2026-03-10"), _info(S2, "2026-09-05"),
                                 frozenset({S2}), 40.0)
    assert (LS, "before") in [(f.source, f.role) for f in frames]


def test_the_labels_name_the_side_the_collection_and_the_date():
    before = gif_mod.Frame(S2, date(2026, 4, 5), ("k",), "before")
    plain = gif_mod.Frame(LS, date(2026, 3, 10), ("k",), "")
    assert gif_mod.label_of(before) == "Before · Sentinel-2 2026-04-05"
    assert gif_mod.label_of(plain) == "Landsat 2026-03-10"


def _alert(before, after, *, polygon=None, token=0):
    alerts = gpd.GeoDataFrame({"alert_polygon": [polygon], "status": ["Confirmed"]},
                              geometry=[box(0.9, 0.9, 1.1, 1.1)], crs="EPSG:4326")
    return SimpleNamespace(token=token, alerts=alerts, alert_id=0, grid="GRID", before_info=before, after_info=after)


def _ui(rows=ROWS, filt=(frozenset({S2, LS, PL}), 40.0), ctx_token=0):
    app_state = SimpleNamespace(scene_index=solara.reactive(_tables(rows)))
    widgets = SimpleNamespace(
        imagery_ctx=solara.reactive({"bbox": BBOX, "token": ctx_token, "date_min": date(2026, 5, 1)}),
        timeline_filter=solara.reactive(filt))
    return app_state, widgets, AnalysisState()


def test_capture_takes_the_frames_the_outline_and_the_detection_date():
    app_state, widgets, analysis = _ui()
    job = gif_mod.capture_gif(app_state, widgets, analysis, _alert(_info(S2, "2026-04-05"), _info(S2, "2026-09-05")))
    assert len(job.frames) == 6 and job.grid == "GRID" and job.detect_date == date(2026, 5, 1)
    assert job.outline.equals(box(0.9, 0.9, 1.1, 1.1))  # no reviewed polygon: the alert's own
    drawn = box(1.0, 1.0, 1.05, 1.05)
    job = gif_mod.capture_gif(app_state, widgets, analysis,
                              _alert(_info(S2, "2026-04-05"), _info(S2, "2026-09-05"), polygon=drawn))
    assert job.outline.equals(drawn)


@pytest.mark.parametrize("before,after,key", [
    (None, _info(S2, "2026-09-05"), "gif_no_images"),
    (_info(S2, "2026-01-01"), _info(S2, "2026-09-05"), "gif_no_images"),  # not in the index
    (_info(S2, "2026-04-05"), _info(S2, "2026-04-05"), "gif_same_image"),  # Review Focus #5
])
def test_capture_refuses_what_cannot_be_animated(before, after, key):
    app_state, widgets, analysis = _ui()
    with pytest.raises(gif_mod.GifRefused) as err:
        gif_mod.capture_gif(app_state, widgets, analysis, _alert(before, after))
    assert err.value.key == f"analysis_tile.export_labels.{key}"


def test_capture_refuses_a_ctx_of_another_alert():
    app_state, widgets, analysis = _ui(ctx_token=5)
    with pytest.raises(gif_mod.GifRefused):
        gif_mod.capture_gif(app_state, widgets, analysis, _alert(_info(S2, "2026-04-05"), _info(S2, "2026-09-05")))


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (0, 120, 0)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def stubbed(monkeypatch):
    calls = {"outlined": []}
    monkeypatch.setattr(gif_mod, "_ee_geometry", lambda geom: "OUTLINE")
    monkeypatch.setattr(gif_mod.imagery, "rebuild_image", lambda source, keys, grid: (source, tuple(keys)))
    monkeypatch.setattr(gif_mod.alert_gif, "frame_image",
                        lambda image, vis, outline: calls["outlined"].append(outline) or image)
    monkeypatch.setattr(gif_mod.alert_gif, "fetch_frame", lambda image, region: _png())
    return calls


def _job(days, detect=date(2026, 5, 1)):
    frames = tuple(gif_mod.Frame(S2, date.fromisoformat(d), ("k",), role) for d, role in days)
    return gif_mod.GifJob(frames=frames, grid="GRID", outline=box(0, 0, 1, 1), detect_date=detect, alert_id=0,
                          before_info=_info(S2, "2026-04-05"), after_info=_info(S2, "2026-09-05"))


def test_the_outline_starts_at_the_first_detection(stubbed, tmp_path):
    job = _job([("2026-03-05", ""), ("2026-04-05", "before"), ("2026-09-05", "after"), ("2026-10-05", "")])
    out = gif_mod.build_gif(job, tmp_path / "a.gif")
    assert stubbed["outlined"] == [None, None, "OUTLINE", "OUTLINE"]
    with Image.open(out) as im:
        assert im.n_frames == 4
    assert [p.name for p in tmp_path.iterdir()] == ["a.gif"]  # no .part left behind


def test_a_frame_that_fails_is_skipped(stubbed, monkeypatch, tmp_path):
    def _flaky(image, region):
        if image[1] == ("k",) and _flaky.n == 0:
            _flaky.n += 1
            raise RuntimeError("thumbnail 500")
        return _png()
    _flaky.n = 0
    monkeypatch.setattr(gif_mod.alert_gif, "fetch_frame", _flaky)
    out = gif_mod.build_gif(_job([("2026-03-05", ""), ("2026-04-05", "before"), ("2026-09-05", "after")]),
                            tmp_path / "a.gif")
    with Image.open(out) as im:
        assert im.n_frames == 2


def test_fewer_than_two_rendered_frames_refuses_and_writes_nothing(stubbed, monkeypatch, tmp_path):
    monkeypatch.setattr(gif_mod.alert_gif, "fetch_frame",
                        lambda image, region: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(gif_mod.GifRefused) as err:
        gif_mod.build_gif(_job([("2026-04-05", "before"), ("2026-09-05", "after")]), tmp_path / "a.gif")
    assert err.value.key == "analysis_tile.export_labels.gif_too_few"
    assert list(tmp_path.iterdir()) == []


def test_the_gif_is_named_after_the_composite():
    name = paths.alert_gif_name("/set", 3, _info(S2, "2026-04-05"), _info(LS, "2026-09-05"))
    assert name == "/set/alert_3_s_050426_l_050926.gif"
    assert name[:-4] == paths.alert_image_name("/set", 3, _info(S2, "2026-04-05"), _info(LS, "2026-09-05"))[:-4]
