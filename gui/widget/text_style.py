"""Shared inline text styles (ported from the spatial_risk scaffold).

``MUTED`` replaces Vuetify's ``text--secondary`` helper class. That class has no
colour of its own; it resolves through a ``.theme--dark.v-application`` /
``.theme--light.v-application`` ancestor selector, and Vuetify teleports dialog
and menu content into a global overlay container that jupyter-vuetify's
``createDivs()`` hard-codes as ``theme--light`` under voila. Dimming by opacity
sidesteps the ancestor lookup: the element inherits its card's own (correctly
themed) colour and fades it, so it is right in both themes and both hosts.
0.7 matches the dark-theme alpha exactly and the light one closely
(0.7 x 0.87 = 0.61 vs 0.6).
"""

MUTED = "opacity: 0.7;"

__all__ = ["MUTED"]
