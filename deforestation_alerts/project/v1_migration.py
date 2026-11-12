"""Convert a version 1 recipe (``recipe_parameters.json`` + ``alert_db.csv``,
written by the old Voila app) into a version 2 project, in place
(spec 2026-11-11 v1 recipe migration). No GUI imports."""

import ast
import hashlib
import json
import shutil
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from pydantic import ValidationError
from shapely import wkt
from shapely.errors import ShapelyError
from shapely.geometry import shape
from shapely.ops import unary_union

from deforestation_alerts.errors import KeyedError
from deforestation_alerts.project import io, paths
from deforestation_alerts.project.models import (
    PREDEFINED_DIGITS,
    AlertSet,
    AlertSetParams,
    AoiRecord,
    BandMap,
    CustomSource,
    GeeVisLayer,
    MaskLayer,
    MigratedFrom,
    Period,
    PeriodMask,
    PredefinedSource,
    Project,
    period_id,
    set_id,
)
from deforestation_alerts.project.review_groups import polygon_area_ha
from deforestation_alerts.project.review_status import NOT_REVIEWED, reviewed_mask
from deforestation_alerts.project.share import canonical_set_paths
from deforestation_alerts.project.store_schema import DETECTED_AREA, GEOM_COLUMNS, STORE_COLUMNS
from deforestation_alerts.scripts.alert_store import save_gdf_to_parquet
from deforestation_alerts.scripts.image_sources import LANDSAT, S2

RECIPE_FILE = "recipe_parameters.json"
TABLE_FILE = "alert_db.csv"
#: Written first and removed last by a conversion: its files are leftovers of one that was interrupted.
CONVERTING = ".v1_converting"

WGS84 = "EPSG:4326"
#: v1 columns holding text; read_csv makes an all-blank one float, the store keeps them as objects.
_TEXT_COLUMNS = ("status", "alert_sources", "before_img", "after_img", "description", "admin1", "admin2", "admin3")
#: v1 image id prefix -> the v2 timeline source it belongs to.
_PICK_SOURCES = (("COPERNICUS/S2", S2), ("LANDSAT/", LANDSAT))


def pixel_ha(sources: list[str]) -> float:
    """One alert pixel at the equator in hectares: 10 m when a Sentinel-2
    based source (RADD, GLAD-S2) is in the set, else Landsat's 28 m."""
    return 0.01 if {"RADD", "GLAD-S2"} & set(sources) else 0.0784


def fill_areas(gdf: gpd.GeoDataFrame, sources: list[str]) -> None:
    """``detected_area_ha`` from the pixel count; ``area_ha`` is the area of
    the reviewer's drawn polygon, else a v1 value above 0, else the detected one."""
    # v1 counted pixels of a geographic grid: as wide as at the equator in degrees, so cos(latitude) narrower
    bounds = gdf.geometry.bounds
    cos_lat = np.cos(np.radians((bounds["miny"] + bounds["maxy"]) / 2)).fillna(1.0)
    gdf[DETECTED_AREA] = pd.to_numeric(gdf["count"], errors="coerce") * pixel_ha(sources) * cos_lat
    area = pd.to_numeric(gdf["area_ha"], errors="coerce")
    # v1 measured a drawn polygon in Web Mercator (too large by 1/cos² latitude): measured again as a Save does
    drawn = pd.Series([polygon_area_ha(g) if g is not None and not g.is_empty else np.nan for g in gdf["alert_polygon"]],
                      index=gdf.index)
    gdf["area_ha"] = drawn.where(drawn > 0, area.where(area > 0, gdf[DETECTED_AREA]))


def v2_pick(value, millis) -> Optional[str]:
    """v1's saved image (an Earth Engine id) and its time in milliseconds as
    the v2 pick ``"<source> <YYYY-MM-DD> "`` (the text the timeline writes),
    or ``None`` when the collection is not one the v2 timeline indexes."""
    if not isinstance(value, str):
        return None
    source = next((src for prefix, src in _PICK_SOURCES if value.startswith(prefix)), None)
    try:
        ms = float(millis)
    except (TypeError, ValueError):
        return None
    if source is None or pd.isna(ms):
        return None
    return f"{source} {datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()} "


def _note(notes: list, key: str, **values) -> None:
    """A conversion note as its message key and values: the dialog shows it in the user's language."""
    notes.append({"key": key, "values": values})


