"""User-defined (RADD-shaped) custom alert sources: slots, names, dictionary entries.

Pure — no Earth Engine import — so it is testable offline; ``aoi_helper`` runs
``init_ee()`` at import. AuxModel (recipe import), the Aux dialog (name
checks), the AOI search (dictionary entries) and the Filter step (decoding)
all share these rules.

Encoding: each alert source owns one decimal digit of the per-pixel alert sum
(GLAD-L 10^0, RADD 10^1, GLAD-S2 10^2, CCDC 10^3). Custom source ``slot`` s
owns digit 10^(4+s), so five custom sources fit an Int32 band.
"""

import logging

logger = logging.getLogger(__name__)

MAX_CUSTOM_SOURCES = 5
CUSTOM_BASE_EXPONENT = 4
BUILTIN_SOURCE_NAMES = ("GLAD-L", "RADD", "GLAD-S2", "CCDC")
CUSTOM_KIND = "CUSTOM"


def slot_value(slot):
    """Digit value of custom ``slot`` (0-based): 10^4 for slot 0 … 10^8 for slot 4."""
    if not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot < MAX_CUSTOM_SOURCES:
        raise ValueError(f"custom source slot out of range: {slot!r}")
    return 10 ** (CUSTOM_BASE_EXPONENT + slot)


def _key(name):
    return str(name or "").strip().casefold()


def validate_name(name, existing_names, current=None):
    """Why ``name`` cannot be used, or ``None`` when it can.

    ``existing_names`` are the names already in the list; ``current`` is the
    name of the entry being edited, so keeping it unchanged is not a duplicate.
    """
    key = _key(name)
    if not key:
        return "empty"
    if key in {_key(n) for n in BUILTIN_SOURCE_NAMES}:
        return "reserved"
    others = [_key(n) for n in existing_names]
    if current is not None and _key(current) in others:
        others.remove(_key(current))
    if key in others:
        return "duplicate"
    return None


def normalize_sources(items):
    """Clean a list read from a recipe: trimmed name/asset, valid unique names, ≤ 5."""
    if not isinstance(items, (list, tuple)):
        if items:
            logger.warning("custom_alerts is not a list; ignoring %r", items)
        return []
    kept = []
    for item in items:
        if not isinstance(item, dict):
            logger.warning("Dropping malformed custom alert source %r", item)
            continue
        name = str(item.get("name") or "").strip()
        asset = str(item.get("asset") or "").strip()
        error = validate_name(name, [k["name"] for k in kept])
        if not asset or error is not None:
            logger.warning("Dropping custom alert source %r (%s)", item, error or "no asset")
            continue
        if len(kept) == MAX_CUSTOM_SOURCES:
            logger.warning("Dropping custom alert source %r (more than %d)", item, MAX_CUSTOM_SOURCES)
            continue
        kept.append({"name": name, "asset": asset})
    return kept


def is_custom_entry(entry):
    """True for an alerts-dictionary entry that is a user-defined custom source."""
    return isinstance(entry, dict) and entry.get("kind") == CUSTOM_KIND


def remove_custom_alerts_dictionary(alert_metadata):
    """Remove every custom entry, leaving the built-in sources."""
    for key in [k for k, v in alert_metadata.items() if is_custom_entry(v)]:
        del alert_metadata[key]
    return alert_metadata


def set_custom_alerts_dictionary(alert_metadata, sources, start_date, end_date):
    """Replace the custom entries with ``sources``, slot = list position.

    The name is the dictionary key shown in the Filter source select. Replacing
    the whole set handles renames and deletions without tracking old names.
    """
    remove_custom_alerts_dictionary(alert_metadata)
    for slot, source in enumerate(sources):
        slot_value(slot)  # raises past the cap
        alert_metadata[source["name"]] = {
            "collection": source["asset"],
            "kind": CUSTOM_KIND,
            "slot": slot,
            "start_date": start_date,
            "end_date": end_date,
        }
    return alert_metadata


def custom_names_from_rasters(raster_dict):
    """``{slot: name}`` for the custom sources of one search's raster dictionary.

    The Filter step decodes with this, so names always match the slots the
    rasters were built with, whatever changed in the Aux dialog since.
    """
    if not isinstance(raster_dict, dict):
        return {}
    return {
        entry["custom_slot"]: name
        for name, entry in raster_dict.items()
        if isinstance(entry, dict) and "custom_slot" in entry
    }
