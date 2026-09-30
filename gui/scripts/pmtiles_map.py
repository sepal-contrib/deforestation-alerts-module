"""Draw an alert set's PMTiles on the map (spec §9.2).

ipyleaflet's PMTilesLayer renders through protomaps-leaflet ``json_style``,
which copies paint values verbatim (a MapLibre ``match`` expression paints
black) and ignores opacity. So: one layer per status and geometry type with
scalar paint values, and a *lighter colour*, not opacity, for earlier sets.
The layer is built directly (``vectortileserver.TileClient`` only serves the
archive); ``client.create_leaflet_layer`` pulls in extras this path does not need.
"""

import logging

logger = logging.getLogger("SEPALUI.deforestation_alerts")

#: status_code -> colour (0 not reviewed, 1 confirmed, 2 false positive, 3 need revision)
STATUS_COLORS = {0: "#9e9e9e", 1: "#d32f2f", 2: "#388e3c", 3: "#f9a825"}


def _lighten(hex_color: str, amount: float = 0.6) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    mix = lambda c: int(c + (255 - c) * amount)  # noqa: E731
    return f"#{mix(r):02x}{mix(g):02x}{mix(b):02x}"


def build_alert_style(url: str, faded: bool = False) -> dict:
    layers = []
    for code, color in STATUS_COLORS.items():
        c = _lighten(color) if faded else color
        flt = ["==", "status_code", code]
        layers.append({"id": f"alerts-fill-{code}", "type": "fill", "source": "alerts",
                       "source-layer": "alerts", "filter": flt, "paint": {"fill-color": c}})
        layers.append({"id": f"alerts-line-{code}", "type": "line", "source": "alerts",
                       "source-layer": "alerts", "filter": flt,
                       "paint": {"line-color": c, "line-width": 1}})
        layers.append({"id": f"points-{code}", "type": "circle", "source": "alerts",
                       "source-layer": "points", "filter": flt,
                       "paint": {"circle-radius": 4, "circle-color": c,
                                 "circle-stroke-color": c, "circle-stroke-width": 1}})
    return {"version": 8, "sources": {"alerts": {"type": "vector", "url": url}}, "layers": layers}


def add_set_pmtiles(map_, path, *, key: str, name: str, faded: bool = False):
    """Blocking (starts/reuses the tile server): call from a worker thread."""
    from ipyleaflet import PMTilesLayer
    from vectortileserver.client import TileClient

    client = TileClient(str(path))
    try:
        client.enable_jupyter_loopback()  # no-op when a proxy prefix disabled the bridge
    except Exception:
        logger.warning("jupyter-loopback bridge unavailable for %s", path, exc_info=True)
    layer = PMTilesLayer(url=client.pmtiles_url, style=build_alert_style(client.pmtiles_url, faded))
    layer.name = name
    map_.remove_layer(key, none_ok=True)
    map_.add_layer(layer, key=key)
    return layer


def remove_layer(map_, key: str) -> None:
    map_.remove_layer(key, none_ok=True)
