"""Filename helpers for alert artifacts (raster downloads + gdf exports).

Single source of truth for encoding the alert source(s) and the AOI date range
into a filename-safe, self-describing name, e.g.
``filtered_alerts_glad-l-radd_2026-01-01_2026-06-12.tif``.
"""

import re


def safe_token(text):
    """Lowercase a label, collapsing any run of non-alphanumerics to one hyphen.

    Turns source names ("GLAD-L", "GLAD S2") and dates ("2026-01-01") into
    filename-safe tokens; hyphens in the input survive as single hyphens.
    Returns ``"na"`` for empty / None / blank input.
    """
    token = re.sub(r"[^0-9a-zA-Z]+", "-", str(text or "")).strip("-").lower()
    return token or "na"


def alert_artifact_name(prefix, sources, start_date, end_date, ext):
    """Build ``<prefix>_<sources>_<start>_<end>.<ext>``.

    ``sources`` is a list of source labels joined by hyphens; an empty list
    falls back to ``"alerts"``. ``start_date`` / ``end_date`` are tokenized
    (``"na"`` when blank). ``ext`` is the bare extension (no dot).
    """
    sources_token = "-".join(safe_token(s) for s in (sources or [])) or "alerts"
    start = safe_token(start_date) if start_date else "na"
    end = safe_token(end_date) if end_date else "na"
    return f"{prefix}_{sources_token}_{start}_{end}.{ext}"
