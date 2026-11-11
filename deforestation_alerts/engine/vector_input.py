"""Vector alert sources: reading a CSV, GeoPackage or shapefile of dated points
or polygons, and writing a period's raw GeoParquet (spec §5).

The add-source form reads the file in two passes: ``read_table`` without
coordinate picks gives the column list (``suggest_columns`` pre-picks them),
then ``summarize`` describes the file under the picked date column. The
runner reads a saved source with ``read_features`` and writes one period's
features with ``normalize_vector``. Everything comes back in EPSG:4326 with
engine dates (int ``yyyyddd``); no AOI filter here, the AOI-raster clip at
harmonize drops what falls outside. Errors are message keys.
"""

import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd

from deforestation_alerts.engine.catalogue import yyyyddd
from deforestation_alerts.engine.vector_dates import DateReading, contradicts, detect, parse
from deforestation_alerts.errors import KeyedError

VECTOR_EXTENSIONS = (".csv", ".gpkg", ".shp")

ERR_UNREADABLE = "steps.alerts.err_vector_unreadable"
ERR_NO_CRS = "steps.alerts.err_vector_no_crs"
ERR_SIDECARS = "steps.alerts.err_vector_sidecars"  # a .shp without its .shx or .dbf
ERR_COLUMNS = "steps.alerts.err_vector_columns"  # a picked column is missing
ERR_NO_FEATURES = "steps.alerts.err_vector_no_features"
#: the form's refusal of a date column in conflict or with nothing readable;
#: a saved source's date problem is ``FILE_CHANGED`` instead
ERR_DATES = "steps.alerts.err_vector_dates"
FILE_CHANGED = "steps.prepare.vector_file_changed"

WGS84 = "EPSG:4326"

_SUGGEST = {
    "lon": ("lon", "lng", "long", "longitude", "x"),
    "lat": ("lat", "latitude", "y"),
    "date": ("date", "fecha", "data", "date_alert", "alert_date", "acq_date", "detection_date"),
}


class VectorInputError(KeyedError, ValueError):
    """A vector alert file the app cannot use; ``key`` names the reason."""

    def __init__(self, key: str, detail: str = "", **values):
        super().__init__(detail or key, key=key, **values)


@dataclass(frozen=True)
class VectorSummary:
    """What the form shows about a file under a date column. ``points`` counts
    a MultiPoint's parts; ``skipped`` rows have no geometry, another geometry
    (lines) or an unreadable date. Dates are None without a date order."""

    reading: DateReading
    points: int
    polygons: int
    skipped: int
    date_min: Optional[date]
    date_max: Optional[date]
    bounds: Optional[tuple[float, float, float, float]]


def driver_of(path) -> str:
    """``"csv"``, ``"gpkg"`` or ``"shp"`` by the file's extension."""
    ext = Path(path).suffix.lower()
    if ext not in VECTOR_EXTENSIONS:
        raise VectorInputError(ERR_UNREADABLE, f"{path}: not a {'/'.join(VECTOR_EXTENSIONS)} file")
    return ext[1:]


def list_layers(path) -> tuple[str, ...]:
    """A GeoPackage's layer names; ``()`` for the other drivers."""
    if driver_of(path) != "gpkg":
        return ()
    try:
        return tuple(str(n) for n in gpd.list_layers(path)["name"])
    except Exception as exc:  # noqa: BLE001 - an unreadable file is the user's, not a bug
        raise VectorInputError(ERR_UNREADABLE, str(exc)) from exc


# ------------------------------------------------------------------ reading
def _sniff(path, encoding: str) -> str:
    """The CSV delimiter among ``,`` ``;`` and tab: the most frequent in the
    header line (column names rarely hold one), ``,`` on a tie."""
    with open(path, encoding=encoding, newline="") as fh:
        head = fh.readline()
    return max((",", ";", "\t"), key=head.count)


def _coordinate(col: pd.Series, sep: str) -> pd.Series:
    text = col.astype("string").str.strip()
    if sep == ";":
        text = text.str.replace(",", ".", regex=False)  # decimal commas go with ``;``
    return pd.to_numeric(text, errors="coerce")


def _csv_frame(path, lon: Optional[str], lat: Optional[str], encoding: str = "utf-8-sig"
               ) -> tuple[pd.DataFrame, str]:
    """The CSV's rows and delimiter, read as UTF-8 (BOM or not) or, failing
    that, as cp1252, the codepage Excel saves a CSV in on Windows."""
    try:
        sep = _sniff(path, encoding)
        header = pd.read_csv(path, sep=sep, encoding=encoding, nrows=0).columns
        missing = [c for c in (lon, lat) if c is not None and c not in header]
        if missing:
            raise VectorInputError(ERR_COLUMNS, f"{path}: no column {missing[0]!r}", column=missing[0])
        return pd.read_csv(path, sep=sep, encoding=encoding, dtype={c: str for c in (lon, lat) if c}), sep
    except UnicodeDecodeError:
        if encoding == "cp1252":
            raise
        return _csv_frame(path, lon, lat, "cp1252")


