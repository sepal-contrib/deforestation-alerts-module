"""AlertAnalysisModel: Analysis's per-alert scratch model (slice 2 replaces it
with Solara reactives, spec §10). Moved here from the deleted recipe-era model
tests; the export/import pair is characterized as it is."""

from deforestation_alerts.model.analysis_model import AlertAnalysisModel


def test_analysis_model_export_defaults_and_id_clamp():
    m = AlertAnalysisModel()
    # default actual_alert_id is -1; export clamps it to 0
    exported = m.export_dictionary()
    assert exported["actual_alert_id"] == 0
    assert exported["max_alert_id"] == 0
    assert exported["last_save_time"] == 0.0

    # import restores actual_alert_id and max_alert_id verbatim
    m.import_from_dictionary({"actual_alert_id": 7, "max_alert_id": 9,
                              "last_save_time": 123.0})
    assert m.actual_alert_id == 7
    assert m.max_alert_id == 9
    assert m.last_save_time == 123.0


def test_analysis_model_export_max_alert_id_mirrors_actual_alert_id():
    # CHARACTERIZATION QUIRK: export_dictionary derives "max_alert_id" from
    # actual_alert_id, NOT from max_alert_id (component/model/analysis_model.py).
    # We pin this so a later refactor must change it consciously.
    m = AlertAnalysisModel()
    m.actual_alert_id = 7
    m.max_alert_id = 9
    exported = m.export_dictionary()
    assert exported["actual_alert_id"] == 7
    assert exported["max_alert_id"] == 7  # mirrors actual_alert_id, NOT 9
