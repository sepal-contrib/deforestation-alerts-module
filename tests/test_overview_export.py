import geopandas as gpd
from shapely.geometry import Polygon
from gui.tile.overview_tile import _bind_export


def _gdf():
    g = gpd.GeoDataFrame(
        {"area_ha": [1.0], "alert_polygon": [None]},
        geometry=gpd.GeoSeries([Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])], crs="EPSG:4326"),
    )
    g["bounding_box"] = g.geometry.envelope
    g["point"] = g.geometry.centroid
    return g


class _FakeBtn:
    def __init__(self):
        self.url = None

    def set_url(self, path):
        self.url = path


def test_bind_export_writes_to_the_workspace_and_sets_a_home_relative_url(ws):
    """The export lands in the user's workspace and the button gets a URL that
    ``su.create_download_link`` can turn into a working SEPAL link.

    A container-absolute path would make ``create_download_link`` fall back to
    the raw path and produce a silent 404, so the home-relative shape is the
    point of the assertion, not an implementation detail.
    """
    btn = _FakeBtn()
    folder = ws.results_dir("recipe_a")
    handler = _bind_export(btn, "gpkg", _gdf(), ws, folder,
                           ["GLAD-L"], "2026-01-01", "2026-06-12")
    handler(None, None, None)

    expected = f"{folder}/alerts_glad-l_2026-01-01_2026-06-12.gpkg"
    assert ws.exists(expected)
    assert btn.url == (
        "module_results/deforestation_alerts/recipe_a/"
        "alerts_glad-l_2026-01-01_2026-06-12.gpkg"
    )


def test_bind_export_failure_leaves_the_button_unset(ws):
    """An export that blows up is logged, not raised into the click handler —
    and must not point the button at a file that was never written."""
    btn = _FakeBtn()
    handler = _bind_export(btn, "shp", _gdf(), ws, ws.results_dir("recipe_a"),
                           ["GLAD-L"], "2026-01-01", "2026-06-12")
    handler(None, None, None)

    assert btn.url is None
    assert ws.client.files.files == {}