def _read_csv(path, lon: Optional[str], lat: Optional[str]) -> gpd.GeoDataFrame:
    try:
        frame, sep = _csv_frame(path, lon, lat)
    except VectorInputError:
        raise
    except Exception as exc:  # noqa: BLE001 - an unreadable file is the user's, not a bug
        raise VectorInputError(ERR_UNREADABLE, str(exc)) from exc
    geometry = [None] * len(frame)
    if lon and lat:
        x, y = _coordinate(frame[lon], sep), _coordinate(frame[lat], sep)
        ok = (x.notna() & y.notna() & (x.abs() <= 180) & (y.abs() <= 90)).to_numpy()
        points = gpd.points_from_xy(x.fillna(0), y.fillna(0))
        geometry = [p if keep else None for p, keep in zip(points, ok)]
    return gpd.GeoDataFrame(frame, geometry=gpd.GeoSeries(geometry, index=frame.index, crs=WGS84), crs=WGS84)


def _check_sidecars(path) -> None:
    """A shapefile needs its ``.shx`` and ``.dbf`` (any case, as on Windows)."""
    p = Path(path)
    try:
        names = {q.name.casefold() for q in p.parent.iterdir()}
    except OSError as exc:
        raise VectorInputError(ERR_UNREADABLE, str(exc)) from exc
    missing = [ext for ext in (".shx", ".dbf") if (p.stem + ext).casefold() not in names]
    if missing:
        raise VectorInputError(ERR_SIDECARS, f"{path}: no {' or '.join(missing)} next to it",
                               missing=", ".join(missing))


def read_table(path, *, layer: Optional[str] = None, lon: Optional[str] = None,
               lat: Optional[str] = None) -> gpd.GeoDataFrame:
    """Every attribute column plus the geometry, in EPSG:4326.

    A CSV's points come from ``lon``/``lat`` (WGS84 degrees); a row with a
    missing, unparseable or out-of-range coordinate has no geometry, and
    without both picks every row has none (the form needs the columns first).
    A GeoPackage or shapefile without a CRS is refused.
    """
    driver = driver_of(path)
    if driver == "csv":
        return _read_csv(path, lon, lat)
    if driver == "shp":
        _check_sidecars(path)
    try:
        gdf = gpd.read_file(path, layer=layer) if driver == "gpkg" else gpd.read_file(path)
    except Exception as exc:  # noqa: BLE001 - a bad file or a missing layer
        raise VectorInputError(ERR_UNREADABLE, str(exc)) from exc
    if gdf.crs is None:
        raise VectorInputError(ERR_NO_CRS, f"{path} has no CRS")
    try:
        return gdf.to_crs(WGS84)
    except Exception as exc:  # noqa: BLE001 - a CRS with no path to WGS84
        raise VectorInputError(ERR_UNREADABLE, str(exc)) from exc


def suggest_columns(columns) -> dict:
    """``{"lon", "lat", "date"}``: the first column whose name matches a known
    name (case-insensitive, exact), or None."""
    names = [c for c in columns if isinstance(c, str) and c != "geometry"]
    out = {}
    for role, known in _SUGGEST.items():
        out[role] = next((c for k in known for c in names if c.casefold() == k), None)
    return out


# ------------------------------------------------------------------ features
def _kinds(geoms: gpd.GeoSeries) -> np.ndarray:
    """Per row: ``"point"``, ``"multipoint"``, ``"polygon"`` or ``""`` (no
    geometry, an empty one, a line or anything else)."""
    out = np.full(len(geoms), "", dtype=object)
    present = (geoms.notna() & ~geoms.is_empty).to_numpy()
    types = geoms.geom_type.to_numpy()
    out[present & (types == "Point")] = "point"
    out[present & (types == "MultiPoint")] = "multipoint"
    out[present & np.isin(types, ("Polygon", "MultiPolygon"))] = "polygon"
    return out


def _column(gdf, name: str) -> pd.Series:
    if name not in gdf.columns or name == gdf.geometry.name:
        raise VectorInputError(ERR_COLUMNS, f"no column {name!r}", column=name)
    return gdf[name]


def _counts(kinds: np.ndarray, geoms: gpd.GeoSeries, keep: np.ndarray) -> tuple[int, int]:
    """(points, polygons) of the kept rows; a MultiPoint counts its parts."""
    multi = keep & (kinds == "multipoint")
    parts = int(geoms[multi].apply(lambda g: len(g.geoms)).sum()) if multi.any() else 0
    return int((keep & (kinds == "point")).sum()) + parts, int((keep & (kinds == "polygon")).sum())


