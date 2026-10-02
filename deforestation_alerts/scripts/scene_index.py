# deforestation_alerts/scripts/scene_index.py
"""The project-level scene index: one Earth Engine request per project returns
the granule metadata of the whole AOI, and every alert is then answered from
the cached tables without a round trip (see
``docs/superpowers/specs/2026-10-02-shared-image-timeline-design.md``).

This module is the Earth-Engine-facing half: the request builders and the
array-to-DataFrame parsers. Every rule that can be expressed over plain data
lives in ``scene_query``, which never imports ``ee``, so the offline test tier
can exercise it.
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field

INDEX_VERSION = 1

# Exactly the strings ``mosaics_helper._scale_for_source`` and ``before_info[0]``
# switch on. The UI label "Sentinel-2" is a display string and never lands here.
SOURCE_S2 = "Sentinel 2"
SOURCE_LANDSAT = "Landsat"
SOURCE_PLANET = "Planet NICFI"

SOURCE_CODE = {SOURCE_S2: "s", SOURCE_LANDSAT: "l", SOURCE_PLANET: "p"}


def row_id(source: str, d: date) -> str:
    """The stable id of one timeline row: its source and its acquisition date.

    A row is one date of one collection, however many granules it mosaics, so
    the id carries no granule key. Selections are held as these ids.
    """
    return f"{SOURCE_CODE[source]}{d:%Y%m%d}"


class Coverage(BaseModel):
    """The contiguous date range the index holds, inclusive at both ends."""

    start: date
    end: date


class PlanetState(BaseModel):
    """Whether the NICFI basemaps answered, and for which region.

    NICFI is licence-gated, so no access is an ordinary outcome rather than an
    error. It is recorded instead of logged because the toolbar has to tell a
    reviewer *why* the Planet band is empty — a collection they have no access
    to reads differently from one with no images this month.
    """

    region: Optional[str] = None
    access: bool = False


class SceneManifest(BaseModel):
    """What is in ``scenes.parquet``, and how old it is."""

    version: int = INDEX_VERSION
    aoi_digest: str
    refreshed: datetime
    covered: Coverage
    counts: dict[str, int] = Field(default_factory=dict)
    planet: PlanetState = Field(default_factory=PlanetState)
