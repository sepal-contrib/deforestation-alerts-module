"""Plugin trait mixins for ``MultiView``, and the order the frontend installs them in.

Each JS plugin of ``js/leaflet-multiview`` that has state on the Python side
has a mixin here: a ``traitlets.HasTraits`` class that declares the plugin's
name (``_plugin_name``, the JS plugin's ``name``), the plugins it needs
(``_plugin_requires``) and its synced traits, all prefixed with the plugin's
snake-case name. A widget class lists the mixins it wants after ``MultiView``:

    class MyViews(MultiView, LayerMenu, Draw, Holes): ...

and its ``plugins`` trait defaults to ``plugin_order(MyViews)``, which is the
list the frontend adapter installs, in that order.
"""

import traitlets


class PluginMixin(traitlets.HasTraits):
    """Base of every plugin mixin. A subclass sets both class attributes."""

    #: The JS plugin's ``name`` (``"layerMenu"``, ``"draw"``, ...).
    _plugin_name: str = ""
    #: The ``_plugin_name``s this plugin needs installed before it.
    _plugin_requires: tuple = ()


def _mixins_in_mro(cls) -> list:
    """The plugin mixins of ``cls`` in MRO order, one per plugin name.

    Only a class that sets ``_plugin_name`` in its own body counts, so a
    widget class that merely inherits from mixins is not itself a plugin.
    """
    found, names = [], set()
    for klass in cls.__mro__:
        name = klass.__dict__.get("_plugin_name")
        if name and issubclass(klass, PluginMixin) and name not in names:
            names.add(name)
            found.append(klass)
    return found


def plugin_order(cls) -> list:
    """The plugin names of ``cls`` in install order.

    The rule: take the mixins in MRO order (for ``class V(MultiView, A, B)``
    that is ``A``, then ``B``), then repeatedly take the first one, in that
    order, whose requirements are all taken already. So a plugin keeps its
    place unless a requirement listed after it pushes it back, right behind
    that requirement.

    Raises ``TypeError("<name> requires <dep>")`` when a requirement is not
    among the class's mixins, and ``TypeError`` when requirements form a
    cycle.
    """
    mixins = _mixins_in_mro(cls)
    names = [m.__dict__["_plugin_name"] for m in mixins]
    requires = {m.__dict__["_plugin_name"]: tuple(m._plugin_requires) for m in mixins}
    for name in names:
        for dep in requires[name]:
            if dep not in requires:
                raise TypeError(f"{name} requires {dep}")
    ordered, pending = [], list(names)
    while pending:
        ready = next((n for n in pending if all(dep in ordered for dep in requires[n])), None)
        if ready is None:
            raise TypeError(f"plugin requirements form a cycle: {', '.join(pending)}")
        ordered.append(ready)
        pending.remove(ready)
    return ordered


class LayerMenu(PluginMixin):
    """A per-view menu of that view's layers (JS plugin ``layerMenu``)."""

    _plugin_name = "layerMenu"
    #: ``{view id: {layer id: bool}}``: the viewer's menu choices. Written by
    #: the frontend (already pruned of layers that left a view); Python may
    #: seed it before the first render.
    layer_menu_toggles = traitlets.Dict().tag(sync=True)


class FitBounds(PluginMixin):
    """A button that fits the views to the last bounds set (``fitBounds``)."""

    _plugin_name = "fitBounds"
    #: ``[[south, west], [north, east]]``, or None to hide the button.
    fit_bounds_bounds = traitlets.Any(None, allow_none=True).tag(sync=True)
    #: The views that get the button: a view id, a list of ids, or ``"all"``.
    fit_bounds_views = traitlets.Any("all").tag(sync=True)


class Draw(PluginMixin):
    """One drawing (a FeatureCollection) edited in every target view (``draw``)."""

    _plugin_name = "draw"
    #: The drawing envelope ``{fc, gen, seq, origin}`` (spec §5.2), written by
    #: both sides; ``DrawState`` is its only Python writer.
    draw = traitlets.Dict().tag(sync=True)
    #: Geoman's global edit mode on every target view.
    draw_edit_mode = traitlets.Bool(False).tag(sync=True)

    @traitlets.default("draw")
    def _default_draw(self):
        # A fresh nested dict per widget: a Dict trait's default is copied
        # shallowly, so a literal default would share ``fc["features"]``.
        return {"fc": {"type": "FeatureCollection", "features": []}, "gen": 0, "seq": 0, "origin": "host"}


class Holes(PluginMixin):
    """*Fill holes* and *Remove hole* on the drawing (``holes``). No traits."""

    _plugin_name = "holes"
    _plugin_requires = ("draw",)


class TileErrors(PluginMixin):
    """A per-view badge when a tile layer fails to load (``tileErrors``). No traits."""

    _plugin_name = "tileErrors"
