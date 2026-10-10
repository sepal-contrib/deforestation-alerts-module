"""The one writer of the Analysis views (``gui.widget.alert_views.AlertViews``).

The views are one browser component (``leaflet-multiview``): their sync,
layer menus and polygon editing run in the browser, and Python talks to them
through synced traits only. This facade is the only code in the app that
writes those traits:

* layers -- every layer on the views is a *spec* (a plain dict the frontend
  diffs by id), kept by ``LayerStore``: one lock around every
  read-modify-write, a new list per write, and ``add(spec, gate=...)`` calling
  the gate outside that lock (the imagery worker passes
  ``AnalysisState.apply_if_current``, so a layer built for an alert the
  reviewer has left is dropped);
* the drawing -- ``DrawState`` owns it, so ``on_drawing_edit`` hears only the
  reviewer's finished edits, never ``set_drawing``;
* the view, the zoom-to-alert bounds, edit mode and the labels.

``PaneTarget`` is the slice of the SepalMap API the layer builders call
(``imagery``, ``gui.scripts.raster_map.add_raster_layer``,
``gui.scripts.vis_layers.builder``): their adds land here as specs, unchanged.
"""

import copy
import itertools
import threading
from contextlib import contextmanager

from pysepal.mapping.basemaps import xyz_tiles

from gui.i18n import t
from gui.widget.multiview import specs as mv_specs
from gui.widget.multiview.draw_state import DrawState
from gui.widget.multiview.layer_store import LayerStore

#: Draw-order bands: alert imagery < visualization layers < earlier reviews.
#: Within a band, later adds draw on top (``PaneTarget`` counts up from it).
Z_BASE = {"alert": 1_000_000, "vis": 2_000_000, "earlier": 3_000_000}

#: The two views, in screen order.
_VIEWS = ({"id": "before"}, {"id": "after"})


def _meta(hide_while_editing=False, report_errors=False) -> dict:
    """The app's ``meta`` keys, each only when it is on."""
    meta = {}
    if hide_while_editing:
        meta["hideWhileEditing"] = True
    if report_errors:
        meta["reportErrors"] = True
    return meta


def tile_spec(layer_id, view, url, *, name, group, visible=True, opacity=1.0, z=None, attribution="",
              hide_while_editing=False, report_errors=False) -> dict:
    """An XYZ tile layer on ``view`` (``"before"``, ``"after"`` or ``"all"``)."""
    return mv_specs.tile_spec(layer_id, view, url, name=name, group=group, visible=visible, opacity=opacity,
                              z=z, attribution=attribution,
                              meta=_meta(hide_while_editing=hide_while_editing, report_errors=report_errors))


def geojson_spec(layer_id, view, data, *, name, group, style=None, visible=True, z=None,
                 hide_while_editing=False) -> dict:
    """A GeoJSON layer on ``view``. A feature's ``properties.style`` is merged
    over ``style`` in the browser."""
    return mv_specs.geojson_spec(layer_id, view, data, name=name, group=group, style=style, visible=visible,
                                 z=z, meta=_meta(hide_while_editing=hide_while_editing))


def pane_labels() -> dict:
    """The component's own strings, in the current language (the ``labels``
    trait: ``"<plugin>.<key>"`` overrides of the plugins' English defaults)."""
    return {
        "layerMenu.title": t("analysis.panes.layers"),
        "fitBounds.button": t("analysis.panes.zoom_alert"),
        "draw.hint": t("analysis.panes.vertex_hint"),
        "holes.fill": t("analysis.panes.fill_holes"),
        "holes.remove": t("analysis.panes.remove_hole"),
        "holes.hint": t("analysis.panes.remove_hole_hint"),
    }


