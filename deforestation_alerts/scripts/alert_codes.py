"""The integrated alert codes decoded into source names, and the ``alert_sources``
text made from them ("GLAD-L, RADD and CCDC"). Pure Python: the store, the set
summary and Integrate use it without loading the report's libraries."""

import ast


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


def get_unique_alerts(alert_list, digit_map):
    """
    Given a list of integers representing sums of alert values,
    return the alert types found, in the order of their digits.

    Each source of the alert set owns one decimal digit of the code (1 or 2
    there when it flagged the pixel).

    Parameters:
        alert_list (list): A list of integers (or strings representing numbers).
        digit_map (dict[int, str]): ``{digit: name}`` of the alert set.

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

    names = {int(digit): name for digit, name in digit_map.items()}

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
