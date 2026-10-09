"""``MultiView``: the ``leaflet-multiview`` component as an anywidget.

The views, their sync and every plugin run in the browser
(``js/leaflet-multiview``). Python talks to them through synced traits only:
the core traits below, plus the traits of each plugin mixin
(``gui.widget.multiview.plugins``) the subclass lists.

This package ships no JS bundle. A consuming app subclasses ``MultiView``
and points ``_esm`` and ``_css`` at its own bundle, which registers the JS
plugins it uses (spec §5.2); this repo's is ``gui.widget.alert_views``.
"""

import anywidget
import traitlets

from .plugins import plugin_order


class MultiView(anywidget.AnyWidget):
    # A consuming app's subclass overrides this with its bundle (spec §5.2).
    # Until then the widget renders nothing instead of anywidget's dev note.
    _esm = "export default {render() {}}"

    #: ``[{"id": ..., "title"?: ...}]``, one entry per view, in layout order.
    views = traitlets.List(traitlets.Dict()).tag(sync=True)
    #: ``"row"`` or ``"grid"`` (``ceil(sqrt(n))`` columns).
    # Not ``layout``: that name is ipywidgets' DOMWidget.layout (a Layout widget).
    view_layout = traitlets.Unicode("row").tag(sync=True)
    #: ``{"url", "attribution", "maxZoom"?}`` of the basemap every view shows.
    basemap = traitlets.Dict().tag(sync=True)
    #: ``"light"`` or ``"dark"``.
    theme = traitlets.Unicode("light").tag(sync=True)
    #: ``{"<plugin>.<key>": text}``: overrides of the plugins' English labels.
    labels = traitlets.Dict().tag(sync=True)
    #: Layer specs (``specs.tile_spec`` / ``specs.geojson_spec``), diffed by id.
    layers = traitlets.List().tag(sync=True)
    #: The plugin names the frontend installs, in order. Defaults to the
    #: class's mixins in dependency order.
    plugins = traitlets.List(traitlets.Unicode()).tag(sync=True)
    #: ``{center, zoom, n, origin}``. ``specs.set_view`` writes host views
    #: (``n`` bumped, ``origin: "host"``); the frontend writes the viewer's
    #: view back with the last ``n`` it saw and ``origin: "user"``.
    view = traitlets.Dict().tag(sync=True)

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        plugin_order(cls)  # a missing requirement fails here, at import, not at render

    @traitlets.default("plugins")
    def _default_plugins(self):
        return plugin_order(type(self))

    @traitlets.default("view")
    def _default_view(self):
        # A fresh dict per widget: a Dict trait's default is copied shallowly,
        # so a literal default would share the ``center`` list.
        return {"center": [0.0, 0.0], "zoom": 2, "n": 0, "origin": "host"}
