"""``AnalysisWidgets``: both views are one ``AlertViews``, written through
``PaneLayers``, with a header cell per view and the app's theme."""

from pysepal.solara.theme import ThemeState

from gui.tile.analysis.pane_layers import PaneLayers, PaneTarget
from gui.tile.analysis.widgets import AnalysisWidgets
from gui.widget.alert_views import AlertViews


def test_one_views_widget_carries_both_panes():
    w = AnalysisWidgets()
    assert isinstance(w.panes_widget, AlertViews)
    assert "analysis-views" in w.panes_widget._dom_classes
    assert isinstance(w.panes, PaneLayers) and w.panes.widget is w.panes_widget
    assert [v["id"] for v in w.panes_widget.views] == ["before", "after"]
    for target, view in ((w.pane_before, "before"), (w.pane_after, "after")):
        assert isinstance(target, PaneTarget)
        assert (target.view, target.group, target.report_errors) == (view, "alert", True)
        assert target.section == "imagery" and target.sections == {"all-alerts": "alerts"}
    assert w.head_before.class_ == "analysis-mapbox" and w.head_before.children == [w.map_title_before]
    assert w.head_after.class_ == "analysis-mapbox" and w.head_after.children == [w.map_title_after]
    for gone in ("map31", "map32", "map_before", "map_after", "draw_before", "draw_after", "aoi_control",
                 "set_synced_view", "set_synced_draw_data"):
        assert not hasattr(w, gone), gone


def test_the_views_follow_the_app_theme():
    theme = ThemeState(mode="dark")
    w = AnalysisWidgets(theme_state=theme)
    assert w.panes_widget.theme == "dark"
    theme.set_dark(False)
    assert w.panes_widget.theme == "light"
    theme.set_dark(True)
    assert w.panes_widget.theme == "dark"


def test_a_sepal_basemap_follows_the_app_theme():
    """SEPAL Dark opens in the dark theme, SEPAL Light in the light one, and a
    theme switch swaps one for the other, whichever the reviewer picked; a
    satellite basemap stays."""
    theme = ThemeState(mode="dark")
    w = AnalysisWidgets(theme_state=theme)
    assert w.panes_widget.basemap_switcher_selected == "sepal_dark"
    theme.set_dark(False)
    assert w.panes_widget.basemap_switcher_selected == "sepal_light"
    w.panes_widget.basemap_switcher_selected = "sepal_dark"  # the reviewer's pick, from the browser
    theme.set_dark(True)
    assert w.panes_widget.basemap_switcher_selected == "sepal_dark"
    theme.set_dark(False)
    assert w.panes_widget.basemap_switcher_selected == "sepal_light"
    w.panes_widget.basemap_switcher_selected = "google"
    theme.set_dark(True)
    assert w.panes_widget.basemap_switcher_selected == "google"
    assert AnalysisWidgets(theme_state=ThemeState(mode="light")).panes_widget.basemap_switcher_selected \
        == "sepal_light"
    assert AnalysisWidgets().panes_widget.theme == "light", "no theme state: light"
