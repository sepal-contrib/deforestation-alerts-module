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


class ChunkRecord(BaseModel):
    """One window the index was fetched in, and whether it landed.

    The index is built a month at a time (spec §5.5 and §9). One ``getInfo``
    over a two-year project asks Earth Engine for the whole span at once and
    has no partial result, so a payload too large leaves no index at all;
    chunked, a failure part-way leaves what did land, and this is the record
    of which months that is. ``ok`` is false for a window that was requested
    and did not come back, so a reader can tell "no images there" from "never
    successfully asked".
    """

    start: date
    end: date
    fetched_at: datetime
    ok: bool = True


class SceneManifest(BaseModel):
    """What is in ``scenes.parquet``, and how old it is."""

    version: int = INDEX_VERSION
    aoi_digest: str
    refreshed: datetime
    covered: Coverage
    counts: dict[str, int] = Field(default_factory=dict)
    planet: PlanetState = Field(default_factory=PlanetState)
    #: Defaulted on purpose: every manifest already on disk was written
    #: before this field existed, and a required field here would turn every
    #: cached project into an unreadable index and a full refetch.
    chunks: list[ChunkRecord] = Field(default_factory=list)


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


# ------------------------------------------------------------ the one request
S2_COLLECTION = "COPERNICUS/S2_HARMONIZED"
LANDSAT_COLLECTIONS = ("LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2")

#: The property every row's ``key`` comes from, per source. Sentinel-2 and
#: Planet keys are asset ids, so the chosen image is rebuilt with
#: ``ee.Image(key)``. A Landsat key is its ``LANDSAT_SCENE_ID``, because the
#: rebuild has to find the *same* scene in both the surface-reflectance and
#: the TOA collection to pan-sharpen it, and those are two different assets.
KEY_PROPERTY = {SOURCE_S2: "system:id", SOURCE_LANDSAT: "LANDSAT_SCENE_ID", SOURCE_PLANET: "system:id"}

#: The cloud prefilters the retired ``imagery_service`` applied, kept so the
#: candidate set does not change (spec §5.1).
MAX_CLOUD = 90


def _window(start: date, end: date):
    """``[start, end]`` as the half-open pair ``filterDate`` actually wants.

    ``filterDate``'s end is **exclusive**, so an inclusive window has to ask
    for the day after. Both ends are ``ee.Date`` objects: ``str()`` on an
    Earth Engine object renders a *graph*, not a date string, so passing
    ``str(ee.Date(...).advance(...))`` would hand the filter nonsense.
    """
    import ee

    return (ee.Date(start.isoformat()), ee.Date(end.isoformat()).advance(1, "day"))


def _row(img, source: str, cloud_prop: str, tile):
    """One scene as a feature whose geometry is the granule's true footprint.

    The footprint, not its bounding box: ``distinct("tile")`` carries *this*
    geometry into the tiles table, and the exact-overlap test downstream is
    only worth running if it is not a second bounding-box test.
    """
    import ee

    img = ee.Image(img)
    return ee.Feature(
        img.geometry(),
        {
            "source": source,
            "millis": img.date().millis(),
            "cloud": ee.Number(img.get(cloud_prop)),
            "tile": tile,
            "key": img.get(KEY_PROPERTY[source]),
        },
    )


