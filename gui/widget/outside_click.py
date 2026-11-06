"""Close a menu on an outside click, except the click that closes one of its
selects' lists.

A ``v.Menu`` holding ``v.Select``s closes with its open list on one outside
click (Vuetify 2's click-outside fires for both). The panel should stay, so
the reviewer can still press Apply. The menu is built with
``close_on_click=False`` and this widget closes it instead.

Why the browser decides: whether a list was open is a fact of the DOM at the
moment of the click. A Python flag, set from the select's focus event, arrives
after the click when the kernel is busy, and the panel then closed with its
list after all. So the script looks at ``mousedown`` (capture phase: before
any handler closes the list) and only counts a click as outside when no list
was open and both the press and the release landed outside the panel, its
lists and its activator. Python hears each outside click through
``outside_clicks`` and closes the menu; a late close only closes late.
"""

import ipyvuetify as v
import traitlets

# Everything lives inside module.exports: ipyvue's legacy loader (its fallback
# today) evaluates only the object literal, so nothing may sit before it.
_TEMPLATE = """
<template>
  <span style="display: none"></span>
</template>
<script>
module.exports = {
  mounted() {
    this.ocDownOutside = false;
    this.ocListWasOpen = false;
    document.addEventListener("mousedown", this.ocOnDown, true);
    document.addEventListener("click", this.ocOnClick, true);
  },
  beforeDestroy() {
    document.removeEventListener("mousedown", this.ocOnDown, true);
    document.removeEventListener("click", this.ocOnClick, true);
  },
  methods: {
    ocPanel() {
      return document.querySelector("." + this.content_class + ".menuable__content__active");
    },
    ocLists() {
      // While the panel is open, any other open menu is one of its lists.
      return Array.from(document.querySelectorAll(".menuable__content__active"))
        .filter((m) => !m.classList.contains(this.content_class));
    },
    ocInside(target, lists) {
      if (!(target instanceof Element)) return true;
      if (target.closest("." + this.content_class)) return true;
      if (target.closest("." + this.activator_class)) return true;
      return lists.some((m) => m.contains(target));
    },
    ocOnDown(e) {
      const lists = this.ocLists();
      this.ocListWasOpen = !!this.ocPanel() && lists.length > 0;
      this.ocDownOutside = !this.ocInside(e.target, lists);
    },
    ocOnClick(e) {
      if (!this.ocPanel() || this.ocListWasOpen || !this.ocDownOutside) return;
      if (this.ocInside(e.target, this.ocLists())) return;
      this.outside_clicks += 1;
    },
  },
};
</script>
"""


class OutsideClickWidget(v.VuetifyTemplate):
    """Invisible: counts the clicks outside the open menu content tagged
    ``content_class`` (and outside its activator, tagged ``activator_class``)
    that do not close one of its lists."""

    template = traitlets.Unicode(_TEMPLATE).tag(sync=True)
    content_class = traitlets.Unicode("").tag(sync=True)
    activator_class = traitlets.Unicode("").tag(sync=True)
    outside_clicks = traitlets.Int(0).tag(sync=True)
