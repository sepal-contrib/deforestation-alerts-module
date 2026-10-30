"""The *Original alert* layer: the Integrate polygons of the alert on screen
and of the rest of its saved review group, outlined on every view.

Drawn when an alert opens (``analysis_tile.view_actual_alert``) and again
after a group Save, which changes the group. It is in the ``alert`` group, so
the next alert's ``panes.clear("alert")`` takes it off. The set's whole
combined raster stays available as the *All alerts* layer
(``imagery.image_slider_map_callback``).
"""

import json

import shapely

from gui.scripts import legend_specs
from gui.tile.analysis import nearby
from gui.tile.analysis.pane_layers import ALERT_TOP_Z, geojson_spec

#: The outlines' spec id on the views, and their layer-menu name.
ORIGINAL_ID = "analysis-original-alert"
ORIGINAL_LAYER_NAME = "Original alert"
#: outline only, in the confirmed-alert colour, so the imagery stays visible
_STYLE = {"color": legend_specs.ALERT_CLASSES[0][1], "weight": 2, "opacity": 1.0, "fill": False}


def cluster_positions(app_state, position=None) -> list:
    """The alert at ``position`` (the one on screen by default) and, while
    grouping is effective, every other row of its saved group; sorted. Empty
    without a valid alert or without polygons."""
    gdf = app_state.alerts_gdf.value
    position = app_state.actual_alert_id.value if position is None else position
    if getattr(gdf, "geometry", None) is None or position is None or not 0 <= position < len(gdf):
        return []
    group = nearby.open_group(app_state, position)
    return sorted({int(position), *(() if group is None else (group[0], *group[1]))})


def layer_spec(gdf, positions) -> dict:
    """The outlines of ``positions``: one GeoJSON spec on every view, under
    the alert's box and above the nearby and earlier outlines, hidden while
    the reviewer draws, in the menu's *Alerts* section."""
    features = []
    for p in positions:
        geom = gdf.geometry.iloc[p]
        if geom is None or geom.is_empty:
            continue
        features.append({"type": "Feature", "properties": {"position": int(p), "alert": int(p) + 1},
                         "geometry": json.loads(shapely.to_geojson(geom))})
    return geojson_spec(ORIGINAL_ID, "all", {"type": "FeatureCollection", "features": features},
                        name=ORIGINAL_LAYER_NAME, group="alert", style=_STYLE, z=ALERT_TOP_Z - 1,
                        hide_while_editing=True, section="alerts")


def draw_layer(widgets, app_state) -> None:
    """Outline the alert on screen and its group (``cluster_positions``);
    take the layer off when there is no alert."""
    panes = getattr(widgets, "panes", None)
    if panes is None:
        return
    positions = cluster_positions(app_state)
    if not positions:
        panes.remove(ORIGINAL_ID)
        return
    panes.add(layer_spec(app_state.alerts_gdf.value, positions))
