"""``DrawState`` on a real ``AlertViews``: host writes, the gen/seq check on
user writes, the re-assert of a stale one, private copies, ``on_edit``.

A frontend write is simulated as ipywidgets applies one from the browser:
``widget.set_state({"draw": envelope})``, which sends the echo, sets the trait
under the property lock and runs the observers."""

import logging
import threading

import comm
import pytest

from gui.widget.alert_views import AlertViews
from gui.widget.multiview import DrawState, MultiView


def _feature(x=0.0):
    ring = [[x, 0.0], [x + 1.0, 0.0], [x + 1.0, 1.0], [x, 1.0], [x, 0.0]]
    return {"type": "Feature", "properties": {"style": {"color": "#2196F3"}},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


A, B, C = _feature(0.0), _feature(10.0), _feature(20.0)


def _fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def _host(gen, *features):
    return {"fc": _fc(*features), "gen": gen, "seq": 0, "origin": "host"}


def _user(widget, fc, gen, seq):
    widget.set_state({"draw": {"fc": fc, "gen": gen, "seq": seq, "origin": "user"}})


@pytest.fixture
def widget(monkeypatch):
    # A comm that sends: once any earlier test has imported ipykernel, the
    # default comm is ipykernel's with ``kernel=None``, and ipywidgets then
    # skips every ``update`` (notify_change), which the re-assert test spies on.
    monkeypatch.setattr(comm, "create_comm", comm._create_comm)
    return AlertViews()


def test_drawstate_needs_the_draw_mixin():
    with pytest.raises(TypeError, match="Draw mixin"):
        DrawState(MultiView())


def test_set_features_sends_a_host_envelope_with_the_next_gen(widget):
    state = DrawState(widget)
    state.set_features([A])
    assert widget.draw == _host(1, A)
    state.set_features(_fc())
    assert widget.draw == _host(2)


def test_set_features_accepts_a_collection_a_feature_a_list_or_none(widget):
    state = DrawState(widget)
    for value, expected in ((_fc(A, B), [A, B]), (B, [B]), ([A], [A]), ((A, B), [A, B]), (None, [])):
        state.set_features(value)
        assert state.features() == expected
        assert state.geojson() == _fc(*expected)
        assert widget.draw["fc"] == _fc(*expected)


def test_set_features_rejects_anything_else(widget):
    state = DrawState(widget)
    with pytest.raises(TypeError):
        state.set_features("not geojson")
    assert widget.draw == _host(0), "nothing was written"


def test_every_write_is_a_new_dict(widget):
    seen = []
    widget.observe(lambda change: seen.append(change["new"]), names="draw")
    state = DrawState(widget)
    state.set_features([A])
    first = widget.draw
    state.set_features([A])
    assert len(seen) == 2 and widget.draw is not first and first["gen"] == 1, "the old envelope was not mutated"


def test_drawstate_starts_from_the_trait(widget):
    widget.draw = _host(4, A)
    state = DrawState(widget)
    assert state.features() == [A]
    state.set_features([B])
    assert widget.draw["gen"] == 5


def test_reads_are_copies_and_never_read_the_trait(widget):
    state = DrawState(widget)
    source = [_feature()]
    state.set_features(source)
    source[0]["geometry"] = None  # the caller's list is not kept
    features = state.features()
    features[0]["properties"]["style"]["color"] = "#000"
    features.append(B)
    state.geojson()["features"].clear()
    widget.draw["fc"]["features"].clear()  # even an in-place change of the trait
    assert state.features() == [A]
    assert state.geojson() == _fc(A)


def test_an_accepted_user_write_updates_the_copy_and_fires_on_edit(widget):
    state = DrawState(widget)
    state.set_features([A])
    seen = []
    state.on_edit(lambda: seen.append(state.features()))
    _user(widget, _fc(B), gen=1, seq=1)
    _user(widget, _fc(B, C), gen=1, seq=2)
    assert seen == [[B], [B, C]]
    assert state.features() == [B, C]
    assert widget.draw["origin"] == "user", "an accepted write is not re-asserted"


def test_a_host_write_never_fires_on_edit(widget):
    state = DrawState(widget)
    seen = []
    state.on_edit(lambda: seen.append(1))
    state.set_features([A])
    widget.set_state({"draw": _host(7, B)})  # a host envelope echoed back by a frontend
    assert seen == []
    assert state.features() == [A]


def test_a_stale_user_write_is_rejected_and_reasserted(widget):
    # Review Focus #1: the reviewer's edit was made on gen 1, but Python has
    # written gen 2 since (Add to map, an alert change). The edit must not land,
    # and the frontend must get Python's drawing again as a newer host write.
    state = DrawState(widget)
    state.set_features([A])  # gen 1: what the frontend was editing
    state.set_features([B])  # gen 2: still on its way to the browser
    seen = []
    state.on_edit(lambda: seen.append(1))
    sent = []
    widget._send = lambda msg, buffers=None: sent.append(msg)  # a spy on the comm
    try:
        _user(widget, _fc(C), gen=1, seq=1)
    finally:
        del widget._send
    assert seen == []
    assert state.features() == [B], "the private copy, not the trait the stale write landed in"
    assert widget.draw == _host(3, B)
    assert [m["method"] for m in sent] == ["echo_update", "update"]
    assert sent[-1]["state"]["draw"] == _host(3, B), "the re-assert went to the frontend"
    _user(widget, _fc(C), gen=3, seq=1)  # the frontend adopted gen 3: its next edit is accepted
    assert seen == [1] and state.features() == [C]


@pytest.mark.parametrize("seq", [1, 0, -1, "2", True, None, 2.5])
def test_a_user_write_without_a_higher_whole_seq_is_rejected(widget, seq):
    state = DrawState(widget)
    state.set_features([A])
    _user(widget, _fc(B), gen=1, seq=1)  # accepted: the last seq is now 1
    _user(widget, _fc(C), gen=1, seq=seq)
    assert state.features() == [B]
    assert widget.draw == _host(2, B)


def test_a_user_write_with_a_newer_gen_than_python_is_rejected(widget):
    state = DrawState(widget)
    state.set_features([A])
    _user(widget, _fc(B), gen=9, seq=1)
    assert state.features() == [A]
    assert widget.draw == _host(2, A)


def test_unsubscribe_stops_the_callback(widget):
    state = DrawState(widget)
    seen = []
    unsubscribe = state.on_edit(lambda: seen.append(1))
    unsubscribe()
    unsubscribe()  # twice is harmless
    _user(widget, _fc(B), gen=0, seq=1)
    assert seen == [] and state.features() == [B]


def test_a_failing_callback_does_not_stop_the_others(widget, caplog):
    state = DrawState(widget)
    seen = []

    def broken():
        raise RuntimeError("boom")

    state.on_edit(broken)
    state.on_edit(lambda: seen.append(1))
    with caplog.at_level(logging.ERROR):
        _user(widget, _fc(B), gen=0, seq=1)
    assert seen == [1]
    assert "on_edit callback failed" in caplog.text


def test_set_edit_mode_is_a_plain_switch(widget):
    state = DrawState(widget)
    state.set_edit_mode(1)
    assert widget.draw_edit_mode is True
    state.set_edit_mode(False)
    assert widget.draw_edit_mode is False
    assert widget.draw == _host(0), "the drawing is untouched"


def test_concurrent_host_writes_reach_the_trait_in_gen_order(widget):
    state = DrawState(widget)
    gens = []
    widget.observe(lambda change: gens.append(change["new"]["gen"]), names="draw")

    def writer(x):
        for _ in range(50):
            state.set_features([_feature(x)])

    threads = [threading.Thread(target=writer, args=(float(i),)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert gens == list(range(1, 201))
    assert widget.draw["gen"] == 200 and widget.draw["fc"] == state.geojson()
