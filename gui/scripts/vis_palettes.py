"""Colour ramps and RGB band-role helpers for visualization layers.

Pure (no Solara, no i18n): the visualization dialog supplies the labels.
Ramps are bare 6-digit hex, low -> high, ported from spatial_risk.
"""

import re
from typing import Dict, List, Optional, Tuple

RAMPS: Dict[str, List[str]] = {
    "grayscale": ["000000", "ffffff"],
    "blues": ["ffffff", "c6dbef", "4292c6", "08306b"],
    "greens": ["ffffff", "a1d99b", "31a354", "00441b"],
    "oranges": ["ffffff", "fdae6b", "e6550d", "7f2704"],
    "cool_warm": ["313695", "74add1", "fee090", "f46d43", "a50026"],
    "viridis": ["440154", "3b528b", "21918c", "5ec962", "fde725"],
    "terrain": ["006633", "e5ffcc", "662a00", "d8d8d8", "f5f5f5"],
    "heat": ["ffffb2", "fd8d3c", "e31a1c", "800026"],
    "magma": ["000004", "3b0f70", "8c2981", "de4968", "fe9f6d", "fcfdbf"],
}

DEFAULT_RAMP = "grayscale"


def _bare(value) -> str:
    """Normalise to the GEE palette form: lowercase, no ``#``."""
    return str(value).strip().lstrip("#").lower()


def ramp_palette(ramp: str, invert: bool = False) -> List[str]:
    """The palette of a named ramp, reversed when ``invert``."""
    palette = list(RAMPS[ramp])
    if invert:
        palette.reverse()
    return palette


def ramp_choice(palette) -> Tuple[Optional[str], bool]:
    """``(ramp_key, inverted)`` for a palette, or ``(None, False)`` if it is no ramp."""
    pal = [_bare(c) for c in palette or []]
    if not pal:
        return None, False
    for key, ramp in RAMPS.items():
        if pal == ramp:
            return key, False
        if pal == list(reversed(ramp)):
            return key, True
    return None, False


def ramp_css(ramp: str, invert: bool = False) -> str:
    """A CSS ``linear-gradient`` previewing a ramp."""
    return "linear-gradient(to right, " + ", ".join(f"#{c}" for c in ramp_palette(ramp, invert)) + ")"


def ramp_colormap(palette, name="vis"):
    from matplotlib.colors import LinearSegmentedColormap
    return LinearSegmentedColormap.from_list(name, ["#" + _bare(c) for c in palette])


ROLES = ("red", "green", "blue", "nir", "swir")
RGB_COMBOS = {
    "true_color": ("red", "green", "blue"),
    "false_color": ("nir", "red", "green"),
    "swir": ("swir", "nir", "red"),
}
# Checked in order; the first band of the image matching a role's pattern wins.
_ROLE_PATTERNS = {
    "red": (r"red", r"r", r"(sr_)?b4"),
    "green": (r"green", r"g", r"(sr_)?b3"),
    "blue": (r"blue", r"b", r"(sr_)?b2"),
    "nir": (r"nir", r"n", r"b8a?", r"sr_b5"),
    "swir": (r"swir1?", r"b11", r"sr_b6", r"swir2", r"b12", r"sr_b7"),
}


def guess_roles(band_names) -> dict:
    names = list(band_names or [])
    out = {}
    for role, patterns in _ROLE_PATTERNS.items():
        for pat in patterns:
            hit = next((n for n in names if re.fullmatch(pat, str(n), flags=re.IGNORECASE)), None)
            if hit is not None:
                out[role] = hit
                break
    return out


def available_combos(roles) -> list:
    return [k for k, need in RGB_COMBOS.items() if all(roles.get(r) for r in need)]


def combo_bands(combo, roles):
    need = RGB_COMBOS.get(combo)
    if need is None or not all(roles.get(r) for r in need):
        return None
    return [roles[r] for r in need]
