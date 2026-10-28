"""Review packages and reviewed copies (spec 2026-10-10 §5-§7, §9): the zip
format a creator and its evaluators exchange.

A zip carries a ``share.json`` header and an exact allow-list of members;
every read goes through ``open_checked``. Pure: no GUI, no Earth Engine.
Every refusal is a ``ShareError`` naming a ``share.*`` message; the GUI
renders it with ``error_text``.
"""

import json
import math
import shutil
import stat
import zipfile
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterator, Literal, Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from pydantic import BaseModel, ValidationError
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from deforestation_alerts.engine.growth_links import project_rows
from deforestation_alerts.errors import KeyedError
from deforestation_alerts.project import grouping, growth, review_groups
from deforestation_alerts.project import io as project_io
from deforestation_alerts.project import models, paths
from deforestation_alerts.project import split as split_mod
from deforestation_alerts.project.models import SCHEMA_VERSION, AlertSet, Period, Project, ReviewCopy
from deforestation_alerts.project.review_status import NOT_REVIEWED, VERDICTS, is_reviewed, reviewed_mask
from deforestation_alerts.project.store_schema import (
    GROWS_FROM,
    GROWS_FROM_AUTO,
    REVIEW_AFTER,
    REVIEW_BEFORE,
    REVIEW_GROUP,
)
from deforestation_alerts.project.questions import column, is_answer_column
from deforestation_alerts.project.review_copy import refuse_if_read_only
from deforestation_alerts.scripts import local_files, scene_index, scene_query
from deforestation_alerts.scripts.alert_store import load_gdf_from_parquet, store_generation, store_lock
from deforestation_alerts.scripts.naming import safe_token

FORMAT = "defo-alerts-share"
#: The version a package, and a return made without grouping, is written with.
FORMAT_VERSION = 1
#: A return of a copy whose grouping is effective (spec 2026-10-26 §8.3): every
#: row assigned to the evaluator, ``Not reviewed`` ones (detached members) too.
RETURN_GROUPS_VERSION = 2
READABLE_VERSIONS = (FORMAT_VERSION, RETURN_GROUPS_VERSION)
KIND_PACKAGE = "review-package"
KIND_RETURN = "review-return"

# Split on purpose: tests/test_i18n.py reads a "share.<x>" string constant as a
# message key of the ``share`` namespace, and this one is a file name.
HEADER_MEMBER = "share" + ".json"
MANIFEST_MEMBER = "project.json"
AOI_MEMBER = "aoi.geojson"
STORE_MEMBER = "alerts/alert_db.parquet"
SCENES_MEMBER = "imagery/scenes.parquet"
TILES_MEMBER = "imagery/tiles.parquet"
INDEX_MEMBER = "imagery/index.json"
TEMPLATE_MEMBER = "template/report.docx"
REVIEWS_MEMBER = "reviews.parquet"
#: A format-2 return's flag (spec 2026-10-26 §8.3): the row was in a group of
#: two or more when packaged and is not in the copy. Only a copy that recorded
#: its packaged groups (``ReviewCopy.grouped_at_packaging``) writes it.
DETACHED = "detached"
#: The earlier sets' alerts a copy links against (spec 2026-10-20 §6.6). An
#: older app refuses a package with it as ``share.err.unexpected_member``.
EARLIER_MEMBER = paths.EARLIER_SNAPSHOT

PACKAGE_REQUIRED = frozenset({HEADER_MEMBER, MANIFEST_MEMBER, AOI_MEMBER, STORE_MEMBER})
PACKAGE_OPTIONAL = frozenset({SCENES_MEMBER, TILES_MEMBER, INDEX_MEMBER, TEMPLATE_MEMBER, EARLIER_MEMBER})
RETURN_REQUIRED = frozenset({HEADER_MEMBER, REVIEWS_MEMBER})

#: Uncompressed sizes a zip may declare (spec §9): per member, all together,
#: and the header's own (a few hundred bytes in practice).
MAX_MEMBER_BYTES = 2 * 1024 ** 3
MAX_TOTAL_BYTES = 8 * 1024 ** 3
MAX_HEADER_BYTES = 1024 ** 2


class ShareError(KeyedError, ValueError):
    """A package or a reviewed copy was refused (spec §9). ``key`` names a
    ``share.*`` message and ``params`` its values; ``str(exc)`` is the key."""

    def __init__(self, key: str, **params):
        super().__init__(key, key=key, **params)
        self.params = params


class ShareHeader(BaseModel):
    """``share.json`` (spec §5.1). A return adds ``returned_at``."""

    format: str
    format_version: int
    kind: str
    app_version: str
    origin_project: str
    set_id: str
    set_fingerprint: str
    split_id: str
    evaluator: str
    evaluator_index: int
    questions_digest: str
    created_at: datetime
    returned_at: Optional[datetime] = None


