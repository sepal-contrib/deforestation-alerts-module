"""Tier-1: the Overview tile renders alerts as status marker clusters + a toggle
menu control (the original Voila UX) on the single shared map.

Regression coverage for two bugs found porting the tile:
- alerts must actually render from BOTH data feeds — the Filter feed
  (``convert_to_geopandas``: active polygon ``geometry`` + ``bounding_box`` +
  ``point``) and the recipe-load feed (``backfill_geometry`` adds a polygon
  ``geometry``). The marker path reads only ``point`` locations, so unlike the
  earlier ``GeoData`` approach it never JSON-serializes a Polygon.
- one ``MarkerCluster`` per status + a ``MenuControl`` toggle are added to the
  map, and each cluster is named so the tile can remove exactly its own layers.

No GEE, no browser.
"""

import geopandas as gpd
from ipyleaflet import Map, MarkerCluster
from shapely.geometry import Point, Polygon

from deforestation_alerts.scripts.alert_filter_helper import convert_to_geopandas
from deforestation_alerts.scripts.recipe_helper import backfill_geometry
from deforestation_alerts.scripts.overview_helper import (
    build_status_marker_clusters,
    create_markers_ipyvuetify,
)
from gui.tile.overview_tile import _OVERVIEW_AOI_LAYER, sync_overview_aoi_layer


class _FakeMap:
    """Records add_ee_layer / remove_layer calls (no GEE, no real ipyleaflet)."""

    def __init__(self):
        self.added = []
        self.removed = []

    def add_ee_layer(self, ee_object, vis_params, name):
        self.added.append(name)

    def remove_layer(self, name, none_ok=False):
        self.removed.append(name)


def _filter_feed_gdf():
    """alerts_gdf as the Filter tile publishes it (convert_to_geopandas)."""
    poly = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    vectorized = gpd.GeoDataFrame(
        {
            "alert_type_unique": [[1], [110]],
            "count": [4, 9],
            "alert_date_min": [2022.1, 2023.2],
            "alert_date_max": [2022.3, 2023.4],
            "area_ha": [0.5, 1.2],
            "geometry": [poly, poly],
        },
        geometry="geometry",
        crs="EPSG:4326",
    )
    gdf = convert_to_geopandas(vectorized)
    gdf.loc[0, "status"] = "Confirmed"  # exercise >1 status group
    return gdf


def _recipe_load_feed_gdf():
    """alerts_gdf as the recipe-load path builds it (backfill_geometry adds a
    polygon ``geometry`` from ``bounding_box``)."""
    bbox = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    gdf = gpd.GeoDataFrame(
        {
            "alert_date_min": [2022.1, 2023.2],
            "alert_date_max": [2022.3, 2023.4],
            "status": ["Confirmed", "Not reviewed"],
            "bounding_box": [bbox, bbox],
            "point": [Point(0.5, 0.5), Point(0.6, 0.6)],
            "alert_polygon": [None, None],
        }
    )
    return backfill_geometry(gdf)


def test_markers_built_and_grouped_by_status():
    for gdf in (_filter_feed_gdf(), _recipe_load_feed_gdf()):
        groups = create_markers_ipyvuetify(
            gdf, "point", ["alert_date_min", "alert_date_max"], "status",
            lambda w, e, d: None,
        )
        # one group per distinct status, every row turned into a marker
        assert set(groups) == set(gdf["status"].unique())
        assert sum(len(v) for v in groups.values()) == len(gdf)


def test_markers_built_when_a_row_has_missing_dates():
    # Live regression: vectorize.py yields None (-> NaN) dates for a polygon whose
    # date pixels are all nodata. The marker builder converts every row's dates in
    # one loop with no per-row guard, so a single NaN used to raise and blank the
    # ENTIRE Overview map (total still showed). Every row must still become a marker.
    gdf = _filter_feed_gdf()
    gdf.loc[0, "alert_date_min"] = float("nan")
    gdf.loc[0, "alert_date_max"] = float("nan")
    groups = create_markers_ipyvuetify(
        gdf, "point", ["alert_date_min", "alert_date_max"], "status",
        lambda w, e, d: None,
    )
    assert sum(len(v) for v in groups.values()) == len(gdf)


def test_aoi_outline_drawn_when_active_and_on_its_own_layer():
    # The AOI tile removes its "aoi" layer on deactivation, so the Overview draws
    # the AOI itself — on a DISTINCT layer it owns, never the AOI tile's "aoi".
    m = _FakeMap()
    sync_overview_aoi_layer(m, feature_collection=object(), show=True)
    assert m.added == [_OVERVIEW_AOI_LAYER]
    assert _OVERVIEW_AOI_LAYER != "aoi"        # must not collide with the AOI tile
    assert m.removed == [_OVERVIEW_AOI_LAYER]  # idempotent: always clears first


def test_aoi_outline_removed_when_inactive_or_no_aoi():
    # Deactivated: remove the outline, never add it.
    m = _FakeMap()
    sync_overview_aoi_layer(m, feature_collection=object(), show=False)
    assert m.added == []
    assert m.removed == [_OVERVIEW_AOI_LAYER]
    # Active but no AOI selected: nothing to draw.
    m2 = _FakeMap()
    sync_overview_aoi_layer(m2, feature_collection=None, show=True)
    assert m2.added == []
    assert m2.removed == [_OVERVIEW_AOI_LAYER]


def test_clusters_and_menu_control_added_with_named_layers():
    for gdf in (_filter_feed_gdf(), _recipe_load_feed_gdf()):
        groups = create_markers_ipyvuetify(
            gdf, "point", ["alert_date_min", "alert_date_max"], "status",
            lambda w, e, d: None,
        )
        m = Map()
        clusters, menu = build_status_marker_clusters(
            m, groups, name_prefix="overview-cluster"
        )
        # one cluster per status, all on the map, all named for targeted cleanup
        assert len(clusters) == len(groups)
        on_map = [layer for layer in m.layers if isinstance(layer, MarkerCluster)]
        assert len(on_map) == len(groups)
        for c in clusters:
            assert c.name.startswith("overview-cluster-")
        # the menu control was added so the user can toggle statuses
        assert menu in m.controls
