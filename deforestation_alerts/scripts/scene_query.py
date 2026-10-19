# deforestation_alerts/scripts/scene_query.py
"""Everything the shared image timeline decides over plain data: which scenes
an alert can see, where they land on a piecewise date axis, which pair is
picked by default, and whether the cached index still answers.

This module must never import ``ee``. The offline test tier fails any
unmarked test that reaches Earth Engine (``tests/conftest.py``), so keeping
the rules here is what makes them testable without a live session.
"""

import calendar
import re
from datetime import date, datetime, timedelta
from typing import Iterable, NamedTuple, Optional

import pandas as pd

from deforestation_alerts.scripts.scene_index import (
    INDEX_VERSION,
    SOURCE_CODE,
    SOURCE_LANDSAT,
    SOURCE_PLANET,
    SOURCE_S2,
    row_id,
)

TTL_HOURS = 24
TAIL_DAYS = 10

Window = tuple[date, date]


def shift_months(d: date, months: int) -> date:
    """``d`` moved by whole months, the day clamped to the target month's length."""
    total = (d.year * 12 + d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def needs_refresh(manifest, want: Window, *, digest: str, now) -> str:
    """What the cached index needs before it can answer ``want``.

    ``"rebuild"`` — there is no usable index: none on disk, one written by an
    older layout, or one built for a different AOI, whose tile footprints and
    granule set do not describe this project.
    ``"widen"`` — the index is for this AOI but does not reach across ``want``.
    ``"tail"`` — the index covers ``want`` but is older than ``TTL_HOURS``, so
    recently published scenes may be missing from its end.
    ``""`` — it answers as it is.

    Coverage outranks age: a widen that reaches the wanted end refetches that
    end anyway, so reporting ``"tail"`` there would cost a second request for
    nothing.
    """
    if manifest is None or manifest.version != INDEX_VERSION or manifest.aoi_digest != digest:
        return "rebuild"
    if want[0] < manifest.covered.start or want[1] > manifest.covered.end:
        return "widen"
    if (now - manifest.refreshed).total_seconds() > TTL_HOURS * 3600:
        return "tail"
    return ""


def refresh_window(manifest, want: Window, mode: str) -> Window:
    """The one contiguous range to fetch for ``mode``.

    A widen whose wanted window overhangs *both* sides of the coverage is
    served in two calls, the later side first: images published after the
    index was built are what a reviewer is most often missing. The caller
    loops until ``needs_refresh`` goes quiet.
    """
    if mode == "rebuild":
        return want
    if manifest is None:
        raise ValueError(f"mode {mode!r} needs a manifest")
    covered = manifest.covered
    if mode == "tail":
        return (covered.end - timedelta(days=TAIL_DAYS), max(want[1], covered.end))
    if mode == "widen":
        if want[1] > covered.end:
            return (covered.end, want[1])
        if want[0] < covered.start:
            return (want[0], covered.start)
        raise ValueError("nothing to widen: the coverage already spans the wanted window")
    raise ValueError(f"nothing to fetch for mode {mode!r}")


# -------------------------------------------------------------- scenes_for

SELECTION_COLUMNS = ("id", "source", "date", "cloud", "n", "hidden")

_EMPTY = pd.DataFrame(
    {
        "id": pd.Series(dtype=object),
        "source": pd.Series(dtype=object),
        "date": pd.Series(dtype="datetime64[ns]"),
        "cloud": pd.Series(dtype="float64"),
        "n": pd.Series(dtype="int64"),
        "hidden": pd.Series(dtype=bool),
    },
    columns=list(SELECTION_COLUMNS),
)


def overlapping_tiles(tiles, bbox) -> set[str]:
    """The tiles whose true footprint meets ``bbox``.

    The footprint, not the bounding box: a Landsat scene whose slanted
    footprint misses the alert must not look like coverage just because its
    envelope reaches it. ``sindex.query(predicate="intersects")`` is what
    guarantees that -- it filters its bounding-box candidates by the real
    predicate, and an empty or missing footprint (what a Planet row would
    leave) simply never matches, which is the answer ``intersects`` gives for
    one too. The second pass below is therefore redundant on the pinned
    GeoPandas, and is kept because that exactness is a property of the index's
    predicate support and not of anything this function promises; it costs one
    pass over a handful of candidates. Dropping *both* would be the bug.
    """
    if tiles is None or len(tiles) == 0:
        return set()
    candidates = tiles.iloc[list(tiles.sindex.query(bbox, predicate="intersects"))]
    exact = candidates[candidates.geometry.intersects(bbox)]
    return set(exact["tile"])


def scenes_for(tables, bbox, start: date, end: date, *, max_cloud: Optional[float] = None) -> pd.DataFrame:
    """The timeline's rows for one alert: one row per (collection, date).

    ``start`` and ``end`` are both inclusive -- they are the dates a reviewer
    asked to see, not a half-open interval.

    ``cloud`` is the mean over exactly the granules that overlap ``bbox``,
    which reproduces the ``aggregate_mean`` of the per-alert request this
    replaces: that request's ``filterBounds`` chose *which* granules to
    average, and ``CLOUDY_PIXEL_PERCENTAGE``/``CLOUD_COVER`` was always a
    whole-granule statistic, so averaging the same subset locally gives the
    same number.

    Rows over ``max_cloud`` are returned with ``hidden=True`` instead of being
    dropped, so the timeline can show the reviewer that an image exists there
    before they decide to raise the limit.
    """
    scenes = tables.scenes
    if scenes is None or len(scenes) == 0:
        return _EMPTY.copy()

    keep = overlapping_tiles(tables.tiles, bbox)
    # An empty tile means an AOI-wide mosaic (a Planet basemap): there is no
    # footprint to test, so it covers every alert.
    in_area = scenes["tile"].isin(keep) | (scenes["tile"] == "")
    in_window = scenes["date"].between(pd.Timestamp(start), pd.Timestamp(end))
    rows = scenes[in_area & in_window]
    if len(rows) == 0:
        return _EMPTY.copy()

    grouped = (
        rows.groupby(["source", "date"], as_index=False, sort=True)
        .agg(cloud=("cloud", "mean"), n=("key", "size"))
    )
    grouped["id"] = [row_id(src, d) for src, d in zip(grouped["source"], grouped["date"].dt.date)]
    # NaN cloud (a Planet basemap) is never over the limit: it has no figure
    # to compare, and hiding it would make the collection look unavailable.
    if max_cloud is None:
        grouped["hidden"] = False
    else:
        grouped["hidden"] = grouped["cloud"].gt(float(max_cloud)).fillna(False)
    grouped["hidden"] = grouped["hidden"].astype(bool)
    out = grouped.sort_values(["date", "source"], kind="stable").reset_index(drop=True)
    return out[list(SELECTION_COLUMNS)].astype({"n": "int64"})


def source_counts(sel: pd.DataFrame) -> dict[str, tuple[int, int]]:
    """``{source: (visible, hidden)}`` for the collection toggles, every source
    present even at zero so the toolbar shows a count rather than nothing."""
    out = {name: (0, 0) for name in SOURCE_CODE}
    for name, part in sel.groupby("source", sort=False):
        hidden = int(part["hidden"].sum())
        out[name] = (len(part) - hidden, hidden)
    return out


def month_counts(sel: pd.DataFrame) -> dict[tuple[int, int], int]:
    """``{(year, month): visible rows}``, the number a collapsed month shows.

    Visible only: with a cloud limit set, a collapsed month claiming images
    the reviewer cannot reach on expanding it would be a lie.
    """
    shown = sel[~sel["hidden"]]
    if len(shown) == 0:
        return {}
    counts: dict[tuple[int, int], int] = {}
    for key in zip(shown["date"].dt.year, shown["date"].dt.month):
        counts[key] = counts.get(key, 0) + 1
    return counts


# ----------------------------------------------------------------- month axis

COLLAPSED_PX = 66.0
#: the most of the track the collapsed months may take together. A long alert
#: collapses many months, and at a fixed 66px each they crowded out the
#: margins that hold the before and after candidates: a 24-month alert left
#: those months 0px and drew the blocks past the track's right edge.
FOLD_SHARE = 0.45
#: below this width a collapsed month is too narrow to read on its own, so
#: ``fold_groups`` draws each calendar half-year of them as one block.
FOLD_GROUP_BELOW_PX = 30.0


class MonthSegment(NamedTuple):
    """One month's slice of the track.

    ``days`` is the number of days of this month that the view shows, so the
    first and last months of a view are short. ``ppd`` is pixels per day,
    zero for a collapsed month, where every date lands on the block.
    """

    year: int
    month: int
    start: date
    end: date
    days: int
    x: float
    w: float
    ppd: float
    collapsed: bool


def months_between(start: date, end: date) -> list[tuple[int, int]]:
    """Every ``(year, month)`` from ``start``'s month to ``end``'s month, both
    inclusive."""
    out: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def month_segments(
    start: date,
    end: date,
    collapsed: Iterable[tuple[int, int]],
    track_px: float,
    *,
    collapsed_px: float = COLLAPSED_PX,
) -> dict[tuple[int, int], MonthSegment]:
    """Lay the months of ``[start, end]`` across ``track_px`` pixels.

    Collapsed months take ``collapsed_px`` each, but never more than
    ``FOLD_SHARE`` of the track together; the expanded ones divide what is
    left in proportion to the days they show, all at one pixels-per-day, so a
    date's position still means its date.
    """
    collapsed = set(collapsed)
    keys = months_between(start, end)
    spans: dict[tuple[int, int], tuple[date, date, int]] = {}
    for year, month in keys:
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
        s = max(first, start)
        e = min(last, end)
        spans[(year, month)] = (s, e, (e - s).days + 1)

    n_collapsed = sum(1 for key in keys if key in collapsed)
    if n_collapsed:
        collapsed_px = min(collapsed_px, track_px * FOLD_SHARE / n_collapsed)
    expanded_days = sum(spans[key][2] for key in keys if key not in collapsed)
    free_px = track_px - collapsed_px * n_collapsed  # never under 1 - FOLD_SHARE of it
    ppd = free_px / expanded_days if expanded_days else 0.0

    segments: dict[tuple[int, int], MonthSegment] = {}
    x = 0.0
    for key in keys:
        s, e, days = spans[key]
        is_collapsed = key in collapsed
        w = collapsed_px if is_collapsed else days * ppd
        segments[key] = MonthSegment(
            key[0], key[1], s, e, days, x, w, 0.0 if is_collapsed else ppd, is_collapsed
        )
        x += w
    return segments


def x_for(segments: dict[tuple[int, int], MonthSegment], d: date, *, end: bool = False) -> float:
    """The pixel offset of ``d`` on the track laid out by ``month_segments``.

    ``end=True`` asks for the right edge of what ``d`` occupies, which is what
    the alert-window band needs. A date outside the view clamps to the
    nearest edge rather than raising: a saved selection from an earlier,
    wider view is a normal thing to meet.
    """
    if not segments:
        return 0.0
    keys = sorted(segments)
    first, last = segments[keys[0]], segments[keys[-1]]
    if d < first.start:
        return 0.0
    if d > last.end:
        return last.x + last.w
    seg = segments[(d.year, d.month)]
    if seg.collapsed:
        return seg.x + (seg.w if end else 0.0)
    return seg.x + ((d - seg.start).days + (1 if end else 0)) * seg.ppd


def half_of(key: tuple[int, int]) -> tuple[int, int]:
    """``(year, 1)`` for January-June, ``(year, 2)`` for July-December."""
    return (key[0], 1 if key[1] <= 6 else 2)


def fold_groups(
    segments: dict[tuple[int, int], MonthSegment],
    split: Iterable[tuple[int, int]] = (),
) -> list[list[tuple[int, int]]]:
    """The collapsed months as the blocks to draw, in track order.

    A collapsed month is its own block while it is wide enough to read
    (``FOLD_GROUP_BELOW_PX``). Narrower than that -- a long alert, after
    ``FOLD_SHARE`` has shrunk them -- consecutive collapsed months of one
    calendar half-year share a block, so the strip reads "Jan - Jun 2024"
    instead of two dozen unlabelled slivers. Calendar halves rather than runs
    of six from the alert's start, so a block always names a half of a year.
    A half-year in ``split`` (keys from ``half_of``), which the reviewer has
    unfolded, comes back as single months. Only the drawing changes: every
    month keeps its own segment, so a date's position is the same either way.
    """
    split = set(split)
    keys = sorted(segments)
    groups: list[list[tuple[int, int]]] = []
    for i, key in enumerate(keys):
        seg = segments[key]
        if not seg.collapsed:
            continue
        last = groups[-1] if groups else None
        joins = (seg.w < FOLD_GROUP_BELOW_PX and half_of(key) not in split and last is not None
                 and i > 0 and last[-1] == keys[i - 1] and half_of(last[-1]) == half_of(key))
        if joins:
            last.append(key)
        else:
            groups.append([key])
    return groups


def auto_collapsed(start: date, end: date, a0: date, a1: date) -> set[tuple[int, int]]:
    """The months to collapse when an alert first opens: those lying wholly
    inside the alert window ``[a0, a1]``.

    Inside the window every image shows partial clearing, so none of it is a
    legal before or after; those months are context, not choices, and
    collapsing them is what lets a GLAD window of several months fit beside
    the two margins the reviewer actually picks from. The window's own
    boundary months stay expanded, because they hold the nearest candidates.
    """
    out: set[tuple[int, int]] = set()
    for year, month in months_between(start, end):
        s = max(date(year, month, 1), start)
        e = min(date(year, month, calendar.monthrange(year, month)[1]), end)
        if a0 <= s and e <= a1:
            out.add((year, month))
    return out


# ------------------------------------------------------------- default pick


def default_pick(sel: pd.DataFrame, a0: date, a1: date) -> tuple[Optional[str], Optional[str]]:
    """The pair to mark when an alert opens: ``(before_id, after_id)``.

    Nearest first, among the rows the cloud limit does not hide. A side whose
    every candidate is over the limit is left unselected (spec §3): that is
    what makes the readout's "raise the limit" guidance reachable. The rows
    are still drawn, faded, so the reviewer can see that images exist there
    before deciding.

    Nothing inside ``[a0, a1]`` is ever picked, because an image taken while
    the alert was accumulating shows partial clearing and would understate
    the change. In-window rows stay *selectable* -- a long window may leave
    no other choice -- but the reviewer has to ask for one.

    Both bounds are inclusive: a scene acquired on ``a0`` is the last look
    before the window opened, and one acquired on ``a1`` the first after it
    closed. The one exception is a single-day window (``a0 == a1``): there a
    scene on that day would satisfy both sides and be returned twice, a pair
    that shows no change. It goes to *after* -- the first look once the alert
    fired -- and *before* is taken from strictly earlier dates.

    A Planet NICFI basemap is a whole month's mosaic, not a scene that
    witnesses one date, so it is never offered as either side's default.
    """
    if sel is None or len(sel) == 0:
        return (None, None)
    usable = sel[sel["source"] != SOURCE_PLANET]
    if a0 == a1:
        before_all = usable[usable["date"] < pd.Timestamp(a0)]
    else:
        before_all = usable[usable["date"] <= pd.Timestamp(a0)]
    after_all = usable[usable["date"] >= pd.Timestamp(a1)]

    def _nearest(part: pd.DataFrame, *, newest: bool) -> Optional[str]:
        pool = part[~part["hidden"]]
        if len(pool) == 0:
            return None
        row = pool.loc[pool["date"].idxmax() if newest else pool["date"].idxmin()]
        return str(row["id"])

    return (_nearest(before_all, newest=True), _nearest(after_all, newest=False))


# ------------------------------------------------- saved picks and the GIF

_CODE_SOURCE = {code: source for source, code in SOURCE_CODE.items()}
#: ``info[3]`` as ``export`` stores it: ``"<source> <YYYY-MM-DD> "``.
_SAVED = re.compile(r"^\s*(.+?) (\d{4}-\d{2}-\d{2})\s*$")
#: the order of two collections on one date, in the GIF as on the strip
_RANK = {SOURCE_S2: 0, SOURCE_LANDSAT: 1, SOURCE_PLANET: 2}


def parse_row_id(rid) -> tuple[str, date]:
    """``(source, date)`` of a timeline row id: ``row_id`` read backwards."""
    try:
        return _CODE_SOURCE[rid[0]], datetime.strptime(rid[1:], "%Y%m%d").date()
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError(f"not a timeline row id: {rid!r}") from exc


def saved_pick_id(value) -> Optional[str]:
    """The row id of a saved ``before_img`` / ``after_img``, or ``None``.

    ``None`` for anything that is not ``"<source> <YYYY-MM-DD> "`` with a
    source the index knows: an alert never saved (NaN), a value the old
    per-pane selector wrote, a store edited by hand. Such a side opens on the
    automatic pick (spec D5); nothing is migrated.
    """
    if not isinstance(value, str):
        return None
    m = _SAVED.match(value)
    if m is None or m.group(1) not in SOURCE_CODE:
        return None
    try:
        return row_id(m.group(1), date.fromisoformat(m.group(2)))
    except ValueError:
        return None


def granule_keys(tables, bbox, source: str, day) -> list[str]:
    """Every granule key of ``source`` on ``day`` whose footprint meets ``bbox``.

    What the chosen (collection, date) mosaics for one alert. A key with no
    tile is an AOI-wide mosaic (a Planet basemap) and always counts.
    """
    tiles = overlapping_tiles(tables.tiles, bbox)
    rows = tables.scenes
    same = rows[(rows["source"] == source) & (rows["date"] == pd.Timestamp(day))]
    return sorted(same[same["tile"].isin(tiles) | (same["tile"] == "")]["key"])


def gif_frames(rows: pd.DataFrame, before_id, after_id, n: int = 2) -> pd.DataFrame:
    """The GIF's rows, oldest first, with a ``role`` column (spec D6).

    The two picks (``role`` "before" / "after"), plus up to ``n`` rows before
    the earlier pick and ``n`` after the later one. A neighbour is never
    hidden by the cloud limit, never a Planet basemap (a monthly mosaic is not
    a dated look, the rule ``default_pick`` follows) and never between the
    picks. The picks themselves are frames whatever they are. On one date
    Sentinel-2 comes before Landsat. Empty when either pick is not in ``rows``.
    """
    empty = rows.iloc[0:0].assign(role=pd.Series(dtype=object))
    ids = set(rows["id"]) if len(rows) else set()
    if not before_id or not after_id or before_id not in ids or after_id not in ids:
        return empty
    ordered = (rows.assign(_rank=rows["source"].map(_RANK).fillna(len(_RANK)))
               .sort_values(["date", "_rank"], kind="stable").reset_index(drop=True))
    where = {rid: i for i, rid in enumerate(ordered["id"])}
    lo, hi = sorted((where[before_id], where[after_id]))
    pool = (~ordered["hidden"].astype(bool)) & (ordered["source"] != SOURCE_PLANET)
    earlier = [i for i in range(lo) if pool[i]][-n:] if n else []
    later = [i for i in range(hi + 1, len(ordered)) if pool[i]][:n]
    chosen = earlier + sorted({lo, hi}) + later
    out = ordered.loc[chosen].drop(columns="_rank").reset_index(drop=True)
    out["role"] = ""
    out.loc[out["id"] == before_id, "role"] = "before"
    out.loc[out["id"] == after_id, "role"] = "after"
    return out