def _open_zip(zip_path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ShareError("share.err.not_a_zip", path=str(zip_path)) from exc


def _unsafe(info: zipfile.ZipInfo) -> bool:
    """An absolute name, a ``..`` part, a Windows separator or drive, a
    symlink, or an encrypted member."""
    name = info.filename
    return (name.startswith("/") or "\\" in name or PureWindowsPath(name).drive != ""
            or ".." in PurePosixPath(name).parts
            or stat.S_ISLNK(info.external_attr >> 16) or bool(info.flag_bits & 0x1))


@contextmanager
def open_checked(zip_path, required: frozenset, optional: frozenset = frozenset()) -> Iterator[zipfile.ZipFile]:
    """The zip at ``zip_path``, open, once every member passed the checks of
    spec §9: each name is in ``required | optional``, safe and unique, within
    the size caps, and every ``required`` member is there. Nothing outside
    the allow-list is ever read. Raises ``ShareError``."""
    zf = _open_zip(zip_path)
    with zf:
        allowed = required | optional
        seen, total = set(), 0
        for info in zf.infolist():
            name = info.filename
            if _unsafe(info) or name in seen:
                raise ShareError("share.err.unsafe_member", member=name)
            seen.add(name)
            if name not in allowed:
                raise ShareError("share.err.unexpected_member", member=name)
            if info.file_size > MAX_MEMBER_BYTES:
                raise ShareError("share.err.too_large", member=name)
            total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise ShareError("share.err.too_large", member="")
        missing = sorted(required - seen)
        if missing:
            raise ShareError("share.err.missing_member", member=missing[0])
        yield zf


def read_header(zf) -> ShareHeader:
    """The zip's ``share.json``: another format, a format version outside
    ``READABLE_VERSIONS`` (a return) or ``FORMAT_VERSION`` (a package) and an
    unreadable header are each refused with their own message."""
    try:
        info = zf.getinfo(HEADER_MEMBER)
    except KeyError:
        raise ShareError("share.err.no_header") from None
    if _unsafe(info):  # peek_header does not go through open_checked
        raise ShareError("share.err.unsafe_member", member=HEADER_MEMBER)
    if info.file_size > MAX_HEADER_BYTES:
        raise ShareError("share.err.bad_header")
    try:
        raw = json.loads(zf.read(info).decode("utf-8"))
    # UnicodeDecodeError is a ValueError; RecursionError (deep JSON) is a RuntimeError;
    # a tampered member can also raise zlib.error, NotImplementedError (unknown compression) or EOFError.
    except (ValueError, zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError, EOFError) as exc:
        raise ShareError("share.err.bad_header") from exc
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise ShareError("share.err.wrong_format")
    # format 2 is a grouping return's (spec 2026-10-26 §8.3); packages stay 1
    readable = READABLE_VERSIONS if raw.get("kind") == KIND_RETURN else (FORMAT_VERSION,)
    if raw.get("format_version") not in readable:
        raise ShareError("share.err.newer_format", found=raw.get("format_version"), expected=max(readable))
    try:
        return ShareHeader.model_validate(raw)
    except ValidationError as exc:
        raise ShareError("share.err.bad_header") from exc


def peek_header(zip_path) -> ShareHeader:
    """The header alone, of a zip of any kind (the Import button reads it
    before it knows which allow-list applies)."""
    with _open_zip(zip_path) as zf:
        return read_header(zf)


def evaluator_token(index: int, name: str) -> str:
    """``e<NN>-<name token>``: the 1-based index keeps two evaluators apart
    when their tokens collide (``Ana Maria`` / ``Ana-Maria``) or are empty."""
    return f"e{index:02d}-{safe_token(name)}"


def package_filename(project_name: str, set_id: str, index: int, name: str) -> str:
    return f"{safe_token(project_name)}__{set_id}__{evaluator_token(index, name)}__review.zip"


def return_filename(origin_project: str, set_id: str, index: int, name: str) -> str:
    return f"{safe_token(origin_project)}__{set_id}__{evaluator_token(index, name)}__reviewed.zip"


def review_columns(questions) -> list[str]:
    """The store columns a review writes (spec §7.2): ``status``, the answer
    column of each question (``questions.column``), then the images, the
    admin names, the drawn polygon and its area."""
    out = list(REVIEW_BEFORE)
    for q in questions:
        name = column(q)
        if name not in out:
            out.append(name)
    out += [c for c in REVIEW_AFTER if c not in out]
    return out


def _missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value == ""
    if isinstance(value, BaseGeometry):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):  # a list or an array: a value, not a missing one
        return False


def _number(value) -> bool:
    return isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, (bool, np.bool_))


def _listlike(value) -> bool:
    return isinstance(value, (list, tuple, np.ndarray))


def same_value(a, b) -> bool:
    """Spec §7.3: two missing values (None, NaN, pd.NA, "") are equal;
    lists (``grows_from``, spec 2026-10-20 §6.6) compare as their links
    (``growth.links_of``: an empty one is missing); geometries compare by WKB;
    numbers with ``math.isclose(rel_tol=1e-9)``; anything else by its text."""
    if _listlike(a) or _listlike(b):
        return growth.links_of(a) == growth.links_of(b)
    missing_a, missing_b = _missing(a), _missing(b)
    if missing_a or missing_b:
        return missing_a and missing_b
    if isinstance(a, BaseGeometry) or isinstance(b, BaseGeometry):
        return isinstance(a, BaseGeometry) and isinstance(b, BaseGeometry) and a.wkb == b.wkb
    if _number(a) and _number(b):
        return math.isclose(float(a), float(b), rel_tol=1e-9)
    return str(a) == str(b)


# ---- building (spec §5, §6.4) ------------------------------------------------

#: The scene index's members and where each lives in a project folder.
_IMAGERY = ((SCENES_MEMBER, paths.scenes_path), (TILES_MEMBER, paths.tiles_path),
            (INDEX_MEMBER, paths.scene_manifest_path))


def canonical_set_paths(s: AlertSet, period: Period) -> dict[str, str]:
    """The set's three paths in the canonical layout (``project.paths``),
    relative to the project folder: ``periods/<period_id>/sets/<sources>/...``.
    A package carries these whatever the creator's manifest said, and an
    import writes the store to the first one (spec §5.2, §6.1)."""
    sdir = paths.set_dir(Path(""), s.period_id, s.sources)
    return {
        "store_rel_path": paths.store_path(sdir).as_posix(),
        "combined_rel_path": paths.combined_path(sdir, s.sources, period.start, period.end).as_posix(),
        "footprint_rel_path": paths.footprint_path(sdir).as_posix(),
    }


