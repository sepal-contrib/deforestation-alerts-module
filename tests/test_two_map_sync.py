"""Two-map sync: browser-side jslink for user interactions; explicit dual
writes (helpers) for Python-originated mutations, because jslink propagates
only in a live frontend — never headless."""

import inspect
from types import SimpleNamespace


def _stub_widgets():
    """AnalysisWidgets stand-in exposing only what the helpers touch."""
    from gui.tile.analysis.widgets import AnalysisWidgets

    stub = SimpleNamespace(
        map_before=SimpleNamespace(center=None, zoom=None),
        map_after=SimpleNamespace(center=None, zoom=None),
        draw_before=SimpleNamespace(data=[]),
        draw_after=SimpleNamespace(data=[]),
    )
    stub.set_synced_view = AnalysisWidgets.set_synced_view.__get__(stub)
    stub.set_synced_draw_data = AnalysisWidgets.set_synced_draw_data.__get__(stub)
    return stub


def test_set_synced_view_writes_both_maps():
    w = _stub_widgets()
    w.set_synced_view((12.5, -60.25), 16)
    assert w.map_before.center == w.map_after.center == (12.5, -60.25)
    assert w.map_before.zoom == w.map_after.zoom == 16


def test_set_synced_draw_data_writes_both_controls():
    w = _stub_widgets()
    features = [{"type": "Feature", "geometry": None, "properties": {"id": 1}}]
    w.set_synced_draw_data(features)
    assert w.draw_before.data == features
    assert w.draw_after.data == features
    assert w.draw_before.data is not w.draw_after.data  # no shared mutable list


def test_wiring_uses_jslink_not_kernel_link():
    from gui.tile.analysis import widgets

    src = inspect.getsource(widgets.AnalysisWidgets.__init__)
    assert 'jslink((self.map_before, "center")' in src
    assert 'jslink((self.map_before, "zoom")' in src
    assert 'jslink((self.draw_before, "data")' in src
    for line in src.splitlines():
        assert not line.strip().startswith("link(("), f"kernel link remains: {line.strip()}"


def test_no_single_sided_python_mutations():
    """Python code must go through the helpers; direct one-sided writes to a
    synced trait diverge headlessly under jslink."""
    from gui.tile.analysis import analysis_tile, edit_tools

    for mod in (analysis_tile, edit_tools):
        src = inspect.getsource(mod)
        for needle in ("map_before.center =", "map_before.zoom =",
                       "map_after.center =", "map_after.zoom =",
                       "draw_before.data =", "draw_after.data ="):
            assert needle not in src, (
                f"{mod.__name__} writes a synced trait directly ({needle}); "
                "use set_synced_view/set_synced_draw_data"
            )
