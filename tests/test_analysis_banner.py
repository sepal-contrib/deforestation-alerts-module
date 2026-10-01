"""The Analysis set-status banner (spec §5.3): the same words Overview uses
when the active set is out of date or its files are missing, over the raw
Analysis controls, following every project publish. Never blocks the review.
"""

from datetime import date, datetime

from deforestation_alerts.engine.fingerprint import fingerprint, harm_key, set_inputs
from deforestation_alerts.project.models import (
    AlertSet,
    AlertSetParams,
    Artifact,
    Period,
    Project,
    add_predefined_source,
)
from gui.i18n import t
from gui.store.state_manager import AppState
from gui.tile.analysis.analysis_tile import build_set_status_banner

JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))


def _project(tmp_path):
    now = datetime.now()
    p = Project(name="d", created_at=now, updated_at=now)
    add_predefined_source(p, "GLAD-L")
    p.periods[JAN.id] = JAN
    p.active_period = JAN.id
    p.artifacts[harm_key(JAN.id, "GLAD-L")] = Artifact(
        key=harm_key(JAN.id, "GLAD-L"), kind="harmonized", rel_path="h.tif", fingerprint="x", inputs={},
        content_id="h1", created_at=now)
    rel = f"periods/{JAN.id}/sets/glad-l"
    s = AlertSet(id="x", period_id=JAN.id, sources=["GLAD-L"], digit_map={0: "GLAD-L"}, params=AlertSetParams(),
                 store_rel_path=f"{rel}/alert_db.parquet", combined_rel_path=f"{rel}/c.tif",
                 footprint_rel_path=f"{rel}/footprint.tif", n_alerts=4,
                 fingerprint=fingerprint(set_inputs(p, JAN, ["GLAD-L"], AlertSetParams())), created_at=now)
    p.alert_sets[s.id] = s
    p.active_set = s.id
    store = tmp_path / "d" / s.store_rel_path
    store.parent.mkdir(parents=True)
    store.write_bytes(b"store")
    return p, store


def _shown(banner):
    return None if banner.style_ == "display: none;" else banner.children


def test_the_banner_follows_the_active_sets_status(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    state = AppState()
    banner = build_set_status_banner(state)
    assert _shown(banner) is None  # no project

    p, store = _project(tmp_path)
    state.load_project_state(p)
    assert _shown(banner) is None  # a current set

    state.update_project("d", lambda live: setattr(live.artifacts[harm_key(JAN.id, "GLAD-L")], "content_id", "h2"))
    assert _shown(banner) == [t("steps.overview.stale")]  # re-harmonized since: out of date

    state.update_project("d", lambda live: setattr(live.artifacts[harm_key(JAN.id, "GLAD-L")], "content_id", "h1"))
    assert _shown(banner) is None

    store.unlink()
    state.update_project("d", lambda live: None)  # any publish (an auto-save) re-reads the status
    assert _shown(banner) == [t("steps.overview.missing")]

    state.close_project_state()
    assert _shown(banner) is None
