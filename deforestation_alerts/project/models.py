"""The Project document: one AOI, its alert sources, layers, grid, periods,
files and alert sets. Pydantic, Solara-free, no back-references.

Every union is discriminated on ``kind`` so saving is one ``model_dump_json``
and loading one ``model_validate_json`` -- no hand-written per-type dispatch.
"""

from datetime import date, datetime, timedelta
from typing import Annotated, Literal, Optional, Union

from affine import Affine
from pydantic import BaseModel, Field

from deforestation_alerts.scripts.naming import safe_token

SCHEMA_VERSION = 1
PREDEFINED_KEYS = ("GLAD-L", "RADD", "GLAD-S2")
PREDEFINED_DIGITS = {"GLAD-L": 0, "RADD": 1, "GLAD-S2": 2}
#: Each source owns one decimal digit of the combined Int32 code: nine digits
#: of at most 2 sum to 222,222,222 < 2**31 - 1, and rasterio.features.shapes
#: does not accept int64 (spec D3/D16). The predefined digits stay reserved
#: even when unused, so the cap is 6 custom sources, not 9 in total.
CUSTOM_DIGITS = (3, 4, 5, 6, 7, 8)


class AoiRecord(BaseModel):
    spec: dict  # pysepal AoiSpec.to_dict()
    name: str
    method: str
    digest: str  # geometry digest, or the admin code / asset id for lazy AOIs
    bounds: tuple[float, float, float, float]  # WGS84 minx, miny, maxx, maxy
    geometry_file: Optional[str] = None  # "aoi.geojson" when a sidecar exists


class PredefinedSource(BaseModel):
    kind: Literal["predefined"] = "predefined"
    name: str
    catalogue_key: Literal["GLAD-L", "RADD", "GLAD-S2"]
    digit: int


class NativeGrid(BaseModel):
    """The projection a raster is read or downloaded in (spec §5.1): the
    Earth Engine nominal scale or a GeoTIFF's pixel size in metres (degrees x
    111319.49079327357 for a geographic file), the asset's or file's own six
    affine terms, and a GeoTIFF's header extent in WGS84 (None for Earth
    Engine, whose footprint the coverage request reads itself)."""

    crs: str
    scale: float
    transform: Optional[tuple[float, float, float, float, float, float]] = None
    bounds: Optional[tuple[float, float, float, float]] = None


BandRef = Union[int, str]  # an Earth Engine band name, or a GeoTIFF's 1-based band index


class BandMap(BaseModel):
    alert: Optional[BandRef] = None  # None for the date-only preset
    date: BandRef


#: The three format presets (spec §7.2): their default band names and date encoding.
PRESETS = {
    "radd": {"bands": ("Alert", "Date"), "encoding": "yyddd"},
    "ccdc": {"bands": ("detection_count", "first_detection_date"), "encoding": "decimal_year"},
    "date_only": {"bands": (None, None), "encoding": None},
}


class CustomSource(BaseModel):
    kind: Literal["custom"] = "custom"
    name: str
    origin: Literal["gee", "file"]
    ref: str  # asset id, or absolute path
    asset_type: Literal["IMAGE", "IMAGE_COLLECTION", "FILE"] = "IMAGE"
    format: Literal["radd", "ccdc", "date_only"]
    date_encoding: Literal["yyddd", "yymm", "yyyyddd", "decimal_year"] = "yyddd"
    band_map: BandMap = Field(default_factory=lambda: BandMap(alert="Alert", date="Date"))
    native: Optional[NativeGrid] = None  # discovered when the source was added (spec §6.2)
    digit: int


AlertSource = Annotated[Union[PredefinedSource, CustomSource], Field(discriminator="kind")]


class MaskLayer(BaseModel):
    name: str
    origin: Literal["gee", "file"]
    ref: str
    mode: Literal["keep", "exclude"] = "keep"
    enabled: bool = True


class PeriodMask(BaseModel):
    """A period's choice for one mask: on/off and keep/exclude.

    Forward reference ahead of Task 22, which gives this class its full home
    (``Period.masks``) and owning helpers; reproduced here verbatim from that
    task's brief so the two definitions merge as a no-op.
    """

    on: bool = True
    mode: Literal["keep", "exclude"] = "keep"


