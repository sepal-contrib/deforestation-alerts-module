"""The names and legends of the step layers (spec §4.3, §9.4): a source's
preview becomes its harmonized raster under the same key, and a mask's legend
follows the active period's choice without a rebuild (§7.3)."""

import threading
from datetime import date, datetime

from deforestation_alerts.project.models import NativeGrid, Period, Project, add_mask, set_period_mask
from gui.scripts import source_layers as sl
from gui.scripts.map_layers import MapLayers

JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
FEB = Period(id="2026-02-01_2026-02-28", start=date(2026, 2, 1), end=date(2026, 2, 28))


class _Map:
    def add_layer(self, layer, key=""):
        pass

    def remove_layer(self, key, none_ok=False):
        pass


def _sync(monkeypatch):
    import gui.scripts.map_layers as ml
    monkeypatch.setattr(ml, "spawn_in_context", lambda target, args=(), **kw: target(*args) or threading.current_thread())


def _project():
    p = Project(name="l", created_at=datetime.now(), updated_at=datetime.now())
    p.periods = {JAN.id: JAN, FEB.id: FEB}
    p.active_period = FEB.id
    add_mask(p, name="forest", origin="gee", ref="x", asset_type="IMAGE", band="b",
             native=NativeGrid(crs="EPSG:4326", scale=30.0), mode="keep", period_id=FEB.id)
    return p


def test_names_follow_the_spec():
    assert sl.preview_name("RADD") == "RADD · preview"
    assert sl.harmonized_name("RADD", JAN) == "RADD · 2026-01-01 → 2026-01-31"
    assert sl.mask_name("forest") == "Mask forest"


def test_a_preview_legend_becomes_the_harmonized_one(monkeypatch):
    _sync(monkeypatch)
    layers = MapLayers(_Map())
    key = "src:RADD"
    layers.show(key, lambda: None, legend=sl.preview_legend(key, "RADD", date(2025, 10, 3), JAN.end))
    assert layers.legends.value[0].spec.title.key == "legend.title.preview"
    layers.refresh(key, lambda: None, "c1", legend=sl.harmonized_legend(key, "RADD", JAN))
    (entry,) = layers.legends.value
    assert entry.spec.title.key == "legend.title.harmonized" and entry.label.key == "legend.name.harmonized"


def test_mask_legends_follow_the_choice_and_the_active_period(monkeypatch):
    _sync(monkeypatch)
    p = _project()
    layers = MapLayers(_Map())
    layers.show("mask:forest", lambda: None, content_id="c", legend=sl.mask_layer_legend(p, "forest"))
    label = lambda: layers.legends.value[0].spec.labels[0].key  # noqa: E731
    assert label() == "legend.mask.keep"
    set_period_mask(p, FEB.id, "forest", mode="exclude")
    sl.refresh_mask_legends(layers, p)
    assert label() == "legend.mask.exclude"
    p.active_period = JAN.id  # the mask is off in January
    sl.refresh_mask_legends(layers, p)
    assert label() == "legend.mask.off"


def test_refreshing_mask_legends_never_rebuilds_nor_touches_other_layers(monkeypatch):
    """A mask shown before alignment has no content id (an Earth Engine
    preview), and a job may record a new one between ``content_of`` and the
    refresh (preflight M7): either way the refresh only swaps the legend."""
    _sync(monkeypatch)
    p = _project()
    built = []
    layers = MapLayers(_Map())
    layers.show("mask:forest", lambda: built.append("mask"), legend=sl.mask_layer_legend(p, "forest"))
    layers.show("src:RADD", lambda: built.append("src"), content_id="c1",
                legend=sl.harmonized_legend("src:RADD", "RADD", FEB))
    assert layers.content_of("mask:forest") is None and layers.content_of("src:RADD") == "c1"
    assert layers.content_of("off") is None
    set_period_mask(p, FEB.id, "forest", on=False)
    sl.refresh_mask_legends(layers, p)
    assert built == ["mask", "src"]
    by_id = {e.layer_id: e for e in layers.legends.value}
    assert by_id["mask:forest"].spec.labels[0].key == "legend.mask.off"
    assert by_id["src:RADD"].spec.title.key == "legend.title.harmonized"
    # build=None with a content id that differs from the requested one: still legend-only
    layers.refresh("mask:forest", None, "new", legend=sl.mask_layer_legend(p, "forest"))
    assert built == ["mask", "src"] and layers.is_on("mask:forest")
    # a key that is off records nothing
    layers.hide("mask:forest")
    layers.refresh("mask:forest", None, None, legend=sl.mask_layer_legend(p, "forest"))
    assert not layers.is_on("mask:forest") and [e.layer_id for e in layers.legends.value] == ["src:RADD"]


def test_the_set_legend_names_its_period_and_sources():
    from types import SimpleNamespace

    legend = sl.set_legend("alerts:set", SimpleNamespace(period_id=JAN.id, sources=["GLAD-L", "RADD"]))
    assert legend.layer_id == "alerts:set" and legend.spec.title.key == "legend.title.set"
    assert dict(legend.spec.title.args) == {"period": JAN.id, "sources": "GLAD-L + RADD"}
