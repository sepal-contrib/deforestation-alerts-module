"""Fill a dialog's Name field from the raster the user picks (spec §4).

The suggestion follows the raster until the user types a name of their own:
a field that still holds the last suggestion (or nothing) is refilled when
the raster changes; a typed name is never overwritten."""

import solara


def use_default_name(ref, name, set_name, suggest) -> bool:
    """Hook: fill ``name`` from ``suggest(ref)`` when ``ref`` changes. True while
    the name is still the suggestion (or empty), so a field can keep its hint
    until the user types a name of their own."""
    last = solara.use_ref("")

    def _fill():
        if not ref:
            return
        if name and name != last.current:
            return  # the user's own name
        value = suggest(ref)
        last.current = value
        if value and value != name:
            set_name(value)

    solara.use_effect(_fill, [ref])
    return not name or name == last.current
