"""The one rule for "this alert is reviewed" (spec §3): a stored ``status``
that is present, not ``""`` and not ``"Not reviewed"``. The split, the return
selection, the merge, the set's reviewed count and Analysis's tally all use it,
so they never disagree on a blank or missing status.

It also holds the review vocabulary: the stored statuses, the verdicts a
reviewer picks, their map codes and their label keys. Every other module
uses these names rather than writing the strings out."""

import pandas as pd

#: The status Integrate writes on every new alert.
NOT_REVIEWED = "Not reviewed"
CONFIRMED = "Confirmed"
FALSE_POSITIVE = "False Positive"
NEED_REVISION = "Need revision"

#: The statuses a review Save stores, in the order the review panel lists them.
VERDICTS = (CONFIRMED, FALSE_POSITIVE, NEED_REVISION)
#: The verdicts that record forest loss: the polygon is kept, the loss driver
#: is asked, and earlier periods show them near a new alert.
LOSS_VERDICTS = (CONFIRMED, NEED_REVISION)
#: status -> the code the map tiles, the legend and the charts use. Anything
#: not listed is read as 0.
STATUS_CODES = {NOT_REVIEWED: 0, CONFIRMED: 1, FALSE_POSITIVE: 2, NEED_REVISION: 3}
#: verdict -> the translation key of its label.
VERDICT_LABEL_KEYS = {CONFIRMED: "analysis_tile.verdict.true", FALSE_POSITIVE: "analysis_tile.verdict.false",
                      NEED_REVISION: "analysis_tile.verdict.revision"}


def _missing(value) -> bool:
    if value is None or value is pd.NA:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):  # a list or an array: not a missing scalar
        return False


def is_reviewed(status) -> bool:
    """Whether one stored ``status`` is a review: not None/NaN/``pd.NA``, not
    ``""``, not ``"Not reviewed"``."""
    return not _missing(status) and status not in ("", NOT_REVIEWED)


def reviewed_mask(gdf) -> "pd.Series":
    """``is_reviewed`` for every row of ``gdf``, as a boolean Series aligned
    on its index; all False when it has no ``status`` column (an older store)."""
    if gdf is None:
        return pd.Series([], dtype=bool)
    if "status" not in gdf.columns:
        return pd.Series(False, index=gdf.index, dtype=bool)
    status = gdf["status"]
    return (status.notna() & ~status.isin(["", NOT_REVIEWED])).astype(bool)
