# deforestation_alerts/scripts/scene_query.py
"""Everything the shared image timeline decides over plain data: which scenes
an alert can see, where they land on a piecewise date axis, which pair is
picked by default, and whether the cached index still answers.

This module must never import ``ee``. The offline test tier fails any
unmarked test that reaches Earth Engine (``tests/conftest.py``), so keeping
the rules here is what makes them testable without a live session.
"""

import calendar
from datetime import date, timedelta
from typing import Iterable, NamedTuple, Optional

import pandas as pd

from deforestation_alerts.scripts.scene_index import INDEX_VERSION, SOURCE_CODE, SOURCE_PLANET, row_id

TTL_HOURS = 24
TAIL_DAYS = 10

Window = tuple[date, date]


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

    Two steps, and both are needed. The spatial index narrows candidates by
    bounding box, which is fast and admits too much; ``intersects`` against
    just those candidates' real geometry then settles it. Skipping the second
    step would let a Landsat scene whose slanted footprint misses the alert
    look like coverage, because its bounding box still reaches it.
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

    Collapsed months take ``collapsed_px`` each; the expanded ones divide
    what is left in proportion to the days they show, all at one
    pixels-per-day, so a date's position still means its date.
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
    expanded_days = sum(spans[key][2] for key in keys if key not in collapsed)
    # A track too narrow for its collapsed blocks would otherwise give a
    # negative pixels-per-day and draw the later months off to the left.
    free_px = max(0.0, track_px - collapsed_px * n_collapsed)
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
    closed.

    A Planet NICFI basemap is a whole month's mosaic, not a scene that
    witnesses one date, so it is never offered as either side's default.
    """
    if sel is None or len(sel) == 0:
        return (None, None)
    usable = sel[sel["source"] != SOURCE_PLANET]
    before_all = usable[usable["date"] <= pd.Timestamp(a0)]
    after_all = usable[usable["date"] >= pd.Timestamp(a1)]

    def _nearest(part: pd.DataFrame, *, newest: bool) -> Optional[str]:
        pool = part[~part["hidden"]]
        if len(pool) == 0:
            return None
        row = pool.loc[pool["date"].idxmax() if newest else pool["date"].idxmin()]
        return str(row["id"])

    return (_nearest(before_all, newest=True), _nearest(after_all, newest=False))
