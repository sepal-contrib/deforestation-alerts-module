"""Custom alert source (GEE-id) dictionary wiring — Tier-1 logic.

aoi_helper runs init_ee() at import, so these import it inside the test and depend
on the `gee` fixture; they skip cleanly offline. The logic under test is pure dict
manipulation (no EE round-trip).
"""


def test_add_custom_evicts_prior_custom_and_keeps_builtins(gee):
    from deforestation_alerts.scripts.aoi_helper import (
        add_custom_alerts_dictionary,
        remove_custom_alerts_dictionary,
    )

    md = {
        "GLAD-L": {
            "collection": "projects/glad/alert/UpdResult",
            "start_date": "2018-01-01",
            "end_date": "2026-01-01",
        }
    }

    add_custom_alerts_dictionary(md, "First", "users/x/a", "2015-01-01", "2026-06-10")
    assert md["First"] == {
        "collection": "users/x/a",
        "kind": "CUSTOM",
        "start_date": "2015-01-01",
        "end_date": "2026-06-10",
    }

    # Renaming: there is exactly one custom source, so adding under a new name
    # evicts the previous custom entry (built-ins are untouched).
    add_custom_alerts_dictionary(md, "Second", "users/x/b", "2015-01-01", "2026-06-10")
    assert "First" not in md
    assert md["Second"]["collection"] == "users/x/b"
    assert "GLAD-L" in md

    # Removing drops the custom entry (whatever its current name) and nothing else.
    remove_custom_alerts_dictionary(md)
    assert "Second" not in md
    assert "GLAD-L" in md
