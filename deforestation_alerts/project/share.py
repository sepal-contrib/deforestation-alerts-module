"""Review packages and reviewed copies (spec 2026-10-10 §5-§7, §9): the zip
format a creator and its evaluators exchange.

A zip carries a ``share.json`` header and an exact allow-list of members;
every read goes through ``open_checked``. Pure: no GUI, no Earth Engine.
Every refusal is a ``ShareError`` naming a ``share.*`` message; the GUI
renders it with ``error_text``.
"""

import json
import math
import stat
import zipfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Iterator, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, ValidationError
from shapely.geometry.base import BaseGeometry

from deforestation_alerts.errors import KeyedError
from deforestation_alerts.project.questions import column
from deforestation_alerts.scripts.naming import safe_token

FORMAT = "defo-alerts-share"
FORMAT_VERSION = 1
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

PACKAGE_REQUIRED = frozenset({HEADER_MEMBER, MANIFEST_MEMBER, AOI_MEMBER, STORE_MEMBER})
PACKAGE_OPTIONAL = frozenset({SCENES_MEMBER, TILES_MEMBER, INDEX_MEMBER, TEMPLATE_MEMBER})
RETURN_REQUIRED = frozenset({HEADER_MEMBER, REVIEWS_MEMBER})

#: Uncompressed sizes a zip may declare (spec §9): per member, all together,
#: and the header's own (a few hundred bytes in practice).
MAX_MEMBER_BYTES = 2 * 1024 ** 3
MAX_TOTAL_BYTES = 8 * 1024 ** 3
MAX_HEADER_BYTES = 1024 ** 2

#: The columns a review Save writes (``gui/tile/analysis/export.py``
#: ``_save_attributes``) around the answer columns of the questions.
_REVIEW_BEFORE = ("status",)
_REVIEW_AFTER = ("before_img", "before_img_info", "after_img", "after_img_info",
                 "admin1", "admin2", "admin3", "alert_polygon", "area_ha")


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
    """The zip's ``share.json``: another format, another format version and
    an unreadable header are each refused with their own message."""
    try:
        info = zf.getinfo(HEADER_MEMBER)
    except KeyError:
        raise ShareError("share.err.no_header") from None
    if info.file_size > MAX_HEADER_BYTES:
        raise ShareError("share.err.bad_header")
    try:
        raw = json.loads(zf.read(info).decode("utf-8"))
    except (ValueError, zipfile.BadZipFile) as exc:  # UnicodeDecodeError is a ValueError
        raise ShareError("share.err.bad_header") from exc
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise ShareError("share.err.wrong_format")
    if raw.get("format_version") != FORMAT_VERSION:
        raise ShareError("share.err.newer_format", found=raw.get("format_version"), expected=FORMAT_VERSION)
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
    out = list(_REVIEW_BEFORE)
    for q in questions:
        name = column(q)
        if name not in out:
            out.append(name)
    out += [c for c in _REVIEW_AFTER if c not in out]
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


def same_value(a, b) -> bool:
    """Spec §7.3: two missing values (None, NaN, pd.NA, "") are equal;
    geometries compare by WKB; numbers with ``math.isclose(rel_tol=1e-9)``;
    anything else by its text."""
    missing_a, missing_b = _missing(a), _missing(b)
    if missing_a or missing_b:
        return missing_a and missing_b
    if isinstance(a, BaseGeometry) or isinstance(b, BaseGeometry):
        return isinstance(a, BaseGeometry) and isinstance(b, BaseGeometry) and a.wkb == b.wkb
    if _number(a) and _number(b):
        return math.isclose(float(a), float(b), rel_tol=1e-9)
    return str(a) == str(b)
