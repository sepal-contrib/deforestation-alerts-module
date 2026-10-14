"""The integrated alert codes decoded into source names, and the ``alert_sources``
text made from them ("GLAD-L, RADD and CCDC"). Pure Python: the store, the set
summary and Integrate use it without loading the report's libraries."""

import ast

from deforestation_alerts.scripts.custom_sources import CUSTOM_BASE_EXPONENT, MAX_CUSTOM_SOURCES

#: The predefined sources of the fixed layout, by the digit each one owns.
_FIXED_DIGITS = {0: "GLAD-L", 1: "RADD", 2: "GLAD-S2", 3: "CCDC"}


def format_list(items):
    if not items:  # Handle empty list
        return ""
    if len(items) == 1:
        return str(items[0])
    elif len(items) == 2:
        return f"{items[0]} and {items[1]}"
    else:
        return f"{', '.join(map(str, items[:-1]))} and {items[-1]}"


def parse_formatted_string(formatted_string):
    if not formatted_string:  # Handle empty string
        return []
    if " and " not in formatted_string:  # Only one item
        return [formatted_string]
    parts = formatted_string.rsplit(" and ", 1)
    if ", " in parts[0]:  # Multiple items
        return parts[0].split(", ") + [parts[1]]
    else:  # Two items
        return parts


def get_unique_alerts(alert_list, custom_names=None, digit_map=None):
    """
    Given a list of integers representing sums of alert values,
    return the alert types found, in the order of their digits.

    The alert values are constructed by summing contributions from:
    - GLAD-L: ones digit (1 or 2 if triggered)
    - RADD: tens digit (10 or 20 → digit 1 or 2 if triggered)
    - GLAD-S2: hundreds digit (100 or 200 → digit 1 or 2 if triggered)
    - CCDC: thousands digit (1000 or 2000 → digit 1 or 2 if triggered)
    - custom slot s (0–4): digit 10^(4+s) (1 or 2 if triggered)

    Parameters:
        alert_list (list): A list of integers (or strings representing numbers).
        custom_names (dict[int, str] | None): ``{slot: name}`` for the custom
            sources of the run; a slot without a name decodes as "Custom <s+1>".
        digit_map (dict[int, str] | None): ``{digit: name}`` of the alert set;
            when given, it alone decides the names (the fixed layout above and
            ``custom_names`` are ignored).

    Returns:
        List[str]: Unique alert type names found in the list, lowest digit first.
    """
    # If input is a string, attempt to convert it into a Python literal
    if isinstance(alert_list, str):
        try:
            alert_list = ast.literal_eval(alert_list)
        except (ValueError, SyntaxError):
            # If invalid, treat as an empty list for safety
            alert_list = []

    if not isinstance(alert_list, list):
        alert_list = [alert_list]

    if digit_map is not None:
        names = {int(digit): name for digit, name in digit_map.items()}
    else:
        # Custom sources: slot s owns digit 10^(4+s).
        names = {**_FIXED_DIGITS, **{
            CUSTOM_BASE_EXPONENT + slot: (custom_names or {}).get(slot) or f"Custom {slot + 1}"
            for slot in range(MAX_CUSTOM_SOURCES)
        }}

    found = set()
    for value in alert_list:
        try:
            # float() first: the float64 export yields 10000.0, and CSV
            # round-trips can hand the same over as "10000.0".
            value = int(float(value))
        except (TypeError, ValueError):
            continue  # Skip invalid entries
        found.update(digit for digit in names if (value // 10 ** digit) % 10 in (1, 2))

    out = []
    for digit in sorted(found):
        if names[digit] not in out:
            out.append(names[digit])
    return out
