"""App-owned host for a full-stage workspace (the Analysis two-map stage).

Why not MapApp's ``display: "step"`` slot: with the workflow steps living in
the right panel there is no drawer entry to activate a MapApp step from. This
widget is a fixed overlay between the left rail and the right panel (the CSS
lives in the shell's ``solara.Style``; it reads the ``--sepal-drawer-width`` /
``--sepal-right-panel-width`` vars MapApp pushes onto ``:root``). It is
rendered at ``Page`` level as a sibling of MapApp — a ``v-navigation-drawer``
child cannot host a fixed overlay because the drawer's transform re-roots
``position: fixed``.

The stage is mounted once and toggled with ``v-show`` (DOM kept, so the raw
ipyvuetify/ipyleaflet widgets never lose their views). Leaflet re-measures a
map only on a window resize, and a map that is hidden at that moment measures
0x0 and keeps drawing tiles for that size once it is shown again. So the
``visible`` watcher dispatches a resize on the next tick on BOTH edges — the
same nudge ``MapApp.vue`` uses for its narrow layout:

* on show, for the stage's own maps, hidden until now;
* on hide, for the shell's shared map, hidden while the stage showed. The shell
  re-displays that map *before* it hides the stage (``_PanelBridge``), and the
  frontend applies kernel messages in order, so the nudge finds it displayed.
"""

import ipyvuetify as v
from ipywidgets import DOMWidget, widget_serialization
from traitlets import Bool, Instance, List, Unicode


class StageHost(v.VuetifyTemplate):
    visible = Bool(False).tag(sync=True)
    content = List(Instance(DOMWidget), default_value=[]).tag(
        sync=True, **widget_serialization
    )

    # ipyvue evaluates a runtime template's <script> with
    # ``new Function("module", "exports", src)``: it must assign
    # ``module.exports``. ``export default`` is a SyntaxError there and only
    # survives via ipyvue's deprecated fallback (which warns on every mount).
    template = Unicode(
        """
<template>
  <div class="defo-stage" v-show="visible">
    <jupyter-widget v-for="(w, i) in content" :key="i" :widget="w"></jupyter-widget>
  </div>
</template>
<script>
module.exports = {
  watch: {
    visible() {
      this.$nextTick(() => window.dispatchEvent(new Event('resize')));
    },
  },
};
</script>
"""
    ).tag(sync=True)