def _parse_wkt(text, col: str, i: int, notes: list):
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return wkt.loads(text)
    except ShapelyError:
        _note(notes, "migrated.note_unreadable", alert=i, column=col)
        return None


def _codes(value, i: int, notes: list):
    """``alert_type_unique`` as v1 wrote it (``"[1, 2]"``) back to the list v2
    stores. An unreadable cell is left empty and its text kept in the note:
    the store's list column cannot hold it."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        _note(notes, "migrated.note_unreadable_codes", alert=i, value=value)
        return None


def read_table(csv_path: Path) -> tuple[gpd.GeoDataFrame, list[dict]]:
    """v1's ``alert_db.csv`` as the frame a v2 store holds (``alert_id`` from
    0, ``geometry`` = the bounding box, every store column but ``set_id``),
    and notes on what could not be read. Call ``fill_areas`` before saving it."""
    notes: list[dict] = []
    df = pd.read_csv(csv_path)
    df = df.drop(columns=[c for c in df.columns if c.startswith("Unnamed:")] + ["gee_id"], errors="ignore")
    df["alert_id"] = range(len(df))
    # columns v1 lacks (radar picks, review stamps, ...) are added empty, as an older v2 store reads
    for col in STORE_COLUMNS:
        if col not in df.columns and col not in ("geometry", "set_id"):
            df[col] = pd.Series(None, index=df.index, dtype=object)
    for col in ("admin1", "admin2", "admin3"):
        # v1 wrote "Paraguay,"; a Save in v2 writes the name alone
        df[col] = df[col].map(lambda v: v.strip().rstrip(", ") if isinstance(v, str) else v)
    for col in _TEXT_COLUMNS:
        df[col] = df[col].astype(object)
    status = df["status"]
    df["status"] = status.where(status.notna() & (status.astype(str).str.strip() != ""), NOT_REVIEWED)
    df["alert_type_unique"] = [_codes(v, i, notes) for i, v in enumerate(df["alert_type_unique"])]
    for side in ("before", "after"):
        # object: a list of NaN would make the column float, which a Save's pick text cannot go in
        df[f"{side}_img"] = pd.Series([v2_pick(v, ms) or v for v, ms in zip(df[f"{side}_img"], df[f"{side}_img_info"])],
                                      index=df.index, dtype=object)
    geoms = {col: gpd.GeoSeries([_parse_wkt(v, col, i, notes) for i, v in enumerate(df[col])], index=df.index, crs=WGS84)
             for col in GEOM_COLUMNS[1:]}
    gdf = gpd.GeoDataFrame(df.drop(columns=list(geoms)), geometry=geoms["bounding_box"].copy())
    for col, series in geoms.items():
        gdf[col] = series
    return gdf, notes


ERR_AOI = "migrated.err_aoi"
ERR_TABLE = "migrated.err_table"
ERR_PARAMS = "migrated.err_params"
ERR_LEFTOVER = "migrated.err_leftover"
#: v1's CCDC alerts: the custom source a ``ccdc_layer`` becomes, on its v1 digit.
CCDC = "CCDC"
CCDC_DIGIT = 3
MASK_NAME = "v1 mask"
LAYER_NAME = "v1 layer"
_VIS_KEYS = ("bands", "min", "max", "palette")


class V1MigrationError(KeyedError):
    """The conversion failed and wrote nothing. ``key`` is ``migrated.err_aoi``,
    ``migrated.err_table`` or ``migrated.err_params`` (``values`` hold ``error``),
    or ``migrated.err_leftover`` (``values`` hold ``files``)."""


def is_v1_recipe(folder: Path) -> bool:
    """Both v1 files are in ``folder`` and it is not a project yet."""
    folder = Path(folder)
    return ((folder / RECIPE_FILE).is_file() and (folder / TABLE_FILE).is_file()
            and not (folder / paths.MANIFEST).exists())


def parse_vis(text) -> dict:
    """``bands``, ``min``, ``max`` and ``palette`` of the Earth Engine vis
    params a v1 user typed (``"{'min': 0, 'max': 3}"``, braces optional);
    ``{}`` when the text does not read as a dict."""
    if not isinstance(text, str):
        return {}
    text = text.strip()
    if not text.startswith("{"):
        text = "{" + text + "}"
    try:
        vis = ast.literal_eval(text)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return {}
    return {k: vis[k] for k in _VIS_KEYS if k in vis} if isinstance(vis, dict) else {}


@contextmanager
def _failing_as(key: str):
    try:
        yield
    except V1MigrationError:
        raise
    except Exception as exc:
        raise V1MigrationError(f"{key}: {exc}", key=key, error=str(exc)) from exc


def _piece(notes: list, label: str, build):
    """A mask, layer or source model, or None with a note when it does not validate."""
    try:
        return build()
    except ValidationError as exc:
        _note(notes, "migrated.note_not_imported", label=label, error=exc.errors()[0]["msg"])
        return None


def _aux_layer(notes: list, asset, vis_text):
    """v1's extra layer with its vis, or with the default display (and a note) when the vis does not validate."""
    try:
        return GeeVisLayer(name=LAYER_NAME, asset=asset, **parse_vis(vis_text))
    except ValidationError as exc:
        layer = _piece(notes, LAYER_NAME, lambda: GeeVisLayer(name=LAYER_NAME, asset=asset))
        if layer is not None:
            _note(notes, "migrated.note_default_display", label=LAYER_NAME, error=exc.errors()[0]["msg"])
        return layer


