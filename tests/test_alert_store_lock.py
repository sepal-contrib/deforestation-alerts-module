"""The per-store lock (spec §4.4): every write of an alert store holds its
lock from before the serialization until the file is in place, so two
writers that both check the store's generation in their ``guard`` cannot
both pass the check before either replaces the file."""

import threading
import time

import geopandas as gpd
import pytest
from shapely.geometry import box

from deforestation_alerts.scripts import alert_store
from deforestation_alerts.scripts.alert_store import load_gdf_from_parquet, save_gdf_to_parquet, store_lock
from gui.scripts.set_store import store_generation


class StoreMoved(Exception):
    """The guard's refusal: the store is not the generation the writer read."""


def _alerts(status, n=3):
    return gpd.GeoDataFrame({"alert_id": list(range(n)), "status": [status] * n},
                            geometry=[box(i, 0, i + 1, 1) for i in range(n)], crs="EPSG:4326")


def _guard(store, read_generation, on_check=None):
    """What Save, split and merge check right before the replace: the store
    on disk is still the generation they read (``set_store.generation_changed``)."""
    def check():
        if on_check is not None:
            on_check()
        if store_generation(store) != read_generation:
            raise StoreMoved(str(store))
    return check


def test_one_lock_per_real_path(tmp_path):
    store = tmp_path / "set" / "alert_db.parquet"
    store.parent.mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "set")
    assert store_lock(store) is store_lock(str(store))
    assert store_lock(tmp_path / "link" / "alert_db.parquet") is store_lock(store)
    assert store_lock(tmp_path / "set" / ".." / "set" / "alert_db.parquet") is store_lock(store)
    assert store_lock(tmp_path / "other.parquet") is not store_lock(store)


def test_the_second_guarded_write_sees_the_moved_generation_and_is_refused(tmp_path):
    """Two writers read the same generation. A's guard holds while B starts its
    write: B must wait for A's replace, then see the moved generation and
    write nothing. Without the lock B's guard would pass while A's file was
    not in place yet, and B would overwrite A unnoticed."""
    store = tmp_path / "alert_db.parquet"
    save_gdf_to_parquet(_alerts("Not reviewed"), store)
    read = store_generation(store)  # both writers read this one
    a_in_guard, b_started = threading.Event(), threading.Event()
    order, errors = [], {}

    def a_holds():
        a_in_guard.set()
        assert b_started.wait(5)
        time.sleep(0.3)  # B would run its guard now if nothing held it back
        order.append("A checks")

    def write(name, status, on_check):
        try:
            save_gdf_to_parquet(_alerts(status), store, guard=_guard(store, read, on_check))
            order.append(f"{name} replaced")
        except StoreMoved as exc:
            errors[name] = exc
            order.append(f"{name} refused")

    a = threading.Thread(target=write, args=("A", "Confirmed", a_holds))
    b = threading.Thread(target=write, args=("B", "False Positive", lambda: order.append("B checks")))
    a.start()
    assert a_in_guard.wait(5)
    b.start()
    b_started.set()
    a.join(10)
    b.join(10)

    assert order == ["A checks", "A replaced", "B checks", "B refused"]
    assert set(errors) == {"B"}
    assert load_gdf_from_parquet(store)["status"].tolist() == ["Confirmed"] * 3  # A's write, untouched
    assert not list(tmp_path.glob("*.part"))


def test_writes_of_different_stores_do_not_wait_for_each_other(tmp_path):
    first, second = tmp_path / "a.parquet", tmp_path / "b.parquet"
    in_guard, release = threading.Event(), threading.Event()

    def held():
        in_guard.set()
        assert release.wait(5)

    t = threading.Thread(target=save_gdf_to_parquet, args=(_alerts("Confirmed"), first), kwargs={"guard": held})
    t.start()
    assert in_guard.wait(5)
    try:
        save_gdf_to_parquet(_alerts("Need revision"), second)  # does not block on the other store's lock
        assert load_gdf_from_parquet(second)["status"].iloc[0] == "Need revision"
    finally:
        release.set()
        t.join(10)
    assert first.exists()


def test_a_refused_or_failed_write_releases_the_lock(tmp_path):
    store = tmp_path / "alert_db.parquet"
    save_gdf_to_parquet(_alerts("Not reviewed"), store)

    def refuse():
        raise StoreMoved("no")

    with pytest.raises(StoreMoved):
        save_gdf_to_parquet(_alerts("Confirmed"), store, guard=refuse)
    acquired = []

    def other_thread():
        lock = store_lock(store)
        acquired.append(lock.acquire(timeout=2))
        lock.release()

    t = threading.Thread(target=other_thread)
    t.start()
    t.join(5)
    assert acquired == [True]
    assert load_gdf_from_parquet(store)["status"].tolist() == ["Not reviewed"] * 3


def test_a_writer_holding_the_lock_can_write_through_it(tmp_path):
    """Split and merge read the store under its lock, then write: the lock is
    re-entrant, so their own write does not deadlock."""
    store = tmp_path / "alert_db.parquet"
    save_gdf_to_parquet(_alerts("Not reviewed"), store)
    with store_lock(store):
        read = store_generation(store)
        gdf = load_gdf_from_parquet(store)
        gdf["status"] = "Confirmed"
        save_gdf_to_parquet(gdf, store, guard=_guard(store, read))
    assert load_gdf_from_parquet(store)["status"].tolist() == ["Confirmed"] * 3


def test_the_registry_holds_one_lock_per_path_across_threads(tmp_path):
    store = tmp_path / "alert_db.parquet"
    seen = []
    threads = [threading.Thread(target=lambda: seen.append(store_lock(store))) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(5)
    assert len(seen) == 8 and all(lock is seen[0] for lock in seen)
    assert alert_store._STORE_LOCKS[str(store.resolve())] is seen[0]