def trimmed_manifest(p: Project, set_id: str) -> Project:
    """The manifest a package carries (spec §5.2): a deep copy of ``p`` with
    the one set (and its split) and its period, both active; no artifacts
    and no coverage (the copy never prepares or integrates); no file layers
    (their paths are the creator's); no template and no review copy (the
    importer sets both); the AOI read from ``aoi.geojson``; and the set's
    paths in the canonical layout. ``p`` is left as it is."""
    s = p.alert_sets.get(set_id)
    period = p.periods.get(s.period_id) if s is not None else None
    if s is None or period is None:
        raise ShareError("share.err.no_set", set_id=set_id)
    if p.aoi is None:
        raise ShareError("share.err.no_aoi")
    return p.model_copy(deep=True, update={
        "alert_sets": {set_id: s.model_copy(deep=True, update=canonical_set_paths(s, period))},
        "active_set": set_id,
        "periods": {period.id: period.model_copy(deep=True)},
        "active_period": period.id,
        "artifacts": {},
        "coverage": {},
        "vis_layers": {k: v.model_copy(deep=True) for k, v in p.vis_layers.items() if v.kind != "file"},
        "report_template": None,
        "review_copy": None,
        "aoi": p.aoi.model_copy(update={"geometry_file": paths.AOI_SIDECAR}),
    })


