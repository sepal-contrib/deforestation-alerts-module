"""A small chip that copies its text to the browser's clipboard when clicked.

The copy runs in the browser, inside the click handler itself: browsers only
allow a clipboard write during a user gesture, and a round trip through Python
(click -> kernel -> message back) can lose it. The kernel hears about each copy
through ``copied_count`` (bumped by the script), so a caller can toast.

``navigator.clipboard`` exists only in secure contexts (https, localhost); on
plain http the script falls back to ``document.execCommand("copy")`` on a
temporary input, as pysepal's ``CopyToClip`` does.
"""

import ipyvuetify as v
import traitlets

# Everything lives inside module.exports: ipyvue's legacy loader (its fallback
# today) evaluates only the object literal, so nothing may sit before it.
_TEMPLATE = """
<template>
  <span :style="style" :title="text" @click="copy" v-text="text"></span>
</template>
<script>
module.exports = {
  computed: {
    style() {
      return "font-family:monospace;font-size:11px;padding:1px 6px;margin:2px;"
        + "border-radius:3px;cursor:pointer;"
        + (this.review ? "background:rgba(91,182,36,0.18);" : "opacity:0.8;");
    },
  },
  methods: {
    copy() {
      const text = this.text;
      const done = () => { this.copied_count += 1; };
      const fallback = () => {
        const input = document.createElement("input");
        input.value = text;
        document.body.appendChild(input);
        input.focus();
        input.select();
        try { if (document.execCommand("copy")) done(); } catch (e) { /* nothing to do */ }
        document.body.removeChild(input);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, fallback);
      } else {
        fallback();
      }
    },
  },
};
</script>
"""


class CopyChipWidget(v.VuetifyTemplate):
    """The chip: shows ``text``; a click copies it and bumps ``copied_count``."""

    template = traitlets.Unicode(_TEMPLATE).tag(sync=True)
    text = traitlets.Unicode("").tag(sync=True)
    review = traitlets.Bool(False).tag(sync=True)
    copied_count = traitlets.Int(0).tag(sync=True)
