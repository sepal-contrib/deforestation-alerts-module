import os
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


def test_bind_export_writes_file_and_sets_url(tmp_path):
    btn = _FakeBtn()
    handler = _bind_export(btn, "gpkg", _gdf(), str(tmp_path),
                           ["GLAD-L"], "2026-01-01", "2026-06-12")
    handler(None, None, None)
    assert btn.url is not None
    assert os.path.basename(btn.url) == "alerts_glad-l_2026-01-01_2026-06-12.gpkg"
    assert os.path.exists(btn.url)
