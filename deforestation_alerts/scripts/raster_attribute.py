"""Per-alert zonal attribution. Reads a (multiband) raster and adds one column
per selected band to the alerts gdf, using a chosen statistic over the pixels each
alert polygon covers. Pure rasterio/geopandas/numpy — no GEE, no UI (mirrors
vectorize.py). Generalizes vectorize._date_stats (min/max of one band) to a
configurable reducer over many bands, read via per-polygon windows so it scales
to national alert counts (cost ~ sum of polygon bbox areas, not n_polygons * grid)."""

import re

import numpy as np
import rasterio
from rasterio import features

from deforestation_alerts.errors import ProjectRuleError
from deforestation_alerts.project.store_schema import RESERVED_COLUMNS
from deforestation_alerts.project.questions import is_answer_column

#: Columns the alerts gdf already carries when attribution runs (the ids
#: included: Integrate writes them first), and ``evaluator``, which an
#: evaluator split writes later (spec §3). Attribution columns must not
#: silently overwrite these.
ALERT_RESERVED_COLUMNS = RESERVED_COLUMNS

#: What an attribution column named like a questionnaire answer
#: (``questions.is_answer_column``: the ``q_`` prefix) is written as instead.
#: The exports drop a ``q_`` column no current question owns, and a question
#: keyed after it would overwrite it on save.
ANSWER_PREFIX_RENAME = "attr_"


def safe_column(name):
    """Lowercase, collapse runs of non-alphanumerics to one underscore.

    Returns ``"band"`` for empty / None / blank input."""
    col = re.sub(r"[^0-9a-zA-Z]+", "_", str(name or "")).strip("_").lower()
    return col or "band"


def display_column(text) -> str:
    """What the column field shows while typing: lower case, other characters
    as ``_`` (``safe_column`` also trims the ends when the config is built)."""
    return re.sub(r"[^0-9a-zA-Z]+", "_", str(text or "")).lower()


def resolve_column_names(specs, reserved=()):
    """Return specs with each `column` made unique vs `reserved` + earlier specs
    (suffix `_2`, `_3`, …). Falls back to the band name when `column` is blank.
    A column in the questionnaire's ``q_`` namespace is written with
    ``ANSWER_PREFIX_RENAME`` in front (``q_cover`` -> ``attr_q_cover``): the
    ``q_`` prefix is the answers' own."""
    used = set(reserved)
    out = []
    for spec in specs:
        base = spec.get("column") or spec["band"]
        if is_answer_column(base):
            base = f"{ANSWER_PREFIX_RENAME}{base}"
        name = base
        k = 2
        while name in used:
            name = f"{base}_{k}"
            k += 1
        used.add(name)
        out.append({**spec, "column": name})
    return out


def reserved_for(layers, skip=None, reserved=ALERT_RESERVED_COLUMNS) -> set:
    """The columns a layer's bands must not take (spec D3): ``reserved`` (the
    alert columns) and every column of the other ``layers``. ``skip`` is the
    index of the layer being edited: its own columns stay its own."""
    out = set(reserved)
    for i, layer in enumerate(layers or []):
        if i != skip:
            out.update(b["column"] for b in (layer or {}).get("bands", []))
    return out


def band_names(raster_path):
    """List band names from a raster's descriptions, falling back to band_N."""
    return [key for key, _text in band_keys_and_texts(raster_path)]


def band_keys_and_texts(raster_path):
    """Each band as ``(key, text)``, from one read of the file: the key is its
    ``band_names`` value (what a config and its columns use), the text what a
    form shows: its description, else "Band N" (as ``user_raster.read_header``)."""
    with rasterio.open(raster_path) as src:
        return [
            (desc, desc) if desc else (f"band_{i}", f"Band {i}")
            for i, desc in enumerate(src.descriptions, start=1)
        ]


def _majority(values):
    """Most frequent value; ties broken toward the smallest value."""
    uniq, counts = np.unique(values, return_counts=True)
    top = counts.max()
    # uniq is sorted ascending, so the first max-count entry is the smallest value.
    return uniq[counts == top][0]


_REDUCERS = {
    "majority": _majority,
    "mean": np.mean,
    "median": np.median,
    "min": np.min,
    "max": np.max,
}


