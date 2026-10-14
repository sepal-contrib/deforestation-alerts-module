"""Builders for the ``layers`` trait's specs, and the host side of the view.

The spec shape is the JS core's (spec §3):

    {id, view, kind, url | data, style?, name, visible, opacity, z,
     group, attribution?, meta}

``view`` is a view id, a list of view ids, or ``ALL``. ``z`` may be None, so
that a store can fill it from its z bands (``LayerStore``). ``group`` and
``meta`` are free: apps use ``group`` to clear layers together, and plugins
read ``meta`` (``hideWhileEditing``, ``reportErrors``).
"""

import threading

#: The ``view`` value that targets every view.
ALL = "all"

_VIEW_LOCK = threading.Lock()


def _view(view):
    if view is None:
        return ALL
    if isinstance(view, str):
        return view
    return list(view)


def tile_spec(layer_id, view, url, *, name, group=None, visible=True, opacity=1.0, z=None, attribution="",
              meta=None) -> dict:
    """An XYZ tile layer. ``view=None`` means ``ALL``."""
    return {"id": layer_id, "view": _view(view), "kind": "tile", "url": url, "name": name,
            "visible": bool(visible), "opacity": float(opacity), "z": None if z is None else int(z),
            "group": group, "attribution": attribution, "meta": dict(meta or {})}


def geojson_spec(layer_id, view, data, *, name, group=None, style=None, visible=True, opacity=1.0, z=None,
                 meta=None) -> dict:
    """A GeoJSON layer. Each feature's ``properties.style`` is merged over
    ``style`` in the browser. ``view=None`` means ``ALL``."""
    return {"id": layer_id, "view": _view(view), "kind": "geojson", "data": data, "style": dict(style or {}),
            "name": name, "visible": bool(visible), "opacity": float(opacity),
            "z": None if z is None else int(z), "group": group, "meta": dict(meta or {})}


def set_view(widget, center, zoom) -> None:
    """Move every view to ``center`` (``(lat, lon)``) and ``zoom``.

    Bumps ``n`` past every ``n`` this process has written, even when the
    frontend's write-back carried an older one, so the frontend always
    applies it.
    """
    zoom = float(zoom)
    with _VIEW_LOCK:
        last = max(int((widget.view or {}).get("n", 0)), getattr(widget, "_mv_host_n", 0))
        widget._mv_host_n = last + 1
        widget.view = {"center": [float(center[0]), float(center[1])],
                       "zoom": int(zoom) if zoom.is_integer() else zoom,
                       "n": last + 1, "origin": "host"}