# call under io.manifest_lock (as v1_open.ensure_converted does): its cleanup removes manifest temp files
def convert(folder: Path, resolve_aoi: Callable[[dict], dict]) -> Project:
    """Turn the v1 recipe in ``folder`` into a locked project in the same
    folder and return it as saved. ``resolve_aoi(params)`` returns the AOI as
    a WGS84 GeoJSON FeatureCollection. The v1 files are never changed; on any
    failure every file written is removed and a ``V1MigrationError`` raised."""
    folder = Path(folder)
    with _failing_as(ERR_PARAMS):
        if not is_v1_recipe(folder):
            raise V1MigrationError(key=ERR_PARAMS, error=f"{folder} is not an unconverted version 1 recipe")
        params = json.loads((folder / RECIPE_FILE).read_text(encoding="utf-8"))
        start, end = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
        selected = list(params.get("selected_alert_sources") or [])
    with _failing_as(ERR_TABLE):
        gdf, notes = read_table(folder / TABLE_FILE)
        fill_areas(gdf, selected)
    with _failing_as(ERR_AOI):
        fc = resolve_aoi(params)
        geom = unary_union([shape(f["geometry"]) for f in fc["features"]])
        if geom.is_empty:
            raise V1MigrationError(key=ERR_AOI, error="the area of interest has no geometry")
    with _failing_as(ERR_PARAMS):
        p = _project(folder, params, (start, end), selected, gdf, geom, notes)
    with _failing_as(ERR_TABLE):
        return _write(folder, p, gdf, fc)