def _s2_rows(aoi, start: date, end: date):
    """Every Sentinel-2 granule over ``aoi``, keyed by its MGRS tile."""
    import ee

    d0, d1 = _window(start, end)
    coll = (ee.ImageCollection(S2_COLLECTION).filterBounds(aoi).filterDate(d0, d1)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", MAX_CLOUD)))
    return coll.map(lambda img: _row(img, SOURCE_S2, "CLOUDY_PIXEL_PERCENTAGE",
                                     ee.String(ee.Image(img).get("MGRS_TILE"))))


def _landsat_rows(aoi, start: date, end: date):
    """Every Landsat 8/9 scene over ``aoi``, keyed by its WRS-2 path and row."""
    import ee

    coll = ee.ImageCollection(LANDSAT_COLLECTIONS[0])
    for cid in LANDSAT_COLLECTIONS[1:]:
        coll = coll.merge(ee.ImageCollection(cid))
    d0, d1 = _window(start, end)
    coll = coll.filterBounds(aoi).filterDate(d0, d1).filter(ee.Filter.lt("CLOUD_COVER", MAX_CLOUD))

    # WRS-2 path/row, zero-padded, as the tile key. Scenes of one path/row
    # have the same nominal footprint, so the first one met represents the
    # rest; real footprints drift by a few hundred metres between passes, so
    # the overlap test is approximate for an alert sitting on a scene edge.
    def row(img):
        img = ee.Image(img)
        tile = (ee.Number(img.get("WRS_PATH")).format("%03d")
                .cat("_").cat(ee.Number(img.get("WRS_ROW")).format("%03d")))
        return _row(img, SOURCE_LANDSAT, "CLOUD_COVER", tile)

    return coll.map(row)


def scene_request(aoi, start: date, end: date):
    """Every Sentinel-2 granule and Landsat scene over ``aoi`` in one request.

    The reply is a dictionary of parallel scalar arrays rather than a list of
    records, because ``aggregate_array`` over a server-side collection is what
    lets one ``getInfo()`` resolve the whole index. ``end`` is inclusive: it is
    the last date a reviewer asked to see, which ``_window`` turns into the
    half-open range ``filterDate`` wants.

    The date window is applied **only** to the two source collections. Filtering
    the merged ``FeatureCollection`` again would return nothing: ``_row`` builds
    features carrying ``millis``, not ``system:time_start``, and that is the one
    property ``FeatureCollection.filterDate`` reads.

    The ``CLOUDY_PIXEL_PERCENTAGE`` / ``CLOUD_COVER`` < ``MAX_CLOUD`` prefilters
    are the ones the retired ``imagery_service`` applied, kept so the candidate set
    does not change (spec §5.1). The slider's top stop therefore means
    "everything the index holds", not literally 100%.
    """
    import ee

    rows = ee.FeatureCollection(_s2_rows(aoi, start, end).merge(_landsat_rows(aoi, start, end)))
    tiles = rows.distinct("tile").map(
        lambda f: ee.Feature(f).set({
            "coords": ee.Feature(f).geometry().coordinates(),
            "gtype": ee.Feature(f).geometry().type(),
        })
    )
    # Each array aggregates the *same* collection, so the nth element of every
    # one of them belongs to the nth feature; ``_checked`` in the parser is the
    # guard if a graph change ever breaks that.
    return ee.Dictionary({
        "scenes": ee.Dictionary({
            "source": rows.aggregate_array("source"),
            "millis": rows.aggregate_array("millis"),
            "cloud": rows.aggregate_array("cloud"),
            "tile": rows.aggregate_array("tile"),
            "key": rows.aggregate_array("key"),
        }),
        "tiles": ee.Dictionary({
            "tile": tiles.aggregate_array("tile"),
            "coords": tiles.aggregate_array("coords"),
            "gtype": tiles.aggregate_array("gtype"),
        }),
    })


def planet_request(collection_id: str, start: date, end: date):
    """The NICFI monthly basemaps overlapping ``[start, end]``.

    ``collection_id`` is passed in rather than looked up: the region mapping
    lives in ``gui.scripts.vis_layers.NICFI_COLLECTIONS`` and this module must
    not import from ``gui``. A basemap is already an AOI-wide mosaic, so there
    is nothing to filter by bounds and no footprint to record.
    """
    import ee

    d0, d1 = _window(start.replace(day=1), end)
    coll = ee.ImageCollection(collection_id).filterDate(d0, d1)
    return ee.Dictionary({
        "key": coll.aggregate_array(KEY_PROPERTY[SOURCE_PLANET]),
        # The basemap's own start date, i.e. the first of its month.
        "millis": coll.aggregate_array("system:time_start"),
    })
