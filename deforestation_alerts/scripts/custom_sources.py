"""Legacy digit slots of recipe-era custom sources (``10^(4+slot)``), kept only
so ``alert_codes.get_unique_alerts`` can decode a store that has no
``digit_map`` (spec §8). New custom sources use project digits 3-8 and the
rules in ``project/names.py``.
"""

MAX_CUSTOM_SOURCES = 5
CUSTOM_BASE_EXPONENT = 4
