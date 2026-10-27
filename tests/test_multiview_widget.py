"""MultiView (gui/widget/multiview): the core traits, the plugin mixins, the
install order of the plugins, and the spec builders. Pure Python, no browser."""

from pathlib import Path

import pytest
import traitlets

from gui.widget.multiview import (
    ALL,
    Draw,
    FitBounds,
    Holes,
    LayerMenu,
    MultiView,
    Pick,
    PluginMixin,
    TileErrors,
    geojson_spec,
    plugin_order,
    set_view,
    tile_spec,
)

ROOT = Path(__file__).resolve().parents[1]
URL = "https://example.invalid/{z}/{x}/{y}.png"

CORE = {"views", "view_layout", "basemap", "theme", "labels", "layers", "plugins", "view"}
MIXIN_TRAITS = {"layer_menu_toggles", "fit_bounds_bounds", "fit_bounds_views", "draw", "draw_edit_mode", "pick_tag", "pick_event"}


class AllViews(MultiView, LayerMenu, FitBounds, Draw, Holes, TileErrors, Pick):
    pass


# ---- core traits ------------------------------------------------------------

def test_the_core_traits_and_their_defaults():
    w = MultiView()
    assert CORE <= set(w.keys)
    assert w.views == [] and w.view_layout == "row" and w.basemap == {} and w.theme == "light"
    assert w.labels == {} and w.layers == [] and w.plugins == []
    assert w.view == {"center": [0.0, 0.0], "zoom": 2, "n": 0, "origin": "host"}


def test_layout_stays_the_ipywidgets_layout_trait():
    # the arrangement is ``view_layout``: ``layout`` must stay ipywidgets' Layout,
    # or the frontend logs "Could not add LayoutView" and Layout(height=...) is lost
    import ipywidgets

    w = MultiView(layout=ipywidgets.Layout(height="300px"))
    assert isinstance(w.layout, ipywidgets.Layout) and w.layout.height == "300px"
    assert w.get_state()["layout"].startswith("IPY_MODEL_")


def test_the_base_class_has_a_placeholder_esm_that_renders_nothing():
    assert MultiView._esm == "export default {render() {}}"
    assert MultiView().get_state()["_esm"] == "export default {render() {}}"


def test_nested_defaults_are_not_shared_between_widgets():
    # a Dict trait copies its default shallowly: nested lists need a @default
    a, b = AllViews(), AllViews()
    a.view["center"].append(1.0)
    a.draw["fc"]["features"].append({"type": "Feature"})
    assert b.view["center"] == [0.0, 0.0] and b.draw["fc"]["features"] == []
    assert AllViews().view["center"] == [0.0, 0.0] and AllViews().draw["fc"]["features"] == []


# ---- mixins -------------------------------------------------------------------

def test_mixin_traits_are_synced_and_in_the_first_state():
    w = AllViews()
    assert CORE | MIXIN_TRAITS <= set(w.keys)
    state = w.get_state()
    assert state["plugins"] == ["layerMenu", "fitBounds", "draw", "holes", "tileErrors", "pick"]
    assert state["layer_menu_toggles"] == {}
    assert state["fit_bounds_bounds"] is None and state["fit_bounds_views"] == "all"
    assert state["draw"] == {"fc": {"type": "FeatureCollection", "features": []}, "gen": 0, "seq": 0,
                             "origin": "host"}
    assert state["draw_edit_mode"] is False


def test_a_class_without_a_mixin_has_none_of_its_traits():
    class OnlyMenu(MultiView, LayerMenu):
        pass

    w = OnlyMenu()
    assert "layer_menu_toggles" in w.keys
    assert not ({"fit_bounds_bounds", "fit_bounds_views", "draw", "draw_edit_mode", "pick_tag", "pick_event"} & set(w.keys))
    assert w.plugins == ["layerMenu"]


def test_every_mixin_names_its_js_plugin():
    names = {m: m._plugin_name for m in (LayerMenu, FitBounds, Draw, Holes, TileErrors, Pick)}
    assert names == {LayerMenu: "layerMenu", FitBounds: "fitBounds", Draw: "draw", Holes: "holes",
                     TileErrors: "tileErrors", Pick: "pick"}
    assert Holes._plugin_requires == ("draw",)
    assert Pick._plugin_requires == ("draw",)
    assert all(issubclass(m, PluginMixin) for m in names)


# ---- plugin order -----------------------------------------------------------------

def test_plugins_keep_mro_order_when_no_requirement_is_out_of_place():
    class V(MultiView, TileErrors, LayerMenu, FitBounds):
        pass

    assert plugin_order(V) == ["tileErrors", "layerMenu", "fitBounds"]
    assert V().plugins == ["tileErrors", "layerMenu", "fitBounds"]


def test_a_plugin_listed_before_its_requirement_moves_right_behind_it():
    class HolesLast(MultiView, LayerMenu, FitBounds, Holes, Draw):
        pass

    class HolesFirst(MultiView, Holes, LayerMenu, Draw, TileErrors):
        pass

    assert plugin_order(HolesLast) == ["layerMenu", "fitBounds", "draw", "holes"]
    assert plugin_order(HolesFirst) == ["layerMenu", "draw", "holes", "tileErrors"]
    assert HolesFirst().get_state()["plugins"] == ["layerMenu", "draw", "holes", "tileErrors"]


