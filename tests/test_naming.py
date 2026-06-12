from deforestation_alerts.scripts.naming import safe_token, alert_artifact_name


def test_safe_token_sanitizes():
    assert safe_token("GLAD-L") == "glad-l"
    assert safe_token("GLAD S2") == "glad-s2"
    assert safe_token("2026-01-01") == "2026-01-01"
    assert safe_token("") == "na"
    assert safe_token(None) == "na"


def test_alert_artifact_name_joins_sources_and_dates():
    name = alert_artifact_name("filtered_alerts", ["GLAD-L", "RADD"],
                               "2026-01-01", "2026-06-12", "tif")
    assert name == "filtered_alerts_glad-l-radd_2026-01-01_2026-06-12.tif"


def test_alert_artifact_name_handles_missing_pieces():
    assert alert_artifact_name("alerts", [], "", "", "gpkg") == "alerts_alerts_na_na.gpkg"