class GeeVisLayer(BaseModel):
    kind: Literal["gee"] = "gee"
    name: str
    asset: str
    bands: list[str] = []
    min: float = 0.0
    max: float = 1.0
    palette: list[str] = []
    show_in_analysis: bool = False


class FileVisLayer(BaseModel):
    kind: Literal["file"] = "file"
    name: str
    path: str
    band: int = 1
    colormap: str = "viridis"
    min: float = 0.0
    max: float = 1.0
    show_in_analysis: bool = False


class NicfiVisLayer(BaseModel):
    kind: Literal["nicfi"] = "nicfi"
    name: str
    month: Optional[str] = None  # "YYYY-MM"; None = the active period's last month
    show_in_analysis: bool = False


VisLayer = Annotated[
    Union[GeeVisLayer, FileVisLayer, NicfiVisLayer], Field(discriminator="kind")
]


class GridSpec(BaseModel):
    epsg: str  # "EPSG:32721"
    resolution: float  # metres
    xmin: float  # snapped to a multiple of resolution
    ymax: float
    width: int
    height: int

    @property
    def affine(self) -> Affine:
        r = float(self.resolution)
        return Affine(r, 0.0, float(self.xmin), 0.0, -r, float(self.ymax))

    @property
    def signature(self) -> str:
        """Equality-only token for 'same pixels'. repr() keeps floats exact."""
        return (
            f"{self.epsg}|{float(self.resolution)!r}|{float(self.xmin)!r}|"
            f"{float(self.ymax)!r}|{self.width}x{self.height}"
        )


class Period(BaseModel):
    id: str
    start: date
    end: date

    def search_window(self, look_back_days: int) -> tuple[date, date]:
        return self.start - timedelta(days=int(look_back_days)), self.end


class RegionCoverage(BaseModel):
    """One region of a source's footprint that meets the AOI (spec §5.1)."""

    name: str
    fraction: float  # of the AOI inside this region's footprint
    first: Optional[date] = None  # None for a custom source (no catalogue dates)
    last: Optional[date] = None


class SourceCoverage(BaseModel):
    """Where and when a source has data for the AOI with ``aoi_digest`` (spec §6.2, D19)."""

    aoi_digest: str
    checked_on: date
    fraction: float  # 0..1 of the AOI inside the footprint (the union of the regions)
    regions: list[RegionCoverage] = []
    gaps: list[tuple[date, date]] = []  # days no collection holds (GLAD-L 2024-07-17 -> 2024-12-31)


class Artifact(BaseModel):
    key: str
    kind: Literal["raw_alert", "raw_mask", "mask", "aoi_mask", "harmonized", "combined", "footprint", "store",
                  "pmtiles"]
    rel_path: str  # relative to the project folder; "" when no_data
    fingerprint: str  # of the inputs that produced it
    inputs: dict
    content_id: str  # new uuid4 hex on every write; downstream fingerprints use it
    size_bytes: int = 0
    no_data: bool = False  # slice 1 only; such a record now reads missing (spec §5.1)
    stats: dict = Field(default_factory=dict)  # counts made while the file was written (spec §5.1): harmonized
    # {alert_pixels, alert_pixels_unmasked, date_min, date_max}; aligned mask {on_pixels}
    created_at: datetime


class AlertSetParams(BaseModel):
    min_size_ha: float = 0.5
    area_mode: Literal["aoi", "drawn"] = "aoi"
    area_geojson: Optional[dict] = None
    sorting: int = 1  # the Filter step's numeric codes (1..4)
    max_alerts: int = 0  # 0 = keep all
    attribute_config: Optional[dict] = None


class AlertSet(BaseModel):
    id: str
    period_id: str
    sources: list[str]  # sorted
    digit_map: dict[int, str]  # this set's own copy; decoding never reads project.sources
    params: AlertSetParams
    masks_used: list[dict] = []  # [{"name", "mode", "content_id"}]
    footprint_from: list[str] = []  # set ids whose pixels were removed
    store_rel_path: str
    combined_rel_path: str
    footprint_rel_path: str
    n_alerts: int = 0
    n_reviewed: int = 0
    last_alert_index: int = 0
    fingerprint: str
    created_at: datetime


