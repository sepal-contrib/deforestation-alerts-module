"""Draw a local grid raster through localtileserver (spec §9.1).

Rasters written by the engine carry internal nearest overviews, so no pyramid
is built here. Colormaps must be matplotlib Colormap objects: localtileserver
rejects dicts. Blocking (starts a TileClient): call from a worker thread.
"""

from matplotlib.colors import ListedColormap

#: 1 = confirmed (orange), 2 = probable (purple)
ALERT_COLORMAP = ListedColormap(["#ff9800", "#7b1fa2"], name="alerts")


def add_raster_layer(map_, path, *, key: str, name: str, band: int = 1,
                     colormap=ALERT_COLORMAP, vmin=1, vmax=2, nodata=0, opacity: float = 1.0):
    from localtileserver import TileClient, get_leaflet_tile_layer

    client = TileClient(str(path))
    layer = get_leaflet_tile_layer(
        client, indexes=[band], colormap=colormap, vmin=vmin, vmax=vmax, nodata=nodata,
        name=name, opacity=opacity, max_zoom=20, max_native_zoom=20,
    )
    map_.remove_layer(key, none_ok=True)
    map_.add_layer(layer, key=key)
    return layer
