import json
import threading
from datetime import date, datetime, timedelta, timezone

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from deforestation_alerts.project import paths
from deforestation_alerts.scripts import local_files, scene_index
from gui.scripts import scene_cache

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
S2, LS = scene_index.SOURCE_S2, scene_index.SOURCE_LANDSAT


def _tables(rows, tiles=("W",)):
    scenes = pd.DataFrame(rows, columns=list(scene_index.SCENE_COLUMNS)).astype({"date": "datetime64[ns]"})
    tdf = gpd.GeoDataFrame(
        {"tile": list(tiles)},
        geometry=gpd.GeoSeries([box(0, 0, 2, 2)] * len(tiles), crs=scene_index.WGS84),
        crs=scene_index.WGS84,
    )
    return scene_index.SceneTables(scenes=scenes, tiles=tdf)


CHUNK = _tables([(S2, pd.Timestamp("2026-05-10"), 10.0, "W", "k1"),
                 (LS, pd.Timestamp("2026-05-12"), 3.0, "W", "k2")])


def test_reading_a_project_with_no_index_yields_nothing(data_root):
    assert scene_cache.read_index(paths.project_dir("absent")) == (None, None)


def test_the_planet_access_state_survives_a_chunk_that_did_not_ask(data_root):
    """The toolbar disables the Planet toggle from ``manifest.planet.access``.
    A background widening chunk fetches no NICFI, so it must not overwrite what
    the first build learned."""
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(
        pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW,
        planet=scene_index.PlanetState(region="americas", access=True),
    )
    later = _tables([(S2, pd.Timestamp("2026-06-04"), 8.0, "W", "k3")])
    _, manifest = scene_cache.store_chunk(pdir, later, (date(2026, 6, 1), date(2026, 6, 30)), "d1", now=NOW)
    assert manifest.planet.access is True and manifest.planet.region == "americas"

    # A rebuild for a different AOI starts over: another region, another answer.
    _, manifest = scene_cache.store_chunk(
        pdir, later, (date(2026, 6, 1), date(2026, 6, 30)), "d2", now=NOW
    )
    assert manifest.planet.access is False and manifest.planet.region is None


def test_a_stored_chunk_round_trips_through_disk(data_root):
    pdir = paths.project_dir("p")
    tables, manifest = scene_cache.store_chunk(
        pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW
    )
    assert manifest.covered == scene_index.Coverage(start=date(2026, 5, 1), end=date(2026, 5, 31))
    assert manifest.aoi_digest == "d1" and manifest.refreshed == NOW
    assert manifest.counts == {S2: 1, LS: 1}

    back, back_manifest = scene_cache.read_index(pdir)
    assert back_manifest == manifest
    assert len(back.scenes) == 2 and set(back.scenes["key"]) == {"k1", "k2"}
    assert back.tiles.crs.to_epsg() == 4326
    assert back.scenes["date"].dtype == tables.scenes["date"].dtype


def test_a_second_chunk_widens_the_coverage_and_adds_its_rows(data_root):
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    later = _tables([(S2, pd.Timestamp("2026-06-04"), 8.0, "W", "k3")], tiles=("W", "E"))
    tables, manifest = scene_cache.store_chunk(
        pdir, later, (date(2026, 5, 31), date(2026, 6, 30)), "d1", now=NOW + timedelta(hours=1)
    )
    assert manifest.covered == scene_index.Coverage(start=date(2026, 5, 1), end=date(2026, 6, 30))
    assert len(tables.scenes) == 3
    assert set(tables.tiles["tile"]) == {"W", "E"}  # the new tile joined, the old one stayed


def test_a_refetched_scene_replaces_its_row_rather_than_duplicating_it(data_root):
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    corrected = _tables([(S2, pd.Timestamp("2026-05-10"), 44.0, "W", "k1")])
    tables, _ = scene_cache.store_chunk(
        pdir, corrected, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW
    )
    assert len(tables.scenes) == 2
    assert tables.scenes.set_index("key").loc["k1", "cloud"] == pytest.approx(44.0)


def test_replace_drops_the_old_rows(data_root):
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    fresh = _tables([(S2, pd.Timestamp("2026-07-01"), 1.0, "W", "k9")])
    tables, manifest = scene_cache.store_chunk(
        pdir, fresh, (date(2026, 7, 1), date(2026, 7, 31)), "d2", now=NOW, replace=True
    )
    assert list(tables.scenes["key"]) == ["k9"]
    assert manifest.aoi_digest == "d2"
    assert manifest.covered == scene_index.Coverage(start=date(2026, 7, 1), end=date(2026, 7, 31))