def _resolve_band_index(src, band_name):
    """Map a band name to its 1-based index: match a description, else band_N."""
    descs = [d if d else f"band_{i}" for i, d in enumerate(src.descriptions, start=1)]
    if band_name in descs:
        return descs.index(band_name) + 1
    raise ProjectRuleError(
        f"Attribution band {band_name!r} not found in raster (have {descs})",
        key="errors.attribute_band_missing", band=band_name, bands=", ".join(descs),
    )


def attribute_alerts(gdf, raster_path, band_stats):
    """Return a copy of `gdf` with one new column per `band_stats` entry.

    `band_stats`: list of ``{"band": <name>, "stat": <reducer>, "column": <out>}``.
    For each alert polygon: read only the raster window under its bounds, mask to
    the polygon, drop nodata / non-finite pixels, apply the reducer. Empty
    coverage -> ``None``. Polygons are reprojected to the raster CRS for masking;
    the returned gdf keeps the original CRS/geometry with the new columns appended.
    Raises ValueError if the raster has no CRS or a requested band is missing.
    """
    out = gdf.copy()
    if not band_stats:
        return out

    with rasterio.open(raster_path) as src:
        if src.crs is None:
            raise ProjectRuleError(f"Attribution raster has no CRS: {raster_path}", key="errors.attribute_no_crs",
                                   path=str(raster_path))
        nodata = src.nodata

        # Resolve band->index + reducer up front (fail loud on bad config).
        resolved = [
            (spec["column"], _resolve_band_index(src, spec["band"]),
             _REDUCERS[spec["stat"]])
            for spec in band_stats
        ]

        if out.empty:
            for col, _, _ in resolved:
                out[col] = []
            return out

        # Reproject polygons to the raster CRS once for masking.
        geoms = gdf.geometry
        if gdf.crs is not None and gdf.crs != src.crs:
            geoms = geoms.to_crs(src.crs)

        columns = {col: [] for col, _, _ in resolved}
        for geom in geoms:
            shape = geom.__geo_interface__
            try:
                window = features.geometry_window(src, [shape])
            except Exception:
                window = None
            if window is None or window.width == 0 or window.height == 0:
                for col, _, _ in resolved:
                    columns[col].append(None)
                continue
            win_transform = src.window_transform(window)
            out_shape = (int(window.height), int(window.width))
            mask = features.geometry_mask(
                [shape], out_shape=out_shape,
                transform=win_transform, invert=True,  # True INSIDE the polygon
            )
            if not mask.any():
                # An alert smaller than a pixel of a coarser raster can cover
                # no pixel centre at all: it takes the pixels it touches.
                mask = features.geometry_mask(
                    [shape], out_shape=out_shape, transform=win_transform,
                    invert=True, all_touched=True,
                )
            for col, idx, reducer in resolved:
                band = src.read(idx, window=window)
                vals = band[mask]
                vals = vals[np.isfinite(vals)]
                if nodata is not None:
                    vals = vals[vals != nodata]
                columns[col].append(reducer(vals) if vals.size else None)

    for col, _, _ in resolved:
        out[col] = columns[col]
    return out


def build_attribute_config(source, kind, band_rows, reserved=ALERT_RESERVED_COLUMNS, name=None):
    """Assemble the ``attribute_raster`` config from Aux-tile rows.

    `band_rows`: list of ``{"include": bool, "band": str, "stat": str,
    "column": str}``, an Earth Engine band's also with its ``crs`` and
    ``scale`` (metres, ``nominalScale``), which are kept for Integrate's
    free-space estimate. Keeps only included rows, sanitizes the output column
    and de-duplicates it vs `reserved` + earlier included rows.

    With a layer `name`, each column is ``<name>_<band>`` (the rows' own
    ``column`` is ignored) and the config carries ``"name"``. Without one
    (a layer saved before layers had names) the column defaults to the band
    name, as it always did. Returns ``{"source", "kind", "bands": [...]}``.
    """
    name = (str(name).strip() if name else "") or None
    included = [
        {"band": r["band"], "stat": r["stat"],
         "column": safe_column(f"{name}_{r['band']}" if name else (r.get("column") or r["band"])),
         **{k: r[k] for k in ("crs", "scale") if r.get(k) is not None}}
        for r in band_rows if r.get("include")
    ]
    included = resolve_column_names(included, reserved=reserved)
    config = {"source": source, "kind": kind, "bands": included}
    if name:
        config["name"] = name
    return config