def _project(folder: Path, params: dict, dates: tuple, selected: list, gdf, geom, notes: list) -> Project:
    now = datetime.now()
    period = Period(id=period_id(*dates), start=dates[0], end=dates[1])
    sources, masks, layers, masks_used = {}, {}, {}, []
    for name in selected:
        if name in PREDEFINED_DIGITS:
            sources[name] = PredefinedSource(name=name, catalogue_key=name, digit=PREDEFINED_DIGITS[name])
        elif name == CCDC and params.get("ccdc_layer"):
            src = _piece(notes, CCDC, lambda: CustomSource(
                name=CCDC, origin="gee", ref=params["ccdc_layer"], asset_type="IMAGE", format="ccdc",
                date_encoding="decimal_year",
                band_map=BandMap(alert="detection_count", date="first_detection_date"), digit=CCDC_DIGIT))
            if src is not None:
                sources[CCDC] = src
        if name not in sources:
            _note(notes, "migrated.note_source_kept", name=name)
    if params.get("mask_layer"):
        mask = _piece(notes, MASK_NAME, lambda: MaskLayer(name=MASK_NAME, origin="gee", ref=params["mask_layer"],
                                                          asset_type="IMAGE"))
        if mask is not None:
            masks[MASK_NAME] = mask
            masks_used = [{"name": MASK_NAME, "mode": "keep", "content_id": "v1"}]
            layers[MASK_NAME] = GeeVisLayer(name=MASK_NAME, asset=mask.ref)
    if params.get("aux_layer"):
        layer = _aux_layer(notes, params["aux_layer"], params.get("aux_layer_vis"))
        if layer is not None:
            layers[LAYER_NAME] = layer
    period = period.model_copy(update={
        "sources": [n for n in selected if n in sources],
        "digits": {CCDC: CCDC_DIGIT} if CCDC in sources else {},
        "masks": {MASK_NAME: PeriodMask()} if masks else {},
    })
    set_sources = sorted(selected)
    digit_map = {PREDEFINED_DIGITS[n]: n for n in set_sources if n in PREDEFINED_DIGITS}
    if CCDC in set_sources:
        digit_map[CCDC_DIGIT] = CCDC
    selection = params.get("alert_selection_polygons") or {}
    drawn = bool(selection.get("features"))
    n_reviewed = int(reviewed_mask(gdf).sum())
    s = AlertSet(
        id=set_id(period.id, set_sources), period_id=period.id, sources=set_sources, digit_map=digit_map,
        params=AlertSetParams(min_size_ha=params["min_area"], sorting=params["alert_sorting_method"],
                              max_alerts=params["max_number_alerts"], area_mode="drawn" if drawn else "aoi",
                              area_geojson=selection if drawn else None),
        masks_used=masks_used, store_rel_path="", combined_rel_path="", footprint_rel_path="",
        n_alerts=len(gdf), n_reviewed=n_reviewed,
        last_alert_index=min(max(int(params.get("actual_alert_id") or 0), 0), max(len(gdf) - 1, 0)),
        fingerprint="v1:" + hashlib.sha1((folder / TABLE_FILE).read_bytes()).hexdigest(),
        created_at=datetime.fromtimestamp((folder / TABLE_FILE).stat().st_mtime),
    )
    s = s.model_copy(update=canonical_set_paths(s, period))
    template = params.get("custom_report_template")
    return Project(
        name=folder.name, created_at=now, updated_at=now,
        aoi=AoiRecord(spec={}, name=params.get("aoi_name") or folder.name, method=params.get("aoi_method") or "",
                      digest=hashlib.sha1(geom.wkb).hexdigest(), bounds=tuple(geom.bounds),
                      geometry_file=paths.AOI_SIDECAR),
        sources=sources, new_period_sources=list(sources), masks=masks, vis_layers=layers,
        periods={period.id: period}, active_period=period.id, alert_sets={s.id: s}, active_set=s.id,
        report_template=template if template and Path(template).is_file() else None,
        multi_evaluator=False,
        migrated_from=MigratedFrom(migrated_at=now, n_alerts=len(gdf), n_reviewed=n_reviewed, notes=notes),
    )


def _write(folder: Path, p: Project, gdf, fc: dict) -> Project:
    """The store, ``aoi.geojson``, then the manifest, between the writing and
    the removal of the ``CONVERTING`` marker; returns the project read back.
    Leftovers of an interrupted conversion (the marker is there) are removed
    first; without the marker, an existing store or ``aoi.geojson`` refuses.
    On failure, what this call wrote is removed."""
    s = p.alert_sets[p.active_set]
    store = paths.resolve(folder, s.store_rel_path)
    aoi_file = folder / paths.AOI_SIDECAR
    marker = folder / CONVERTING
    if marker.exists():
        store.unlink(missing_ok=True)
        aoi_file.unlink(missing_ok=True)
    else:
        found = [target.relative_to(folder).as_posix() for target in (store, aoi_file) if target.exists()]
        if found:
            files = ", ".join(found)
            raise V1MigrationError(f"{files} already in {folder}", key=ERR_LEFTOVER, files=files)
    # the highest folder of the store's path this call creates
    made = [d for d in store.parents if folder in d.parents and not d.exists()]
    marker.touch()
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        gdf["set_id"] = s.id
        save_gdf_to_parquet(gdf, store)
        aoi_file.write_text(json.dumps(fc), encoding="utf-8")
        io.save_project(p, folder.parent)
        loaded = io.load_project(folder.name, folder.parent)
        marker.unlink()
        return loaded
    except BaseException:
        if made:
            shutil.rmtree(made[-1], ignore_errors=True)
        else:
            store.unlink(missing_ok=True)
        aoi_file.unlink(missing_ok=True)
        # convert ran on a folder without a manifest: any there now is this call's
        (folder / paths.MANIFEST).unlink(missing_ok=True)
        for tmp in folder.glob(f".{paths.MANIFEST}.*.tmp"):
            tmp.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
        raise
