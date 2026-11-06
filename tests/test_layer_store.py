"""``gui.widget.multiview.LayerStore``: the one writer of a ``MultiView``'s
``layers`` trait (spec §5.3). Plain Python: no browser, no app imports."""

import sys
import threading

from gui.widget.multiview import LayerStore, MultiView, specs

BANDS = {"alert": 1_000_000, "vis": 2_000_000, "earlier": 3_000_000}
URL = "https://example.invalid/{z}/{x}/{y}.png"


def _spec(layer_id, group, **kw):
    return specs.tile_spec(layer_id, "all", URL, name=layer_id, group=group, **kw)


def _store():
    return LayerStore(MultiView(), z_bands=BANDS)


def test_add_appends_and_replaces_a_same_id_spec_in_place_with_a_new_list():
    store = _store()
    writes = []
    store.widget.observe(lambda ch: writes.append(ch["new"]), names="layers")
    store.add(_spec("a", "alert"))
    store.add(_spec("b", "vis"))
    store.add(_spec("a", "alert", opacity=0.5))
    assert store.ids() == ["a", "b"]
    assert store.spec("a")["opacity"] == 0.5
    assert len(writes) == 3
    assert writes[1] is not writes[2], "a new list on every write, so the trait syncs"


def test_a_spec_without_z_takes_its_group_band():
    store = _store()
    store.add(_spec("a", "alert"))
    store.add(_spec("v", "vis"))
    store.add(_spec("x", "vis", z=7))
    store.add(_spec("n", "nobody"))
    assert store.spec("a")["z"] == 1_000_000
    assert store.spec("v")["z"] == 2_000_000
    assert store.spec("x")["z"] == 7, "an explicit z is kept"
    assert store.spec("n")["z"] == 0, "a group the bands do not name draws at 0"


def test_the_store_keeps_its_own_copy_of_a_spec():
    store = _store()
    spec = specs.geojson_spec("g", "all", {"type": "FeatureCollection", "features": []}, name="g",
                              group="alert", style={"color": "red"})
    store.add(spec)
    assert spec["z"] is None, "the caller's spec is not changed"
    spec["style"]["color"] = "blue"
    spec["data"]["features"].append({"type": "Feature"})
    stored = store.spec("g")
    assert stored["style"] == {"color": "red"} and stored["data"]["features"] == []


def test_clear_drops_one_group_and_remove_drops_one_id():
    store = _store()
    for layer_id, group in (("a", "alert"), ("v", "vis"), ("e", "earlier"), ("a2", "alert")):
        store.add(_spec(layer_id, group))
    store.clear("alert")
    assert store.ids() == ["v", "e"]
    store.remove("e")
    assert store.ids() == ["v"]
    assert store.ids("vis") == ["v"]
    assert store.ids("alert") == []


def test_removing_a_missing_id_or_clearing_an_empty_group_writes_nothing():
    store = _store()
    store.add(_spec("v", "vis"))
    writes = []
    store.widget.observe(lambda ch: writes.append(ch["new"]), names="layers")
    store.remove("missing")
    store.clear("alert")
    assert writes == []
    assert store.ids() == ["v"]


def test_spec_returns_a_copy_or_none():
    store = _store()
    store.add(_spec("a", "alert"))
    got = store.spec("a")
    got["name"] = "changed"
    got["meta"]["x"] = 1
    assert store.spec("a")["name"] == "a"
    assert store.spec("a")["meta"] == {}
    assert store.spec("missing") is None


def test_a_gate_can_decline_the_add():
    store = _store()
    store.add(_spec("a", "alert"), gate=lambda write: None)
    assert store.ids() == []
    store.add(_spec("a", "alert"), gate=lambda write: write())
    assert store.ids() == ["a"]


def test_the_gate_runs_outside_the_store_lock():
    """The imagery worker's gate takes the alert lock (``apply_if_current``)
    and then writes; the UI thread holds the alert lock (``reset_alert``) and
    then clears the alert group. If the store called the gate while holding
    its own lock, the two threads would take the locks in opposite orders and
    deadlock."""
    store = _store()
    alert_lock = threading.RLock()
    gate_entered = threading.Event()
    ui_holds_alert = threading.Event()

    def gate(write):
        gate_entered.set()
        ui_holds_alert.wait(5)
        with alert_lock:
            write()

    def worker():
        store.add(_spec("a", "alert"), gate=gate)

    def ui():
        gate_entered.wait(5)
        with alert_lock:
            ui_holds_alert.set()
            store.clear("alert")

    threads = [threading.Thread(target=worker, daemon=True), threading.Thread(target=ui, daemon=True)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(5)
    assert not any(th.is_alive() for th in threads), "deadlock: the gate ran inside the store lock"
    assert store.ids() == ["a"]


def test_concurrent_vis_adds_and_alert_clears_never_lose_a_vis_layer():
    store = _store()

    def adds():
        for i in range(200):
            store.add(_spec(f"vis:{i}", "vis"))

    def clears():
        for _ in range(200):
            store.add(_spec("before:x", "alert"))
            store.clear("alert")

    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # switch threads as often as possible
    try:
        threads = [threading.Thread(target=adds, daemon=True), threading.Thread(target=clears, daemon=True)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(10)
    finally:
        sys.setswitchinterval(old)
    assert not any(th.is_alive() for th in threads)
    assert store.ids("vis") == [f"vis:{i}" for i in range(200)]
    assert store.ids("alert") == []


def test_the_package_exports_the_store():
    import gui.widget.multiview as package

    assert "LayerStore" in package.__all__
    assert package.LayerStore is LayerStore
