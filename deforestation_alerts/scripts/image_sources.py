"""The image sources the Analysis timeline can show, and the menu that groups them.

One place for every fact about a source (stored name, row-id code, colour, native
pixel size, ...). No ``ee`` and no ``gui`` imports, so engine and GUI both read it.
"""

from dataclasses import dataclass
from typing import Optional

#: the stored names, exactly the strings ``mosaics_helper`` and ``before_info[0]`` switch on
S2 = "Sentinel 2"
LANDSAT = "Landsat"
PLANET = "Planet NICFI"
S1_ASC = "Sentinel 1 ascending"
S1_DESC = "Sentinel 1 descending"
#: the menu key of the two Sentinel-1 passes
S1 = "Sentinel 1"


@dataclass(frozen=True)
class ImageSource:
    name: str
    code: str
    label: str
    entry: str
    rank: int
    band_weight: int
    color_var: str
    color_light: str
    color_dark: str
    cloud: bool
    optical: bool
    monthly: bool
    native_m: float


@dataclass(frozen=True)
class MenuEntry:
    key: str
    label: str
    group: str  # "optical" | "radar"
    default_on: bool
    members: tuple[str, ...]


SOURCES: dict[str, ImageSource] = {
    s.name: s
    for s in (
        ImageSource(S2, "s", "Sentinel-2", S2, 0, 17, "--dat-s2", "#3a6db5", "#7fa8e0", True, True, False, 10.0),
        ImageSource(LANDSAT, "l", "Landsat", LANDSAT, 1, 14, "--dat-landsat", "#9a7240", "#c9a87c", True, True, False, 15.0),
        ImageSource(PLANET, "p", "Planet", PLANET, 2, 10, "--dat-planet", "#3f8f32", "#9bd98a", False, True, True, 4.77),
        ImageSource(S1_ASC, "a", "Sentinel-1 asc", S1, 3, 10, "--dat-s1", "#a2457f", "#d98ac0", False, False, False, 10.0),
        ImageSource(S1_DESC, "d", "Sentinel-1 desc", S1, 4, 10, "--dat-s1", "#a2457f", "#d98ac0", False, False, False, 10.0),
    )
}

MENU: tuple[MenuEntry, ...] = (
    MenuEntry(S2, "Sentinel-2", "optical", True, (S2,)),
    MenuEntry(LANDSAT, "Landsat", "optical", True, (LANDSAT,)),
    MenuEntry(PLANET, "Planet", "optical", False, (PLANET,)),
    MenuEntry(S1, "Sentinel-1", "radar", False, (S1_ASC, S1_DESC)),
)


def is_radar(name: str) -> bool:
    """Whether ``name`` is a radar source. An unknown name is not: it keeps
    the optical handling every source had before radar existed."""
    s = SOURCES.get(name)
    return bool(s and not s.optical)


def dated_optical(name: str) -> bool:
    """Whether the auto pick and the GIF may use this source."""
    s = SOURCES.get(name)
    return bool(s and s.optical and not s.monthly)


def entry_of(name: str) -> Optional[MenuEntry]:
    return next((e for e in MENU if name in e.members), None)


def enabled_entries(project) -> frozenset[str]:
    """The menu keys switched on: the project's choice, else the defaults."""
    chosen = getattr(project, "image_sources", None)
    if chosen is None:
        return frozenset(e.key for e in MENU if e.default_on)
    return frozenset(e.key for e in MENU if e.key in chosen)


def enabled_sources(project) -> frozenset[str]:
    on = enabled_entries(project)
    return frozenset(m for e in MENU if e.key in on for m in e.members)