class Project(BaseModel):
    schema_version: int = SCHEMA_VERSION
    name: str
    created_at: datetime
    updated_at: datetime
    aoi: Optional[AoiRecord] = None
    sources: dict[str, AlertSource] = {}
    masks: dict[str, MaskLayer] = {}
    vis_layers: dict[str, VisLayer] = {}
    grid: Optional[GridSpec] = None
    look_back_days: int = 90
    periods: dict[str, Period] = {}
    active_period: Optional[str] = None
    coverage: dict[str, SourceCoverage] = {}
    artifacts: dict[str, Artifact] = {}
    alert_sets: dict[str, AlertSet] = {}
    active_set: Optional[str] = None
    report_template: Optional[str] = None

    @property
    def locked(self) -> bool:
        """AOI and grid are frozen once any set exists (spec D10)."""
        return bool(self.alert_sets)


def period_id(start: date, end: date) -> str:
    return f"{start.isoformat()}_{end.isoformat()}"


def sources_slug(sources) -> str:
    """The set id's and set folder's source part: sorted, file-safe tokens."""
    return "-".join(safe_token(s) for s in sorted(sources, key=str.casefold))


def set_id(period_id: str, sources: list[str]) -> str:
    return f"{period_id}__{sources_slug(sources)}"


def add_predefined_source(p: Project, key: str) -> PredefinedSource:
    if key not in PREDEFINED_DIGITS:
        raise ValueError(f"unknown predefined source {key!r}")
    if key in p.sources:
        raise ValueError(f"{key} is already a source")
    src = PredefinedSource(name=key, catalogue_key=key, digit=PREDEFINED_DIGITS[key])
    # Replaced, not mutated: the app publishes shallow copies of the project
    # that share this dict with a render on another thread.
    p.sources = {**p.sources, key: src}
    return src


def next_custom_digit(p: Project) -> int:
    used = {s.digit for s in p.sources.values()}
    for d in CUSTOM_DIGITS:
        if d not in used:
            return d
    raise ValueError("all 6 custom source slots are in use")


def add_custom_source(p: Project, *, name: str, origin: str, ref: str, asset_type: str, format: str,
                      date_encoding: str, band_map: BandMap, native: Optional[NativeGrid]) -> CustomSource:
    """Validate the name (spec §5.1), take the lowest free custom digit and add
    the source. Raises ``ValueError`` with a message key: ``steps.alerts.*``
    from ``validate_source_name``, or ``steps.alerts.cap_reached`` once all 6
    custom slots are in use -- a key too, so every caller can pass this
    function's errors through ``t()`` (controller ruling, Task 7)."""
    from deforestation_alerts.project.names import validate_source_name

    error = validate_source_name(name, p)
    if error:
        raise ValueError(error)
    try:
        digit = next_custom_digit(p)
    except ValueError:
        raise ValueError("steps.alerts.cap_reached") from None
    src = CustomSource(name=name.strip(), origin=origin, ref=ref, asset_type=asset_type, format=format,
                       date_encoding=date_encoding, band_map=band_map, native=native, digit=digit)
    p.sources = {**p.sources, src.name: src}  # replaced, not mutated (published snapshots share it)
    return src


def sets_using_source(p: Project, name: str) -> list[str]:
    return sorted(s.id for s in p.alert_sets.values() if name in s.sources)


def update_custom_source(p: Project, name: str, **changes) -> CustomSource:
    """Edit a custom source's ref, format, encoding, band map or native grid.
    Refused once a set used it: "add a new one instead" (spec §5.1)."""
    if sets_using_source(p, name):
        raise ValueError("steps.alerts.edit_used")
    src = p.sources[name]
    if src.kind != "custom":
        raise ValueError(f"{name} is not a custom source")
    changes.pop("name", None)
    changes.pop("digit", None)
    new = src.model_copy(update=changes)
    p.sources = {**p.sources, name: new}
    return new


def remove_source(p: Project, name: str) -> None:
    """Drop the definition and, for a custom source, its coverage record. The
    artifacts and their files go through ``project.cleanup`` (spec §5.2)."""
    src = p.sources.get(name)
    p.sources = {k: s for k, s in p.sources.items() if k != name}
    if src is not None and src.kind == "custom" and name in p.coverage:
        p.coverage = {k: c for k, c in p.coverage.items() if k != name}