class PaneLayers:
    """Everything Python writes to the Analysis views.

    ``widget`` is an ``AlertViews``; construction gives it the two views, the
    zoom-to-alert button on the after view only, the satellite basemap and
    the translated labels.
    """

    def __init__(self, widget, gee_interface=None):
        self.widget = widget
        self._gee = gee_interface
        self._store = LayerStore(widget, z_bands=Z_BASE)
        self._draw = DrawState(widget)
        widget.views = [dict(v) for v in _VIEWS]
        widget.fit_bounds_views = ["after"]
        satellite = xyz_tiles["SATELLITE"]
        widget.basemap = {"url": satellite["url"], "attribution": satellite["attribution"]}
        widget.labels = pane_labels()

    # ---- layers ---------------------------------------------------------
    def add(self, spec, *, gate=None) -> None:
        """Add ``spec`` (or replace the one with its id); ``gate(write)``
        decides whether it lands (``LayerStore.add``)."""
        self._store.add(spec, gate=gate)

    def remove(self, layer_id) -> None:
        self._store.remove(layer_id)

    def clear(self, group) -> None:
        self._store.clear(group)

    def spec(self, layer_id):
        return self._store.spec(layer_id)

    def ids(self, group=None) -> list:
        return self._store.ids(group)

    def tile_url(self, ee_object, vis) -> str:
        """The XYZ URL of an Earth Engine object. Blocks on getMapId: call it
        from a worker."""
        from pysepal.mapping.visualization import process_vis_params

        image, _obj, params = process_vis_params(ee_object, vis_params=dict(vis or {}), viz={},
                                                 use_map_vis=False)
        if self._gee is not None:
            map_id = self._gee.get_map_id(image, params)
        else:
            map_id = image.getMapId(params)
        return map_id["tile_fetcher"].url_format

    # ---- view -----------------------------------------------------------
    def set_view(self, center, zoom) -> None:
        """Move every view to ``center`` (``(lat, lon)``) and ``zoom``."""
        mv_specs.set_view(self.widget, center, zoom)

    def set_focus_bounds(self, bounds) -> None:
        """The zoom-to-alert button's bounds: shapely's ``(minx, miny, maxx,
        maxy)``, or None to hide the button."""
        if bounds is None:
            self.widget.fit_bounds_bounds = None
            return
        minx, miny, maxx, maxy = (float(b) for b in bounds)
        self.widget.fit_bounds_bounds = [[miny, minx], [maxy, maxx]]

    def relabel(self) -> None:
        """Re-read the labels in the current language."""
        self.widget.labels = pane_labels()

    # ---- drawing --------------------------------------------------------
    def set_edit_mode(self, on) -> None:
        self._draw.set_edit_mode(bool(on))

    def set_drawing(self, features) -> None:
        """Replace the drawing in every view. Never heard by ``on_drawing_edit``."""
        self._draw.set_features(copy.deepcopy(list(features)))

    def drawing_features(self) -> list:
        """A copy of the drawing's features (``properties.style`` included)."""
        return copy.deepcopy(self._draw.features())

    def drawing_geojson(self) -> dict:
        """The drawing as ``geojson_to_geodataframe`` takes it: the display
        style is not an attribute of the reviewed polygon."""
        features = []
        for f in self.drawing_features():
            props = {k: v for k, v in (f.get("properties") or {}).items() if k != "style"}
            features.append({**f, "properties": props})
        return {"type": "FeatureCollection", "features": features}

    def on_drawing_edit(self, callback):
        """``callback()`` after each edit the reviewer finished in the browser,
        never after ``set_drawing``. Returns the unsubscribe."""
        return self._draw.on_edit(callback)

    def target(self, view, *, group, report_errors=False):
        """A map-like target the layer builders add to (``PaneTarget``)."""
        return PaneTarget(self, view, group, report_errors=report_errors)


class PaneTarget:
    """One view (or ``"all"``) and one group, as a map the layer builders
    can add to. Spec ids are ``key`` on ``"all"``, else ``"<view>:<key>"``."""

    def __init__(self, panes, view, group, report_errors=False):
        self.panes = panes
        self.view = view
        self.group = group
        self.report_errors = bool(report_errors)
        self._gate = threading.local()
        self._seq = itertools.count()

    def _id(self, key) -> str:
        return key if self.view == mv_specs.ALL else f"{self.view}:{key}"

    def _z(self) -> int:
        return Z_BASE[self.group] + next(self._seq)

    @contextmanager
    def gated(self, gate):
        """Inside the block, this thread's adds run as ``gate(write)``, which
        may decline to run ``write`` (``imagery._gated`` passes one built on
        ``AnalysisState.apply_if_current``). Other threads are not gated."""
        self._gate.fn = gate
        try:
            yield self
        finally:
            self._gate.fn = None

    def _add(self, spec) -> None:
        self.panes.add(spec, gate=getattr(self._gate, "fn", None))

    def add_ee_layer(self, ee_object, vis_params=None, name="", shown=True, opacity=1.0, key="", **_):
        """SepalMap's ``add_ee_layer``: the image's tiles as a tile spec."""
        url = self.panes.tile_url(ee_object, vis_params or {})
        self._add(tile_spec(self._id(key or name), self.view, url, name=name, group=self.group,
                            visible=shown, opacity=opacity, z=self._z(), attribution="Google Earth Engine",
                            report_errors=self.report_errors))

    addLayer = add_ee_layer

    def add_layer(self, layer, key=None):
        """An ipyleaflet ``TileLayer`` -- localtileserver's, from
        ``raster_map.add_raster_layer`` -- as a tile spec on its URL."""
        self._add(tile_spec(self._id(key or layer.name), self.view, layer.url, name=layer.name,
                            group=self.group, visible=layer.visible, opacity=layer.opacity, z=self._z(),
                            attribution=getattr(layer, "attribution", "") or "",
                            report_errors=self.report_errors))

    def remove_layer(self, key, none_ok=False):
        layer_id = self._id(key)
        if self.panes.spec(layer_id) is None:
            if none_ok:
                return
            raise ValueError(f"no layer {layer_id!r} on the analysis views")
        self.panes.remove(layer_id)