def aoi_geojson_for_package(p, pdir) -> Optional[dict]:
    """The AOI sidecar's GeoJSON, when the project has a readable one; None
    otherwise (an Earth Engine AOI without file inputs never needed one: the
    caller then passes the AOI step's geometry, spec §5.3)."""
    if p is None or p.aoi is None or not p.aoi.geometry_file:
        return None
    try:
        return json.loads((Path(pdir) / paths.AOI_SIDECAR).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _aoi_collection(data, key: str) -> dict:
    """``data`` as a plain FeatureCollection of its geometries (no properties,
    no ``crs`` member), or ``ShareError(key)`` when it holds no readable,
    non-empty geometry. Reads a sidecar geopandas wrote and an Earth Engine
    ``getInfo`` of the AOI collection alike."""
    features = data.get("features") if isinstance(data, dict) and data.get("type") == "FeatureCollection" else None
    if not features or not isinstance(features, list):
        raise ShareError(key)
    out = []
    for feature in features:
        try:
            geom = shape(feature["geometry"])
        except Exception as exc:  # noqa: BLE001 -- any unreadable geometry refuses the AOI
            raise ShareError(key) from exc
        if geom.is_empty:
            raise ShareError(key)
        out.append({"type": "Feature", "properties": {}, "geometry": mapping(geom)})
    return {"type": "FeatureCollection", "features": out}


def _copy_file(src, dest) -> None:
    shutil.copyfile(src, dest)


def _snapshot_store(store: Path, dest: Path, set_id: str) -> Path:
    """Copy the set's store under its lock (spec §4.4, §5): a Save, a split or
    a merge cannot replace it meanwhile, and an Integrate swap (which takes
    no store lock) is caught by the generation check."""
    with store_lock(store):
        before = store_generation(store)
        if before is None:
            raise ShareError("share.err.store_missing", set_id=set_id)
        _copy_file(store, dest)
        if store_generation(store) != before:
            raise ShareError("share.err.store_changed", set_id=set_id)
    return dest


def _snapshot_imagery(pdir: Path, dest_dir: Path) -> dict:
    """``{member: copy}`` of the scene index, copied under its lock; ``{}``
    unless all three files are there (a partial index reads as none)."""
    with scene_index.index_lock(pdir):
        sources = {member: where(pdir) for member, where in _IMAGERY}
        if not all(src.is_file() for src in sources.values()):
            return {}
        dest_dir.mkdir(parents=True, exist_ok=True)
        copies = {}
        for member, src in sources.items():
            copies[member] = dest_dir / src.name
            _copy_file(src, copies[member])
    return copies


def _check_assignments(gdf, sp) -> None:
    """The store's ``evaluator`` column is the split's: some rows assigned,
    every assigned name one of ``sp.evaluators``."""
    if split_mod.EVALUATOR_COLUMN not in gdf.columns:
        raise ShareError("share.err.split_mismatch")
    assigned = set(split_mod.assignments(gdf)) - {""}
    if not assigned or not assigned <= set(sp.evaluators):
        raise ShareError("share.err.split_mismatch")


def _write_zip(path: Path, texts: dict, files: dict) -> Path:
    """``texts`` (member -> str) first, then ``files`` (member -> local path)."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for member, text in texts.items():
            zf.writestr(member, text)
        for member, src in files.items():
            zf.write(src, arcname=member)
    return path


def _earlier_alerts(p: Project, pdir: Path, s: AlertSet, gdf, dest: Path) -> Optional[Path]:
    """The snapshot of the earlier sets' alerts a copy of ``s`` (its alerts
    ``gdf``) links against, written to ``dest`` (``growth.write_snapshot``):
    the parent candidates and every row a link names (``growth.snapshot_rows``,
    seeded with the set's ``grows_from`` and ``grows_from_auto``). None when
    there is nothing to carry."""
    pool = project_rows(p, pdir, p.periods[s.period_id])
    seeds = [lk for col in (GROWS_FROM, GROWS_FROM_AUTO) if col in gdf.columns
             for cell in gdf[col] for lk in growth.links_of(cell)]
    rows = growth.snapshot_rows(pool, seeds)
    return growth.write_snapshot(rows, dest) if rows else None


def build_package(p: Project, pdir: Path, set_id: str, evaluator: str, aoi_geojson: dict,
                  out_dir: Path, *, app_version: str) -> Path:
    """The review package of ``evaluator`` for set ``set_id`` (spec §5), at
    ``out_dir / package_filename(...)``. Built in a scratch folder and moved
    into ``out_dir`` whole. Blocking: call it off the event loop.

    ``aoi_geojson`` is the AOI geometry as GeoJSON (``aoi_geojson_for_package``
    or the AOI step's). The packaged split is marked ``packaged`` with the
    questionnaire's digest; ``p`` itself is not changed: the caller records
    both on the creator's set. Raises ``ShareError``, or ``ReadOnlyProject``
    for a review copy (spec §6.3)."""
    refuse_if_read_only(p)
    pdir = Path(pdir)
    trimmed = trimmed_manifest(p, set_id)
    s = p.alert_sets[set_id]
    sp = s.split
    if sp is None:
        raise ShareError("share.err.no_split", set_id=set_id)
    if evaluator not in sp.evaluators:
        raise ShareError("share.err.unknown_evaluator", evaluator=evaluator)
    index = sp.evaluators.index(evaluator) + 1
    aoi = _aoi_collection(aoi_geojson, "share.err.no_aoi")
    digest = split_mod.questions_digest(p.questions)
    shared = trimmed.alert_sets[set_id]
    trimmed.alert_sets = {set_id: shared.model_copy(update={
        "split": shared.split.model_copy(update={"packaged": True, "questions_digest": digest})})}
    header = ShareHeader(format=FORMAT, format_version=FORMAT_VERSION, kind=KIND_PACKAGE, app_version=app_version,
                         origin_project=p.name, set_id=set_id, set_fingerprint=s.fingerprint, split_id=sp.split_id,
                         evaluator=evaluator, evaluator_index=index, questions_digest=digest,
                         created_at=datetime.now())
    template = Path(p.report_template) if p.report_template else None
    with local_files.scratch_dir() as tmp:
        store = _snapshot_store(paths.resolve(pdir, s.store_rel_path), tmp / "alert_db.parquet", set_id)
        gdf = load_gdf_from_parquet(store)
        _check_assignments(gdf, sp)
        files = {STORE_MEMBER: store, **_snapshot_imagery(pdir, tmp / "imagery")}
        earlier = _earlier_alerts(p, pdir, s, gdf, tmp / EARLIER_MEMBER)
        if earlier is not None:
            files[EARLIER_MEMBER] = earlier
        if template is not None and template.is_file():
            files[TEMPLATE_MEMBER] = template
        texts = {HEADER_MEMBER: header.model_dump_json(indent=2),
                 MANIFEST_MEMBER: trimmed.model_dump_json(indent=2),
                 AOI_MEMBER: json.dumps(aoi)}
        built = _write_zip(tmp / package_filename(p.name, set_id, index, evaluator), texts, files)
        return local_files.copy_into(built, Path(out_dir) / built.name)


def return_rows(gdf, evaluator: str, questions, *, all_assigned: bool = False,
                packaged: Optional[list[int]] = None) -> pd.DataFrame:
    """What a reviewed copy returns (spec §6.4): the rows assigned to
    ``evaluator`` and reviewed (``is_reviewed``), or every row assigned to
    it with ``all_assigned`` (a format-2 return, spec 2026-10-26 §8.3);
    ``alert_id``, ``evaluator`` and the review columns the store has. With
    ``packaged`` (the copy's ``grouped_at_packaging``), a ``DETACHED`` column
    too: the row's id is in ``packaged`` and the row is not in a group now."""
    mask = split_mod.assignments(gdf).to_numpy() == evaluator
    if not all_assigned:
        mask = mask & reviewed_mask(gdf).to_numpy()
    cols = ["alert_id", split_mod.EVALUATOR_COLUMN] + [c for c in review_columns(questions) if c in gdf.columns]
    rows = gdf.loc[mask, cols]
    if packaged is not None:
        detached = np.isin(review_groups.ids(gdf), packaged) & ~review_groups.grouped_mask(gdf)
        rows = rows.assign(**{DETACHED: detached[mask]})
    return rows


def _write_table(frame, dest: Path) -> Path:
    """``frame`` as parquet: GeoParquet with ``alert_polygon`` as its active
    geometry (EPSG:4326) when that column is there, plain parquet otherwise."""
    out = pd.DataFrame(frame).reset_index(drop=True)
    if "alert_polygon" in out.columns:
        order = list(out.columns)
        geoms = [g if isinstance(g, BaseGeometry) else None for g in out["alert_polygon"]]
        out = gpd.GeoDataFrame(out.drop(columns="alert_polygon"),
                               geometry=gpd.GeoSeries(geoms, crs="EPSG:4326")).rename_geometry("alert_polygon")
        out = out[order]
    out.to_parquet(dest, index=False)
    return dest


def read_table(path) -> pd.DataFrame:
    """A parquet file ``_write_table`` (or the store writer) made: a
    GeoDataFrame when it carries geometry, else a DataFrame."""
    try:
        return gpd.read_parquet(path)
    except ValueError:  # "Missing geo metadata": a plain parquet
        return pd.read_parquet(path)


def build_return(p: Project, pdir: Path, out_dir: Path, *, app_version: str) -> Path:
    """The reviewed copy of a review copy (spec §6.4), at
    ``out_dir / return_filename(...)``: its header and ``reviews.parquet``.
    Allowed at any time; each holds the latest state. Blocking: call it off
    the event loop. Raises ``ShareError``.

    With the copy's grouping effective (its split's record, spec 2026-10-26
    §8.3) the return is format 2 and carries every row assigned to the
    evaluator, so a member detached in the copy comes back; otherwise it is
    format 1, exactly as before grouping. A format-2 return of a copy that
    recorded its packaged groups flags the rows detached since (``DETACHED``)."""
    rc = p.review_copy
    if rc is None:
        raise ShareError("share.err.not_a_copy")
    s = p.alert_sets.get(rc.set_id)
    if s is None:
        raise ShareError("share.err.no_set", set_id=rc.set_id)
    groups = grouping.effective(p, s) is not None
    now = datetime.now()
    header = ShareHeader(format=FORMAT, format_version=RETURN_GROUPS_VERSION if groups else FORMAT_VERSION,
                         kind=KIND_RETURN, app_version=app_version,
                         origin_project=rc.origin_project, set_id=rc.set_id, set_fingerprint=rc.set_fingerprint,
                         split_id=rc.split_id, evaluator=rc.evaluator, evaluator_index=rc.evaluator_index,
                         questions_digest=split_mod.questions_digest(p.questions), created_at=now, returned_at=now)
    with local_files.scratch_dir() as tmp:
        store = _snapshot_store(paths.resolve(Path(pdir), s.store_rel_path), tmp / "alert_db.parquet", s.id)
        rows = return_rows(load_gdf_from_parquet(store), rc.evaluator, p.questions, all_assigned=groups,
                           packaged=rc.grouped_at_packaging if groups else None)
        reviews = _write_table(rows, tmp / REVIEWS_MEMBER)
        name = return_filename(rc.origin_project, rc.set_id, rc.evaluator_index, rc.evaluator)
        built = _write_zip(tmp / name, {HEADER_MEMBER: header.model_dump_json(indent=2)}, {REVIEWS_MEMBER: reviews})
        return local_files.copy_into(built, Path(out_dir) / built.name)


# ---- importing (spec §6.1, §6.5) ----------------------------------------------

#: Where an imported copy keeps its report template (an importer-chosen path).
TEMPLATE_REL = "template/report.docx"


def _inside(folder: Path, rel: str) -> Path:
    """``folder / rel`` resolved, refused unless it lies inside ``folder``."""
    root = Path(folder).resolve()
    dest = (root / rel).resolve()
    if dest == root or not dest.is_relative_to(root):
        raise ShareError("share.err.unsafe_member", member=rel)
    return dest


def _extract(zf, member: str, dest: Path) -> Path:
    """Copy one member out to ``dest``, a path the importer chose. The
    member's declared size bounds what is read (``zipfile`` stops there and
    checks its CRC)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(member) as src, open(dest, "wb") as out:
        shutil.copyfileobj(src, out)
    return dest


def _package_manifest(zf, header: ShareHeader) -> Project:
    """``project.json``, checked against the header (spec §6.1 step 2): one
    set, the header's, with its fingerprint and split, the header's evaluator
    at its index, a period and sources that make the set's id (so the
    canonical paths built from them stay plain tokens), and an AOI."""
    try:
        p = Project.model_validate_json(zf.read(MANIFEST_MEMBER))
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        raise ShareError("share.err.bad_manifest", error=str(exc)) from exc
    if p.schema_version != SCHEMA_VERSION:
        raise ShareError("share.err.bad_manifest", error=f"schema {p.schema_version}")
    s = p.alert_sets.get(header.set_id)
    if s is None or len(p.alert_sets) != 1 or s.id != header.set_id:
        raise ShareError("share.err.payload_mismatch", what="set_id")
    if s.fingerprint != header.set_fingerprint:
        raise ShareError("share.err.payload_mismatch", what="set_fingerprint")
    sp = s.split
    if sp is None or sp.split_id != header.split_id:
        raise ShareError("share.err.payload_mismatch", what="split_id")
    i = header.evaluator_index
    if not 1 <= i <= len(sp.evaluators) or sp.evaluators[i - 1] != header.evaluator:
        raise ShareError("share.err.payload_mismatch", what="evaluator")
    period = p.periods.get(s.period_id)
    if (period is None or period.id != s.period_id or models.period_id(period.start, period.end) != s.period_id
            or models.set_id(s.period_id, s.sources) != s.id):
        raise ShareError("share.err.payload_mismatch", what="set_layout")
    if p.aoi is None:
        raise ShareError("share.err.payload_mismatch", what="aoi")
    return p


def _package_aoi(zf) -> dict:
    try:
        data = json.loads(zf.read(AOI_MEMBER).decode("utf-8"))
    except ValueError as exc:
        raise ShareError("share.err.bad_aoi") from exc
    return _aoi_collection(data, "share.err.bad_aoi")


def _check_store(path: Path, header: ShareHeader) -> gpd.GeoDataFrame:
    """The store, refused unless it reads, has ``alert_id`` (unique
    integers), ``status`` and ``evaluator``, and holds the header's set only."""
    try:
        gdf = load_gdf_from_parquet(path)
    except Exception as exc:  # noqa: BLE001 -- any unreadable store is refused the same way
        raise ShareError("share.err.bad_store", error=str(exc)) from exc
    missing = [c for c in ("alert_id", "status", split_mod.EVALUATOR_COLUMN) if c not in gdf.columns]
    if missing:
        raise ShareError("share.err.bad_store", error=f"missing columns {missing}")
    ids = gdf["alert_id"]
    if not pd.api.types.is_integer_dtype(ids) or not ids.is_unique or ids.isna().any():
        raise ShareError("share.err.bad_store", error="alert_id is not a unique integer id")
    if "set_id" in gdf.columns and not (gdf["set_id"] == header.set_id).all():
        raise ShareError("share.err.bad_store", error="rows of another set")
    return gdf


def _grouped_ids(gdf, evaluator: str) -> list[int]:
    """The ``alert_id``s of ``evaluator``'s rows in a group of two rows or
    more: what a copy records when imported (``grouped_at_packaging``)."""
    mine = split_mod.assignments(gdf).to_numpy() == evaluator
    return sorted(int(a) for a in review_groups.ids(gdf)[mine & review_groups.grouped_mask(gdf)])


def _package_imagery(zf, tmp: Path) -> dict:
    """``{member: staged file}`` of the scene index with Planet access reset
    (spec §6.5: the evaluator's own NICFI access is checked, not the
    creator's); ``{}`` when the package has no complete, readable index (a
    cache miss: the copy builds its own)."""
    names = set(zf.namelist())
    if not all(member in names for member, _ in _IMAGERY):
        return {}
    staged = {member: _extract(zf, member, tmp / "imagery" / PurePosixPath(member).name) for member, _ in _IMAGERY}
    try:
        manifest = scene_index.SceneManifest.model_validate_json(staged[INDEX_MEMBER].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    # Aged past the TTL so the first open runs a tail fetch (TAIL_DAYS only),
    # which is the only request that asks NICFI and so learns the access.
    stale = manifest.refreshed - timedelta(hours=scene_query.TTL_HOURS + 1)
    staged[INDEX_MEMBER].write_text(
        manifest.model_copy(update={"planet": scene_index.PlanetState(), "refreshed": stale}
                            ).model_dump_json(indent=2),
        encoding="utf-8")
    return staged


def _claim_folder(base: Path, header: ShareHeader) -> Path:
    """A new folder ``<origin>__e<NN>-<evaluator>`` under ``base``, ``-2``,
    ``-3``... when a folder of that name (any case) exists. ``mkdir`` without
    ``exist_ok`` is the claim: two imports never share a folder."""
    stem = f"{safe_token(header.origin_project)}__{evaluator_token(header.evaluator_index, header.evaluator)}"
    base.mkdir(parents=True, exist_ok=True)
    existing = [d.name for d in base.iterdir() if d.is_dir()]
    for n in range(1, 1000):
        name = stem if n == 1 else f"{stem}-{n}"
        if project_io.validate_project_name(name, existing) is not None:
            continue
        try:
            (base / name).mkdir(exist_ok=False)
        except FileExistsError:
            continue
        return base / name
    raise ShareError("share.err.no_free_name", name=stem)


def _package_earlier(zf, tmp: Path) -> Optional[Path]:
    """The staged earlier-alerts snapshot, checked to read; None for a package
    without one (built before it, or with no earlier alert to carry)."""
    if EARLIER_MEMBER not in zf.namelist():
        return None
    staged = _extract(zf, EARLIER_MEMBER, tmp / EARLIER_MEMBER)
    try:
        growth.read_snapshot(staged)
    except Exception as exc:  # noqa: BLE001 -- any unreadable snapshot is refused the same way
        raise ShareError("share.err.bad_store", error=f"{EARLIER_MEMBER}: {exc}") from exc
    return staged


def _write_copy(folder: Path, base: Path, header: ShareHeader, manifest: Project, aoi: dict, store: Path,
                imagery: dict, template: Optional[Path], earlier: Optional[Path] = None,
                grouped: Optional[list[int]] = None) -> None:
    """Every file to the destination the importer derives, then the manifest.
    The copy has a ``snapshot_at`` exactly when it has the earlier alerts,
    and records ``grouped`` as its ``grouped_at_packaging``."""
    s = manifest.alert_sets[header.set_id]
    period = manifest.periods[s.period_id]
    canonical = canonical_set_paths(s, period)
    store_dest = _inside(folder, canonical["store_rel_path"])
    store_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(store, store_dest)
    _inside(folder, paths.AOI_SIDECAR).write_text(json.dumps(aoi), encoding="utf-8")
    for member, where in _IMAGERY:
        if member in imagery:
            dest = _inside(folder, paths.rel(folder, where(folder)))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(imagery[member], dest)
    template_dest = None
    if template is not None:
        template_dest = _inside(folder, TEMPLATE_REL)
        template_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template, template_dest)
    if earlier is not None:
        shutil.copyfile(earlier, _inside(folder, paths.EARLIER_SNAPSHOT))
    copy = manifest.model_copy(update={
        "name": folder.name,
        "alert_sets": {s.id: s.model_copy(update=canonical)},
        "active_set": s.id,
        "periods": {period.id: period},
        "active_period": period.id,
        "artifacts": {},
        "coverage": {},
        "vis_layers": {k: v for k, v in manifest.vis_layers.items() if v.kind != "file"},
        "aoi": manifest.aoi.model_copy(update={"geometry_file": paths.AOI_SIDECAR}),
        # Relative, so a renamed copy folder still finds it (resolved by readers).
        "report_template": TEMPLATE_REL if template_dest is not None else None,
        "review_copy": ReviewCopy(evaluator=header.evaluator, evaluator_index=header.evaluator_index,
                                  set_id=header.set_id, split_id=header.split_id,
                                  set_fingerprint=header.set_fingerprint, origin_project=header.origin_project,
                                  imported_at=datetime.now(),
                                  snapshot_at=header.created_at if earlier is not None else None,
                                  grouped_at_packaging=grouped),
    })
    project_io.save_project(copy, base)


def _import(zip_path, base: Path) -> str:
    header = peek_header(zip_path)
    if header.kind == KIND_RETURN:
        raise ShareError("share.err.is_return")
    if header.kind != KIND_PACKAGE:
        raise ShareError("share.err.unknown_kind", kind=header.kind)
    with open_checked(zip_path, PACKAGE_REQUIRED, PACKAGE_OPTIONAL) as zf, local_files.scratch_dir() as tmp:
        # Everything is read and checked in the scratch folder before a
        # folder is claimed: a refused package writes nothing (spec §6.1).
        header = read_header(zf)
        manifest = _package_manifest(zf, header)
        aoi = _package_aoi(zf)
        store = _extract(zf, STORE_MEMBER, tmp / "alert_db.parquet")
        grouped = _grouped_ids(_check_store(store, header), header.evaluator)
        imagery = _package_imagery(zf, tmp)
        template = _extract(zf, TEMPLATE_MEMBER, tmp / "report.docx") if TEMPLATE_MEMBER in zf.namelist() else None
        earlier = _package_earlier(zf, tmp)
        folder = _claim_folder(base, header)
        try:
            _write_copy(folder, base, header, manifest, aoi, store, imagery, template, earlier, grouped)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return folder.name


def import_package(zip_path, root: Optional[Path] = None) -> Project:
    """Import a review package as a new review copy (spec §6.1) under
    ``root`` (default: the data directory) and return it as saved. Nothing
    is written for a refused package; a failure while writing removes the
    claimed folder. Blocking: call it off the event loop. Raises
    ``ShareError`` (``share.err.import_failed`` for anything unforeseen)."""
    base = Path(root) if root else paths.data_dir()
    try:
        name = _import(Path(zip_path), base)
    except ShareError:
        raise
    except Exception as exc:  # noqa: BLE001 -- one message for the user, the detail for the log
        raise ShareError("share.err.import_failed", error=str(exc)) from exc
    return project_io.load_project(name, base)


def find_existing_copy(header: ShareHeader, root: Optional[Path] = None) -> Optional[str]:
    """The name of a project under ``root`` that is a review copy of the
    same split for the same evaluator (spec §6.1 re-import), or None."""
    base = Path(root) if root else paths.data_dir()
    for info in project_io.list_projects(base):
        try:
            raw = json.loads((paths.project_dir(info.name, base) / paths.MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rc = raw.get("review_copy") if isinstance(raw, dict) else None
        if isinstance(rc, dict) and rc.get("split_id") == header.split_id and rc.get("evaluator") == header.evaluator:
            return info.name
    return None


# ---- merging a reviewed copy (spec §7) ------------------------------------------


@dataclass
class ReturnPreview:
    """What merging one reviewed copy would do (spec §7.1-§7.3): its header,
    its rows indexed by ``alert_id``, the alert ids of each class, the review
    columns it writes and the columns it carries that are ignored.
    ``n_groups`` counts the groups (two rows or more, in the store or in the
    return) its classified rows belong to; ``class_groups`` the same per
    class (``"new"``, ``"conflicts"``, ``"unchanged"``)."""

    header: ShareHeader
    rows: pd.DataFrame
    new: list[int]
    conflicts: list[int]
    unchanged: list[int]
    ignored: list[int]
    columns: list[str]
    extra_columns: list[str]
    questions_changed: bool
    n_groups: int = 0
    class_groups: dict = field(default_factory=dict)


def _read_reviews(zf, evaluator: str, version: int = FORMAT_VERSION) -> pd.DataFrame:
    """``reviews.parquet``, checked (spec §7.1): unique integer ``alert_id``s,
    every ``evaluator`` the header's, every ``status`` a verdict (or
    ``Not reviewed`` in a format-2 return, spec 2026-10-26 §8.3), every
    ``review_group`` missing or a whole number, ``DETACHED`` (when there)
    a boolean column. Indexed by ``alert_id``."""
    try:
        rows = read_table(BytesIO(zf.read(REVIEWS_MEMBER)))
    except Exception as exc:  # noqa: BLE001 - any unreadable payload is the same refusal
        raise ShareError("share.merge_err_payload") from exc
    ids = rows["alert_id"] if "alert_id" in rows.columns else None
    if ids is None or not pd.api.types.is_integer_dtype(ids) or not ids.is_unique or ids.isna().any():
        raise ShareError("share.merge_err_payload")
    who = rows[split_mod.EVALUATOR_COLUMN] if split_mod.EVALUATOR_COLUMN in rows.columns else None
    if who is None or not all(isinstance(v, str) and v == evaluator for v in who):
        raise ShareError("share.merge_err_payload")
    allowed = set(VERDICTS) | ({NOT_REVIEWED} if version >= RETURN_GROUPS_VERSION else set())
    if "status" not in rows.columns or not all(isinstance(v, str) and v in allowed for v in rows["status"]):
        raise ShareError("share.merge_err_payload")
    if REVIEW_GROUP in rows.columns and not _whole_numbers(rows[REVIEW_GROUP]):
        raise ShareError("share.merge_err_payload")
    if DETACHED in rows.columns and not pd.api.types.is_bool_dtype(rows[DETACHED]):
        raise ShareError("share.merge_err_payload")
    return rows.set_index("alert_id")


def _whole_numbers(column) -> bool:
    """Every value of ``column`` missing or a whole number."""
    raw = pd.Series(column.to_numpy(dtype=object))
    values = pd.to_numeric(raw, errors="coerce")
    if (values.isna() & raw.notna()).any():
        return False
    present = values.dropna().astype(float)
    return bool(np.isfinite(present).all() and (present % 1 == 0).all())


def _positions(gdf) -> dict[int, int]:
    """Row position of each ``alert_id`` of a store."""
    return {int(a): i for i, a in enumerate(gdf["alert_id"].tolist())}


def _cell(gdf, i: int, col: str):
    """The value at row position ``i`` of ``col``; None without the column
    (a creator question added after the set was made, spec §7.2)."""
    return gdf[col].iat[i] if col in gdf.columns else None


def _group_id(value, sizes) -> Optional[int]:
    """``value`` as an id when it names a group of two rows or more."""
    return None if pd.isna(value) or sizes.get(value, 0) < 2 else int(value)


def classify_return(preview: ReturnPreview, store_gdf) -> ReturnPreview:
    """``preview`` with its rows classified against ``store_gdf`` (spec §7.3):
    ignored (no such alert, or another evaluator's), unchanged (every review
    column equal, ``same_value``), conflict (the creator's row is reviewed and
    differs), new (otherwise). ``read_return`` classifies against the store it
    is given; a merge classifies again against the store it writes.

    Groups (spec 2026-10-26 §8.3, §8.4): a returned ``Not reviewed`` row is
    unchanged unless the evaluator detached it in the copy (its ``DETACHED``
    flag; any row of a return without the column) and the store still holds
    it in a group of two rows or more. Rows connected through
    ``review_group``, in the store or in the return, among the evaluator's
    rows, are one component: a conflict in it makes every non-ignored row of
    it a conflict."""
    who = split_mod.assignments(store_gdf).tolist()
    at = _positions(store_gdf)
    evaluator = preview.header.evaluator
    rows = preview.rows
    store_groups = review_groups.group_values(store_gdf)
    store_sizes = store_groups.value_counts().to_dict()
    theirs = review_groups.group_values(rows).set_axis(rows.index)  # all <NA> without the column
    their_sizes = theirs.value_counts().to_dict()
    statuses = rows["status"].tolist()
    detached = rows[DETACHED].tolist() if DETACHED in rows.columns else [True] * len(rows)
    kind = {}
    for k, aid in enumerate(rows.index):
        aid, i = int(aid), at.get(int(aid))
        if i is None or who[i] != evaluator:
            kind[aid] = "ignored"
        elif statuses[k] == NOT_REVIEWED and (not detached[k]
                                              or _group_id(store_groups.iat[i], store_sizes) is None):
            kind[aid] = "unchanged"
        elif all(same_value(_cell(store_gdf, i, c), rows.at[aid, c]) for c in preview.columns):
            kind[aid] = "unchanged"
        elif is_reviewed(_cell(store_gdf, i, "status")):
            kind[aid] = "conflicts"
        else:
            kind[aid] = "new"
    groups = {}  # alert id -> the ids of the groups (>= 2 rows) it is in
    if store_sizes or their_sizes:
        comps = review_groups.Components()
        store_ids = review_groups.ids(store_gdf)
        for i in np.flatnonzero(store_groups.notna().to_numpy()):
            if who[i] == evaluator:
                comps.union(int(store_ids[i]), int(store_groups.iat[i]))
        for aid, value in theirs.dropna().items():
            if kind[int(aid)] != "ignored":
                comps.union(int(aid), int(value))
        conflicted = {comps.find(aid) for aid, k in kind.items() if k == "conflicts"}
        for aid, k in kind.items():
            if k == "ignored":
                continue
            if k != "conflicts" and comps.find(aid) in conflicted:
                kind[aid] = "conflicts"
            i = at[aid]
            ids = {_group_id(store_groups.iat[i], store_sizes), _group_id(theirs.at[aid], their_sizes)} - {None}
            if ids:
                groups[aid] = ids
    out = {"new": [], "conflicts": [], "unchanged": [], "ignored": []}
    for aid, k in kind.items():
        out[k].append(aid)
    class_groups = {k: len(set().union(*(groups.get(a, ()) for a in out[k]))) for k in ("new", "conflicts", "unchanged")}
    return replace(preview, **out, n_groups=len(set().union(*groups.values())), class_groups=class_groups)


def read_return(zip_path, p: Project, store_gdf) -> ReturnPreview:
    """The preview of merging the reviewed copy at ``zip_path`` into ``p``'s
    active set, whose alerts are ``store_gdf`` (spec §7.1). Refused with a
    ``ShareError``, each with its own message: ``p`` is a review copy, the
    zip or its header (``open_checked``, ``read_header``), not a reviewed
    copy, another set than the active one, a set re-run since, an earlier
    split, an evaluator not in the split, a bad payload, no alerts loaded.
    A changed questionnaire is a warning (``questions_changed``), not a
    refusal: answers are merged by question key. Blocking: off the loop."""
    if p.review_copy is not None:
        raise ShareError("share.merge_err_review_copy")
    # The kind first, as Import does: a package's members are not a reviewed
    # copy's, and the allow-list would refuse it as an unexpected file.
    kind = peek_header(zip_path).kind
    if kind == KIND_PACKAGE:
        raise ShareError("share.merge_err_kind")
    if kind != KIND_RETURN:
        raise ShareError("share.err.unknown_kind", kind=kind)
    with open_checked(zip_path, RETURN_REQUIRED) as zf:
        header = read_header(zf)
        if header.kind != KIND_RETURN:  # the zip changed since the peek
            raise ShareError("share.merge_err_kind")
        s = p.alert_sets.get(p.active_set) if p.active_set else None
        if s is None or s.id != header.set_id:
            raise ShareError("share.merge_err_wrong_set", set=header.set_id)
        if s.fingerprint != header.set_fingerprint:
            raise ShareError("share.merge_err_rerun")
        if s.split is None or s.split.split_id != header.split_id:
            raise ShareError("share.merge_err_old_split")
        if header.evaluator not in s.split.evaluators:
            raise ShareError("share.merge_err_evaluator", evaluator=header.evaluator)
        rows = _read_reviews(zf, header.evaluator, header.format_version)
    if store_gdf is None or "alert_id" not in store_gdf.columns:
        raise ShareError("share.merge_err_no_alerts")
    review = review_columns(p.questions)
    preview = ReturnPreview(
        header=header, rows=rows, new=[], conflicts=[], unchanged=[], ignored=[],
        columns=[c for c in review if c in rows.columns],
        extra_columns=[c for c in rows.columns if c not in review and c not in (split_mod.EVALUATOR_COLUMN, DETACHED)],
        questions_changed=header.questions_digest != split_mod.questions_digest(p.questions),
    )
    return classify_return(preview, store_gdf)


def _put(frame, label, col: str, value) -> None:
    """``frame.at[label, col] = value``; a missing value is written as None,
    a list-like one (``grows_from``) as a list, and a column whose dtype
    refuses the value (a float column of unset image names, a ``list<null>``
    column read back as arrays) becomes object first."""
    if _listlike(value):
        value = list(value)
        if frame[col].dtype != object:
            frame[col] = frame[col].astype(object)
    elif value is pd.NA or (isinstance(value, float) and math.isnan(value)):
        value = None
    try:
        frame.at[label, col] = value
    except (TypeError, ValueError):
        frame[col] = frame[col].astype(object)
        frame.at[label, col] = value


def apply_return(store_gdf, preview: ReturnPreview, choice: Literal["mine", "theirs"]) -> gpd.GeoDataFrame:
    """A copy of ``store_gdf`` with the reviewed copy merged (spec §7.4): the
    *new* rows, and the *conflicts* when ``choice`` is ``"theirs"``, take the
    return's review columns. A creator question's column the store lacks is
    created first (``""``, as a Save does, spec §7.2); a column the return
    lacks is left as it is. ``preview`` must be classified against
    ``store_gdf`` (``classify_return``).

    ``review_group`` (spec 2026-10-26 §8.4) is written as ``Int64``; a
    format-2 return creates it when the store lacks it, a format-1 return
    never does. With the column there, the merged table must keep the group
    invariant (``review_groups.check_groups``), else
    ``ShareError("share.merge_err_groups")``."""
    if choice not in ("mine", "theirs"):
        raise ValueError(f"unknown merge choice {choice!r}")
    out = store_gdf.copy()
    ids = list(preview.new) + (list(preview.conflicts) if choice == "theirs" else [])
    if not ids:
        return out
    columns = [c for c in preview.columns if c != REVIEW_GROUP or c in out.columns
               or preview.header.format_version >= RETURN_GROUPS_VERSION]
    for col in columns:
        if col == REVIEW_GROUP:
            out[col] = pd.array(review_groups.group_values(out), dtype="Int64")
        elif col not in out.columns:
            out[col] = "" if is_answer_column(col) else None
    at = _positions(out)
    for aid in ids:
        label = out.index[at[aid]]
        for col in columns:
            value = preview.rows.at[aid, col]
            if col == REVIEW_GROUP:
                out.at[label, col] = pd.NA if _missing(value) else int(value)
            else:
                _put(out, label, col, value)
    if REVIEW_GROUP in out.columns:
        try:
            review_groups.check_groups(out)
        except review_groups.GroupInvariantError as exc:
            raise ShareError("share.merge_err_groups") from exc
    return out