def test_an_index_of_another_aoi_is_not_merged_into(data_root):
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    fresh = _tables([(S2, pd.Timestamp("2026-05-10"), 1.0, "W", "k9")])
    tables, manifest = scene_cache.store_chunk(
        pdir, fresh, (date(2026, 5, 1), date(2026, 5, 31)), "d2", now=NOW
    )
    assert list(tables.scenes["key"]) == ["k9"] and manifest.aoi_digest == "d2"


def test_a_failed_write_leaves_the_previous_index_in_place(data_root, monkeypatch):
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    bad = _tables([(S2, pd.Timestamp("2026-06-04"), 8.0, "W", "k3")])

    boom = {"n": 0}
    real = scene_cache._write_manifest

    def explode(*a, **k):
        boom["n"] += 1
        raise OSError("disk full")

    monkeypatch.setattr(scene_cache, "_write_manifest", explode)
    with pytest.raises(OSError):
        scene_cache.store_chunk(pdir, bad, (date(2026, 6, 1), date(2026, 6, 30)), "d1", now=NOW)
    monkeypatch.setattr(scene_cache, "_write_manifest", real)
    assert boom["n"] == 1  # the explosion is the one this test means to trigger, not a retry

    tables, manifest = scene_cache.read_index(pdir)
    assert manifest.covered.end == date(2026, 5, 31)
    assert not list(paths.imagery_dir(pdir).glob("*.part"))
    # The index is still a consistent generation, not a mix that reads as
    # scenes whose tiles are missing: every scene row names a known tile.
    assert set(tables.scenes["tile"]) <= set(tables.tiles["tile"])


def test_a_failed_scene_write_still_leaves_every_scene_row_findable(data_root, monkeypatch):
    """The three files are written tiles-first for this reason: a crash between
    two replacements must not leave scene rows naming tiles that are not there."""
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    new_tile = _tables([(S2, pd.Timestamp("2026-06-04"), 8.0, "E", "k3")], tiles=("E",))

    real = local_files.write_into

    def explode(path, *a, **k):
        if path == paths.scenes_path(pdir):
            raise OSError("disk full")
        return real(path, *a, **k)

    monkeypatch.setattr(scene_cache.local_files, "write_into", explode)
    with pytest.raises(OSError):
        scene_cache.store_chunk(pdir, new_tile, (date(2026, 6, 1), date(2026, 6, 30)), "d1", now=NOW)
    monkeypatch.setattr(scene_cache.local_files, "write_into", real)

    tables, manifest = scene_cache.read_index(pdir)
    assert set(tables.scenes["key"]) == {"k1", "k2"}          # the old scenes
    assert set(tables.scenes["tile"]) <= set(tables.tiles["tile"])  # still findable
    assert manifest.covered.end == date(2026, 5, 31)


def test_two_threads_storing_chunks_both_land(data_root):
    """Review Focus #4: the background widener and a foreground widen-by-a-month
    write the same parquet. The loser must add to the winner's rows."""
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    start = threading.Barrier(2)

    def add(month, key):
        start.wait()
        scene_cache.store_chunk(
            pdir,
            _tables([(S2, pd.Timestamp(f"2026-{month:02d}-04"), 8.0, "W", key)]),
            (date(2026, month, 1), date(2026, month, 28)),
            "d1",
            now=NOW,
        )

    threads = [threading.Thread(target=add, args=(6, "k6")), threading.Thread(target=add, args=(4, "k4"))]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    tables, manifest = scene_cache.read_index(pdir)
    assert set(tables.scenes["key"]) == {"k1", "k2", "k4", "k6"}
    # min of the starts, max of the ends: 1 Apr from the April chunk, 28 Jun
    # from the June one — the windows above stop on the 28th.
    assert manifest.covered == scene_index.Coverage(start=date(2026, 4, 1), end=date(2026, 6, 28))


