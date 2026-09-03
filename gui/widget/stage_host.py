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
ipyvuetify/ipyleaflet widgets never lose their views). ipyleaflet recomputes
its canvas only on a window resize, so a map first shown after being hidden
would render grey; the ``visible`` watcher dispatches that resize on the next
tick — the same nudge ``MapApp.vue`` uses for its narrow layout.
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
    visible(shown) {
      if (shown) {
        this.$nextTick(() => window.dispatchEvent(new Event('resize')));
      }
    },
  },
};
</script>
"""
    ).tag(sync=True)
