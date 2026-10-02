# deforestation_alerts/scripts/scene_query.py
"""Everything the shared image timeline decides over plain data: which scenes
an alert can see, where they land on a piecewise date axis, which pair is
picked by default, and whether the cached index still answers.

This module must never import ``ee``. The offline test tier fails any
unmarked test that reaches Earth Engine (``tests/conftest.py``), so keeping
the rules here is what makes them testable without a live session.
"""

from datetime import date, timedelta

from deforestation_alerts.scripts.scene_index import INDEX_VERSION

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
