"""Draw a local grid raster through localtileserver (spec §9.1).

Rasters written by the engine carry internal nearest overviews, so no pyramid
is built here. Colormaps must be matplotlib Colormap objects: localtileserver
rejects dicts. Blocking (starts a TileClient): call from a worker thread.
"""

from gui.scripts import legend_specs

#: 1 = confirmed (orange), 2 = probable (purple); from the one palette table
ALERT_COLORMAP = legend_specs.alert_colormap()
ALERT_EE_VIS = legend_specs.alert_ee_vis()
#: an aligned mask (1 = on, 0 = no-data): one grey for keep and exclude masks alike
MASK_COLOR = legend_specs.MASK_COLOR
MASK_OPACITY = legend_specs.MASK_OPACITY
MASK_COLORMAP = legend_specs.mask_colormap()
MASK_EE_VIS = legend_specs.mask_ee_vis()


def add_raster_layer(map_, path, *, key: str, name: str, band: int = 1,
                     colormap=ALERT_COLORMAP, vmin=1, vmax=2, nodata=0, opacity: float = 1.0,
                     visible: bool = True):
    from localtileserver import TileClient, get_leaflet_tile_layer

    client = TileClient(str(path))
    layer = get_leaflet_tile_layer(
        client, indexes=[band], colormap=colormap, vmin=vmin, vmax=vmax, nodata=nodata,
        name=name, opacity=opacity, visible=visible, max_zoom=20, max_native_zoom=20,
    )
    map_.add_layer(layer, key=key)  # replaces a same-key layer itself (inside a LockedMap gate)
    return layer
