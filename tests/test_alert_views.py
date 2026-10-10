"""``AlertViews``: the app's ``MultiView`` points at the committed bundle and
installs the plugins its mixins name, in order."""

from gui.widget.alert_views import STATIC, AlertViews


def test_alert_views_ships_the_committed_bundle():
    state = AlertViews().get_state()
    assert state["_esm"] == (STATIC / "multiview.js").read_text()
    assert state["_css"] == (STATIC / "multiview.css").read_text()


def test_alert_views_installs_its_mixins_plugins_in_order():
    widget = AlertViews()
    assert widget.plugins == ["layerMenu", "fitBounds", "draw", "holes"]
    for trait in ("layer_menu_toggles", "fit_bounds_bounds", "fit_bounds_views", "draw", "draw_edit_mode"):
        assert trait in widget.keys, trait


def test_alert_views_installs_holes_after_draw():
    plugins = AlertViews().plugins
    assert plugins.index("draw") < plugins.index("holes")