def test_store_chunk_only_rereads_the_index_while_holding_the_lock(data_root, monkeypatch):
    """Structural companion to the race test above: the previous test's 5/5
    regression margin is real evidence, but it is still a race it hopes to
    win -- a sufficiently descheduled thread could let a broken version pass
    on a slow or loaded machine. This spies on ``read_index`` and asserts the
    project's lock is already held every time ``store_chunk`` calls it, which
    is true on any machine and any thread ordering, not just usually true."""
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)

    real_read = scene_cache.read_index
    locked_at_entry = []

    def spy(p):
        locked_at_entry.append(scene_cache._lock_for(p).locked())
        return real_read(p)

    monkeypatch.setattr(scene_cache, "read_index", spy)
    start = threading.Barrier(2)

    def add(month, key):
        start.wait()
        scene_cache.store_chunk(
            pdir,
            _tables([(S2, pd.Timestamp(f"2026-{month:02d}-04"), 8.0, "W", key)]),
            (date(2026, month, 1), date(2026, month, 28)),
            "d1",
            now=NOW,
        )

    threads = [threading.Thread(target=add, args=(6, "k6")), threading.Thread(target=add, args=(4, "k4"))]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    # One re-read per store_chunk call (the two threads), both under the lock.
    assert len(locked_at_entry) == 2 and all(locked_at_entry)


# ----------------------------------------------------------- dedup identity

def test_the_same_key_string_under_two_sources_keeps_both_rows(data_root):
    """``merge_tables`` dedups on ``(source, key)``, not ``key`` alone.
    ``key`` is drawn from a different namespace per source (an asset id for
    Sentinel-2/Planet, a ``LANDSAT_SCENE_ID`` for Landsat), so it is unique
    only by luck; deduping on ``key`` alone would let a cross-source
    collision silently delete one of two real granules. This would fail if
    ``merge_tables`` deduped on ``key`` alone: the second store would then
    treat the Landsat row as a re-fetch of the Sentinel-2 one and keep only
    one row."""
    pdir = paths.project_dir("p")
    first = _tables([(S2, pd.Timestamp("2026-05-10"), 10.0, "W", "same-key")])
    scene_cache.store_chunk(pdir, first, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    second = _tables([(LS, pd.Timestamp("2026-05-12"), 3.0, "W", "same-key")])
    tables, _ = scene_cache.store_chunk(pdir, second, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    assert len(tables.scenes) == 2
    assert set(tables.scenes["source"]) == {S2, LS}


# ------------------------------------------------------- unreadable index

def test_a_hand_corrupted_manifest_reads_as_no_index(data_root):
    """``read_index``'s answer to a missing index and an unreadable one is the
    same (a rebuild), per its own docstring -- so a hand-edited or
    truncated ``index.json`` must not raise out of ``read_index``."""
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    paths.scene_manifest_path(pdir).write_text("{not json")
    assert scene_cache.read_index(pdir) == (None, None)


def test_a_truncated_scenes_parquet_reads_as_no_index(data_root):
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    with open(paths.scenes_path(pdir), "wb") as f:
        f.write(b"not a parquet file")
    assert scene_cache.read_index(pdir) == (None, None)


def test_a_scenes_parquet_with_the_wrong_columns_reads_as_no_index(data_root):
    """A ``scenes.parquet`` that parses fine but was written by a different
    column layout -- same ``version``, so the layout gate above does not
    catch it -- must not raise a bare ``KeyError`` out of ``read_index``
    (and, through it, out of ``store_chunk``). This is the corruption shape
    the brief's error-style note actually names: a readable file, a wrong
    schema."""
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    renamed = pd.read_parquet(paths.scenes_path(pdir)).rename(columns={"cloud": "cloud_pct"})
    renamed.to_parquet(paths.scenes_path(pdir), index=False)

    assert scene_cache.read_index(pdir) == (None, None)
    # store_chunk must rebuild rather than raise out of read_index's failure.
    later = _tables([(S2, pd.Timestamp("2026-06-04"), 8.0, "W", "k3")])
    tables, manifest = scene_cache.store_chunk(pdir, later, (date(2026, 6, 1), date(2026, 6, 30)), "d1", now=NOW)
    assert list(tables.scenes["key"]) == ["k3"] and manifest.covered.start == date(2026, 6, 1)


def test_a_manifest_from_a_future_version_reads_as_no_index(data_root):
    """``read_index``'s own layout gate: a manifest a later release wrote may
    still parse (pydantic ignores fields it does not know), but its
    ``version`` says its parquet columns are not a promise this build made."""
    pdir = paths.project_dir("p")
    scene_cache.store_chunk(pdir, CHUNK, (date(2026, 5, 1), date(2026, 5, 31)), "d1", now=NOW)
    manifest_path = paths.scene_manifest_path(pdir)
    raw = json.loads(manifest_path.read_text())
    raw["version"] = scene_index.INDEX_VERSION + 1
    manifest_path.write_text(json.dumps(raw))

    assert scene_cache.read_index(pdir) == (None, None)