def test_a_subclass_inherits_its_parents_plugins():
    class Child(AllViews):
        pass

    assert Child().plugins == ["layerMenu", "fitBounds", "draw", "holes", "tileErrors", "pick"]


def test_a_missing_requirement_fails_when_the_class_is_created():
    with pytest.raises(TypeError, match="holes requires draw"):
        class Broken(MultiView, LayerMenu, Holes):
            pass


def test_a_requirement_cycle_fails_when_the_class_is_created():
    class Ping(PluginMixin):
        _plugin_name = "ping"
        _plugin_requires = ("pong",)

    class Pong(PluginMixin):
        _plugin_name = "pong"
        _plugin_requires = ("ping",)

    with pytest.raises(TypeError, match="cycle"):
        class Loop(MultiView, Ping, Pong):
            pass


def test_a_third_party_mixin_with_its_own_traits_works_like_a_built_in_one():
    class Timeline(PluginMixin):
        _plugin_name = "timeline"
        _plugin_requires = ("layerMenu",)
        timeline_dates = traitlets.List().tag(sync=True)

    class V(MultiView, Timeline, LayerMenu):
        pass

    w = V()
    assert w.plugins == ["layerMenu", "timeline"]
    assert "timeline_dates" in w.keys and w.get_state()["timeline_dates"] == []


def test_the_plugins_trait_can_still_be_set_explicitly():
    w = AllViews(plugins=["layerMenu"])
    assert w.plugins == ["layerMenu"]


# ---- spec builders ------------------------------------------------------------------

def test_tile_spec_has_the_exact_core_shape():
    assert tile_spec("t", "after", URL, name="True Color") == {
        "id": "t", "view": "after", "kind": "tile", "url": URL, "name": "True Color", "visible": True,
        "opacity": 1.0, "z": None, "group": None, "attribution": "", "meta": {},
    }
    s = tile_spec("t", ["before", "after"], URL, name="T", group="alert", visible=0, opacity=1, z=7,
                  attribution="GEE", meta={"reportErrors": True})
    assert s["view"] == ["before", "after"] and s["group"] == "alert" and s["visible"] is False
    assert s["opacity"] == 1.0 and isinstance(s["opacity"], float) and s["z"] == 7
    assert s["attribution"] == "GEE" and s["meta"] == {"reportErrors": True}


def test_geojson_spec_has_the_exact_core_shape():
    data = {"type": "FeatureCollection", "features": []}
    assert geojson_spec("bb", ALL, data, name="BB") == {
        "id": "bb", "view": "all", "kind": "geojson", "data": data, "style": {}, "name": "BB",
        "visible": True, "opacity": 1.0, "z": None, "group": None, "meta": {},
    }
    g = geojson_spec("bb", "before", data, name="BB", group="alert", style={"color": "yellow"}, z=3,
                     meta={"hideWhileEditing": True})
    assert g["style"] == {"color": "yellow"} and g["z"] == 3 and g["meta"] == {"hideWhileEditing": True}


def test_view_none_targets_every_view_and_a_tuple_becomes_a_list():
    assert ALL == "all"
    assert tile_spec("t", None, URL, name="T")["view"] == "all"
    assert geojson_spec("g", None, {}, name="G")["view"] == "all"
    assert tile_spec("t", ("a", "b"), URL, name="T")["view"] == ["a", "b"]


def test_the_builders_copy_style_and_meta():
    style, meta = {"color": "red"}, {"reportErrors": True}
    g = geojson_spec("g", ALL, {}, name="G", style=style, meta=meta)
    t = tile_spec("t", ALL, URL, name="T", meta=meta)
    style["color"] = "blue"
    meta["reportErrors"] = False
    assert g["style"] == {"color": "red"} and g["meta"] == {"reportErrors": True}
    assert t["meta"] == {"reportErrors": True}


# ---- the view -------------------------------------------------------------------------

def test_set_view_bumps_n_and_marks_the_view_as_the_hosts():
    w = MultiView()
    set_view(w, (-1.5, -60.25), 16)
    assert w.view == {"center": [-1.5, -60.25], "zoom": 16, "n": 1, "origin": "host"}
    set_view(w, (-1.5, -60.25), 16)
    assert w.view["n"] == 2, "the same view again is still a new host view"
    set_view(w, [3, 4], 12.5)
    assert w.view == {"center": [3.0, 4.0], "zoom": 12.5, "n": 3, "origin": "host"}


def test_a_user_write_back_does_not_count_as_a_host_view():
    w = MultiView()
    set_view(w, (0, 0), 5)
    set_view(w, (1, 1), 6)
    # the frontend's debounced write-back, sent before it saw n=2, lands late
    w.view = {"center": [9.0, 9.0], "zoom": 3, "n": 1, "origin": "user"}
    set_view(w, (2, 2), 7)
    assert w.view["n"] == 3, "never reuses an n the frontend has already applied"
    w.view = {"center": [9.0, 9.0], "zoom": 3, "n": 3, "origin": "user"}
    set_view(w, (2, 2), 7)
    assert w.view == {"center": [2.0, 2.0], "zoom": 7, "n": 4, "origin": "host"}


# ---- the dependency -----------------------------------------------------------------------

def test_anywidget_is_a_declared_dependency():
    lines = (ROOT / "sepal_environment.yml").read_text().splitlines()
    assert any(line.split("#", 1)[0].strip() == "- anywidget>=0.9" for line in lines)
