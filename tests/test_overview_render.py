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