def _as_date(v: int) -> date:
    return date(v // 1000, 1, 1) + timedelta(days=v % 1000 - 1)


def summarize(gdf, date_col: str, order: Optional[str] = None) -> VectorSummary:
    """The file under ``date_col``, read with ``order`` or else the detected
    order. With neither, only geometry decides what is skipped and there are
    no dates."""
    values = _column(gdf, date_col)
    reading = detect(values)
    use = order or reading.order
    kinds = _kinds(gdf.geometry)
    keep = kinds != ""
    dates = None
    if use is not None:
        dates = parse(values, use)
        keep = keep & (dates > 0)
    points, polygons = _counts(kinds, gdf.geometry, keep)
    bounds = tuple(float(b) for b in gdf.geometry[keep].total_bounds) if keep.any() else None
    kept = dates[keep] if dates is not None and keep.any() else None
    return VectorSummary(reading=reading, points=points, polygons=polygons, skipped=int((~keep).sum()),
                         date_min=_as_date(int(kept.min())) if kept is not None else None,
                         date_max=_as_date(int(kept.max())) if kept is not None else None,
                         bounds=bounds)


def usable_features(gdf, date_col: str, order: str, *, dates: Optional[np.ndarray] = None
                    ) -> tuple[gpd.GeoDataFrame, dict]:
    """The features that burn: columns ``date`` (int ``yyyyddd`` > 0) and
    ``geometry``, in ``gdf``'s CRS. Points stay, a MultiPoint becomes its
    points, polygons and multipolygons stay; any other geometry and an
    unreadable date are dropped. ``dates`` is the column already parsed."""
    if dates is None:
        dates = parse(_column(gdf, date_col), order)
    kinds = _kinds(gdf.geometry)
    keep = (kinds != "") & (dates > 0)
    points, polygons = _counts(kinds, gdf.geometry, keep)
    frame = gpd.GeoDataFrame({"date": dates[keep]}, geometry=gdf.geometry[keep].to_numpy(), crs=gdf.crs)
    multi = kinds[keep] == "multipoint"
    if multi.any():
        frame = pd.concat([frame[~multi], frame[multi].explode(index_parts=False)], ignore_index=True)
    stats = {"features_read": len(gdf), "features_skipped": int((~keep).sum()),
             "points": points, "polygons": polygons}
    return frame.reset_index(drop=True), stats


def read_features(src) -> tuple[gpd.GeoDataFrame, dict]:
    """A saved vector ``CustomSource``'s usable features.

    The file was checked when the source was added; a picked column now gone,
    a stored date order the values contradict, a date column with nothing
    readable left, or points where the spec has no side all mean the file
    changed since (``FILE_CHANGED``).
    """
    spec = src.vector
    try:
        gdf = read_table(src.ref, layer=spec.layer, lon=spec.lon, lat=spec.lat)
        values = _column(gdf, spec.date)
    except VectorInputError as exc:
        if exc.key != ERR_COLUMNS:
            raise
        raise VectorInputError(FILE_CHANGED, str(exc), path=src.ref) from exc
    if contradicts(values, spec.date_order):
        raise VectorInputError(FILE_CHANGED, f"{src.ref}: dates no longer read as {spec.date_order}",
                               path=src.ref)
    dates = parse(values, spec.date_order)
    if len(gdf) and not (dates > 0).any():
        raise VectorInputError(FILE_CHANGED, f"{src.ref}: no date reads as {spec.date_order}", path=src.ref)
    feats, stats = usable_features(gdf, spec.date, spec.date_order, dates=dates)
    if stats["points"] and spec.side_m is None:
        raise VectorInputError(FILE_CHANGED, f"{src.ref} now has points and the source has no side",
                               path=src.ref)
    return feats, stats


def normalize_vector(src, win_start: date, win_end: date, out_path) -> dict:
    """Write ``src``'s features dated within ``[win_start, win_end]`` as
    GeoParquet (``date``, ``geometry``; EPSG:4326) at ``out_path``, through a
    ``.part`` file renamed into place. An empty window writes an empty file.
    Returns ``read_features``' stats plus ``features_in_window``."""
    feats, stats = read_features(src)
    lo, hi = yyyyddd(win_start), yyyyddd(win_end)
    window = feats[(feats["date"] >= lo) & (feats["date"] <= hi)]
    out = gpd.GeoDataFrame({"date": window["date"].to_numpy(dtype=np.int64)},
                           geometry=gpd.GeoSeries(window.geometry.to_numpy(), crs=WGS84), crs=WGS84)
    out_path = Path(out_path)
    part = out_path.with_name(out_path.name + ".part")
    try:
        out.to_parquet(part, index=False)
        os.replace(part, out_path)
    finally:
        part.unlink(missing_ok=True)
    return {**stats, "features_in_window": len(out)}
