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
#: Int32 holds 9 decimal digits (max 2 * 10^8 < 2^31), and rasterio.features
#: does not accept int64 -- so 3 predefined + 6 custom sources.
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


class CustomSource(BaseModel):
    kind: Literal["custom"] = "custom"
    name: str
    origin: Literal["gee", "file"]
    ref: str  # asset id, or absolute path
    format: Literal["radd", "ccdc"]
    digit: int


AlertSource = Annotated[Union[PredefinedSource, CustomSource], Field(discriminator="kind")]


class MaskLayer(BaseModel):
    name: str
    origin: Literal["gee", "file"]
    ref: str
    mode: Literal["keep", "exclude"] = "keep"
    enabled: bool = True


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


class Artifact(BaseModel):
    key: str
    kind: Literal["raw_alert", "raw_mask", "harmonized", "combined", "footprint", "store", "pmtiles"]
    rel_path: str  # relative to the project folder; "" when no_data
    fingerprint: str  # of the inputs that produced it
    inputs: dict
    content_id: str  # new uuid4 hex on every write; downstream fingerprints use it
    size_bytes: int = 0
    no_data: bool = False  # the source does not cover the window
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


def set_id(period: str, sources: list[str]) -> str:
    slug = "-".join(safe_token(s) for s in sorted(sources, key=str.casefold))
    return f"{period}__{slug}"


def add_predefined_source(p: Project, key: str) -> PredefinedSource:
    if key not in PREDEFINED_DIGITS:
        raise ValueError(f"unknown predefined source {key!r}")
    if key in p.sources:
        raise ValueError(f"{key} is already a source")
    src = PredefinedSource(name=key, catalogue_key=key, digit=PREDEFINED_DIGITS[key])
    p.sources[key] = src
    return src


def next_custom_digit(p: Project) -> int:
    used = {s.digit for s in p.sources.values()}
    for d in CUSTOM_DIGITS:
        if d not in used:
            return d
    raise ValueError("all 6 custom source slots are in use")


def remove_source(p: Project, name: str) -> None:
    p.sources.pop(name, None)
