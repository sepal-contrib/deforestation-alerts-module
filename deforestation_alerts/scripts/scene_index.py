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
from typing import Any, Mapping, NamedTuple, Optional

import geopandas as gpd
import pandas as pd
from pydantic import BaseModel, Field
from shapely.geometry import shape

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


SCENE_COLUMNS = ("source", "date", "cloud", "tile", "key")
WGS84 = "EPSG:4326"


class SceneTables(NamedTuple):
    """The cached index: one row per granule, plus each tile's footprint once.

    The footprints are kept apart because a granule's footprint repeats on
    every date it was acquired -- an AOI of 30 Sentinel-2 granules over six
    months is thousands of scene rows but still 30 polygons.
    """

    scenes: pd.DataFrame
    tiles: gpd.GeoDataFrame


def _checked(arrays: Mapping[str, list[Any]], names) -> int:
    lengths = {name: len(arrays.get(name) or ()) for name in names}
    if len(set(lengths.values())) > 1:
        # defensive: a server-side graph bug in the request Task 4 builds, not
        # reachable from the UI -- see tests/test_errors.py PROGRAMMER_ERRORS.
        raise ValueError(f"scene arrays of different length: {lengths}")
    return next(iter(lengths.values()), 0)


def _scene_frame(arrays: Mapping[str, list[Any]]) -> pd.DataFrame:
    _checked(arrays, ("source", "millis", "cloud", "tile", "key"))
    df = pd.DataFrame(
        {
            "source": pd.Series(arrays["source"], dtype="string").astype(object),
            # normalize() drops the time of day: the index is keyed by
            # acquisition *date*, which is what the timeline and the saved
            # provenance show. Two granules of one pass share that date.
            "date": pd.to_datetime(pd.Series(arrays["millis"], dtype="float64"), unit="ms", utc=True)
            .dt.tz_localize(None)
            .dt.normalize(),
            "cloud": pd.Series(arrays["cloud"], dtype="float64"),
            "tile": pd.Series(arrays["tile"], dtype="string").astype(object),
            "key": pd.Series(arrays["key"], dtype="string").astype(object),
        },
        columns=list(SCENE_COLUMNS),
    )
    return df.astype({"source": object, "tile": object, "key": object})


def _tile_frame(arrays: Mapping[str, list[Any]]) -> gpd.GeoDataFrame:
    _checked(arrays, ("tile", "coords", "gtype"))
    # The footprints come from ``img.geometry()``, never from its bounds -- see
    # the spec: a bbox here would turn the exact-footprint test into a second
    # bbox test and let Landsat over-selection through. The geometry type
    # travels with the coordinates because a granule footprint is usually a
    # Polygon but not always.
    geoms = [
        shape({"type": gtype, "coordinates": coords})
        for gtype, coords in zip(arrays.get("gtype") or (), arrays.get("coords") or ())
    ]
    return gpd.GeoDataFrame(
        {"tile": list(arrays.get("tile") or ())}, geometry=gpd.GeoSeries(geoms, crs=WGS84), crs=WGS84
    )


def parse_scenes(raw: Mapping[str, Any]) -> SceneTables:
    """The one request's reply, as tables."""
    return SceneTables(scenes=_scene_frame(raw.get("scenes") or {}), tiles=_tile_frame(raw.get("tiles") or {}))


def parse_planet(raw: Mapping[str, Any]) -> pd.DataFrame:
    """Planet NICFI monthly basemaps as scene rows.

    A basemap is one cloud-minimized mosaic of a whole month over the whole
    AOI, so it has no granule footprint to test and no cloud figure to
    compare: ``tile`` is empty, which ``scene_query`` reads as "covers every
    alert", and ``cloud`` is NaN, which the cloud filter never hides.
    """
    keys = list(raw.get("key") or ())
    return _scene_frame(
        {
            "source": [SOURCE_PLANET] * len(keys),
            "millis": list(raw.get("millis") or ()),
            "cloud": [float("nan")] * len(keys),
            "tile": [""] * len(keys),
            "key": keys,
        }
    )


EMPTY_SCENE_ARRAYS = {"source": [], "millis": [], "cloud": [], "tile": [], "key": []}


def empty_tables() -> SceneTables:
    """An index with the right columns and no rows, so the timeline can render
    its "no images" state before any fetch has run."""
    return SceneTables(
        scenes=_scene_frame(dict(EMPTY_SCENE_ARRAYS)),
        tiles=_tile_frame({"tile": [], "coords": [], "gtype": []}),
    )
