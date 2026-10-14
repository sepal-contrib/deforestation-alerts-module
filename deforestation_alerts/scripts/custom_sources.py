"""Legacy digit slots of recipe-era custom sources (``10^(4+slot)``), kept only
so ``report_builder.get_unique_alerts`` can decode a store that has no
``digit_map`` (spec §8). New custom sources use project digits 3-8 and the
rules in ``project/names.py``.
"""

MAX_CUSTOM_SOURCES = 5
CUSTOM_BASE_EXPONENT = 4


def slot_value(slot):
    """Digit value of custom ``slot`` (0-based): 10^4 for slot 0 … 10^8 for slot 4."""
    if not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot < MAX_CUSTOM_SOURCES:
        raise ValueError(f"custom source slot out of range: {slot!r}")
    return 10 ** (CUSTOM_BASE_EXPONENT + slot)
